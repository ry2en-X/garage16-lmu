"""V0.9.7 — the analysis measured against laps whose truth is known (tests/helpers/reference_lap.py).

Every metric the LapTime Analyzer shows for a corner is compared with the value read from a 2 kHz
simulation of the lap, after the lap went through the same path a real one takes: recorded the
way LMU and the client record (50 Hz frames, 60 Hz polls, lap_dist refreshed at 5 Hz), written
as a parquet file, read by load_lap, measured by corner_analysis / lap_insights.

The tolerances are DERIVED (see the helper's docstring), not tuned:
  pedal points, apex   <= the distance between two recorded frames there (+0.05 m rounding)
  minimum speed        <= deceleration x frame time (+0.05 km/h rounding)
  exit speed           <= acceleration x frame time (+0.05 km/h rounding)
  times, deltas        <= 2 ms

Then the faults real recordings have, each with what must happen: duplicate polls and timestamp
jitter change nothing beyond the tolerances; dropped samples away from a measurement change
nothing, inside one they widen the error by at most the gap (and the reported resolution says
so); NaN values and a single nonsense sample do not crash and do not move the corner; a session
restart inside the file is refused, never analysed as a lap; a truncated file only yields corners
on the stretch both laps cover.

And a guard against silent algorithm changes: the results on the reference laps are kept in
tests/reference/v097_golden.json together with ANALYSIS_CACHE_VERSION and INSIGHTS_VERSION. If
the numbers change while those versions stay the same, the test fails — cached and stored results
would then disagree with fresh ones without anybody noticing. Regenerate with
GARAGE16_UPDATE_GOLDEN=1 after bumping the version(s).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pytest

from server import telemetry_analysis as ta
from server.analysis_cache import ANALYSIS_CACHE_VERSION
from server.lap_insights import INSIGHTS_VERSION
from server import lap_insights
from tests.helpers import reference_lap as R

KEYS = ["speed", "brake", "throttle", "long_g"]
GOLDEN = Path(__file__).parent / "reference" / "v097_golden.json"
TIME_TOL = 0.002


@pytest.fixture(scope="module")
def laps():
    return R.simulate(R.TRACK_M, R.LAP_A), R.simulate(R.TRACK_M, R.LAP_B)


def _load(tmp_path, name, df):
    path = tmp_path / f"{name}.parquet"
    df.to_parquet(path, index=False)
    ta.clear_lap_cache()
    return ta.load_lap(path, KEYS)


def _measure(tmp_path, a_df, b_df):
    sa, sb = _load(tmp_path, "a", a_df), _load(tmp_path, "b", b_df)
    return sa, sb, ta.corner_analysis(sa, sb)


POINTS = ("brake", "throttle_on", "throttle_50", "throttle")


def _check_corner(lap: R.ReferenceLap, corner: dict, measured: dict, extra_m: float = 0.0, label: str = ""):
    truth = lap.truth_corner(corner["start"], corner["end"])
    for key in POINTS:
        if truth[key] is None:
            assert measured[key] is None, (label, key)
            continue
        tol = lap.resolution_m(truth[key]) + 0.05 + extra_m
        assert measured[key] is not None, (label, key)
        assert abs(measured[key] - truth[key]) <= tol, (label, key, measured[key], truth[key], tol)
        # the resolution the analysis REPORTS for the point covers the true error (V0.9.7)
        assert abs(measured[key] - truth[key]) <= measured["res"][key] + 0.05 + extra_m, (label, key, measured["res"])
    assert abs(measured["apex"] - truth["apex"]) <= lap.resolution_m(truth["apex"]) + 0.05 + extra_m, (label, "apex")
    assert 0 <= measured["min"] - truth["min"] + 0.05 <= R.BRAKE_DECEL / R.FRAME_HZ * 3.6 + 0.1, (label, "min", measured["min"], truth["min"])
    if truth["exit"] is not None:
        assert abs(measured["exit"] - truth["exit"]) <= R.ACCEL / R.FRAME_HZ * 3.6 + 0.05, (label, "exit")
    assert abs(measured["time"] - truth["time"]) <= TIME_TOL, (label, "time", measured["time"], truth["time"])


def _check_all(laps, corners, extra_m=0.0, label=""):
    A, B = laps
    assert len(corners) == 3, (label, [c["apex"] for c in corners])
    for c in corners:
        _check_corner(A, c, c["a"], extra_m, f"{label} A{c['n']}")
        _check_corner(B, c, c["b"], extra_m, f"{label} B{c['n']}")
        true_delta = A.truth_corner(c["start"], c["end"])["time"] - B.truth_corner(c["start"], c["end"])["time"]
        assert abs(c["delta"] - true_delta) <= TIME_TOL, (label, c["n"], c["delta"], true_delta)


# ---------------------------------------------------------------- the clean recording

@pytest.mark.parametrize("variant", [
    dict(),                                   # 60 Hz polls of 50 Hz frames, lap_dist at 5 Hz
    dict(scoring_every=1),                    # lap_dist every frame
    dict(poll_hz=None),                       # one row per frame
    dict(phase=0.0), dict(phase=0.93),        # frames at other moments
])
def test_every_corner_metric_matches_the_truth_within_the_recording_resolution(tmp_path, laps, variant):
    A, B = laps
    _, _, corners = _measure(tmp_path, R.record(A, **variant), R.record(B, **variant))
    _check_all(laps, corners, label=str(variant))


def test_the_delta_curve_and_the_lap_time_difference_are_the_true_ones(tmp_path, laps):
    A, B = laps
    sa, sb, corners = _measure(tmp_path, R.record(A), R.record(B))
    grid = np.arange(5.0, R.TRACK_M - 5.0, 1.0)
    measured = ta.time_at(sa, grid) - ta.time_at(sb, grid)
    truth = A.time_at(grid) - B.time_at(grid)
    assert np.max(np.abs(measured - truth)) <= TIME_TOL
    assert abs(sum(c["delta"] for c in corners) - (A.lap_time - B.lap_time)) <= TIME_TOL
    # the expected story: lap B is faster in the second corner only, by its higher minimum speed
    assert abs(corners[0]["delta"]) <= TIME_TOL and abs(corners[2]["delta"]) <= TIME_TOL
    assert abs(corners[1]["delta"] - (A.lap_time - B.lap_time)) <= TIME_TOL


def test_the_stored_per_lap_corners_match_the_truth(tmp_path, laps):
    """lap_insights (computed once at upload, used for the corner history) measures the same."""
    A, _ = laps
    df = R.record(A)
    stats = lap_insights.compute({k: df[k].to_numpy() for k in ("t", "lap_dist", "speed", "brake", "throttle")},
                                 lap_time=A.lap_time)
    stored = stats.get("corners")
    assert stored and len(stored) == 3
    for apex, brake, vmin, full, exit_kmh in stored:
        truth = A.truth_corner(apex - 400, apex + 400)
        assert abs(apex - truth["apex"]) <= A.resolution_m(truth["apex"]) + 0.05
        assert abs(brake - truth["brake"]) <= A.resolution_m(truth["brake"]) + 0.05
        assert abs(full - truth["throttle"]) <= A.resolution_m(truth["throttle"]) + 0.05
        assert 0 <= vmin - truth["min"] + 0.05 <= R.BRAKE_DECEL / R.FRAME_HZ * 3.6 + 0.1
    profile = stats.get("profile") or stats.get("time_profile")
    if profile:
        marks = np.arange(1, len(profile["cum"]) + 1) * profile["step"]
        assert np.max(np.abs(np.array(profile["cum"]) - A.time_at(marks))) <= TIME_TOL


# ---------------------------------------------------------------- faults

def test_duplicate_polls_change_nothing(tmp_path, laps):
    A, B = laps
    _, _, clean = _measure(tmp_path, R.record(A), R.record(B))
    _, _, dup = _measure(tmp_path, R.inject(R.record(A), "duplicates"), R.inject(R.record(B), "duplicates"))
    assert json.dumps(clean, sort_keys=True) == json.dumps(dup, sort_keys=True)


def test_timestamp_jitter_stays_within_the_tolerances(tmp_path, laps):
    A, B = laps
    a = R.inject(R.record(A), "jitter", ms=1.5)
    b = R.inject(R.record(B), "jitter", ms=1.5, rng_seed=2)
    _, _, corners = _measure(tmp_path, a, b)
    # 1.5 ms of clock noise moves a point by up to 1.5 ms x speed
    _check_all(laps, corners, extra_m=0.0015 * R.VMAX, label="jitter")


def test_samples_missing_on_a_straight_change_no_corner(tmp_path, laps):
    A, B = laps
    a = R.inject(R.record(A), "drop", at=1500.0, seconds=0.5)
    _, _, corners = _measure(tmp_path, a, R.record(B))
    _check_all(laps, corners, label="drop on straight")


def test_samples_missing_in_a_braking_zone_widen_the_error_by_at_most_the_gap(tmp_path, laps):
    A, B = laps
    truth = A.truth_corner(1900.0, 2600.0)["brake"]
    gap_s = 0.2
    a = R.inject(R.record(A), "drop", at=truth - 6.0, seconds=gap_s)
    _, _, corners = _measure(tmp_path, a, R.record(B))
    t2 = corners[1]
    gap_m = gap_s * float(A.speed_at(truth))
    assert abs(t2["a"]["brake"] - truth) <= gap_m + A.resolution_m(truth) + 0.05
    # ... and the analysis SAYS the point is less certain there: the reported resolution spans the gap
    assert t2["a"]["res"]["brake"] >= 0.8 * gap_m   # (the car is already slowing inside the gap)
    assert abs(t2["a"]["brake"] - truth) <= t2["a"]["res"]["brake"] + 0.05
    assert t2["b"]["res"]["brake"] < 2.0, "the other lap is unaffected"


def test_nan_values_do_not_crash_or_move_a_corner(tmp_path, laps):
    A, B = laps
    a = R.inject(R.record(A), "nan", at=2100.0, rows=3, columns=["speed", "brake"])
    _, _, corners = _measure(tmp_path, a, R.record(B))
    assert len(corners) == 3
    _check_corner(A, corners[0], corners[0]["a"], label="nan T1")
    _check_corner(A, corners[2], corners[2]["a"], label="nan T3")
    assert abs(corners[1]["a"]["apex"] - A.truth_corner(corners[1]["start"], corners[1]["end"])["apex"]) <= 3.0


def test_a_single_nonsense_speed_sample_moves_nothing(tmp_path, laps):
    A, B = laps
    a = R.inject(R.record(A), "spike", at=1500.0, value=300.0)
    _, _, corners = _measure(tmp_path, a, R.record(B))
    _check_all(laps, corners, label="spike")


def test_a_session_restart_inside_the_file_is_refused(tmp_path, laps):
    A, _ = laps
    df = R.inject(R.record(A), "restart", at=2500.0)
    path = tmp_path / "restart.parquet"
    df.to_parquet(path, index=False)
    with pytest.raises(ta.AnalysisError):
        ta.load_lap(path, KEYS)


def test_a_truncated_lap_only_yields_corners_both_laps_cover(tmp_path, laps):
    A, B = laps
    _, _, corners = _measure(tmp_path, R.inject(R.record(A), "truncate", at=2600.0), R.record(B))
    assert corners and all(c["end"] <= 2600.0 for c in corners)
    _check_corner(A, corners[0], corners[0]["a"], label="truncated T1")


# ---------------------------------------------------------------- guard against silent changes

def _fingerprint(corners, sa):
    keep = ("start", "end", "apex", "delta")
    lap_keys = ("res", "brake", "apex", "min", "throttle_on", "throttle_50", "throttle", "exit", "time",
                "brake_len", "decel_peak", "decel_mean", "coast_m", "accel_exit")
    return {
        "axis": sa.axis,
        "corners": [{**{k: c[k] for k in keep}, "a": {k: c["a"].get(k) for k in lap_keys},
                     "b": {k: c["b"].get(k) for k in lap_keys},
                     "phases": [(p["phase"], p["delta"]) for p in c.get("phases", [])]} for c in corners],
    }


def test_results_only_change_together_with_the_cache_and_insights_versions(tmp_path, laps):
    A, B = laps
    sa, _, corners = _measure(tmp_path, R.record(A), R.record(B))
    current = {"analysis_cache_version": ANALYSIS_CACHE_VERSION, "insights_version": INSIGHTS_VERSION,
               "result": json.loads(json.dumps(_fingerprint(corners, sa)))}
    if os.environ.get("GARAGE16_UPDATE_GOLDEN") == "1" or not GOLDEN.exists():
        GOLDEN.parent.mkdir(exist_ok=True)
        GOLDEN.write_text(json.dumps(current, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        if os.environ.get("GARAGE16_UPDATE_GOLDEN") != "1":
            pytest.fail(f"{GOLDEN} did not exist — written now; commit it")
        return
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    if current["result"] == golden["result"]:
        return
    same_versions = (golden["analysis_cache_version"] == ANALYSIS_CACHE_VERSION
                     and golden["insights_version"] == INSIGHTS_VERSION)
    assert not same_versions, (
        "The analysis results on the reference laps changed, but ANALYSIS_CACHE_VERSION "
        "(server/analysis_cache.py) and INSIGHTS_VERSION (server/lap_insights.py) did not: cached and "
        "stored results would silently disagree with fresh ones. Bump the version(s), then regenerate "
        "with GARAGE16_UPDATE_GOLDEN=1.")
    pytest.fail("Versions were bumped — regenerate tests/reference/v097_golden.json with GARAGE16_UPDATE_GOLDEN=1 "
                "(and check scripts/consistency_check.py lists the stored laps to recompute).")
