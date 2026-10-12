"""
scripts/track_limits_probe.py — records, frame by frame, every LMU signal the track-limits
check depends on (V0.9.3.1), so the open questions can be answered with REAL data
(docs/LMU_VERIFICATION_PROTOCOL.md, section V0.9.3.1). Run on the Windows PC while driving:

    py -m scripts.track_limits_probe --csv probe.csv
    py -m scripts.track_limits_probe --csv probe.csv --seconds 600

Needs no server and no network. While it runs, every CHANGE of a game signal is printed
with the time since the last finish line, e.g.

    lap 4   +2.37 s after the line   track_limits_steps 3 -> 4
    lap 4   +0.03 s after the line   last_lap_time 92.418 -> -1.000

which is exactly what decides:
  * how many steps an offence adds and how they relate to points (steps per point/penalty)
  * how late LMU scores an offence after the line (the late window, LATE_WINDOW_SECONDS)
  * when LMU sets and clears mLapInvalidated around the line
  * whether LMU writes mLastLapTime <= 0 for an invalid lap, and when
  * what mSurfaceType reports on kerbs, grass, gravel, astroturf and painted run-off, and
    while a wheel is in the air
  * V0.9.4 (Stint Analyzer): when mNumPitstops rises during a stop, which tyre compound index
    and name LMU reports before and after a tyre change, what mTimeGapCarAhead/-Behind show in
    practice and in a race, and which values mFlag takes (blue flag = 6)
Write down what you did (lap, corner, what LMU showed on screen) next to the output and
send it together with the CSV.
"""

from __future__ import annotations

import argparse
import csv
import sys
import time

from client.telemetry.reader import LMUSharedMemoryReader, SharedMemoryNotFound

WHEELS = ("fl", "fr", "rl", "rr")
WATCHED = ("lap_number", "last_lap_time", "count_lap_flag", "lap_invalidated", "track_limits_steps",
           "num_penalties", "in_pits", "steps_per_point", "steps_per_penalty", "surfaces",
           # V0.9.4 (Stint Analyzer, protocol point (w)): pit stops, position, compound, flag
           "num_pitstops", "place", "compound_front", "compound_rear", "flag")


def snapshot(frame) -> dict:
    tele, scoring, session = frame.telemetry, frame.scoring, frame.session
    surfaces = []
    for wheel in getattr(tele, "wheel", ())[:4]:
        surfaces.append(int(getattr(wheel, "surface_type", -1)))
    return {
        "et": round(float(tele.elapsed_time), 3),
        "lap_number": int(tele.lap_number),
        "lap_dist": round(float(scoring.lap_dist), 1),
        "last_lap_time": round(float(scoring.last_lap_time), 3),
        "count_lap_flag": int(scoring.count_lap_flag),
        "lap_invalidated": int(getattr(tele, "lap_invalidated", -1)),
        "track_limits_steps": int(getattr(tele, "track_limits_steps", -1)),
        "num_penalties": int(getattr(scoring, "num_penalties", -1)),
        "in_pits": int(bool(scoring.in_pits)),
        "path_lateral": round(float(getattr(scoring, "path_lateral", float("nan"))), 2),
        "track_edge": round(float(getattr(scoring, "track_edge", float("nan"))), 2),
        "steps_per_point": int(getattr(session, "track_limits_steps_per_point", -1)),
        "steps_per_penalty": int(getattr(session, "track_limits_steps_per_penalty", -1)),
        "session": int(getattr(session, "session", -1)),
        "surfaces": "/".join(str(s) for s in surfaces),
        **{f"surface_{w}": (surfaces[i] if i < len(surfaces) else -1) for i, w in enumerate(WHEELS)},
        # V0.9.4 — what the Stint Analyzer reads (protocol point (w)): when does num_pitstops
        # rise (entry, box, exit?), which compound index/name belongs to which tyre, what do
        # the time gaps show in practice / race, which values does mFlag take
        "num_pitstops": int(getattr(scoring, "num_pitstops", -1)),
        "place": int(getattr(scoring, "place", -1)),
        "flag": int(getattr(scoring, "flag", -1)),
        "compound_front": f"{int(getattr(tele, 'front_tire_compound_index', -1))}:{_text(getattr(tele, 'front_tire_compound_name', b''))}",
        "compound_rear": f"{int(getattr(tele, 'rear_tire_compound_index', -1))}:{_text(getattr(tele, 'rear_tire_compound_name', b''))}",
        "time_gap_ahead": round(float(getattr(tele, "time_gap_car_ahead", float("nan"))), 2),
        "time_gap_behind": round(float(getattr(tele, "time_gap_car_behind", float("nan"))), 2),
        "fuel": round(float(getattr(tele, "fuel", float("nan"))), 2),
        "fuel_capacity": round(float(getattr(tele, "fuel_capacity", float("nan"))), 1),
        "dent_max": max((int(v) for v in getattr(tele, "dent_severity", ())), default=-1),
        "session_time_remaining": round(float(getattr(session, "session_time_remaining", float("nan"))), 1),
    }


def _text(raw) -> str:
    if isinstance(raw, (bytes, bytearray)):
        return bytes(raw).split(b"\x00", 1)[0].decode("utf-8", "replace").strip()
    return str(raw or "").strip()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--csv", required=True, help="write every frame to this CSV file")
    parser.add_argument("--seconds", type=float, default=None, help="stop after this many seconds (default: Ctrl+C)")
    parser.add_argument("--hz", type=float, default=60.0)
    args = parser.parse_args(argv)

    reader = LMUSharedMemoryReader()
    try:
        reader.open()
    except SharedMemoryNotFound as exc:
        print(f"LMU shared memory not found: {exc}")
        return 1
    started = time.monotonic()
    previous = None
    line_et = None
    frames = 0
    print("Recording — drive; Ctrl+C to stop.")
    try:
        with open(args.csv, "w", newline="", encoding="utf-8") as handle:
            writer = None
            for frame in reader.stream(poll_hz=args.hz):
                row = snapshot(frame)
                if writer is None:
                    writer = csv.DictWriter(handle, fieldnames=list(row.keys()))
                    writer.writeheader()
                writer.writerow(row)
                frames += 1
                if previous is not None:
                    if row["lap_number"] != previous["lap_number"]:
                        line_et = row["et"]
                    for key in WATCHED:
                        if row[key] != previous[key]:
                            since = f"{row['et'] - line_et:+.2f} s after the line" if line_et is not None else "(before the first line)"
                            print(f"lap {row['lap_number']:<3} {since:<26} {key} {previous[key]} -> {row[key]}"
                                  f"   @ {row['lap_dist']:.0f} m")
                previous = row
                if args.seconds is not None and time.monotonic() - started >= args.seconds:
                    break
    except KeyboardInterrupt:
        pass
    finally:
        reader.close()
    print(f"\n{frames} frames written to {args.csv}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
