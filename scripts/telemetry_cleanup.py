"""
scripts/telemetry_cleanup.py — Reports (and, only when explicitly told
to, deletes) orphaned telemetry files, per the completion-sprint audit's
§12. See server/telemetry_lifecycle.py for the actual detection logic and
the safety reasoning (in particular: why an orphan candidate must be
older than --min-age-hours before it's touched at all).

Usage (from the project root, same environment as the server):

    python -m scripts.telemetry_cleanup
        Dry run (the default — this is deliberate, see below). Reports
        both kinds of discrepancy and exits 0. Deletes nothing, ever, in
        this mode.

    python -m scripts.telemetry_cleanup --execute
        Actually deletes files found to be orphaned (age-filtered, see
        server/telemetry_lifecycle.py). Still only ever reports
        DB-rows-with-missing-files — those are never auto-resolved by
        this tool under any flag; see that module's docstring for why.

    python -m scripts.telemetry_cleanup --min-age-hours 24
        Raise the safety margin before a file is even considered an
        orphan candidate (default 1 hour). Never useful to LOWER this
        below the default without a specific reason — it exists
        specifically to rule out files from an upload that's still in
        flight.

Run this against the same database and telemetry storage volume the
server itself uses (i.e., inside the server container, or with the same
LMU_GARAGE_DB_URL / LMU_GARAGE_STORAGE_DIR env vars set) — pointing it at
the wrong pair would make every file look orphaned.
"""

from __future__ import annotations

import argparse
import sys
from datetime import timedelta

from server.config import settings
from server.database import SessionLocal
from server.models import Lap
from server.telemetry_lifecycle import find_missing_files, find_orphaned_files


def _describe_database(url: str) -> str:
    """Dialect + location, never the password."""
    from sqlalchemy.engine import make_url

    parsed = make_url(url)
    if parsed.get_backend_name() == "sqlite":
        return f"sqlite ({parsed.database})"
    return f"{parsed.get_backend_name()} ({parsed.host}:{parsed.port or ''}/{parsed.database})"


def _wrong_database_suspicion(url: str, lap_count: int, orphan_count: int):
    """--execute deletes files. It is only safe when this process is looking
    at the SAME database the server uses; pointing it at the wrong one makes
    every file look orphaned. Two tell-tale signs of that, both refused:

    - production settings but the SQLite default database: the server's real
      database URL was not passed to this process (`docker compose exec`
      does NOT inherit the DB URL the container entrypoint builds for the
      server — see README's exact command);
    - an empty database (0 laps) alongside orphan candidates: a real
      deployment with telemetry files has laps.
    """
    from sqlalchemy.engine import make_url

    if settings.is_production and make_url(url).get_backend_name() == "sqlite":
        return (
            "production settings but the SQLite default database — the server's real database URL "
            "wasn't passed to this process, so every file would look orphaned. See README, \"Telemetry "
            "storage cleanup\", for the exact docker compose command."
        )
    if lap_count == 0 and orphan_count > 0:
        return (
            f"this database has 0 laps but {orphan_count} telemetry file(s) exist — that is what pointing "
            "the tool at the wrong database looks like. Check the 'Database:' line above."
        )
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--execute", action="store_true",
        help="Actually delete orphaned files. Without this flag, nothing is ever deleted.",
    )
    parser.add_argument(
        "--min-age-hours", type=float, default=1.0,
        help="Only treat a file with no matching DB row as orphaned if it's at least this old (default: 1.0).",
    )
    args = parser.parse_args()

    db_url = settings.database_url
    print(f"Database : {_describe_database(db_url)}")
    print(f"Storage  : {settings.telemetry_storage_dir}")
    print()

    db = SessionLocal()
    try:
        lap_count = db.query(Lap).count()
        missing = find_missing_files(db)
        orphans = find_orphaned_files(db, min_age=timedelta(hours=args.min_age_hours))
    finally:
        db.close()

    if args.execute:
        problem = _wrong_database_suspicion(db_url, lap_count, len(orphans))
        if problem:
            print(f"REFUSING to delete anything: {problem}", file=sys.stderr)
            return 2

    print(f"=== DB rows with a missing telemetry file ({len(missing)}) ===")
    if missing:
        print("These are NEVER auto-resolved by this tool — review manually.")
        for m in missing:
            print(f"  lap_id={m.lap_id} driver_id={m.driver_id} telemetry_path={m.telemetry_path}")
    else:
        print("  none")

    print()
    print(f"=== Orphaned files with no DB row ({len(orphans)}) ===")
    total_bytes = sum(o.size_bytes for o in orphans)
    if orphans:
        for o in orphans:
            print(f"  {o.path}  ({o.size_bytes:,} bytes, modified {o.modified_at.isoformat()})")
        print(f"  total: {total_bytes:,} bytes")
    else:
        print("  none")

    if not orphans:
        return 0

    if not args.execute:
        print()
        print(f"Dry run — no files deleted. Re-run with --execute to delete these {len(orphans)} file(s).")
        return 0

    print()
    print(f"--execute given: deleting {len(orphans)} file(s)...")
    deleted, failed = 0, 0
    for o in orphans:
        try:
            o.path.unlink()
            deleted += 1
        except OSError as exc:
            print(f"  FAILED to delete {o.path}: {exc}", file=sys.stderr)
            failed += 1
    print(f"Deleted {deleted} file(s), {total_bytes:,} bytes freed" + (f", {failed} failed" if failed else "") + ".")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
