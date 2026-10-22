"""
scripts/trackcheck_eval.py — how often is the track-limit decision right? (V0.9.9)

Precision, recall, false-positive and false-negative rate of the lap decision
(server/lap_decision.py) on a LABELLED set of laps, always with the case counts.

    python -m scripts.trackcheck_eval
        The synthetic reference set: the curated cases (tests/helpers/trackcheck_cases.py) plus a
        generated grid (3 tracks x 2 cars x 5 seeds x 2 noise levels x 8 situations). The label of
        every lap comes from its geometry (tests/helpers/trackcheck.py), not from Garage16's rules.
        --json FILE / --markdown FILE write the results; --quick runs the curated cases only.

    python -m scripts.trackcheck_eval --labels labels.csv
        REAL laps from the database, labelled by a person (docs/V0_9_9_NAS_ANLEITUNG.md):
        CSV with the columns lap_id,label[,note] — label "clean" or "violation" (anything else is
        skipped and listed). Every lap is judged again with the current rules from its stored file
        and check inputs (nothing is changed), and its stored verdict is reported next to it.

Definitions (positive = the label says "violation"):
  flagged       the decision is REVIEW or INVALID (the lap is not counted automatically)
  precision     TP / (TP + FP)       of the flagged laps, how many really were violations
  recall        TP / (TP + FN)       of the violations, how many were flagged
  FPR           FP / (FP + TN)       of the clean laps, how many were flagged
  FNR           FN / (FN + TP)       of the violations, how many passed as VALID
The same four for "invalidated" (INVALID only). Laps whose recording was manipulated or is
insufficient are reported separately (share not passed as VALID): they have no geometric label
that a rate could be computed against.

The synthetic numbers measure the rules against a model of LMU — they are NOT a measurement of
real-world accuracy. Only --labels with real laps is.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def rates(rows, positive_status=("review", "invalid")) -> dict:
    tp = sum(1 for r in rows if r["label"] == "violation" and r["status"] in positive_status)
    fn = sum(1 for r in rows if r["label"] == "violation" and r["status"] not in positive_status)
    fp = sum(1 for r in rows if r["label"] == "clean" and r["status"] in positive_status)
    tn = sum(1 for r in rows if r["label"] == "clean" and r["status"] not in positive_status)

    def ratio(a, b):
        return round(a / b, 4) if b else None
    return {"tp": tp, "fp": fp, "tn": tn, "fn": fn, "n": tp + fp + tn + fn,
            "precision": ratio(tp, tp + fp), "recall": ratio(tp, tp + fn),
            "fpr": ratio(fp, fp + tn), "fnr": ratio(fn, fn + tp)}


def grid():
    from tests.helpers.trackcheck import CARS, TRACKS, Excursion, Scenario
    from tests.helpers.trackcheck_cases import GRASS_SPOTS

    out = []
    for track in TRACKS:
        section, kerb = GRASS_SPOTS[track]
        for car in CARS:
            for seed in range(5):
                for noise in (0.0, 1.0):
                    base = dict(track=track, car=car, seed=seed, noise=noise)
                    tag = f"{track}/{car.name}/seed{seed}/noise{noise:g}"
                    out.append(Scenario(f"grid clean {tag}", **base))
                    out.append(Scenario(f"grid kerbs {tag}", line="kerbs", **base))
                    for lmu in ("strict", "lenient", "silent"):
                        out.append(Scenario(f"grid off ({lmu}) {tag}", excursions=(Excursion(section, kerb + 0.3),),
                                            lmu=lmu, **base))
                    out.append(Scenario(f"grid 5 cm inside {tag}", excursions=(Excursion(section, -0.05),), lmu="silent", **base))
                    out.append(Scenario(f"grid 5 cm beyond {tag}", excursions=(Excursion(section, 0.05),), lmu="silent", **base))
                    out.append(Scenario(f"grid two wheels off {tag}", excursions=(Excursion(section, -0.3),), lmu="silent", **base))
    return out


def judge_synthetic(scenarios):
    from tests.helpers.trackcheck import judge

    rows = []
    for sc in scenarios:
        check, lap, _df = judge(sc)
        manipulated = bool(sc.manipulations) or sc.frame_hz < 10 or (sc.drop is not None and sc.drop[1] > 1.0)
        rows.append({"name": sc.name, "label": lap.label, "status": check.status, "expected": sc.expected,
                     "manipulated": manipulated and "duplicate_rows" not in sc.manipulations and "drop_093" not in sc.manipulations,
                     "codes": [f.code for f in check.verdict.findings if f.severity != "info"]})
    return rows


def summarize(rows) -> dict:
    plain = [r for r in rows if not r.get("manipulated")]
    manipulated = [r for r in rows if r.get("manipulated")]
    return {
        "flagged": rates(plain),
        "invalidated": rates(plain, ("invalid",)),
        "manipulated": {"n": len(manipulated), "not_valid": sum(1 for r in manipulated if r["status"] != "valid"),
                        "passed_as_valid": [r["name"] for r in manipulated if r["status"] == "valid"]},
        "false_negatives": [r["name"] for r in plain if r["label"] == "violation" and r["status"] == "valid"],
        "false_positives": [r["name"] for r in plain if r["label"] == "clean" and r["status"] != "valid"],
        "status_counts": {f"{label} -> {status}": n for (label, status), n in
                          Counter((r["label"], r["status"]) for r in plain).most_common()},
    }


def markdown(title: str, summary: dict) -> str:
    def row(name, m):
        def pct(x):
            return "–" if x is None else f"{100 * x:.1f} %"
        return (f"| {name} | {m['n']} | {m['tp']} | {m['fp']} | {m['tn']} | {m['fn']} | {pct(m['precision'])} | "
                f"{pct(m['recall'])} | {pct(m['fpr'])} | {pct(m['fnr'])} |")
    lines = [f"### {title}", "",
             "| Decision counted as positive | n | TP | FP | TN | FN | Precision | Recall | FPR | FNR |",
             "|---|---|---|---|---|---|---|---|---|---|",
             row("REVIEW or INVALID (not counted automatically)", summary["flagged"]),
             row("INVALID only (rejected automatically)", summary["invalidated"]), ""]
    m = summary["manipulated"]
    if m["n"]:
        lines.append(f"Manipulated / insufficient recordings: {m['not_valid']} of {m['n']} not passed as VALID"
                     + (f" (passed: {', '.join(m['passed_as_valid'])})" if m["passed_as_valid"] else "") + ".")
    if summary["false_negatives"]:
        lines.append(f"False negatives ({len(summary['false_negatives'])}): " + "; ".join(summary["false_negatives"][:12])
                     + (" …" if len(summary["false_negatives"]) > 12 else ""))
    if summary["false_positives"]:
        lines.append(f"False positives ({len(summary['false_positives'])}): " + "; ".join(summary["false_positives"][:12]))
    return "\n".join(lines) + "\n"


def real_labels(path: Path):
    from types import SimpleNamespace

    from server import integrity
    from server.database import SessionLocal
    from server.lap_check import check_lap, inputs_for_lap
    from server.models import Lap

    rows, skipped = [], []
    with path.open(encoding="utf-8") as handle:
        wanted = [r for r in csv.DictReader(handle)]
    with SessionLocal() as db:
        for r in wanted:
            label = (r.get("label") or "").strip().lower()
            try:
                lap_id = int(r.get("lap_id", ""))
            except ValueError:
                skipped.append((r.get("lap_id"), "lap_id is not a number"))
                continue
            if label not in ("clean", "violation"):
                skipped.append((lap_id, f"label {label!r} is neither clean nor violation"))
                continue
            lap = db.get(Lap, lap_id)
            if lap is None:
                skipped.append((lap_id, "no such lap"))
                continue
            checked = integrity.verify(lap)
            if not checked.ok:
                skipped.append((lap_id, f"file failed its integrity check: {checked.status}"))
                continue
            path_ok, _ = integrity.resolve_inside_storage(lap.telemetry_path)
            inputs = inputs_for_lap(lap)
            meta = SimpleNamespace(track_name=lap.track_name, lap_time=lap.lap_time, sector_times=lap.sector_times or [],
                                   sample_count=lap.sample_count, tail_samples=None, game_lap_state=None, lap_report=None,
                                   track_grip_level=None, fixed_setup=None, track_limits_steps_per_point=None,
                                   track_limits_steps_per_penalty=None)
            check = check_lap(db, path_ok, meta, client_valid=bool(inputs.get("client_valid", True)), exclude_lap_id=lap.id,
                              inputs=inputs, client_version=lap.client_version)
            stored = "invalid" if not lap.is_valid else ((lap.validation or {}).get("status") or "valid")
            rows.append({"name": f"lap {lap_id} {lap.track_name} {r.get('note', '')}".strip(), "lap_id": lap_id,
                         "label": label, "status": check.status, "stored_status": stored,
                         "codes": [f.code for f in check.verdict.findings if f.severity != "info"]})
        db.rollback()          # nothing is ever written
    return rows, skipped


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--labels")
    parser.add_argument("--quick", action="store_true")
    parser.add_argument("--json")
    parser.add_argument("--markdown")
    args = parser.parse_args(argv)

    if args.labels:
        rows, skipped = real_labels(Path(args.labels))
        result = {"source": "real laps (labelled)", "current_rules": summarize(rows),
                  "stored_verdicts": summarize([{**r, "status": r["stored_status"]} for r in rows]),
                  "rows": rows, "skipped": skipped}
        text = (markdown(f"Real laps, current rules (n = {len(rows)})", result["current_rules"])
                + "\n" + markdown("Real laps, verdicts stored at upload", result["stored_verdicts"]))
        if skipped:
            text += "\nSkipped: " + "; ".join(f"{a}: {b}" for a, b in skipped) + "\n"
    else:
        from tests.helpers.trackcheck_cases import cases

        curated = judge_synthetic(cases())
        result = {"source": "synthetic reference set", "curated": summarize(curated),
                  "curated_mismatches": [r["name"] for r in curated if r["status"] != r["expected"]]}
        text = markdown(f"Curated reference cases (n = {len(curated)})", result["curated"])
        if not args.quick:
            generated = judge_synthetic(grid())
            result["grid"] = summarize(generated)
            text += "\n" + markdown(f"Generated grid (n = {len(generated)})", result["grid"])
        if result["curated_mismatches"]:
            text += "\nCurated cases deciding differently than expected: " + "; ".join(result["curated_mismatches"]) + "\n"
    print(text)
    if args.json:
        Path(args.json).write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    if args.markdown:
        Path(args.markdown).write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
