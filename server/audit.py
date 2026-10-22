"""
audit.py — a chained log of integrity, validation and admin events (V0.9.9).

What is logged (`kind`):
  lap.accepted       an upload was stored: sha256, size, the decision with the validator versions
  lap.quarantined    a lap's file failed its integrity check (server/integrity.py)
  lap.released       an admin released a quarantined lap after its file verified again
  lap.reviewed       an admin cleared or invalidated a lap from the review queue
  lap.invalidated    an admin invalidated a lap directly
  lap.revalidated    scripts/revalidate_laps.py --apply changed a stored verdict
  lap.backfilled     scripts/integrity_check.py --backfill signed a lap stored before V0.9.9
  lap.resigned       scripts/integrity_check.py --resign re-signed a lap with the current key
  integrity.sweep    a full integrity check ran (counts only)
  driver.deleted     a driver and their laps were deleted (counts only, no name)
  audit.pruned       rows older than the retention were removed (anchor for verification)
Rejected uploads are in the upload log (server/upload_log.py), not here.

Never logged: secrets, tokens, passwords, e-mail addresses, display names, IP addresses.

The chain
---------
Each row's `mac` = HMAC(integrity key, previous row's mac + "\\n" + canonical JSON of this row's
kind, actor, lap id, request id and detail — which carries the timestamp as text). `verify_chain`
recomputes every link: a row that was changed, deleted or inserted afterwards breaks it at that
point. Rows are appended under a lock (PostgreSQL advisory lock; SQLite serialises writers anyway).
A database trigger additionally refuses UPDATEs of stored rows (migration 0017).

Limits, stated plainly: a hash chain protects against changes by somebody WITHOUT the integrity
key. Whoever holds the key and can write the database can rewrite the whole chain consistently;
the only defence against that is a copy of the chain head kept elsewhere — every backup manifest
records it (scripts/backup.sh, taken from the dump itself), `scripts/restore.sh` refuses a backup
whose restored head differs (`compare_head`, V0.9.9.1), and `scripts/integrity_check.py --audit`
prints it.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Iterable, List, Optional

import sqlalchemy as sa
from sqlalchemy.orm import Session

from . import integrity
from .models import AuditEvent

logger = logging.getLogger("lmu_garage.audit")

RETENTION_DAYS_DEFAULT = 365
_LOCK_ID = 0x6761726167653136  # "garage16"


def retention_days() -> int:
    import os

    try:
        return max(30, int(os.environ.get("LMU_GARAGE_AUDIT_RETENTION_DAYS", RETENTION_DAYS_DEFAULT)))
    except ValueError:
        return RETENTION_DAYS_DEFAULT


def _canonical(kind: str, actor: str, lap_id, request_id, detail) -> str:
    return json.dumps({"kind": kind, "actor": actor, "lap_id": lap_id, "request_id": request_id, "detail": detail},
                      sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)


def _message(prev: Optional[str], content: str) -> bytes:
    return f"{prev or ''}\n{content}".encode()


def _lock(db: Session) -> None:
    if db.get_bind().dialect.name == "postgresql":
        db.execute(sa.text("SELECT pg_advisory_xact_lock(:k)"), {"k": _LOCK_ID})


def _clean(detail: Optional[dict]) -> dict:
    """JSON-safe, small: drops None, cuts long strings and lists."""
    out = {}
    for key, value in (detail or {}).items():
        if value is None:
            continue
        if isinstance(value, str):
            value = value[:300]
        elif isinstance(value, (list, tuple)):
            value = list(value)[:50]
        out[str(key)] = value
    return out


def append(db: Session, kind: str, *, actor: str = "system", lap_id: Optional[int] = None,
           driver_id: Optional[int] = None, request_id: Optional[str] = None,
           detail: Optional[dict] = None) -> AuditEvent:
    """Appends one event in the caller's transaction (the caller commits). Call it LAST before
    the commit — the chain lock is held until the transaction ends."""
    _lock(db)
    now = datetime.now(timezone.utc)
    body = {"ts": now.isoformat(timespec="microseconds"), **_clean(detail)}
    body = json.loads(json.dumps(body, default=str))           # exactly what the database will return
    prev = db.query(AuditEvent.mac).order_by(AuditEvent.id.desc()).limit(1).scalar()
    request_id = (request_id or None) and str(request_id)[:64]
    content = _canonical(kind, actor, lap_id, request_id, body)
    row = AuditEvent(created_at=now.replace(tzinfo=None), kind=kind, actor=actor, lap_id=lap_id, driver_id=driver_id,
                     request_id=request_id, detail=body, prev_mac=prev,
                     mac=integrity.sign(_message(prev, content)))
    db.add(row)
    db.flush()
    return row


def head(db: Session) -> Optional[dict]:
    row = db.query(AuditEvent.id, AuditEvent.mac).order_by(AuditEvent.id.desc()).first()
    return {"id": row.id, "mac": row.mac} if row else None


# what a backup manifest says about the chain head (V0.9.9.1, scripts/restore.sh step 4)
HEAD_ABSENT = "absent"          # manifest without the key: made before V0.9.9 — nothing to compare
HEAD_NONE = "none"              # "audit_head": null — the backed-up database had no audit row


def parse_head_spec(spec: str):
    """'absent' | 'none' | '<id>:<mac>' -> HEAD_ABSENT | HEAD_NONE | {"id", "mac"}. ValueError otherwise
    (an unreadable anchor is never treated as "no anchor")."""
    spec = (spec or "").strip()
    if spec in (HEAD_ABSENT, HEAD_NONE):
        return spec
    ident, sep, mac = spec.partition(":")
    if not sep or not ident.isdigit() or not mac:
        raise ValueError(f"not an audit head: {spec!r} (expected 'absent', 'none' or '<id>:<mac>')")
    return {"id": int(ident), "mac": mac}


def compare_head(db: Session, expected) -> dict:
    """The chain head of this database against the one a backup manifest recorded.
    {"ok", "expected", "found", "problem"}. Rules (docs/RUNBOOKS.md 6):
      HEAD_ABSENT  backup from before V0.9.9: ok, nothing to compare (the chain itself is still
                   checked by consistency_check --deep)
      HEAD_NONE    the database must have no audit row
      {id, mac}    the newest row must be exactly that id with exactly that mac — a deleted last
                   row, a row added after the backup, or a last row rewritten (even with the key)
                   all differ"""
    found = head(db)
    if expected == HEAD_ABSENT:
        return {"ok": True, "expected": None, "found": found, "problem": None}
    if expected == HEAD_NONE:
        ok = found is None
        return {"ok": ok, "expected": None, "found": found,
                "problem": None if ok else "the backup recorded no audit row, the database has some"}
    if found is None:
        return {"ok": False, "expected": expected, "found": None, "problem": "the database has no audit row"}
    if found["id"] != expected["id"]:
        problem = (f"newest row is {found['id']}, the backup recorded {expected['id']} — "
                   + ("rows at the end are missing" if found["id"] < expected["id"] else "rows were added"))
        return {"ok": False, "expected": expected, "found": found, "problem": problem}
    if found["mac"] != expected["mac"]:
        return {"ok": False, "expected": expected, "found": found, "problem": f"row {found['id']} differs from the backup"}
    return {"ok": True, "expected": expected, "found": found, "problem": None}


def verify_chain(rows: Iterable[AuditEvent]) -> dict:
    """{"ok", "rows", "first_broken": id|None, "problem", "unknown_key": n, "anchor"}. The first
    row's prev_mac is the anchor (rows before it were pruned — an audit.pruned event says so).
    A row signed with a key this server does not know makes the result not ok: it cannot be told
    apart from a row inserted without the key."""
    count, unknown = 0, 0
    first_unknown = None
    previous_mac = None
    anchor = None
    for row in rows:
        count += 1
        if count == 1:
            anchor = row.prev_mac
            previous_mac = row.prev_mac
        if row.prev_mac != previous_mac:
            return {"ok": False, "rows": count, "first_broken": row.id, "problem": "link (a row before it was removed or changed)",
                    "unknown_key": unknown, "anchor": anchor}
        content = _canonical(row.kind, row.actor, row.lap_id, row.request_id, row.detail)
        state = integrity.check_mac(_message(row.prev_mac, content), row.mac)
        if state == integrity.UNKNOWN_KEY:
            # cannot be told from a forged row: the chain is NOT confirmed (keep retired keys
            # configured while rows signed with them are kept — docs/RUNBOOKS.md, key rotation)
            unknown += 1
            if first_unknown is None:
                first_unknown = row.id
        elif state != integrity.OK:
            return {"ok": False, "rows": count, "first_broken": row.id, "problem": "content (this row was changed)",
                    "unknown_key": unknown, "anchor": anchor}
        previous_mac = row.mac
    if unknown:
        return {"ok": False, "rows": count, "first_broken": first_unknown,
                "problem": "signed with a key this server does not have (forged, or the key was removed)",
                "unknown_key": unknown, "anchor": anchor}
    return {"ok": True, "rows": count, "first_broken": None, "problem": None, "unknown_key": unknown, "anchor": anchor}


def verify_db(db: Session, batch: int = 2000) -> dict:
    def rows():
        last = 0
        while True:
            chunk = db.query(AuditEvent).filter(AuditEvent.id > last).order_by(AuditEvent.id).limit(batch).all()
            if not chunk:
                return
            yield from chunk
            last = chunk[-1].id
    result = verify_chain(rows())
    result["head"] = head(db)
    return result


def prune(db: Session, now: Optional[datetime] = None, batch: int = 5000, max_batches: int = 20) -> int:
    """Removes rows older than the retention (in batches) and records the anchor. Commits."""
    now = now or datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=retention_days())).replace(tzinfo=None)
    removed = 0
    last_removed = None
    for _ in range(max_batches):
        ids = [i for (i,) in db.query(AuditEvent.id).filter(AuditEvent.created_at < cutoff)
               .order_by(AuditEvent.id).limit(batch).all()]
        if not ids:
            break
        last_removed = db.query(AuditEvent.mac).filter(AuditEvent.id == ids[-1]).scalar()
        db.query(AuditEvent).filter(AuditEvent.id.in_(ids)).delete(synchronize_session=False)
        removed += len(ids)
        db.commit()
    if removed:
        append(db, "audit.pruned", actor="system", detail={"rows": removed, "last_removed_mac": last_removed,
                                                          "retention_days": retention_days()})
        db.commit()
    return removed


def clear_driver(db: Session, driver_id: int) -> int:
    """Driver deletion: their id leaves the log (not covered by the chain); events stay."""
    return db.query(AuditEvent).filter(AuditEvent.driver_id == driver_id).update(
        {AuditEvent.driver_id: None}, synchronize_session=False)


def for_driver(db: Session, driver_id: int, limit: int = 5000) -> List[dict]:
    rows = (db.query(AuditEvent).filter(AuditEvent.driver_id == driver_id)
            .order_by(AuditEvent.id).limit(limit).all())
    return [{"at": r.created_at.isoformat() if r.created_at else None, "kind": r.kind, "lap_id": r.lap_id,
             "detail": r.detail} for r in rows]
