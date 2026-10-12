"""
scripts/car_identity_probe.py — every car of the running LMU session, exactly as LMU
reports it (V0.9.3). Run on the Windows PC while LMU is in a session — ideally one with a
full AI grid, so every model and livery is on track at once.

    python -m scripts.car_identity_probe
    python -m scripts.car_identity_probe --csv cars.csv

Prints, per car: slot, the name shown in the game (vehicle_name), LMU's veh_filename,
LMU's model name (mVehicleModel), the class, and the identity Garage16 would give it
(server/car_identity.py — needs no server or network). Send the CSV to whoever maintains
the identity rules: it is the evidence that decides whether, for example, two
specifications of one model need a rule (car_identity.SPEC_RULES).
"""

from __future__ import annotations

import argparse
import csv
import sys

from client.telemetry.reader import LMUSharedMemoryReader, SharedMemoryNotFound
from server.car_identity import identity_suspects, is_cut, model_identity, resolve


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--csv", help="also write the table to this CSV file")
    args = parser.parse_args(argv)

    reader = LMUSharedMemoryReader()
    try:
        reader.open()
    except SharedMemoryNotFound as exc:
        print(f"LMU shared memory not found: {exc}")
        return 1
    try:
        cars = reader.read_vehicle_catalog()
    finally:
        reader.close()
    if not cars:
        print("No cars found — is a session loaded (not just the menu)?")
        return 1

    rows = []
    for slot, car in enumerate(cars):
        identity = resolve(car["model"], car["veh_file"])
        rows.append({
            "slot": slot, "vehicle_name": car["name"], "veh_filename": car["veh_file"],
            "vehicle_model": car["model"], "class": car["class"],
            "garage16_identity": identity.key, "identity_source": identity.source,
            "model_identity": model_identity(car["model"], car["veh_file"]),
            # V0.9.3.1: LMU cuts model names at 29 characters (see server/car_identity.py)
            "name_cut": is_cut(car["model"]),
        })
    width = max(len(r["vehicle_name"]) for r in rows)
    for r in rows:
        print(f"{r['slot']:>3}  {r['vehicle_name']:<{width}}  {r['veh_filename']:<28} {r['vehicle_model'] or '—':<30} "
              f"{r['class']:<10} -> {r['garage16_identity']}")
    models = sorted({r["garage16_identity"] for r in rows if r["garage16_identity"]})
    print(f"\n{len(rows)} cars, {len(models)} distinct Garage16 identities:")
    for model in models:
        print(f"  {model}")
    suspects = identity_suspects({"identity": r["garage16_identity"], "veh_file": r["veh_filename"],
                                  "car_class": r["class"], "laps": 1} for r in rows)
    if suspects:
        print("\nPlease check (and send this list with the CSV):")
        for item in suspects:
            print(f"  {item['identity']}: {', '.join(item['reasons'])} "
                  f"[veh models: {', '.join(item['veh_models'])}; classes: {', '.join(item['classes'])}]")
    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
        print(f"\nWritten to {args.csv}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
