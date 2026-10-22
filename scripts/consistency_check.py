"""
scripts/consistency_check.py — database and telemetry files, checked against each other (V0.9.7).

    python -m scripts.consistency_check                 dry run: report only, changes nothing
    python -m scripts.consistency_check --deep          also open every file and compare its sha256
    python -m scripts.consistency_check --delete-orphans          remove files no lap points at
                                                                  (and stale upload temp files;
                                                                  both only when older than 1 h)
    python -m scripts.consistency_check --recompute-figures [--limit N]
                                                        recompute the stored per-lap figures that an
                                                        older analysis computed (validity untouched)
    --json                                              the report as JSON (for scripts / cron)

What is checked and what is never touched: see server/consistency.py. Exit code 0 = nothing that
needs an admin, 1 = problems (a lap without its file, an unreadable or changed file, rows pointing
at nothing, V0.9.9.1: an audit head other than --audit-head), 2 = refused (looks like the wrong database).

Run it inside the server container so it sees the same database and storage as the server —
on the NAS (one line):

    docker compose exec -T server sh -c 'LMU_GARAGE_DB_URL="postgresql+psycopg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@${POSTGRES_HOST}:${POSTGRES_PORT}/${POSTGRES_DB}" python -m scripts.consistency_check'

and ALWAYS after a restore (docs/ROLLBACK.md): it is the proof that database and telemetry
volume come from the same moment.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import timedelta

from server.config import settings
from server.consistency import check, delete_orphans, recompute_figures
from server.database import SessionLocal

from scripts.telemetry_cleanup import _describe_database, _wrong_database_suspicion

LABELS = {
    "missing_files": "laps whose telemetry file is missing",
    "outside_storage": "laps whose path points outside the storage folder",
    "unreadable_files": "files that cannot be read",
    "hash_mismatch": "files whose content changed (sha256)",
    "dangling_rows": "rows pointing at something that no longer exists",
    "orphaned_files": "files no lap points at (older than the safety margin)",
    "stale_uploads": "temp files of interrupted uploads",
    "not_judged": "laps without a stored verdict (run scripts.revalidate_laps)",
    "stale_figures": "laps whose stored figures are from an older analysis",
    "bad_metadata": "laps with missing or impossible metadata (lap time, sectors, hash, track/car)",
    "unsupported_figures": "laps whose figures a NEWER server wrote (kept, not recomputed)",
    "size_mismatch": "files whose size differs from the size received at upload",
    "mac_invalid": "laps whose stored hash / size / lap time do not match the server's MAC",
    "quarantined": "laps in quarantine after an integrity failure (scripts.integrity_check)",
    "audit_chain": "the audit log's chain is broken (scripts.integrity_check --audit)",
    "audit_unverifiable": "audit rows signed with a key no longer configured (docs/RUNBOOKS.md 16)",
}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Database and telemetry files, checked against each other.")
    parser.add_argument("--deep", action="store_true")
    parser.add_argument("--delete-orphans", action="store_true")
    parser.add_argument("--recompute-figures", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--min-age-hours", type=float, default=1.0)
    parser.add_argument("--json", action="store_true")
    # V0.9.8 — check a copy of the storage mounted elsewhere (scripts/restore.sh validates its
    # staged copy at /restore while the rows store /data/telemetry/...)
    parser.add_argument("--storage-dir", help="look at this storage folder instead of the configured one")
    parser.add_argument("--stored-prefix", help="the storage path the laps were stored under (replaced by --storage-dir)")
    parser.add_argument("--yes", action="store_true", help="do not ask before deleting files")
    # V0.9.9.1 — scripts/restore.sh: the chain head the backup manifest recorded
    parser.add_argument("--audit-head", metavar="SPEC",
                        help="compare the newest audit row with a backup's manifest: 'absent', 'none' or '<id>:<mac>'")
    args = parser.parse_args(argv)

    min_age = timedelta(hours=max(args.min_age_hours, 1.0))
    if args.storage_dir:
        from pathlib import Path

        settings.telemetry_storage_dir = Path(args.storage_dir)
    if args.delete_orphans and args.stored_prefix:
        parser.error("--delete-orphans is not allowed on a copy (--stored-prefix)")
    expected_head = None
    if args.audit_head is not None:
        from server import audit

        try:
            expected_head = audit.parse_head_spec(args.audit_head)
        except ValueError as exc:
            parser.error(str(exc))
    with SessionLocal() as db:
        report = check(db, deep=args.deep, min_orphan_age=min_age, stored_prefix=args.stored_prefix)
        if args.audit_head is not None:
            report["audit_head"] = audit.compare_head(db, expected_head)
            report["ok"] = report["ok"] and report["audit_head"]["ok"]
        if args.json:
            print(json.dumps(report, indent=2))
        else:
            print(f"Database: {_describe_database(settings.database_url)}")
            print(f"Storage:  {report['storage']}")
            print(f"Laps checked: {report['checked_laps']} ({'deep' if args.deep else 'quick — add --deep to read every file'})")
            for kind, label in LABELS.items():
                n = report["counts"][kind]
                print(f"  {'!!' if n and kind in ('missing_files', 'outside_storage', 'unreadable_files', 'hash_mismatch', 'dangling_rows', 'bad_metadata', 'size_mismatch', 'mac_invalid', 'audit_chain') else '  '} {n:6d}  {label}")
                for entry in report["items"][kind][:10]:
                    print(f"            {entry}")
            if "audit_head" in report:
                head = report["audit_head"]
                if expected_head == audit.HEAD_ABSENT:
                    print("     audit head: the backup records none (made before V0.9.9) — not compared")
                elif head["ok"]:
                    print(f"     audit head: matches the backup ({head['found']['id'] if head['found'] else 'no rows'})")
                else:
                    print(f"  !! audit head: DIFFERS from the backup — {head['problem']} "
                          f"(backup: {head['expected']}, here: {head['found']})")
        if args.delete_orphans:
            orphans = report["counts"]["orphaned_files"] + report["counts"]["stale_uploads"]
            suspicion = _wrong_database_suspicion(settings.database_url, report["checked_laps"], orphans)
            if suspicion:
                print(f"REFUSED to delete: {suspicion}", file=sys.stderr)
                return 2
            # V0.9.8: the list above is the preview; deleting needs an explicit yes
            if orphans and not args.yes:
                try:
                    answer = input(f"Delete these {orphans} file(s)? Type DELETE to continue: ")
                except EOFError:
                    answer = ""
                if answer.strip() != "DELETE":
                    print("Nothing deleted.")
                    return 0 if report["ok"] else 1
            print(f"Removed {delete_orphans(db, min_age=min_age)} file(s).")
        if args.recompute_figures:
            print(f"Figures: {recompute_figures(db, limit=args.limit)}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
