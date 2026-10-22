"""V0.9.9 — the decision layer's measurements at exact values and at their boundaries
(server/lap_decision.py). Every number below is computed by hand from the input; these tests pin
the arithmetic so that a changed comparison, offset or rounding is caught (scripts/mutation_check.py)."""
from __future__ import annotations

import numpy as np
import pytest

from server import lap_decision as ld
from server import track_limits as tl


def _codes(findings):
    return [f.code for f in findings]


# ----------------------------------------------------------------- frame rate and gaps

def test_frame_rate_and_longest_gap_are_measured_from_the_first_frame_not_from_zero():
    # 51 frames from t = 100 s to t = 107 s (a lap that does not start at t = 0)
    t = np.linspace(100.0, 107.0, 51)
    q = {}
    out = ld._quality(t, np.linspace(0, 700, 51), None, q)
    assert q["frames"] == 51 and q["frame_rate_hz"] == 7.29          # 51 / 7 = 7.2857 -> 2 decimals
    assert q["longest_gap_s"] == 0.14
    low = next(f for f in out if f.code == "low_frame_rate")
    assert low.detail == {"frame_rate_hz": 7.29}


def test_a_gap_is_reported_with_its_place_and_two_decimals():
    t = np.concatenate((np.arange(0.0, 10.0, 0.05), 9.95 + 1.2468 + np.arange(0, 200) * 0.05))
    d = t * 50.0
    q = {}
    out = ld._quality(t, d, None, q)
    gap = next(f for f in out if f.code == "coverage_gap")
    k = int(np.argmax(np.diff(t)))
    assert gap.detail == {"seconds": 1.25} and gap.t == pytest.approx(t[k]) and gap.at == pytest.approx(d[k])
    assert q["longest_gap_s"] == 1.247


def test_a_gap_at_a_place_without_distance_has_no_place():
    t = np.array([0.0, 0.05, 2.0, 2.05])
    d = np.array([0.0, np.nan, 100.0, 102.5])
    gap = next(f for f in ld._quality(t, d, None, {}) if f.code == "coverage_gap")
    assert gap.at is None


# ----------------------------------------------------------------- position jumps

def _still(n=41, dt=0.05):
    t = np.arange(n) * dt
    return t, np.zeros(n), np.zeros(n)


def test_two_frames_are_enough_to_measure_motion():
    q = {}
    ld._motion(np.array([0.0, 0.1]), np.array([0.0, 5.0]), np.array([50.0, 50.0]), q)
    assert q["position_jumps"] == 0 and q["speed_distance_ratio"] == 1.0


@pytest.mark.parametrize("jump, flagged", [(50.0, True), (49.99, False)])
def test_a_jump_of_exactly_the_minimum_counts(jump, flagged):
    t, d, v = _still()
    d[20:] = jump
    q = {}
    out = ld._motion(t, d, v, q)
    assert ("position_jump" in _codes(out)) is flagged and q["position_jumps"] == int(flagged)


def test_a_jump_in_the_very_first_distance_update_is_seen():
    t, d, v = _still()
    d[1:] = 120.0
    out = [f for f in ld._motion(t, d, v, {}) if f.code == "position_jump"]
    assert len(out) == 1 and out[0].at == 0.0 and out[0].until == 120.0 and out[0].t == 0.0


def test_a_jump_is_judged_against_the_speed_actually_driven_even_below_1_ms():
    # 0.5 m/s for 100 s = 50 m driven; the distance claims 120 m: 70 m more and > 2x -> a jump
    t = np.arange(0.0, 100.01, 0.5)
    v = np.full(t.size, 0.5)
    d = np.zeros(t.size)
    d[-1] = 120.0
    out = [f for f in ld._motion(t, d, v, {}) if f.code == "position_jump"]
    assert len(out) == 1
    assert out[0].detail == {"advance_m": 120.0, "by_speed_m": 50.0}


def test_jump_details_are_rounded_to_one_decimal():
    t, d, v = _still()
    v[:] = 0.123
    d[20:] = 87.654
    jump = next(f for f in ld._motion(t, d, v, {}) if f.code == "position_jump")
    assert jump.detail == {"advance_m": 87.7, "by_speed_m": 0.1}


# ----------------------------------------------------------------- speed against distance

def test_the_speed_distance_ratio_uses_the_lap_distance_span_and_never_negative_speeds():
    t = np.linspace(0.0, 10.0, 201)
    v = np.full(201, 10.0)
    v[:20] = -3.0                     # reversing in the pit box counts as standing still
    d = np.linspace(1000.0, 1100.0, 201)
    q = {}
    out = ld._motion(t, d, v, q)
    driven = float(np.trapezoid(np.clip(v, 0, None), t)) if hasattr(np, "trapezoid") else float(np.trapz(np.clip(v, 0, None), t))
    assert q["speed_distance_ratio"] == round(driven / 100.0, 4)
    mismatch = next(f for f in out if f.code == "speed_distance_mismatch")
    assert mismatch.detail == {"ratio": round(driven / 100.0, 4), "by_speed_m": round(driven, 0), "by_lap_dist_m": 100.0}


def test_mismatch_details_keep_four_decimals():
    t = np.linspace(0.0, 10.0, 201)
    v = np.full(201, 10.98765)
    d = np.linspace(0.0, 100.37, 201)
    mismatch = next(f for f in ld._motion(t, d, v, {}) if f.code == "speed_distance_mismatch")
    assert mismatch.detail == {"ratio": 1.0947, "by_speed_m": 110.0, "by_lap_dist_m": 100.0}


# ----------------------------------------------------------------- acceleration

@pytest.mark.parametrize("factor, flagged", [(1.0, False), (1.0000001, True)])
def test_acceleration_of_exactly_the_limit_is_not_flagged(factor, flagged):
    # 0.125 s steps are exact in binary, so (v1 - v0) / 0.125 is exactly the limit
    t = np.arange(0.0, 2.0, 0.125)
    v = np.zeros(t.size)
    v[8:] = ld.ACCEL_REVIEW * 0.125 * factor          # (v - 0) / 0.125 has no rounding error
    d = np.cumsum(v) * 0.125
    out = ld._motion(t, d, v, {})
    assert ("implausible_acceleration" in _codes(out)) is flagged


# ----------------------------------------------------------------- frozen frames

def _frozen_columns(n=200, dt=0.05, start=60, stop=80):
    t = np.arange(n) * dt
    cols = {"t": t, "speed": 40.0 + t, "rpm": 6000 + t, "throttle": 0.5 + t / 100, "brake": np.zeros(n),
            "steering": np.sin(t), "lap_dist": t * 40.0}
    for name in ld.FROZEN_COLUMNS:
        values = np.array(cols[name], dtype=float)
        values[start:stop] = values[start]
        cols[name] = values
    return cols


def test_a_frozen_stretch_is_found_with_its_start_and_length():
    q = {}
    out = ld._frozen(_frozen_columns(), 0, q)
    assert len(out) == 1
    assert out[0].t == pytest.approx(3.0) and out[0].at == pytest.approx(120.0)
    assert out[0].detail == {"seconds": 0.95} and q["longest_frozen_s"] == 0.95


def test_a_frozen_stretch_at_the_very_end_counts_when_no_tail_is_cut():
    q = {}
    out = ld._frozen(_frozen_columns(start=180, stop=200), 0, q)
    assert out and out[0].detail == {"seconds": 0.95}
    assert ld._frozen(_frozen_columns(start=180, stop=200), -3, {})[0].detail == {"seconds": 0.95}
    assert ld._frozen(_frozen_columns(start=180, stop=200), 15, {}) == []


def test_frozen_seconds_keep_two_decimals():
    cols = _frozen_columns(dt=0.0123, start=10, stop=90)
    out = ld._frozen(cols, 0, {})
    assert out[0].detail == {"seconds": round(79 * 0.0123, 2)}


def test_a_frozen_stretch_where_the_distance_is_unknown_has_no_place():
    cols = _frozen_columns()
    cols["lap_dist"][60:80] = np.inf          # equal to itself (so still frozen), but not a place
    out = ld._frozen(cols, 0, {})
    assert out and out[0].at is None
    del cols["lap_dist"]
    assert ld._frozen(cols, 0, {})[0].at is None


def test_frozen_needs_time_and_speed_and_two_frames():
    assert ld._frozen({"t": np.arange(10.0)}, 0, {}) == []
    assert ld._frozen({"speed": np.arange(10.0)}, 0, {}) == []
    q = {}
    assert ld._frozen({"t": np.array([0.0, 0.1]), "speed": np.array([30.0, 30.0])}, 0, q) == []
    assert q["longest_frozen_s"] == 0.1


# ----------------------------------------------------------------- the decision record

def _verdict():
    return tl._verdict([], ["client"])


def test_assess_defaults_use_the_whole_file_and_treat_the_lap_as_plausible():
    cols = _frozen_columns(start=180, stop=200)
    verdict = ld.assess(cols, _verdict(), client_version="0.9.4-" + "x" * 40)
    assert verdict.decision["status"] != ld.INVALID
    frozen = next(f for f in verdict.findings if f.code == "frozen_samples")
    assert frozen.detail == {"seconds": 0.95}, "no row is cut off as tail unless asked"
    assert len(verdict.decision["client_version"]) == 32
    assert verdict.decision["plausibility"] == {"ok": True, "reasons": []}
