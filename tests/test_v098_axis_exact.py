"""V0.9.8 — the distance axis and the point helpers against EXACT expected values (hand-computed),
including every fallback path. scripts/mutation_check.py showed that the reference laps (which test
the result within the recording's resolution) did not notice changes in these paths."""

from __future__ import annotations

import numpy as np
import pytest

from server.distance_axis import EXTRAPOLATE_MAX_S, distance_axis, fill_short_gaps
from server import telemetry_analysis as ta


# ---------------------------------------------------------------- fill_short_gaps

def test_short_gaps_are_bridged_linearly_long_ones_and_the_ends_are_not():
    x = np.array([0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0])
    v = np.array([np.nan, 10.0, np.nan, 30.0, np.nan, np.nan, np.nan, np.nan, 80.0, np.nan])
    out = fill_short_gaps(v, x, max_gap=2.0)
    assert np.isnan(out[0]) and np.isnan(out[9]), "nothing to bridge from at the ends"
    assert out[2] == 20.0, "between 10 (x=1) and 30 (x=3)"
    assert np.isnan(out[4:8]).all(), "x=3..8 is 5 long > 2: stays empty"
    assert np.isnan(v[2]), "the input is not changed"
    exactly = fill_short_gaps(np.array([0.0, np.nan, 4.0]), np.array([0.0, 1.0, 2.0]), max_gap=2.0)
    assert exactly[1] == 2.0, "a gap of exactly max_gap is bridged"
    uneven = fill_short_gaps(np.array([0.0, np.nan, 9.0]), np.array([0.0, 2.0, 3.0]), max_gap=5.0)
    assert uneven[1] == 6.0, "by position, not by index"
    assert np.array_equal(fill_short_gaps(np.array([np.nan, 1.0, np.nan]), np.arange(3.0), 9.0),
                          np.array([np.nan, 1.0, np.nan]), equal_nan=True), "one value: nothing to interpolate"
    clean = np.array([1.0, 2.0])
    assert fill_short_gaps(clean, np.arange(2.0), 1.0) is not None and np.array_equal(fill_short_gaps(clean, np.arange(2.0), 1.0), clean)


# ---------------------------------------------------------------- distance_axis

def _frames(n, dt=0.1):
    return np.arange(n) * dt


def test_between_lap_dist_updates_the_metres_follow_the_speed_exactly():
    t = _frames(11)
    speed = np.full(11, 10.0)                                 # 10 m/s: 1 m per frame
    lap_dist = np.repeat([0.0, 5.0, 10.0], [5, 5, 1])         # updated every 5th frame, held between
    keep, dist, stats = distance_axis(lap_dist, t, speed)
    assert keep.all()
    assert np.allclose(dist, np.arange(11.0)), dist
    assert stats == {"rows": 11, "kept": 11, "anchors": 3, "duplicates": 0, "filled": 8}


def test_the_speed_only_shares_out_the_metres_between_two_updates():
    """Speed 5 then 15 m/s: the first half of the frames covers a quarter of the way."""
    t = _frames(5)
    speed = np.array([5.0, 5.0, 15.0, 15.0, 15.0])
    lap_dist = np.array([0.0, 0.0, 0.0, 0.0, 4.0])            # LMU says 4 m (speed alone would say 4.5)
    _keep, dist, _ = distance_axis(lap_dist, t, speed)
    cum = np.array([0.0, 0.5, 1.5, 3.0, 4.5])                 # trapezoid of the speed
    assert np.allclose(dist, cum / 4.5 * 4.0), "the anchors are met exactly, the speed shapes the inside"


@pytest.mark.parametrize("speed", [np.zeros(6), np.full(6, 100.0)], ids=["speed says nothing", "speed 2.5x too high"])
def test_when_speed_and_lap_dist_disagree_the_metres_are_spread_in_time(speed):
    t = _frames(6)
    lap_dist = np.array([0.0, 0.0, 0.0, 0.0, 0.0, 20.0])      # 20 m in 0.5 s
    _keep, dist, _ = distance_axis(lap_dist, t, speed)
    assert np.allclose(dist, [0.0, 4.0, 8.0, 12.0, 16.0, 20.0])


def test_after_the_last_update_only_half_a_second_is_extrapolated():
    t = _frames(12)                                            # last update at t=0.5, then 0.6 s more
    lap_dist = np.repeat([0.0, 5.0], [5, 7])
    speed = np.full(12, 10.0)
    keep, dist, stats = distance_axis(lap_dist, t, speed)
    assert keep.sum() == 6 + int(round(EXTRAPOLATE_MAX_S / 0.1)), keep
    assert np.allclose(dist[5:], [5.0, 6.0, 7.0, 8.0, 9.0, 10.0]), dist
    assert stats["filled"] == 4 + 5


def test_repeated_polls_are_dropped_and_the_newest_lap_dist_of_a_frame_counts():
    t = np.array([0.0, 0.1, 0.1, 0.2, 0.3, 0.3, 0.4])
    lap_dist = np.array([0.0, 1.0, 1.5, 2.0, 3.0, 3.0, 4.0])
    keep, dist, stats = distance_axis(lap_dist, t, None)
    assert keep.tolist() == [True, True, False, True, True, False, True]
    assert dist.tolist() == [0.0, 1.5, 2.0, 3.0, 4.0]
    assert stats["duplicates"] == 2 and stats["anchors"] == 5 and stats["filled"] == 0


def test_a_standing_car_and_missing_values_drop_rows_and_nothing_usable_is_empty():
    t = _frames(6)
    lap_dist = np.array([0.0, 1.0, 1.0, np.nan, 2.0, 2.0])
    keep, dist, _ = distance_axis(lap_dist, t, None)
    assert keep.tolist() == [True, True, False, False, True, False] and dist.tolist() == [0.0, 1.0, 2.0]
    keep, dist, stats = distance_axis(np.full(3, np.nan), _frames(3), None)
    assert not keep.any() and dist.size == 0
    assert stats == {"rows": 3, "kept": 0, "anchors": 0, "duplicates": 0, "filled": 0}


def test_a_nan_speed_row_between_updates_does_not_bunch_the_metres():
    t = _frames(5)
    lap_dist = np.array([0.0, 0.0, 0.0, 0.0, 4.0])
    speed = np.array([10.0, 10.0, np.nan, 10.0, 10.0])
    _keep, dist, _ = distance_axis(lap_dist, t, speed)
    assert np.allclose(dist, [0.0, 1.0, 2.0, 3.0, 4.0])


# ---------------------------------------------------------------- point helpers

def test_crossing_is_interpolated_between_the_two_samples():
    d = np.array([10.0, 12.0, 14.0])
    assert ta._crossing(np.array([0.0, 0.2, 0.4]), d, 1, 0.1) == 11.0
    assert ta._crossing(np.array([0.0, -0.4, 0.0]), d, 1, 0.2, absolute=True) == 11.0, "steering either way"
    assert ta._crossing(np.array([0.0, 0.05, 0.4]), d, 1, 0.1) == 12.0, "never beyond the sample"
    assert ta._crossing(np.array([0.0, 0.4, 0.4]), d, 0, 0.1) == 10.0
    assert ta._crossing(np.array([0.0, 0.4, 0.4]), d, None, 0.1) == 10.0
    assert ta._crossing(np.array([0.0, 0.4, 0.4]), d, 3, 0.1) == 14.0
    assert ta._crossing(np.array([np.nan, 0.4, 0.4]), d, 1, 0.1) == 12.0, "no value before: the sample itself"
    assert ta._crossing(np.array([0.0, 0.1, 0.4]), d, 1, 0.1) == 12.0, "reaching the threshold exactly at the sample"


def test_spacing_is_the_interval_around_the_point():
    d = np.array([0.0, 1.0, 3.0, 6.0])
    assert ta._spacing(d, 2.0) == 2.0
    assert ta._spacing(d, 1.0) == 2.0, "on an inner sample: the wider neighbour interval"
    assert ta._spacing(d, 3.0) == 3.0
    assert ta._spacing(d, 0.0) == 1.0 and ta._spacing(d, -5.0) == 1.0
    assert ta._spacing(d, 6.0) == 3.0 and ta._spacing(d, 9.0) == 3.0
    assert ta._spacing(np.array([0.0, 1.234, 2.0]), 0.5) == 1.23


def test_a_braking_run_bridges_short_dips_only(monkeypatch):
    monkeypatch.setattr(ta, "BRAKE_RUN_GAP_S", 0.25)
    t = np.arange(10) * 0.125
    on = ta.BRAKE_ON_PCT
    brake = np.array([on, on, 0, on, 0, 0, 0, on, 0, 0], dtype=float)
    assert ta._brake_run(brake, t, 0, 10) == 3, "a dip of 0.125 s is bridged; 0.375 s ends the run"
    exactly = np.array([on, 0, 0, on, 0, 0, 0, 0, 0, 0], dtype=float)
    assert ta._brake_run(exactly, t, 0, 10) == 3, "a dip reaching exactly the limit (0.25 s) is still bridged"
    assert ta._brake_run(np.full(10, on), t, 2, 6) == 5, "stops at `stop`"
    assert ta._first(np.array([False, False, True, True])) == 2 and ta._first(np.zeros(3, bool)) is None


def test_a_stretch_with_a_long_speed_gap_is_shared_out_in_time():
    """More than GAP_FILL_S without a speed between two updates: the speed cannot shape that
    stretch — the metres are spread in time (before V0.9.8 the gap counted as 0 m/s)."""
    t = _frames(11)
    lap_dist = np.array([0.0] * 10 + [10.0])
    speed = np.array([30.0] + [np.nan] * 7 + [30.0, 30.0, 30.0])   # 0.7 s without a speed
    _keep, dist, _ = distance_axis(lap_dist, t, speed)
    assert np.allclose(dist, np.arange(11.0)), dist
    inf = np.array([10.0, np.inf, 10.0, 10.0, 10.0])
    _keep, dist, _ = distance_axis(np.array([0.0, 0.0, 0.0, 0.0, 4.0]), _frames(5), inf)
    assert np.allclose(dist, [0.0, 1.0, 2.0, 3.0, 4.0]), "an infinite speed is bridged like a missing one"


def test_after_the_last_update_nothing_is_extrapolated_without_a_speed():
    t = _frames(8)
    lap_dist = np.repeat([0.0, 5.0], [5, 3])
    speed = np.array([10.0] * 6 + [np.nan, np.nan])
    keep, dist, _ = distance_axis(lap_dist, t, speed)
    assert keep.tolist() == [True] * 6 + [False, False]


def test_time_sharing_is_relative_to_the_stretch_start():
    """The disagreeing stretch starts after the first update (t=0.3), not at 0."""
    t = _frames(7)
    lap_dist = np.array([0.0, 0.0, 0.0, 3.0, 3.0, 3.0, 9.0])
    speed = np.array([10.0, 10.0, 10.0, 10.0, 0.0, 0.0, 0.0])     # second stretch: speed says 0.5 m, LMU 6 m
    _keep, dist, _ = distance_axis(lap_dist, t, speed)
    assert np.allclose(dist, [0.0, 1.0, 2.0, 3.0, 5.0, 7.0, 9.0]), dist


@pytest.mark.parametrize("gap,uses_speed", [(2.25, True), (9.0, True), (2.2, False), (9.1, False)])
def test_the_speed_shapes_a_stretch_exactly_within_the_ratio_limits(gap, uses_speed):
    """ratio = metres by speed / metres by lap_dist; inside [0.5, 2.0] (limits included) the speed
    shapes the stretch, outside it is spread in time. Dyadic numbers: every value is exact."""
    t = np.arange(5) * 0.125
    speed = np.array([4.0, 4.0, 12.0, 12.0, 12.0])               # by speed: 0, .5, 1.5, 3, 4.5 m
    lap_dist = np.array([0.0, 0.0, 0.0, 0.0, gap])
    _keep, dist, _ = distance_axis(lap_dist, t, speed)
    expected = np.array([0.0, 0.5, 1.5, 3.0, 4.5]) / 4.5 * gap if uses_speed else np.arange(5) / 4 * gap
    assert np.allclose(dist, expected), (gap, dist)


def test_crossing_of_a_signed_channel_and_index_zero():
    d = np.array([10.0, 12.0, 14.0])
    assert ta._crossing(np.array([-0.3, 0.2, 0.4]), d, 1, 0.1) == pytest.approx(11.6), "signed: from -0.3 up through 0.1"
    assert ta._crossing(np.array([-0.3, 0.2, 0.4]), d, 1, 0.1, absolute=True) == 12.0
    assert ta._crossing(np.array([0.3, 0.0, 0.0]), d, 0, 0.1) == 10.0, "index 0: no sample before, never wraps around"


def test_spacing_on_a_sample_takes_the_wider_side_and_rounds_to_centimetres():
    d = np.array([1.0, 5.0, 6.0, 7.0])
    assert ta._spacing(d, 5.0) == 4.0
    assert ta._spacing(np.array([0.0, 1.2345, 2.5]), 1.2345) == 1.27


def test_the_sample_right_after_the_start_belongs_to_the_run(monkeypatch):
    monkeypatch.setattr(ta, "BRAKE_RUN_GAP_S", 0.25)
    t = np.arange(10) * 0.125
    on = ta.BRAKE_ON_PCT
    assert ta._brake_run(np.array([on, on, 0, 0, 0, 0, 0, 0, 0, 0], dtype=float), t, 0, 10) == 1


def test_only_the_stretches_that_lack_a_speed_are_spread_in_time():
    """Speed missing from the second update (t=0.5) for 0.8 s: the stretch ENDING there and the one
    after are spread in time; the stretch after that, with a varying speed, is shaped by it again."""
    t = _frames(21)
    lap_dist = np.array([0.0] * 5 + [5.0] * 5 + [10.0] * 5 + [15.0] * 5 + [20.0])
    speed = np.array([10.0] * 5 + [np.nan] * 8 + [10.0] * 2 + [5.0, 5.0, 15.0, 15.0, 15.0, 15.0])
    _keep, dist, _ = distance_axis(lap_dist, t, speed)
    assert np.allclose(dist[:6], [0.0, 1.0, 2.0, 3.0, 4.0, 5.0]), "missing at the END of the stretch: in time"
    assert np.allclose(dist[5:11], [5.0, 6.0, 7.0, 8.0, 9.0, 10.0])
    cum = np.array([0.0, 0.5, 1.5, 3.0, 4.5, 6.0])                 # 5, 5, 15, 15, 15, 15 m/s, 0.1 s apart
    assert np.allclose(dist[15:21], 15.0 + cum / 6.0 * 5.0), "a complete stretch later on: by speed"
