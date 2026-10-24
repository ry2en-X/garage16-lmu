"""
scripts/mutation_check.py — do the tests notice a wrong calculation? (V0.9.8)

Coverage only says a line ran. This deliberately breaks the critical logic, one small change at a
time, and runs the tests that guard it: every change must make at least one test fail ("killed").
A change that survives means the tests would not have noticed that bug — the report lists it.

Mutations (Python AST, one per run): a comparison flipped or loosened (`<`→`<=`, `<=`→`<`, `>`→`>=`,
`>=`→`>`, `==`→`!=`, `!=`→`==`), `+`↔`-`, `and`↔`or`, a numeric constant changed (`x`→`x+1`,
`0`→`1`), `True`↔`False`. Only inside the functions listed in TARGETS — the decisions that must be
right (client address, records, the distance axis, pedal crossings, the write lock, rate limits;
V0.9.9: the lap decision, file integrity and quarantine, upload limits, which laps count, the audit chain).

    python -m scripts.mutation_check                 every target
    python -m scripts.mutation_check client_ip       one target
    python -m scripts.mutation_check --list          what would be mutated

Runs in a staged copy of the tree (scripts/release_check.stage_tree) — the working tree is never
touched. Exit code 0 when every mutant was killed (or an equivalent mutant is listed in
EQUIVALENT with the reason), 1 otherwise.
"""

from __future__ import annotations

import argparse
import ast
import copy
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# target -> (file, functions, the tests that guard them)
TARGETS = {
    "client_ip": ("server/client_ip.py", ["resolve", "_ip", "_trusted"],
                  ["tests/test_v098_client_ip.py", "tests/test_v098_mutation_gaps.py"]),
    "records": ("server/records.py", ["evaluate_and_record"],
                ["tests/test_v098_mutation_gaps.py", "tests/test_v098_record_races.py", "tests/test_concurrency.py",
                 "tests/test_records_car_identity.py", "tests/test_team_dashboard.py", "tests/test_you_vs_team.py"]),
    "distance_axis": ("server/distance_axis.py", ["distance_axis", "fill_short_gaps"],
                      ["tests/test_v098_axis_exact.py", "tests/test_v097_reference_laps.py", "tests/test_v094_laptime.py"]),
    "crossings": ("server/telemetry_analysis.py", ["_crossing", "_spacing", "_brake_run", "_first"],
                  ["tests/test_v098_axis_exact.py", "tests/test_v097_reference_laps.py", "tests/test_v091_accuracy.py"]),
    "write_lock": ("server/write_lock.py", ["maintenance_active", "writing"],
                   ["tests/test_v098_write_lock.py", "tests/test_v098_mutation_gaps.py"]),
    "rate_limit": ("server/rate_limit.py", ["_check"],
                   ["tests/test_rate_limit.py", "tests/test_v098_client_ip.py", "tests/test_v098_mutation_gaps.py"]),
    # V0.9.9: the lap decision, integrity, quarantine, upload limits, which laps count
    "lap_decision": ("server/lap_decision.py",
                     ["expected_signals", "evidence_level", "_signal_findings", "_fresh", "_quality", "_motion", "_frozen", "assess"],
                     ["tests/test_v099_decision_units.py", "tests/test_v099_trackcheck_reference.py", "tests/test_v099_decision.py"]),
    "decision_status": ("server/lap_check.py", ["is_valid", "review_status", "status", "_link"],
                        ["tests/test_v099_decision.py", "tests/test_v093_track_limits.py", "tests/test_v0931_revalidation.py"]),
    "integrity": ("server/integrity.py", ["check_mac", "verify", "resolve_inside_storage", "quarantine", "release"],
                  ["tests/test_v099_integrity.py"]),
    "official": ("server/official.py", ["is_official", "is_visible"],
                 ["tests/test_v099_decision.py", "tests/test_v099_integrity.py"]),
    "upload_limits": ("server/telemetry_analysis.py", ["check_limits"], ["tests/test_v099_decision.py"]),
    "audit_chain": ("server/audit.py", ["verify_chain"], ["tests/test_v099_integrity.py"]),
}

# (target, function, mutation description) -> why the change cannot alter behaviour
EQUIVALENT = {
    # lap_decision (V0.9.9): float tolerances, and guards that the frames before them make unreachable
    ("lap_decision", "_fresh", "line 156: > -> >="): "with one finite frame idx[1:] is empty: the assignment is a no-op either way",
    ("lap_decision", "_fresh", "line 157: > -> >="): "two frames exactly 1e-9 s apart do not occur",
    ("lap_decision", "_quality", "line 168: > -> >="): "fresh frames strictly increase, so with >= 2 of them the duration is > 0",
    ("lap_decision", "_quality", "line 168: 0.0 -> 1.0"): "unreachable for the same reason (duration > 0)",
    ("lap_decision", "_motion", "line 191: > -> >="): "a distance update of exactly 1e-6 m does not occur",
    ("lap_decision", "_motion", "line 193: 0.0 -> 1.0"): "rows with an unknown speed are excluded from every compared segment (`known`)",
    ("lap_decision", "_motion", "line 194: 0.0 -> 1.0"): "a constant offset of the running sum cancels in cum[b] - cum[a]",
    ("lap_decision", "_motion", "line 212: > -> >="): "|ratio - 1| is a multiple of 2^-53 and never exactly the float 0.05",
    # _crossing: the caller passes i = the first sample at/above the threshold, so values[i-1] is
    # below it and values[i] above it — these guards only protect against a broken call
    ("crossings", "_crossing", "line 563: >= -> >"): "values[i-1] == threshold cannot happen (i is the FIRST sample at/above it)",
    ("crossings", "_crossing", "line 563: <= -> <"): "values[i] == values[i-1] cannot happen (one is below, one at/above the threshold)",
    # distance_axis: tolerances against float noise and bounds that cannot bind
    ("distance_axis", "distance_axis", "line 73: > -> >="): "two frames exactly 1e-9 s apart do not occur",
    ("distance_axis", "distance_axis", "line 80: > -> >="): "an advance of exactly 1e-6 m does not occur",
    ("distance_axis", "distance_axis", "line 93: 0.0 -> 1.0"): "a sample without a speed makes its stretches time-shared, so the 0 put in its place is never used",
    ("distance_axis", "distance_axis", "line 86: < -> <="): "with every row an anchor there is nothing between anchors to fill (no effect)",
    ("distance_axis", "distance_axis", "line 100: - -> + #2"): "the upper clip bound never binds: searchsorted - 1 <= len(anchors) - 1",
    ("distance_axis", "distance_axis", "line 100: 1 -> 2 #2"): "rows after the last anchor are never 'between' (nxt_pos == len(anchors))",
    ("distance_axis", "distance_axis", "line 116: 0.0 -> 1.0 #2"): "share is NaN only when the stretch has no time (impossible: frames are distinct)",
    ("distance_axis", "distance_axis", "line 116: 1.0 -> 2.0"): "share stays within [0, 1] by construction (cum and t do not decrease)",
    ("distance_axis", "distance_axis", "line 131: >= -> >"): "a candidate exactly 1e-9 below the running maximum does not occur",
    ("distance_axis", "distance_axis", "line 131: 1e-09 -> 1.000000001"): "tolerance against float noise only",
    ("distance_axis", "distance_axis", "line 131: > -> >="): "an advance of exactly 1e-6 m does not occur",
    ("distance_axis", "distance_axis", "line 132: > -> >="): "rows with running maximum -inf are already unusable",
    ("client_ip", "_ip", "line 57: 1 -> 2"): "split(':', 2) of 'a.b.c.d:port' gives the same first part",
    ("write_lock", "maintenance_active", "line 91: > -> >="): "an age of exactly MARKER_STALE_S (a float) is not observable",
    ("write_lock", "writing", "line 116: >= -> >"): "the deadline reached to the exact float is not observable",
    # V0.9.9
    ("upload_limits", "check_limits", "line 160: 1024 -> 1025"): "the MiB figure in the error message only (the limit is compared in bytes)",
    ("upload_limits", "check_limits", "line 160: 1024 -> 1025 #2"): "the same figure, the second factor of 1024 * 1024",
}

CMP_SWAP = {ast.Lt: ast.LtE, ast.LtE: ast.Lt, ast.Gt: ast.GtE, ast.GtE: ast.Gt, ast.Eq: ast.NotEq, ast.NotEq: ast.Eq}
NAMES = {ast.Lt: "<", ast.LtE: "<=", ast.Gt: ">", ast.GtE: ">=", ast.Eq: "==", ast.NotEq: "!=",
         ast.Add: "+", ast.Sub: "-", ast.And: "and", ast.Or: "or"}


def _sites(func: ast.FunctionDef):
    """(node, description, kind) for every mutable spot inside `func`. V0.9.9.1: descriptions are
    unique per function — a second identical change on the same line is "... #2" (before, one
    EQUIVALENT key silently skipped every mutant with that text, including non-equivalent ones)."""
    seen: dict = {}
    for node, desc, kind in _raw_sites(func):
        seen[desc] = seen.get(desc, 0) + 1
        yield node, (desc if seen[desc] == 1 else f"{desc} #{seen[desc]}"), kind


def _raw_sites(func: ast.FunctionDef):
    for node in ast.walk(func):
        line = getattr(node, "lineno", 0)
        if isinstance(node, ast.Compare):
            for i, op in enumerate(node.ops):
                if type(op) in CMP_SWAP:
                    yield node, f"line {line}: {NAMES[type(op)]} -> {NAMES[CMP_SWAP[type(op)]]}", ("cmp", i)
        elif isinstance(node, ast.BinOp) and type(node.op) in (ast.Add, ast.Sub):
            other = ast.Sub if isinstance(node.op, ast.Add) else ast.Add
            yield node, f"line {line}: {NAMES[type(node.op)]} -> {NAMES[other]}", ("binop", None)
        elif isinstance(node, ast.BoolOp):
            other = "or" if isinstance(node.op, ast.And) else "and"
            yield node, f"line {line}: {NAMES[type(node.op)]} -> {other}", ("boolop", None)
        elif isinstance(node, ast.Constant) and type(node.value) in (int, float) and not isinstance(node.value, bool):
            yield node, f"line {line}: {node.value!r} -> {node.value + 1!r}", ("const", None)
        elif isinstance(node, ast.Constant) and isinstance(node.value, bool):
            yield node, f"line {line}: {node.value} -> {not node.value}", ("bool", None)


def _mutate(node, kind):
    what, i = kind
    if what == "cmp":
        node.ops[i] = CMP_SWAP[type(node.ops[i])]()
    elif what == "binop":
        node.op = ast.Sub() if isinstance(node.op, ast.Add) else ast.Add()
    elif what == "boolop":
        node.op = ast.Or() if isinstance(node.op, ast.And) else ast.And()
    elif what == "const":
        node.value = node.value + 1
    elif what == "bool":
        node.value = not node.value


def mutants(source: str, functions):
    """Yield (function, description, mutated source)."""
    tree = ast.parse(source)
    funcs = {n.name: n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    for name in functions:
        func = funcs[name]
        sites = list(_sites(func))
        for index in range(len(sites)):
            mutated_tree = copy.deepcopy(tree)
            mfunc = {n.name: n for n in ast.walk(mutated_tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}[name]
            node, desc, kind = list(_sites(mfunc))[index]
            # skip docstrings and constants inside f-strings / log messages
            _mutate(node, kind)
            try:
                yield name, desc, ast.unparse(mutated_tree)
            except Exception:  # noqa: BLE001
                continue


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Mutation check of the critical logic.")
    parser.add_argument("targets", nargs="*", default=list(TARGETS))
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--timeout", type=float, default=300.0)
    args = parser.parse_args(argv)

    from scripts.release_check import stage_tree

    if args.list:
        for target in args.targets:
            path, functions, tests = TARGETS[target]
            n = sum(1 for _ in mutants((ROOT / path).read_text(encoding="utf-8"), functions))
            print(f"{target}: {n} mutants in {path} {functions} — guarded by {', '.join(tests)}")
        return 0

    survivors, total, killed = [], 0, 0
    with tempfile.TemporaryDirectory(prefix="garage16-mutants-") as tmp:
        stage = stage_tree(Path(tmp) / "tree")
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        for target in args.targets:
            path, functions, tests = TARGETS[target]
            original = (stage / path).read_text(encoding="utf-8")
            baseline = subprocess.run([sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", *tests],
                                      cwd=stage, env=env, capture_output=True, text=True)
            if baseline.returncode != 0:
                print(f"{target}: the guarding tests fail WITHOUT a mutation — fix them first\n{baseline.stdout[-2000:]}")
                return 1
            for function, desc, source in mutants(original, functions):
                if (target, function, desc) in EQUIVALENT:
                    continue
                total += 1
                (stage / path).write_text(source, encoding="utf-8")
                started = time.time()
                try:
                    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", *tests],
                                       cwd=stage, env=env, capture_output=True, text=True, timeout=args.timeout)
                    dead = r.returncode != 0
                except subprocess.TimeoutExpired:
                    dead = True                       # a mutant that hangs the tests is noticed too
                finally:
                    (stage / path).write_text(original, encoding="utf-8")
                killed += dead
                print(f"{'killed  ' if dead else 'SURVIVED'} {target}.{function} {desc} ({time.time() - started:.1f} s)", flush=True)
                if not dead:
                    survivors.append(f"{target}.{function}: {desc}")
    print(f"\n{killed} of {total} mutants killed ({100.0 * killed / max(total, 1):.0f} %)")
    for s in survivors:
        print(f"  SURVIVED {s}")
    return 0 if not survivors else 1


if __name__ == "__main__":
    sys.exit(main())
