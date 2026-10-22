"""
scripts/integrity_check.py — are all stored lap files still the files that were uploaded? (V0.9.9)

    python -m scripts.integrity_check
        Dry run: hashes EVERY lap file and compares sha256, size and the server's MAC
        (server/integrity.py); verifies the audit chain (server/audit.py) and prints its head.
        Changes nothing. Exit code 0 = everything verified, 1 = problems found.

    --apply                 quarantine every lap whose file failed (it leaves leaderboards and
                            records; an admin releases it after repair) and record the sweep in the
                            audit log
    --apply --backfill      laps stored before V0.9.9 (no size / MAC yet): if the file matches its
                            sha256, store the size and sign the lap. Run once after the update.
    --apply --resign        laps signed with an older integrity key (still configured) are signed
                            with the current one — after a key rotation (docs/RUNBOOKS.md)
    --accept-unknown-key    with --resign: also re-sign laps whose MAC was made with a key this
                            server no longer has. Only after the files were verified against an
                            independent copy (backup) — the old MAC can no longer vouch for the
                            stored hash and lap time.
    --apply --release-verified
                            quarantined laps whose file verifies again (restored from a backup)
                            are released to the state they had before
    --audit                 only the audit chain
    --lap ID                only this lap (repeatable)
    --json FILE             the report as JSON as well

On the NAS (Docker):

    docker compose exec -T server sh -c 'LMU_GARAGE_DB_URL="postgresql+psycopg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@${POSTGRES_HOST}:${POSTGRES_PORT}/${POSTGRES_DB}" python -m scripts.integrity_check'
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server import audit, integrity  # noqa: E402
from server.database import SessionLocal  # noqa: E402
from server.models import Lap  # noqa: E402

MAX_LISTED = 100


def run(argv=None) -> dict:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--backfill", action="store_true")
    parser.add_argument("--resign", action="store_true")
    parser.add_argument("--accept-unknown-key", action="store_true")
    parser.add_argument("--release-verified", action="store_true")
    parser.add_argument("--audit", action="store_true")
    parser.add_argument("--lap", type=int, action="append")
    parser.add_argument("--json")
    args = parser.parse_args(argv)
    if (args.backfill or args.resign or args.release_verified) and not args.apply:
        parser.error("--backfill / --resign / --release-verified change data: add --apply")
    if args.accept_unknown_key and not args.resign:
        parser.error("--accept-unknown-key belongs to --resign")

    started = time.monotonic()
    report = {"apply": args.apply, "laps": 0, "counts": {}, "failures": [], "quarantined_now": 0, "backfilled": 0,
              "resigned": 0, "released": 0, "audit": None}
    counts = Counter()
    with SessionLocal() as db:
        if not args.audit:
            query = db.query(Lap).order_by(Lap.id)
            if args.lap:
                query = query.filter(Lap.id.in_(args.lap))
            for lap in query.all():
                report["laps"] += 1
                result = integrity.verify(lap)
                key = result.status if result.status != integrity.OK else f"ok ({result.mac})"
                counts[key] += 1
                if result.quarantine:
                    if len(report["failures"]) < MAX_LISTED:
                        report["failures"].append({"lap_id": lap.id, "driver_id": lap.driver_id, **result.as_dict()})
                    if args.apply and integrity.quarantine(db, lap, result, actor="script"):
                        report["quarantined_now"] += 1
                    continue
                if not args.apply:
                    continue
                if args.backfill and (lap.telemetry_bytes is None or not lap.integrity_mac) and result.ok:
                    path, _ = integrity.resolve_inside_storage(lap.telemetry_path)
                    lap.telemetry_bytes = path.stat().st_size
                    lap.integrity_mac = integrity.lap_mac(lap)
                    db.add(lap)
                    report["backfilled"] += 1
                elif args.resign and result.ok and lap.integrity_mac and not integrity.signed_with_current_key(lap.integrity_mac):
                    if result.mac == integrity.OK or (result.mac == integrity.UNKNOWN_KEY and args.accept_unknown_key):
                        lap.integrity_mac = integrity.lap_mac(lap)
                        db.add(lap)
                        report["resigned"] += 1
                if args.release_verified and lap.review_status == "quarantined" and result.ok:
                    integrity.release(db, lap, actor="script", note="integrity_check --release-verified")
                    report["released"] += 1
            report["counts"] = dict(counts)
            if args.apply:
                audit.append(db, "integrity.sweep", actor="script", detail={
                    "laps": report["laps"], "counts": dict(counts), "quarantined": report["quarantined_now"],
                    "backfilled": report["backfilled"], "resigned": report["resigned"], "released": report["released"]})
                db.commit()
        report["audit"] = audit.verify_db(db)
    report["seconds"] = round(time.monotonic() - started, 1)
    report["ok"] = not report["failures"] and bool(report["audit"]["ok"])
    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return report


def main(argv=None) -> int:
    report = run(argv)
    print(f"Laps checked: {report['laps']} ({report['seconds']} s)")
    for key, value in sorted(report["counts"].items()):
        print(f"  {key:<40} {value}")
    for failure in report["failures"]:
        print(f"  FAILED lap {failure['lap_id']}: {failure['status']} {failure.get('detail', '')}")
    if report["apply"]:
        print(f"Quarantined now: {report['quarantined_now']} · backfilled: {report['backfilled']} · "
              f"re-signed: {report['resigned']} · released: {report['released']}")
    chain = report["audit"]
    print(f"Audit chain: {'OK' if chain['ok'] else 'BROKEN at row ' + str(chain['first_broken']) + ' — ' + str(chain['problem'])}"
          f" ({chain['rows']} rows, {chain['unknown_key']} with a key no longer configured)")
    if chain.get("head"):
        print(f"Audit head: id {chain['head']['id']} mac {chain['head']['mac']}  <- keep a copy outside the server")
    if not report["apply"] and report["failures"]:
        print("Dry run — nothing changed. --apply quarantines the failed laps.")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
