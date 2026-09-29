"""
scripts/dump_shared_memory.py — Read-only diagnostic tool for verifying
LMU's shared memory layout and semantics against the actual running game.

This is the tool docs/LMU_VERIFICATION_PROTOCOL.md's test procedures
reference. It does NOT modify anything, does NOT record laps, and does
NOT talk to the server — it just reads the shared memory at a fixed rate
and writes every field this project's parser/validator actually depends
on to a CSV, plus prints anomalies to the console as they happen.

Run on Windows, with LMU running and shared memory output enabled:

    python -m scripts.dump_shared_memory
    python -m scripts.dump_shared_memory --out my_session.csv --hz 20

Then drive the session described in whichever protocol item you're
verifying (docs/LMU_VERIFICATION_PROTOCOL.md), stop with Ctrl+C, and
inspect the CSV / console output per that item's "what to look for".

Requires: the same environment as the client (client/lmu, client/telemetry).
No pandas/pyarrow dependency — writes plain CSV with the stdlib so this
runs even in a minimal venv.
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path

# Allow running as `python -m scripts.dump_shared_memory` or
# `python scripts/dump_shared_memory.py` from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from client.telemetry.reader import LMUSharedMemoryReader, SharedMemoryNotFound  # noqa: E402

FIELDNAMES = [
    "wall_clock",
    "elapsed_time",
    "lap_number",           # from telemetry (TelemInfoV01) — what parser.py uses for boundary detection
    "scoring_total_laps",   # from scoring (VehicleScoringInfoV01), if it exists — cross-check source
    "last_lap_time",
    "last_sector1",
    "last_sector2",
    "count_lap_flag",
    "in_pits",
    "lap_dist",
    "speed_ms",
    "track_name",
    "vehicle_name",
    "vehicle_class",        # V0.6.3: powers the class-based main leaderboard
    "veh_filename",         # V0.6.3: powers the exact-model sub-leaderboard
    "session_type",
    "ambient_temp",
    "raining",
]


def _speed(tele) -> float:
    v = tele.local_vel
    return (v.x ** 2 + v.y ** 2 + v.z ** 2) ** 0.5


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=f"lmu_dump_{int(time.time())}.csv", help="Output CSV path.")
    ap.add_argument("--hz", type=float, default=20.0, help="Poll rate (default 20Hz).")
    args = ap.parse_args()

    reader = LMUSharedMemoryReader()
    out_path = Path(args.out)
    print(f"Writing to {out_path} at {args.hz}Hz. Ctrl+C to stop.")
    print("Waiting for LMU shared memory...")

    last_lap_number = None
    frame_count = 0

    with out_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()

        while True:
            try:
                reader.open()
                print("Connected.")
                for frame in reader.stream(poll_hz=args.hz):
                    frame_count += 1
                    tele = frame.telemetry
                    scoring = frame.scoring
                    session = frame.session

                    row = {
                        "wall_clock": time.time(),
                        "elapsed_time": tele.elapsed_time,
                        "lap_number": tele.lap_number,
                        "scoring_total_laps": getattr(scoring, "total_laps", ""),
                        "last_lap_time": scoring.last_lap_time,
                        "last_sector1": scoring.last_sector1,
                        "last_sector2": scoring.last_sector2,
                        "count_lap_flag": scoring.count_lap_flag,
                        "in_pits": scoring.in_pits,
                        "lap_dist": scoring.lap_dist,
                        "speed_ms": _speed(tele),
                        "track_name": session.track_name.decode(errors="replace").strip("\x00"),
                        "vehicle_name": tele.vehicle_name.decode(errors="replace").strip("\x00"),
                        "vehicle_class": scoring.vehicle_class.decode(errors="replace").strip("\x00"),
                        "veh_filename": scoring.veh_filename.decode(errors="replace").strip("\x00"),
                        "session_type": session.session,
                        "ambient_temp": session.ambient_temp,
                        "raining": session.raining,
                    }
                    writer.writerow(row)

                    # Print anomalies as they happen — these are exactly
                    # the P0-7 items the verification protocol asks about.
                    if last_lap_number is not None and tele.lap_number != last_lap_number:
                        delta = tele.lap_number - last_lap_number
                        marker = "OK (+1)" if delta == 1 else f"!! JUMP ({delta:+d}) !!"
                        print(
                            f"[{frame_count}] lap_number {last_lap_number} -> {tele.lap_number} "
                            f"{marker} | last_lap_time={scoring.last_lap_time:.3f} "
                            f"count_lap_flag={scoring.count_lap_flag} in_pits={scoring.in_pits}"
                        )
                    last_lap_number = tele.lap_number

                    if frame_count % (int(args.hz) * 30) == 0:  # every ~30s
                        print(f"[{frame_count}] still recording... t={tele.elapsed_time:.1f}s")

            except SharedMemoryNotFound:
                print("LMU not running / no active session — waiting 5s...")
                reader.close()
                time.sleep(5)
            except KeyboardInterrupt:
                print(f"\nStopped. Wrote {frame_count} frames to {out_path}.")
                reader.close()
                return


if __name__ == "__main__":
    main()
