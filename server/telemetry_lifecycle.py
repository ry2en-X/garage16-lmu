"""telemetry_lifecycle.py — Orphan detection for telemetry storage (V0.7.2
completion sprint, §12).

Two distinct discrepancies between the DB and the storage directory, and
this module deliberately treats them very differently:

  - DB row exists, file missing: reported only, NEVER auto-resolved. A
    Lap row losing its telemetry file (bad restore, manual disk
    intervention, a bug) is a data-loss event an admin should look at and
    decide on — silently invalidating the lap or deleting the row would
    hide the problem instead of surfacing it.

  - File exists, no DB row: a genuine cleanup candidate, but NOT
    automatically safe to delete on sight. routers/telemetry.py's upload
    handler renames the uploaded file to its final content-addressed path
    BEFORE committing the Lap row (see that module's step 6 vs. step 7) —
    so a file with no matching row yet might just be an upload that's
    still in flight, a few milliseconds from getting its row. This module
    only calls a file "orphaned" once it's older than `min_age` (default
    1 hour — generous relative to how long step 6->7 could plausibly
    take, including a slow validation pass), so a live upload is never at
    risk of being swept up mid-flight.

Read-only by design: this module only ever finds and reports. Deleting is
scripts/telemetry_cleanup.py's job, gated behind an explicit --execute
flag — see that script's docstring.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List

from sqlalchemy.orm import Session

from .config import settings
from .models import Lap

DEFAULT_MIN_ORPHAN_AGE = timedelta(hours=1)


@dataclass
class MissingFile:
    lap_id: int
    driver_id: int
    telemetry_path: str


@dataclass
class OrphanFile:
    path: Path
    size_bytes: int
    modified_at: datetime


def find_missing_files(db: Session) -> List[MissingFile]:
    """Lap rows whose telemetry_path doesn't exist on disk. Always safe to
    call — pure read, no filesystem mutation, cheap (one query + a stat
    call per lap)."""
    missing = []
    for lap in db.query(Lap.id, Lap.driver_id, Lap.telemetry_path).all():
        if not Path(lap.telemetry_path).is_file():
            missing.append(MissingFile(lap_id=lap.id, driver_id=lap.driver_id, telemetry_path=lap.telemetry_path))
    return missing


def find_orphaned_files(db: Session, *, min_age: timedelta = DEFAULT_MIN_ORPHAN_AGE) -> List[OrphanFile]:
    """Files under the telemetry storage root with no matching
    Lap.telemetry_path, older than `min_age` (see module docstring for
    why the age check exists — it's the actual safety mechanism here, not
    a tuning knob to relax casually)."""
    known_paths = {
        Path(p).resolve() for (p,) in db.query(Lap.telemetry_path).all()
    }
    cutoff = datetime.now(timezone.utc).timestamp() - min_age.total_seconds()

    root = settings.telemetry_storage_dir
    if not root.exists():
        return []

    orphans = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.resolve() in known_paths:
            continue
        stat = path.stat()
        if stat.st_mtime >= cutoff:
            continue  # too recent to be sure it isn't mid-upload
        orphans.append(OrphanFile(path=path, size_bytes=stat.st_size, modified_at=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)))
    return orphans
