"""
upload_log.py — every upload attempt, recorded (V0.9.7).

Why: "my laps don't show up" must be answerable in a minute. Each attempt leaves one row in
upload_events (outcome, HTTP status, a short reason, request id, lap id, client version, size,
duration) and one log line with the same ids. GET /admin/operations turns the rows into error
rates, the most common reasons and the slowest uploads.

Never in the way of an upload: written in its own short session AFTER the upload's own
transaction finished, and any failure here is logged and swallowed.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from .database import SessionLocal
from .models import UploadEvent

logger = logging.getLogger("lmu_garage.upload")

UPLOAD_EVENT_RETENTION_DAYS = 30
# V0.9.8: the 30 days hold whatever the upload frequency: old rows are removed at server start
# and then at most once an hour (by the next upload), in batches — never one huge delete.
PRUNE_INTERVAL_S = 3600
PRUNE_BATCH = 5000
PRUNE_MAX_BATCHES = 20
_last_prune = 0.0
_prune_lock = threading.Lock()
OUTCOMES = ("accepted", "duplicate", "rejected", "error")


def reason_of(detail) -> str:
    """A short, stable reason from an HTTPException detail (a code when there is one)."""
    if isinstance(detail, dict):
        return str(detail.get("code") or detail.get("message") or "")[:200]
    return str(detail or "")[:200]


def record_upload(*, request_id: Optional[str], driver_id: Optional[int], status_code: int, outcome: str,
                  started: float, reason: Optional[str] = None, lap_id: Optional[int] = None,
                  client_version: Optional[str] = None, track_name: Optional[str] = None,
                  file_bytes: Optional[int] = None) -> None:
    duration_ms = round((time.monotonic() - started) * 1000.0, 1)
    level = logging.INFO if outcome in ("accepted", "duplicate") else logging.WARNING
    logger.log(level, "upload %s (%d) lap=%s driver=%s %s [%s]", outcome, status_code, lap_id, driver_id,
               reason or "", request_id,
               extra={"request_id": request_id, "lap_id": lap_id, "driver_id": driver_id, "outcome": outcome,
                      "status": status_code, "duration_ms": duration_ms})
    try:
        with SessionLocal() as db:
            event = UploadEvent(request_id=request_id, driver_id=driver_id, lap_id=lap_id, outcome=outcome,
                                status_code=status_code, reason=reason, client_version=client_version,
                                track_name=track_name, file_bytes=file_bytes, duration_ms=duration_ms)
            db.add(event)
            db.commit()
    except Exception as exc:  # noqa: BLE001 — the log must never break an upload
        logger.warning("Could not record the upload event: %s", exc)
    maybe_prune()


def prune(db, *, now: Optional[datetime] = None, batch: int = PRUNE_BATCH, max_batches: int = PRUNE_MAX_BATCHES) -> int:
    """Removes rows older than UPLOAD_EVENT_RETENTION_DAYS, batch rows at a time (oldest first,
    via the created_at index), at most max_batches per call. Returns how many were removed."""
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    cutoff = now - timedelta(days=UPLOAD_EVENT_RETENTION_DAYS)
    removed = 0
    for _ in range(max_batches):
        ids = [i for (i,) in db.query(UploadEvent.id).filter(UploadEvent.created_at < cutoff)
               .order_by(UploadEvent.created_at).limit(batch).all()]
        if not ids:
            break
        db.query(UploadEvent).filter(UploadEvent.id.in_(ids)).delete(synchronize_session=False)
        db.commit()
        removed += len(ids)
        if len(ids) < batch:
            break
    if removed:
        logger.info("Removed %d upload log rows older than %d days", removed, UPLOAD_EVENT_RETENTION_DAYS)
    return removed


def maybe_prune(force: bool = False) -> int:
    """prune() at most once per PRUNE_INTERVAL_S in this process (force: now). Never raises."""
    global _last_prune
    with _prune_lock:
        if not force and time.monotonic() - _last_prune < PRUNE_INTERVAL_S:
            return 0
        _last_prune = time.monotonic()
    try:
        with SessionLocal() as db:
            removed = prune(db)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Pruning the upload log failed: %s", exc)
        return 0
    # V0.9.9: the audit log has its own (longer) retention and records each pruning itself
    try:
        from . import audit

        with SessionLocal() as db:
            audit.prune(db)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Pruning the audit log failed: %s", exc)
    return removed


def _event_dict(r) -> dict:
    return {"at": r.created_at.isoformat() if r.created_at else None, "request_id": r.request_id,
            "driver_id": r.driver_id, "lap_id": r.lap_id, "status_code": r.status_code, "outcome": r.outcome,
            "reason": r.reason, "client_version": r.client_version, "track_name": r.track_name,
            "file_bytes": r.file_bytes, "duration_ms": r.duration_ms}


def find(db, request_id: str, limit: int = 20) -> list:
    """V0.9.8: drill-down — every recorded attempt with this request id."""
    rows = db.query(UploadEvent).filter(UploadEvent.request_id == request_id).order_by(UploadEvent.id).limit(limit).all()
    return [_event_dict(r) for r in rows]


MAX_SUMMARY_ROWS = 200_000   # V0.9.8: the view never reads more than this many rows at once


def summary(db, hours: int = 24, recent: int = 50, client_version: Optional[str] = None,
            outcome: Optional[str] = None) -> dict:
    """The operations view: counts per outcome, error rate, reasons, durations, per client
    version, uploads per hour (V0.9.8), the latest failures. Optional filters: client version,
    outcome. Reads only the columns it needs, capped at MAX_SUMMARY_ROWS."""
    since = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=hours)
    q = db.query(UploadEvent).filter(UploadEvent.created_at >= since)
    if client_version:
        q = q.filter(UploadEvent.client_version == client_version)
    if outcome:
        q = q.filter(UploadEvent.outcome == outcome)
    rows = q.order_by(UploadEvent.created_at.desc()).limit(MAX_SUMMARY_ROWS).all()
    timeline: dict = {}
    for r in rows:
        if r.created_at:
            hour = r.created_at.replace(minute=0, second=0, microsecond=0).isoformat()
            slot = timeline.setdefault(hour, {"hour": hour, "uploads": 0, "failed": 0})
            slot["uploads"] += 1
            slot["failed"] += r.outcome in ("rejected", "error")
    counts = {o: 0 for o in OUTCOMES}
    reasons: dict = {}
    versions: dict = {}
    durations = []
    for r in rows:
        counts[r.outcome] = counts.get(r.outcome, 0) + 1
        if r.outcome in ("rejected", "error"):
            key = f"{r.status_code} {r.reason or ''}".strip()
            reasons[key] = reasons.get(key, 0) + 1
        v = versions.setdefault(r.client_version or "?", {"uploads": 0, "failed": 0})
        v["uploads"] += 1
        v["failed"] += r.outcome in ("rejected", "error")
        if r.duration_ms is not None and r.outcome == "accepted":
            durations.append(r.duration_ms)
    total = len(rows)
    failed = counts["rejected"] + counts["error"]
    durations.sort()

    def q(p):
        return durations[min(len(durations) - 1, int(p * (len(durations) - 1) + 0.5))] if durations else None

    latest_q = db.query(UploadEvent).filter(UploadEvent.outcome.in_(("rejected", "error")))
    if client_version:
        latest_q = latest_q.filter(UploadEvent.client_version == client_version)
    latest = latest_q.order_by(UploadEvent.id.desc()).limit(recent).all()
    return {
        "hours": hours,
        "total": total,
        "counts": counts,
        "failure_rate": round(failed / total, 4) if total else None,
        "server_errors": counts["error"],
        "reasons": sorted(({"reason": k, "count": n} for k, n in reasons.items()), key=lambda x: -x["count"])[:15],
        "client_versions": [{"version": k, **v} for k, v in sorted(versions.items())],
        "duration_ms": {"p50": q(0.5), "p95": q(0.95), "max": durations[-1] if durations else None},
        "timeline": sorted(timeline.values(), key=lambda x: x["hour"]),
        "truncated": len(rows) >= MAX_SUMMARY_ROWS,
        "recent_failures": [_event_dict(r) for r in latest],
    }
