"""V0.9.9 — every mutant listed as equivalent in scripts/mutation_check.py still exists.

The list is keyed by line number; when code above such a line moves, the key would silently stop
matching and the mutant would show up as a "survivor" (found in V0.9.9: two _crossing entries had
moved by 24 lines). This test catches a stale key at once instead of in a 40-minute mutation run."""
from __future__ import annotations

from pathlib import Path

from scripts.mutation_check import EQUIVALENT, TARGETS, mutants

ROOT = Path(__file__).resolve().parent.parent


def test_every_equivalent_mutant_still_exists_with_a_reason():
    generated = set()
    for target in {key[0] for key in EQUIVALENT}:
        path, functions, _tests = TARGETS[target]
        source = (ROOT / path).read_text(encoding="utf-8")
        generated |= {(target, function, desc) for function, desc, _ in mutants(source, functions)}
    stale = sorted(key for key in EQUIVALENT if key not in generated)
    assert not stale, f"equivalent-mutant keys that no longer match a mutant (line moved?): {stale}"
    assert all(isinstance(reason, str) and len(reason) > 10 for reason in EQUIVALENT.values())


def test_every_mutant_has_its_own_description_so_one_key_skips_exactly_one_mutant():
    """V0.9.9.1: two identical changes on one line (e.g. both `- 1` in `clip(x - 1, 0, n - 1)`) had
    the same text, so one EQUIVALENT key skipped both — 27 keys skipped 31 mutants, 3 of them not
    equivalent at all (they are killed). Now the second is "... #2" and every key matches one."""
    for target, (path, functions, _tests) in TARGETS.items():
        source = (ROOT / path).read_text(encoding="utf-8")
        descs = [(function, desc) for function, desc, _ in mutants(source, functions)]
        duplicates = sorted({d for d in descs if descs.count(d) > 1})
        assert not duplicates, f"{target}: mutants sharing a description: {duplicates}"
        skipped = [d for d in descs if (target, *d) in EQUIVALENT]
        assert len(skipped) == len([k for k in EQUIVALENT if k[0] == target]), target
