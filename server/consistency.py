"""
consistency.py — do the database and the telemetry files still belong together? (V0.9.7)

One report over everything that can drift apart between laps in the database and files on
disk, and between rows of different tables:

  missing_files      a lap whose telemetry file is gone                         (never auto-fixed)
  outside_storage    a lap whose stored path points outside the storage folder  (never auto-fixed)
  orphaned_files     files no lap points at, older than an hour        (--delete-orphans removes them)
  stale_uploads      leftover .upload-*.tmp files of interrupted uploads, older than an hour (same)
  unreadable_files   files that cannot be opened as parquet                  (deep only; reported)
  hash_mismatch      file content no longer matches the lap's sha256         (deep only; reported)
  size_mismatch      V0.9.9: file size differs from the size stored at upload   (reported)
  mac_invalid        V0.9.9: the lap's stored sha256 / size / lap time do not match the server's
                     MAC — edited in the database or restored from a manipulated backup (reported)
  quarantined        V0.9.9: laps set aside after an integrity failure            (listed, not a problem)
  audit_chain        V0.9.9: the audit log's chain is broken                      (deep only; reported)
  audit_unverifiable V0.9.9: audit rows signed with a key no longer configured  (deep only; listed)
  not_judged         laps without a stored verdict (laps.validation)  -> scripts.revalidate_laps
  stale_figures      laps whose stored figures (laps.lap_stats) were computed by an older
                     analysis (INSIGHTS_VERSION)                    (--recompute-figures redoes them)
  dangling_rows      rows pointing at a lap / driver / team that no longer exists (reported)

Dry run by default — finding never changes anything. The two repairs that are safe on their own
(removing files nothing references, recomputing figures from the file) have to be asked for
explicitly; everything that would hide a data loss (a lap without its file) is only reported,
for an admin to decide. Run it after every restore: it is the check that the database and the
telemetry volume were restored from the same moment.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from . import integrity
from .config import settings
from .lap_insights import INSIGHTS_VERSION
from .models import Driver, Lap, LapReport, RecordEvent, Team, TeamMembership
from .telemetry_lifecycle import DEFAULT_MIN_ORPHAN_AGE, find_orphaned_files, stored_path

logger = logging.getLogger("lmu_garage.consistency")

MAX_LISTED = 100


def _inside(root: Path, path: Path) -> bool:
    try:
        resolved = path.resolve()
    except (OSError, RuntimeError):
        return False
    return resolved == root or root in resolved.parents


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _metadata_problems(lap) -> List[str]:
    """V0.9.8: what a stored lap must have and the upload check guarantees for new ones."""
    out = []
    if not (isinstance(lap.lap_time, (int, float)) and lap.lap_time > 0):
        out.append("lap_time")
    sectors = lap.sector_times
    if not (isinstance(sectors, list) and len(sectors) == 3 and all(isinstance(x, (int, float)) for x in sectors)):
        out.append("sector_times")
    if not lap.telemetry_hash or len(lap.telemetry_hash) != 64:
        out.append("telemetry_hash")
    if not (lap.track_name or "").strip() or not (lap.car_name or "").strip():
        out.append("track/car")
    return out


def _deep_file_check(lap, path: Path, add) -> None:
    """Readable as parquet, and the content still matches the lap's sha256."""
    try:
        import pyarrow.parquet as pq

        pq.read_schema(path)
    except Exception as exc:  # noqa: BLE001
        add("unreadable_files", {"lap_id": lap.id, "error": str(exc)[:200]})
    else:
        if lap.telemetry_hash and _sha256(path) != lap.telemetry_hash:
            add("hash_mismatch", {"lap_id": lap.id, "path": str(path)})


def check(db: Session, *, deep: bool = False, min_orphan_age: timedelta = DEFAULT_MIN_ORPHAN_AGE,
          stored_prefix: Optional[str] = None) -> dict:
    """The report: {"ok", "counts": {kind: n}, "items": {kind: [...]} (at most MAX_LISTED each),
    "deep", "checked_laps", "storage"}."""
    root = settings.telemetry_storage_dir.resolve()
    items: Dict[str, List[dict]] = {k: [] for k in (
        "missing_files", "outside_storage", "orphaned_files", "stale_uploads", "unreadable_files",
        "hash_mismatch", "not_judged", "stale_figures", "dangling_rows", "bad_metadata", "unsupported_figures",
        "size_mismatch", "mac_invalid", "quarantined", "audit_chain", "audit_unverifiable")}
    counts = {k: 0 for k in items}

    def add(kind: str, entry: dict) -> None:
        counts[kind] += 1
        if len(items[kind]) < MAX_LISTED:
            items[kind].append(entry)

    driver_ids = {d for (d,) in db.query(Driver.id).all()}
    team_ids = {t for (t,) in db.query(Team.id).all()}
    laps = db.query(Lap.id, Lap.driver_id, Lap.telemetry_path, Lap.telemetry_hash, Lap.validation, Lap.lap_stats,
                    Lap.lap_time, Lap.sector_times, Lap.track_name, Lap.car_name, Lap.telemetry_bytes,
                    Lap.integrity_mac, Lap.review_status).all()
    lap_ids = {lap.id for lap in laps}
    for lap in laps:
        path = stored_path(lap.telemetry_path, stored_prefix)
        if lap.driver_id not in driver_ids:
            add("dangling_rows", {"table": "laps", "id": lap.id, "missing": f"driver {lap.driver_id}"})
        if not _inside(root, path):
            add("outside_storage", {"lap_id": lap.id, "path": str(path)})
        elif not path.is_file():
            add("missing_files", {"lap_id": lap.id, "driver_id": lap.driver_id, "path": str(path)})
        else:
            if lap.telemetry_bytes is not None and path.stat().st_size != lap.telemetry_bytes:
                add("size_mismatch", {"lap_id": lap.id, "expected": lap.telemetry_bytes, "found": path.stat().st_size})
            if deep:
                _deep_file_check(lap, path, add)
        if not isinstance(lap.validation, dict) or not lap.validation:
            add("not_judged", {"lap_id": lap.id})
        stats = lap.lap_stats if isinstance(lap.lap_stats, dict) else None
        version = stats.get("version") if stats else None
        if isinstance(version, int) and version > INSIGHTS_VERSION:
            # V0.9.8: written by a NEWER server (a rollback after an update) — this one cannot
            # interpret them; they are kept, never "recomputed" downwards
            add("unsupported_figures", {"lap_id": lap.id, "version": version})
        elif not isinstance(version, int) or version < INSIGHTS_VERSION:
            add("stale_figures", {"lap_id": lap.id, "version": version})
        problems_here = _metadata_problems(lap)
        if problems_here:
            add("bad_metadata", {"lap_id": lap.id, "problems": problems_here})
        # V0.9.9: the server's MAC over the lap's identity, hash, size and lap time
        if lap.integrity_mac and lap.telemetry_bytes is not None and not problems_here:
            state = integrity.check_mac(
                integrity.lap_message(lap.id, lap.driver_id, lap.telemetry_hash, lap.telemetry_bytes, lap.lap_time),
                lap.integrity_mac)
            if state == integrity.MAC_INVALID:
                add("mac_invalid", {"lap_id": lap.id})
        elif lap.integrity_mac and lap.telemetry_bytes is None:
            add("mac_invalid", {"lap_id": lap.id, "why": "MAC without a stored size"})
        if lap.review_status == "quarantined":
            add("quarantined", {"lap_id": lap.id, "reason": ((lap.validation or {}).get("quarantine") or {}).get("reason")})

    for orphan in find_orphaned_files(db, min_age=min_orphan_age, stored_prefix=stored_prefix):
        entry = {"path": str(orphan.path), "bytes": orphan.size_bytes, "modified_at": orphan.modified_at.isoformat()}
        add("stale_uploads" if orphan.path.name.startswith(".upload-") else "orphaned_files", entry)

    for (rid, lap_id) in db.query(RecordEvent.id, RecordEvent.lap_id).all():
        if lap_id not in lap_ids:
            add("dangling_rows", {"table": "record_events", "id": rid, "missing": f"lap {lap_id}"})
    for (rid, lap_id) in db.query(LapReport.id, LapReport.lap_id).all():
        if lap_id not in lap_ids:
            add("dangling_rows", {"table": "lap_reports", "id": rid, "missing": f"lap {lap_id}"})
    for (mid, team_id, driver_id) in db.query(TeamMembership.id, TeamMembership.team_id, TeamMembership.driver_id).all():
        if team_id not in team_ids or driver_id not in driver_ids:
            add("dangling_rows", {"table": "team_memberships", "id": mid,
                                  "missing": f"team {team_id}" if team_id not in team_ids else f"driver {driver_id}"})

    if deep:
        from . import audit

        chain = audit.verify_db(db)
        if not chain["ok"] and chain["unknown_key"] and "key" in (chain["problem"] or ""):
            # rows signed with a retired key: reported, but not a reason to refuse a restore
            add("audit_unverifiable", {"first": chain["first_broken"], "rows": chain["unknown_key"]})
        elif not chain["ok"]:
            add("audit_chain", {"first_broken": chain["first_broken"], "problem": chain["problem"]})

    problems = ("missing_files", "outside_storage", "unreadable_files", "hash_mismatch", "dangling_rows", "bad_metadata",
                "size_mismatch", "mac_invalid", "audit_chain")
    return {
        "ok": not any(counts[k] for k in problems),
        "deep": deep,
        "checked_laps": len(laps),
        "storage": str(root),
        "insights_version": INSIGHTS_VERSION,
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "counts": counts,
        "items": items,
    }


def delete_orphans(db: Session, *, min_age: timedelta = DEFAULT_MIN_ORPHAN_AGE) -> int:
    """Removes files no lap points at (and stale upload temp files), older than min_age.

    V0.9.8: under the shared write lock (no upload can store a lap for one of these files while
    they are removed, and a backup never sees half of it), and every candidate is checked against
    the database AGAIN right before it is removed — a file that got referenced between the scan
    and the deletion stays."""
    from .write_lock import writing

    removed = 0
    with writing(wait_s=30):
        candidates = find_orphaned_files(db, min_age=min_age)
        db.expire_all()
        known = {Path(p).resolve() for (p,) in db.query(Lap.telemetry_path).all()}
        for orphan in candidates:
            if orphan.path.resolve() in known:
                logger.info("Not removing %s — a lap points at it now", orphan.path)
                continue
            try:
                orphan.path.unlink()
                removed += 1
            except OSError as exc:
                logger.warning("Could not remove %s: %s", orphan.path, exc)
    return removed


def recompute_figures(db: Session, lap_ids: Optional[List[int]] = None, limit: Optional[int] = None) -> dict:
    """Recomputes laps.lap_stats from the stored file with the CURRENT analysis for laps whose
    figures are older than INSIGHTS_VERSION (or the given ids). Does not touch validity, the
    verdict or the review queue (that is scripts.revalidate_laps). The session settings stored
    with the old figures (from the upload's metadata, not from the file) are kept."""
    from .lap_check import check_lap, inputs_for_lap

    query = db.query(Lap)
    if lap_ids:
        query = query.filter(Lap.id.in_(lap_ids))
    done = failed = skipped = 0
    for lap in query.order_by(Lap.id).all():
        stats = lap.lap_stats if isinstance(lap.lap_stats, dict) else {}
        if not lap_ids and isinstance(stats.get("version"), int) and stats["version"] >= INSIGHTS_VERSION:
            continue
        if limit is not None and done >= limit:
            break
        path = Path(lap.telemetry_path)
        if not _inside(settings.telemetry_storage_dir.resolve(), path) or not path.is_file():
            skipped += 1
            continue
        metadata = SimpleNamespace(
            track_name=lap.track_name, lap_time=lap.lap_time, sector_times=lap.sector_times or [],
            sample_count=lap.sample_count, tail_samples=None, game_lap_state=None, lap_report=None,
            track_grip_level=None, fixed_setup=None, track_limits_steps_per_point=None,
            track_limits_steps_per_penalty=None)
        try:
            result = check_lap(db, path, metadata, client_valid=lap.client_claimed_valid,
                               exclude_lap_id=lap.id, inputs=inputs_for_lap(lap))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Recomputing the figures of lap %s failed: %s", lap.id, exc)
            failed += 1
            continue
        new = dict(result.stats or {})
        if not new.get("version"):
            failed += 1
            continue
        if "session" in stats and "session" not in new:
            new["session"] = stats["session"]
        lap.lap_stats = new
        done += 1
    db.commit()
    return {"recomputed": done, "failed": failed, "skipped_without_file": skipped}
