#!/usr/bin/env python3
"""
scripts/bench_server.py — how long the lap check and the integrity hash take on THIS machine, with
the laps already stored (V0.9.9). Read-only: nothing is written, every transaction is rolled back.

Works inside the server container of V0.9.8 AND V0.9.9 (only interfaces both have are used), so
the two versions can be compared on the NAS with the same real laps:

    docker compose cp scripts/bench_server.py server:/tmp/bench_server.py          (V0.9.8: from the new folder)
    docker compose exec -T server sh -c 'LMU_GARAGE_DB_URL="postgresql+psycopg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@${POSTGRES_HOST}:${POSTGRES_PORT}/${POSTGRES_DB}" python /tmp/bench_server.py --root /app --laps 20 --repeat 3'

Reports per lap (median over --repeat after one warm-up): file size, sha256 time and throughput,
the full lap check (plausibility + track limits + figures, V0.9.9 also the decision layer), and the
peak Python memory of one check; plus totals and the server version. Output: JSON on stdout.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics
import sys
import time
import tracemalloc
from pathlib import Path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--root", default=str(Path(__file__).resolve().parent.parent),
                        help="the application folder (/app in the container)")
    parser.add_argument("--laps", type=int, default=20, help="the most recent N laps whose file exists")
    parser.add_argument("--repeat", type=int, default=3)
    args = parser.parse_args(argv)
    sys.path.insert(0, args.root)
    os.chdir(args.root)

    from types import SimpleNamespace

    from server.database import SessionLocal
    from server.lap_check import check_lap, inputs_for_lap
    from server.main import VERSION
    from server.models import Lap

    rows = []
    with SessionLocal() as db:
        candidates = db.query(Lap).order_by(Lap.id.desc()).limit(args.laps * 5).all()
        laps = [lap for lap in candidates if Path(lap.telemetry_path).is_file()][:args.laps]
        for lap in laps:
            path = Path(lap.telemetry_path)
            data_size = path.stat().st_size
            hashes, checks, peak = [], [], 0
            meta = SimpleNamespace(track_name=lap.track_name, lap_time=lap.lap_time, sector_times=lap.sector_times or [],
                                   sample_count=lap.sample_count, tail_samples=None, game_lap_state=None, lap_report=None,
                                   track_grip_level=None, fixed_setup=None, track_limits_steps_per_point=None,
                                   track_limits_steps_per_penalty=None)
            inputs = inputs_for_lap(lap)
            for i in range(args.repeat + 1):
                t0 = time.perf_counter()
                digest = hashlib.sha256()
                with path.open("rb") as handle:
                    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                        digest.update(chunk)
                t1 = time.perf_counter()
                tracemalloc.start()
                check_lap(db, path, meta, client_valid=bool(inputs.get("client_valid", True)), exclude_lap_id=lap.id,
                          inputs=inputs)
                t2 = time.perf_counter()
                peak = max(peak, tracemalloc.get_traced_memory()[1])
                tracemalloc.stop()
                if i:
                    hashes.append(t1 - t0)
                    checks.append(t2 - t1)
            db.rollback()
            rows.append({"lap_id": lap.id, "track": lap.track_name, "bytes": data_size, "samples": lap.sample_count,
                         "sha256_ms": round(1000 * statistics.median(hashes), 1),
                         "sha256_mb_s": round(data_size / 1e6 / statistics.median(hashes), 1),
                         "check_ms": round(1000 * statistics.median(checks), 1), "check_peak_mb": round(peak / 1e6, 1)})
    out = {"version": VERSION, "laps": len(rows),
           "median": {k: statistics.median(r[k] for r in rows) for k in ("bytes", "sha256_ms", "sha256_mb_s", "check_ms", "check_peak_mb")} if rows else {},
           "max": {k: max(r[k] for r in rows) for k in ("bytes", "sha256_ms", "check_ms", "check_peak_mb")} if rows else {},
           "rows": rows}
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
