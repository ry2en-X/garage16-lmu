"""
scripts/car_identity_report.py — what the car-model identity of every lap is, and what
it would be under the current rules (V0.9.3). See server/car_identity.py.

Usage (project root, same environment/database as the server):

    python -m scripts.car_identity_report
        Dry run (default): every distinct (raw veh_filename, LMU model name) combination
        with its lap count, its current identity, the identity the current rules and the
        learned aliases give it, and the learned aliases incl. conflicts. Changes nothing.

    python -m scripts.car_identity_report --apply
        Writes the re-derived identity to every lap whose identity changes. The raw values
        LMU sent (car_model_raw / car_vehicle_model) are never touched, so this can be run
        again at any time — and undone by restoring a backup or re-running with older
        rules.

    python -m scripts.car_identity_report --csv FILE
        Also writes the combination table as CSV (for checking against real LMU data,
        e.g. the output of scripts/car_identity_probe.py).

On the NAS (Docker) — the database URL must be passed in, as for every script:

    docker compose exec -T server sh -c 'LMU_GARAGE_DB_URL="postgresql+psycopg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@${POSTGRES_HOST}:${POSTGRES_PORT}/${POSTGRES_DB}" python -m scripts.car_identity_report'
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter

from sqlalchemy import func

from server import car_identity
from server.database import SessionLocal
from server.models import CarAlias, Lap


def gather(db):
    aliases = {a.alias_key: a for a in db.query(CarAlias).all()}

    def lookup(key):
        row = aliases.get(key)
        if not car_identity.alias_usable(row):
            return None
        return car_identity.model_identity(row.canonical) or row.canonical

    rows = (
        db.query(Lap.car_model_raw, Lap.car_vehicle_model, Lap.car_model, Lap.car_identity_source, Lap.car_class,
                 func.count(Lap.id))
        .group_by(Lap.car_model_raw, Lap.car_vehicle_model, Lap.car_model, Lap.car_identity_source, Lap.car_class)
        .all()
    )
    merged = {}
    for raw, model, current, source, car_class, count in rows:
        key = (raw, model, current, source)
        entry = merged.setdefault(key, {"count": 0, "classes": set()})
        entry["count"] += count
        if car_class:
            entry["classes"].add(car_class)
    table = []
    for (raw, model, current, source), entry in merged.items():
        count = entry["count"]
        if raw is None and model is None:
            proposed = car_identity.Identity(current, source)   # pre-0.6.3 laps: nothing to derive from
        else:
            proposed = car_identity.resolve(model, raw, lookup)
        table.append({
            "veh_file": raw, "vehicle_model": model, "laps": count, "classes": "/".join(sorted(entry["classes"])),
            "current": current, "current_source": source,
            "proposed": proposed.key, "proposed_source": proposed.source,
            "changes": (proposed.key, proposed.source) != (current, source),
        })
    table.sort(key=lambda r: (str(r["proposed"]), str(r["veh_file"])))
    return table, aliases


def apply(db, table) -> int:
    changed = 0
    for row in table:
        if not row["changes"]:
            continue
        query = db.query(Lap).filter(
            Lap.car_model_raw.is_(None) if row["veh_file"] is None else Lap.car_model_raw == row["veh_file"],
            Lap.car_vehicle_model.is_(None) if row["vehicle_model"] is None else Lap.car_vehicle_model == row["vehicle_model"],
            Lap.car_model.is_(None) if row["current"] is None else Lap.car_model == row["current"],
        )
        changed += query.update(
            {Lap.car_model: row["proposed"], Lap.car_identity_source: row["proposed_source"]}, synchronize_session=False
        )
    db.commit()
    return changed


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--apply", action="store_true", help="write the re-derived identities")
    parser.add_argument("--csv", help="also write the table to this CSV file")
    args = parser.parse_args(argv)

    with SessionLocal() as db:
        table, aliases = gather(db)
        print(f"Car identity rules version {car_identity.IDENTITY_RULES_VERSION}")
        print(f"{'laps':>6}  {'veh_filename':<28} {'LMU model name':<28} {'now':<30} -> proposed")
        for row in table:
            mark = "*" if row["changes"] else " "
            print(f"{row['laps']:>6}{mark} {str(row['veh_file'] or '—'):<28} {str(row['vehicle_model'] or '—'):<28} "
                  f"{str(row['current'] or '—'):<30} -> {row['proposed'] or '—'} [{row['proposed_source'] or '—'}]")
        models = Counter()
        for row in table:
            models[row["proposed"]] += row["laps"]
        print(f"\n{len(models)} car identities over {sum(models.values())} laps (* = would change).")
        if aliases:
            print("\nLearned aliases (veh_filename group -> LMU model):")
            for key, a in sorted(aliases.items()):
                if a.conflict:
                    state = f"CONFLICT with {a.conflict_with!r} — not used"
                elif not car_identity.alias_usable(a):
                    state = f"seen {a.seen_count}x (grid only) — not used until seen {car_identity.ALIAS_MIN_GRID_SIGHTINGS}x"
                else:
                    state = f"seen {a.seen_count}x ({a.source})"
                print(f"  {key:<30} -> {a.canonical:<30} {state}")
        suspects = car_identity.identity_suspects(
            {"identity": row["proposed"], "veh_file": row["veh_file"], "car_class": cls, "laps": row["laps"] if i == 0 else 0}
            for row in table for i, cls in enumerate(row["classes"].split("/") if row["classes"] else [None]))
        if suspects:
            print("\nTo check by hand (nothing is changed automatically):")
            labels = {"cut_name": "LMU cut the model name, full name unknown",
                      "several_models": "laps from veh_filenames with different model parts (two variants?)",
                      "several_classes": "laps in different classes"}
            for item in suspects:
                print(f"  {item['identity']:<34} {item['laps']:>6} laps  " + "; ".join(labels[r] for r in item["reasons"])
                      + f"  [veh models: {', '.join(item['veh_models']) or '—'}; classes: {', '.join(item['classes']) or '—'}]")
        if args.csv:
            with open(args.csv, "w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(table[0].keys()) if table else ["veh_file"])
                writer.writeheader()
                writer.writerows(table)
            print(f"\nTable written to {args.csv}")
        if args.apply:
            changed = apply(db, table)
            print(f"\nApplied: {changed} lap(s) changed.")
        elif any(row["changes"] for row in table):
            print("\nDry run — nothing changed. Re-run with --apply to write the proposed identities.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
