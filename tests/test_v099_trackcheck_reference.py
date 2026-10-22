"""V0.9.9 — the track-limit reference set (section 2 of the V0.9.9 order).

* every curated case (tests/helpers/trackcheck_cases.py) decides as expected, with the reason
  that makes it so (the expectation comes from the geometry / LMU's documented behaviour, never
  from the implementation — tests/helpers/trackcheck.py builds the truth independently);
* the measured error rates of the curated set are pinned (tests/reference/trackcheck_metrics.json,
  regenerate with GARAGE16_UPDATE_TRACKCHECK=1 together with docs/QUALITY.md);
* the exact limits of every rule (at, just below, just above);
* properties (hypothesis): noise within the tolerances never changes a clean verdict, removing a
  signal never makes a lap VALID, reordering samples never passes.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from server import lap_decision as ld
from server import track_limits as tl
from tests.helpers.trackcheck import CARS, TRACKS, Excursion, Scenario, judge
from tests.helpers.trackcheck_cases import cases

ROOT = Path(__file__).resolve().parent.parent
METRICS = ROOT / "tests" / "reference" / "trackcheck_metrics.json"
CASES = cases()


@pytest.mark.parametrize("case", CASES, ids=[c.name for c in CASES])
def test_reference_case(case: Scenario):
    check, lap, _df = judge(case)
    assert check.status == case.expected, f"{case.name}: {case.why} — findings {[f.code for f in check.verdict.findings]}"
    severities = {f.severity for f in check.verdict.findings}
    if case.expected == "valid":
        assert not severities & {tl.REVIEW, tl.INVALID}
    elif case.expected == "review":
        assert tl.REVIEW in severities and check.is_valid and check.review_status == "held"
    else:
        assert not check.is_valid
    # every verdict says what it was based on, and how much evidence there was (a count, no probability)
    decision = check.verdict.decision
    assert decision["validators"] == {"track_limits": tl.TRACK_LIMITS_VERSION, "decision": ld.DECISION_VERSION}
    assert decision["evidence"]["level"] in ("strong", "moderate", "weak", "none")
    assert "not a probability" in decision["evidence"]["note"]


def test_the_set_covers_every_kind_of_case_the_order_names():
    names = " ".join(c.name for c in CASES).lower()
    for needle in ("clean line", "kerbs", "high lateral", "hairpin", "noise", "missing", "frames",       # 2.1
                   "gravel", "chicane cut", "jumps", "+40 m/s", "squeezed", "removed", "never writes",   # 2.2
                   "every row twice", "cut off", "pasted", "repeated frame",
                   "exactly on the line", "5 cm inside", "5 cm beyond", "last metres", "0.9.0"):          # 2.3
        assert needle in names, needle
    assert {c.track for c in CASES} == set(TRACKS) and {c.car.name for c in CASES} == {c.name for c in CARS}
    assert all(c.why for c in CASES), "every case states why"
    assert {c.expected for c in CASES} == {"valid", "review", "invalid"}


def _curated_summary():
    from scripts.trackcheck_eval import judge_synthetic, summarize

    return summarize(judge_synthetic(CASES))


def test_the_measured_error_rates_are_pinned():
    summary = _curated_summary()
    pinned = {k: summary[k] for k in ("flagged", "invalidated", "false_negatives", "false_positives")}
    pinned["manipulated"] = summary["manipulated"]
    if os.environ.get("GARAGE16_UPDATE_TRACKCHECK") == "1":
        METRICS.write_text(json.dumps(pinned, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    stored = json.loads(METRICS.read_text(encoding="utf-8"))
    assert pinned == stored, "the decisions changed: rerun scripts.trackcheck_eval, update docs/QUALITY.md and the pin"
    # the documented numbers in QUALITY.md are these
    quality = (ROOT / "docs" / "QUALITY.md").read_text(encoding="utf-8")
    flagged = stored["flagged"]
    assert f"| curated | {flagged['n']} | {flagged['tp']} | {flagged['fp']} | {flagged['tn']} | {flagged['fn']} |" in quality


# ------------------------------------------------------------------------- exact rule limits

N = 600


def _cols(n=N, **overrides):
    t = np.linspace(0.0, n / 60.0, n)
    cols = {"t": t, "lap_dist": np.linspace(0.0, 600.0, n), "speed": np.full(n, 60.0),
            "lap_invalidated": np.zeros(n), "track_limits_steps": np.zeros(n), "count_lap_flag": np.full(n, 2.0),
            "num_penalties": np.zeros(n), "path_lateral": np.zeros(n), "track_edge": np.full(n, 6.0),
            **{f"surface_type_{w}": np.zeros(n) for w in tl.WHEELS}}
    cols.update(overrides)
    return cols


@pytest.mark.parametrize("beyond,expected", [(1.99, False), (2.0, True), (2.01, True)])
def test_edge_exceeded_at_exactly_two_metres(beyond, expected):
    lateral = np.zeros(N)
    lateral[200:260] = 6.0 + beyond               # 1 s
    v = tl.assess(_cols(path_lateral=lateral))
    assert ("edge_exceeded" in [f.code for f in v.findings]) is expected


@pytest.mark.parametrize("samples,expected", [(17, False), (18, False), (20, True)])
def test_edge_exceeded_needs_three_tenths_of_a_second(samples, expected):
    lateral = np.zeros(N)
    lateral[200:200 + samples] = 9.0              # samples at 60 Hz: 17 -> 0.27 s, 18 -> 0.28 s, 20 -> 0.32 s
    v = tl.assess(_cols(path_lateral=lateral))
    assert ("edge_exceeded" in [f.code for f in v.findings]) is expected


@pytest.mark.parametrize("off,expected", [(2, False), (3, False), (4, True)])
def test_four_wheels_off_needs_three_samples_and_five_hundredths(off, expected):
    """OFF_MIN_SAMPLES = 3 AND OFF_MIN_SECONDS = 0.05: at ~60 Hz three samples span 0.033 s (too
    short), four span 0.050 s."""
    surf = np.zeros(N)
    surf[300:300 + off] = 2.0
    v = tl.assess(_cols(**{f"surface_type_{w}": surf for w in tl.WHEELS}))
    assert ("four_wheels_off" in [f.code for f in v.findings]) is expected


def test_three_wheels_off_is_not_four():
    cols = _cols()
    for w in ("fl", "fr", "rl"):
        s = np.zeros(N)
        s[300:340] = 2.0
        cols[f"surface_type_{w}"] = s
    codes = {f.code: f.severity for f in tl.assess(cols).findings}
    assert codes == {"wheels_off": tl.INFO}


def _decide(cols, client_version="0.9.4", **kw):
    v = tl.assess(cols)
    return ld.assess(cols, v, client_version=client_version, **kw)


@pytest.mark.parametrize("gap,expected", [(0.99, False), (1.02, True)])
def test_a_coverage_gap_counts_from_just_over_one_second(gap, expected):
    t = np.arange(0, 20, 0.02)
    t = np.concatenate([t[t < 5], t[t >= 5] + gap - 0.02])
    n = len(t)
    cols = _cols(n, t=t, lap_dist=60.0 * t, speed=np.full(n, 60.0))
    assert ("coverage_gap" in [f.code for f in _decide(cols).findings]) is expected


@pytest.mark.parametrize("hz,expected", [(10.0, False), (9.5, True)])
def test_the_frame_rate_floor_is_ten_per_second(hz, expected):
    n = int(30 * hz)
    t = np.arange(n) / hz
    cols = _cols(n, t=t, lap_dist=60.0 * t, speed=np.full(n, 60.0))
    assert ("low_frame_rate" in [f.code for f in _decide(cols).findings]) is expected


@pytest.mark.parametrize("jump,expected", [(49.0, False), (50.5, True)])
def test_a_position_jump_needs_fifty_metres_beyond_the_speed(jump, expected):
    cols = _cols()
    d = cols["lap_dist"].copy()
    d[300:] += jump
    cols["lap_dist"] = d
    assert ("position_jump" in [f.code for f in _decide(cols).findings]) is expected


@pytest.mark.parametrize("ratio,expected", [(1.049, False), (1.051, True), (0.951, False), (0.949, True)])
def test_speed_and_distance_may_differ_by_five_percent(ratio, expected):
    cols = _cols(speed=np.full(N, 60.0 * ratio))
    assert ("speed_distance_mismatch" in [f.code for f in _decide(cols).findings]) is expected


@pytest.mark.parametrize("g,expected", [(7.5, False), (8.5, True)])
def test_acceleration_above_eight_g_over_a_tenth_of_a_second(g, expected):
    t = np.arange(0, 10, 0.02)
    v = np.full(len(t), 60.0)
    k = len(t) // 2
    v[k:] = 60.0 - g * 9.80665 * np.clip(t[k:] - t[k], 0, 0.5)     # 0.5 s of braking at g
    d = np.concatenate(([0.0], np.cumsum(0.5 * (v[1:] + v[:-1]) * np.diff(t))))
    cols = _cols(len(t), t=t, lap_dist=d, speed=v)
    assert ("implausible_acceleration" in [f.code for f in _decide(cols).findings]) is expected


@pytest.mark.parametrize("frames,expected", [(24, False), (26, True)])
def test_frozen_frames_from_half_a_second(frames, expected):
    t = np.arange(0, 10, 0.02)                    # 50 Hz: 25 frames = 0.5 s
    n = len(t)
    cols = _cols(n, t=t, lap_dist=60.0 * t + 0.0, speed=60.0 + np.sin(t), rpm=7000 + np.sin(t * 3),
                 throttle=np.full(n, 1.0), brake=np.zeros(n), steering=np.zeros(n))
    k = 200
    for name in ("speed", "rpm", "lap_dist"):
        cols[name] = np.asarray(cols[name], dtype=float).copy()
        cols[name][k:k + frames + 1] = cols[name][k]
    assert ("frozen_samples" in [f.code for f in _decide(cols).findings]) is expected


def test_one_bad_sample_never_invalidates_on_its_own():
    """A single wrong value is REVIEW (a human looks) — never an automatic INVALID."""
    cols = _cols()
    s = cols["speed"].copy()
    s[300] = 110.0
    cols["speed"] = s
    v = _decide(cols)
    assert v.status == tl.STATUS_REVIEW and not v.invalidating


def test_a_missing_flag_is_no_proof_of_a_valid_lap():
    cols = _cols()
    del cols["lap_invalidated"]
    assert _decide(cols).status == tl.STATUS_REVIEW
    cols = _cols(lap_invalidated=np.full(N, np.nan))
    assert _decide(cols).status == tl.STATUS_REVIEW


def test_contradicting_flags_are_review_not_valid():
    """LMU's count flag says "counted", its invalidation flag is garbage -> REVIEW."""
    cols = _cols(lap_invalidated=np.full(N, 7.0))
    v = _decide(cols)
    assert v.status == tl.STATUS_REVIEW and "signals_missing" in [f.code for f in v.findings]


def test_unknown_inputs_version_and_a_reused_file_are_review():
    assert "unsupported_inputs" in [f.code for f in _decide(_cols(), inputs_version=9).findings]
    assert "telemetry_reused" in [f.code for f in _decide(_cols(), reused_by_others=1).findings]


# ------------------------------------------------------------------------------- properties

PROPERTY = settings(max_examples=20, deadline=None, suppress_health_check=[HealthCheck.too_slow], derandomize=True,
                    database=None)          # reproducible, and nothing written into the tested tree
clean_scenarios = st.builds(
    Scenario, name=st.just("property"), track=st.sampled_from(sorted(TRACKS)), car=st.sampled_from(CARS),
    line=st.sampled_from(["clean", "kerbs"]), noise=st.floats(0.0, 1.0), seed=st.integers(0, 10_000),
    frame_hz=st.sampled_from([25.0, 50.0, 60.0]), poll_hz=st.sampled_from([None, 60.0]), lmu=st.just("strict"))


@PROPERTY
@given(clean_scenarios)
def test_property_noise_within_tolerance_keeps_a_clean_lap_valid(sc):
    check, lap, _ = judge(sc)
    assert lap.label == "clean" and check.status == tl.STATUS_VALID, [f.code for f in check.verdict.findings]


@PROPERTY
@given(clean_scenarios, st.sampled_from(ld.expected_signals("0.9.4")))
def test_property_removing_any_signal_never_makes_a_lap_valid(sc, column):
    from tests.helpers.trackcheck import build, record

    lap = build(sc)
    df = record(sc, lap).drop(columns=[column])
    cols = {c: df[c].to_numpy(dtype=float) for c in df.columns}
    assert _decide(cols).status != tl.STATUS_VALID


@PROPERTY
@given(clean_scenarios, st.integers(10, 400), st.integers(5, 60))
def test_property_reordered_samples_never_pass(sc, start, size):
    from server.validation import validate_dataframe
    from tests.helpers.trackcheck import build, lap_time_of, record
    from types import SimpleNamespace

    lap = build(sc)
    df = record(sc, lap)
    a, b = df.iloc[start:start + size], df.iloc[start + size:start + 2 * size]
    import pandas as pd

    swapped = pd.concat([df.iloc[:start], b, a, df.iloc[start + 2 * size:]]).reset_index(drop=True)
    lt = lap_time_of(df, lap, sc)
    meta = SimpleNamespace(lap_time=lt, sector_times=[lt / 3] * 3, sample_count=len(swapped))
    plausible = validate_dataframe(swapped, meta).is_valid
    cols = {c: swapped[c].to_numpy(dtype=float) for c in swapped.columns}
    assert not plausible or _decide(cols).status != tl.STATUS_VALID


@PROPERTY
@given(st.sampled_from(sorted(TRACKS)), st.sampled_from(CARS), st.integers(0, 10_000), st.floats(0.0, 1.0))
def test_property_a_clear_excursion_stays_flagged_under_noise(track, car, seed, noise):
    from tests.helpers.trackcheck_cases import GRASS_SPOTS

    section, kerb = GRASS_SPOTS[track]
    sc = Scenario("property off", track=track, car=car, seed=seed, noise=noise, lmu="silent",
                  excursions=(Excursion(section, kerb + 0.3),))
    check, lap, _ = judge(sc)
    assert lap.label == "violation" and check.status != tl.STATUS_VALID


@pytest.mark.parametrize("version,expected", [
    (None, set()), ("0.8.9", set()), ("0.9.0", {"0.9.0"}), ("0.9.2", {"0.9.0"}), ("0.9.3", {"0.9.0", "0.9.3"}),
    ("0.9.4", {"0.9.0", "0.9.3"}), ("1.0.0", {"0.9.0", "0.9.3"}),
])
def test_which_signals_a_client_version_must_deliver(version, expected):
    groups = dict(ld.EXPECTED_SIGNALS)
    want = [c for since, cols in ld.EXPECTED_SIGNALS if since in expected for c in cols]
    assert ld.expected_signals(version) == want
    assert set(groups) == {"0.9.0", "0.9.3"}


@pytest.mark.parametrize("families,level", [
    ([], "none"), (["lmu_verdict"], "weak"), (["lmu_verdict", "lmu_verdict"], "weak"),
    (["lmu_verdict", "surface"], "moderate"), (["lmu_verdict", "surface", "lmu_steps"], "moderate"),
    (["lmu_verdict", "surface", "lmu_steps", "track_edge"], "strong"),
    (["lmu_verdict", "surface", "lmu_steps", "track_edge", "distance"], "strong"),
])
def test_evidence_is_a_count_of_independent_families(families, level):
    assert ld.evidence_level(families) == level


def test_one_family_alone_is_insufficient_two_are_enough():
    t = np.linspace(0, 10, 600)
    base = {"t": t, "lap_dist": 60.0 * t, "speed": np.full(600, 60.0)}
    one = tl.Verdict(tl.STATUS_VALID, [], ["lap_invalidated", "client"])
    assert "insufficient_evidence" in [f.code for f in ld.assess(base, one).findings]
    two = tl.Verdict(tl.STATUS_VALID, [], ["lap_invalidated", "surface_type", "client"])
    assert "insufficient_evidence" not in [f.code for f in ld.assess(base, two).findings]
    none = tl.Verdict(tl.STATUS_VALID, [], ["client"])
    assert "insufficient_evidence" not in [f.code for f in ld.assess(base, none).findings], "none = not_verifiable's job"


# ------------------------------------------------------------------ the decision layer's measurements, exactly

def test_quality_figures_are_reported_exactly():
    t = np.linspace(0.0, 30.0, 300)                      # 300 frames over exactly 30 s = 10.0 per s
    quality = {}
    findings = ld._quality(t, 60.0 * t, np.full(300, 60.0), quality)
    assert quality == {"frames": 300, "frame_rate_hz": 10.0, "longest_gap_s": round(30.0 / 299, 3)}
    assert findings == [], "exactly 10 frames per second is enough"


def test_a_gap_of_exactly_one_second_is_still_covered():
    step = 0.0625                                         # binary fractions: the gap is exactly 1.0
    t = np.concatenate([np.arange(0, 5, step), 5 - step + 1.0 + np.arange(0, 5, step)])
    quality = {}
    codes = [f.code for f in ld._quality(t, 60.0 * t, np.full(len(t), 60.0), quality)]
    assert quality["longest_gap_s"] == 1.0 and "coverage_gap" not in codes


def test_two_frames_are_judged_one_is_not():
    q2, q1 = {}, {}
    assert ld._quality(np.array([0.0, 0.05]), np.array([0.0, 3.0]), np.array([60.0, 60.0]), q2) == []
    assert q2["frames"] == 2 and q2["frame_rate_hz"] == 40.0
    [one] = ld._quality(np.array([0.0, 0.0]), np.array([0.0, 0.0]), np.array([60.0, 60.0]), q1)
    assert one.code == "low_frame_rate" and one.detail == {"frames": 1}


def test_repeated_polls_are_not_new_frames():
    t = np.array([0.0, 0.0, 0.02, 0.02, 0.04, np.nan, 0.06])
    assert ld._fresh(t).tolist() == [True, False, True, False, True, False, True]
    assert ld._fresh(np.array([1.0])).tolist() == [True]


def test_the_missing_signals_detail_names_the_version_and_columns():
    cols = _cols()
    del cols["track_edge"]
    cols["num_penalties"] = np.full(N, np.nan)
    version = "0.9.4.123456789012345678901234567890"          # longer than the 32 characters kept
    v = tl.assess(cols)
    [finding] = [f for f in ld.assess(cols, v, client_version=version).findings if f.code == "signals_missing"]
    assert finding.detail == {"client_version": version[:32], "missing": ["track_edge"], "empty": ["num_penalties"],
                              "unreadable": []}


STEP = 1 / 64                                              # binary: every sum below is exact


def _steady(seconds=10.0, v=60.0):
    t = np.arange(0, seconds + STEP / 2, STEP)
    return t, v * t, np.full(t.size, v)


def test_motion_figures_of_a_steady_lap_are_exact():
    t, d, v = _steady()
    quality = {}
    assert ld._motion(t, d, v, quality) == []
    assert quality == {"position_jumps": 0, "speed_distance_ratio": 1.0, "max_accel_g": 0.0}
    q = {}
    assert ld._motion(t[:1], d[:1], v[:1], q) == [] and q == {}, "one sample: nothing to measure"


@pytest.mark.parametrize("jump,ratio_ok,expected", [
    (100.0, True, 0),      # advance 100 = exactly 2x the 50 m the speed allows: not "more than twice"
    (100.5, True, 1),
    (99.0, False, 0),      # 49 m beyond the speed: below the 50 m floor
])
def test_a_jump_between_two_distance_updates_exactly(jump, ratio_ok, expected):
    t = np.arange(0, 3 + STEP / 2, STEP)                 # 50 m/s; the distance is held for exactly 1 s
    v = np.full(t.size, 50.0)
    d = 50.0 * t
    held = (t >= 1.0) & (t < 2.0)
    d[held] = 50.0
    d[t >= 2.0] = 50.0 + jump + 50.0 * (t[t >= 2.0] - 2.0)
    quality = {}
    found = [f for f in ld._motion(t, d, v, quality) if f.code == "position_jump"]
    assert quality["position_jumps"] == expected and len(found) == expected
    if expected:
        assert found[0].detail == {"advance_m": jump, "by_speed_m": 50.0} and found[0].at == 50.0


@pytest.mark.parametrize("nan_at,counted", [("start", False), ("end", False), ("after", True), ("before", True)])
def test_a_stretch_with_an_unknown_speed_is_not_judged(nan_at, counted):
    t = np.arange(0, 3 + STEP / 2, STEP)
    v = np.full(t.size, 50.0)
    d = 50.0 * t
    d[(t >= 1.0) & (t < 2.0)] = 50.0
    d[t >= 2.0] = 250.0 + 50.0 * (t[t >= 2.0] - 2.0)
    a = int(np.flatnonzero(t >= 1.0)[0])                   # the last distance update before the hold (50 m)
    b = int(np.flatnonzero(t >= 2.0)[0])                   # ... and the jump: the stretch is rows a..b
    v[{"start": a, "end": b, "after": b + 1, "before": a - 1}[nan_at]] = np.nan
    quality = {}
    ld._motion(t, d, v, quality)
    assert quality["position_jumps"] == (1 if counted else 0)


@pytest.mark.parametrize("finite_speeds,span,measured", [(1, 600.0, False), (2, 600.0, True), (3, 0.0, False), (3, 0.5, True)])
def test_the_lap_ratio_needs_two_speeds_and_some_distance(finite_speeds, span, measured):
    t = np.linspace(0, 10, 11)
    v = np.full(11, np.nan)
    v[:finite_speeds] = 60.0
    d = np.linspace(0, span, 11)
    quality = {}
    ld._motion(t, d, v, quality)
    assert ("speed_distance_ratio" in quality) is measured


def test_the_lap_ratio_is_reported_to_four_decimals():
    t, d, v = _steady()
    quality = {}
    ld._motion(t, d, v * 1.012345678, quality)
    assert quality["speed_distance_ratio"] == 1.0123


def _brute_force_accel(t, v, window):
    """An independent reading of the rule: every new frame against the first frame >= window later."""
    best = 0.0
    for i in range(len(t)):
        for j in range(i + 1, len(t)):
            if t[j] - t[i] >= window:
                best = max(best, abs(v[j] - v[i]) / (t[j] - t[i]))
                break
    return best


@pytest.mark.parametrize("where", [0.5, 3.0, 9.0])
def test_a_single_sample_spike_is_always_seen(where):
    t, d, v = _steady()
    k = int(np.flatnonzero(t >= where)[0])
    v = v.copy()
    v[k] += 30.0                                            # one sample, 30 m/s too high
    quality = {}
    found = [f for f in ld._motion(t, d, v, quality) if f.code == "implausible_acceleration"]
    expected_g = _brute_force_accel(t, v, ld.ACCEL_WINDOW_S) / 9.80665
    assert quality["max_accel_g"] == round(expected_g, 2) and expected_g > 8
    assert len(found) == 1 and found[0].detail == {"g": round(expected_g, 1)}


def test_repeated_polls_do_not_shorten_the_acceleration_window():
    t, d, v = _steady(2.0)
    t2 = np.repeat(t, 2)                                    # every frame polled twice
    v2 = np.repeat(v, 2)
    v2[1::2] += 0.0
    quality = {}
    ld._motion(t2, np.repeat(d, 2), v2, quality)
    assert quality["max_accel_g"] == 0.0


def _frozen_cols(frames, speed=60.0, start=100, n=400):
    t = np.arange(n) * STEP
    cols = {"t": t, "lap_dist": speed * t, "speed": np.full(n, speed) + (np.arange(n) - start) / 1024, "rpm": 7000 + np.arange(n),
            "throttle": np.ones(n), "brake": np.zeros(n), "steering": np.zeros(n)}
    for name in ("lap_dist", "speed", "rpm"):
        cols[name] = np.asarray(cols[name], dtype=float).copy()
        cols[name][start:start + frames + 1] = cols[name][start]
    return cols


@pytest.mark.parametrize("frames,found", [(31, False), (32, True)])
def test_frozen_samples_from_exactly_half_a_second(frames, found):
    quality = {}
    out = ld._frozen(_frozen_cols(frames), 0, quality)
    assert quality["longest_frozen_s"] == round(frames * STEP, 3)
    assert bool(out) is found
    if found:
        assert out[0].detail == {"seconds": 0.5} and out[0].at == 60.0 * 100 * STEP and out[0].t == 100 * STEP


@pytest.mark.parametrize("speed,found", [(5.0, False), (5.0 + 1 / 1024, True)])
def test_frozen_samples_only_at_speed(speed, found):
    cols = _frozen_cols(40, speed=speed)
    assert bool(ld._frozen(cols, 0, {})) is found


def test_frozen_samples_ignore_the_tail_and_missing_columns_and_are_capped():
    cols = _frozen_cols(40, start=350)
    assert ld._frozen(cols, 0, {}) and not ld._frozen(cols, 60, {}), "the tail belongs to the next lap"
    del cols["rpm"]
    assert ld._frozen(cols, 0, {}), "the remaining columns still decide"
    many = _frozen_cols(0, n=2000)
    for k in range(12):
        s0 = 50 + 150 * k
        for name in ("lap_dist", "speed", "rpm"):
            many[name][s0:s0 + 41] = many[name][s0]
    assert len(ld._frozen(many, 0, {})) == ld.MAX_EVENTS
    assert ld._frozen({"t": np.array([0.0])}, 0, {}) == []


def test_the_decision_record_is_complete_and_follows_the_plausibility_check():
    t, d, v = _steady()
    cols = _cols(len(t), t=t, lap_dist=d, speed=v)
    verdict = ld.assess(cols, tl.assess(cols), client_version="0.9.4", plausible=False,
                        plausibility_reasons=[f"r{i}" for i in range(12)])
    dec = verdict.decision
    assert dec["status"] == "invalid" and verdict.status == "valid", "the file failed, the track limits did not"
    assert dec["plausibility"] == {"ok": False, "reasons": [f"r{i}" for i in range(10)]}
    assert dec["client_version"] == "0.9.4" and dec["sources"] == verdict.signals
    assert set(dec) == {"version", "status", "evidence", "quality", "sources", "client_version", "plausibility", "validators"}
    assert ld.assess(cols, tl.assess(cols)).decision["client_version"] is None


@pytest.mark.parametrize("version,found", [(None, False), ("2", False), (1, False), (2, True)])
def test_unsupported_inputs_only_for_a_newer_integer_version(version, found):
    cols = _cols()
    codes = [f.code for f in ld.assess(cols, tl.assess(cols), inputs_version=version).findings]
    assert ("unsupported_inputs" in codes) is found


def test_the_tail_is_not_part_of_the_lap_body():
    t, d, v = _steady()
    t = np.concatenate([t, t[-1] + 2.0 + np.arange(30) * STEP])        # 2 s later: the next lap's first frames
    d = np.concatenate([d, np.arange(30) * 1.0])
    v = np.concatenate([v, np.full(30, 60.0)])
    cols = _cols(len(t), t=t, lap_dist=d, speed=v)
    assert "coverage_gap" in [f.code for f in ld.assess(cols, tl.assess(cols)).findings]
    assert "coverage_gap" not in [f.code for f in ld.assess(cols, tl.assess(cols, tail_samples=30), tail_samples=30).findings]
