"""V0.9.1 (accuracy review) — the numbers the analysis shows, checked against laps whose truth is
known exactly: built from a speed profile, so every braking point, apex speed and time is known.

  * spikes: a brake tap BETWEEN two overview grid points is reported, at the right place and height;
    nothing is reported when the grid is as fine as the samples or the deviation is invisible
  * corner analysis: corners found where they are, braking / throttle points and minimum speeds
    to the sample, corner time differences equal to the true ones, corners adding up to the delta
  * transport precision: never below what the browser displays; whole numbers where whole
  * the prepared-lap cache: bounded, keyed by file identity, never serves a changed file
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from server import telemetry_analysis as ta
from server.analysis_cache import ANALYSIS_CACHE_VERSION
from server.telemetry_analysis import (
    LapSeries,
    channel_decimals,
    CORNER_SMOOTH_M,
    corner_analysis,
    delta_facts,
    delta_series,
    json_series,
    make_grid,
    resample,
    spikes,
)
from server.telemetry_channels import CATALOG, calibrate_orientation

KMH = 1 / 3.6
STEP_M = 0.5  # sample spacing of the synthetic laps (≈60 Hz at 100 km/h)


def _profile(length, corners):
    """Speed (km/h), brake (0..1), throttle (0..1) along the lap for corners given as
    dicts(brake_at, apex_at, apex_kmh, full_throttle_at); 260 km/h on the straights, a linear
    braking ramp from brake_at to the apex, a linear acceleration from the apex back to 260."""
    d = np.arange(0.0, length + STEP_M / 2, STEP_M)
    v = np.full_like(d, 260.0)
    brake = np.zeros_like(d)
    throttle = np.ones_like(d)
    for c in corners:
        b, a, vmin, f = c["brake_at"], c["apex_at"], c["apex_kmh"], c["full_throttle_at"]
        ramp_in = (d >= b) & (d <= a)
        v[ramp_in] = 260.0 - (260.0 - vmin) * (d[ramp_in] - b) / (a - b)
        out_end = a + (260.0 - vmin) * 3.0          # 3 m per km/h back to full speed
        ramp_out = (d > a) & (d <= out_end)
        v[ramp_out] = vmin + (260.0 - vmin) * (d[ramp_out] - a) / (out_end - a)
        brake[ramp_in] = 0.8
        throttle[ramp_in] = 0.0
        part = (d > a) & (d < f)
        throttle[part] = 0.6
    return d, v, brake, throttle


def _lap(length, corners):
    d, v, brake, throttle = _profile(length, corners)
    ms = v * KMH
    t = np.concatenate(([0.0], np.cumsum(np.diff(d) / ((ms[1:] + ms[:-1]) / 2))))
    raw = {"lap_dist": d, "t": t, "speed": ms, "brake": brake, "throttle": throttle}
    return LapSeries(dist=d.copy(), raw=raw, orientation=calibrate_orientation(raw), available=set(raw)), t


C1 = dict(brake_at=800.0, apex_at=960.0, apex_kmh=100.0, full_throttle_at=1100.0)
C2 = dict(brake_at=2200.0, apex_at=2320.0, apex_kmh=150.0, full_throttle_at=2400.0)


# ---------------------------------------------------------------- spikes

def _flat_lap_with_tap(tap_at=503.0, tap_len=3.0, height=0.6):
    d = np.arange(0.0, 1000.0 + 0.5, 1.0)
    brake = np.zeros_like(d)
    brake[(d >= tap_at) & (d < tap_at + tap_len)] = height
    raw = {"lap_dist": d, "t": d / 50.0, "speed": np.full_like(d, 50.0), "brake": brake, "throttle": np.zeros_like(d)}
    return LapSeries(dist=d.copy(), raw=raw, orientation=calibrate_orientation(raw), available=set(raw))


def test_a_brake_tap_between_two_grid_points_is_reported_where_and_how_high_it_was():
    lap = _flat_lap_with_tap()
    values = ta.channel_values(lap, "brake")                     # 0..100 %
    grid, step = make_grid(0.0, 1000.0, 10.0)
    line = resample(lap, values, grid, False)
    assert line.max() == 0.0, "the drawn overview line misses the tap completely — this is the problem"
    found = spikes(lap, values, grid, step, line, span=100.0, decimals=2)
    assert found is not None
    # the tap's samples at 503, 504 and 505 m fall into the cells [495, 505) and [505, 515)
    assert found["i"] == [50, 51]
    assert found["hi"] == [60.0, 60.0] and found["lo"] == [0.0, 0.0]


def test_no_spikes_when_the_grid_is_as_fine_as_the_samples_or_the_deviation_is_invisible():
    lap = _flat_lap_with_tap()
    values = ta.channel_values(lap, "brake")
    fine, fine_step = make_grid(0.0, 1000.0, 1.0)
    assert spikes(lap, values, fine, fine_step, resample(lap, values, fine, False), 100.0, 2) is None, "nothing can hide on a 1 m grid"
    tiny = _flat_lap_with_tap(height=0.01)                        # 1 % of the span: invisible in a chart
    tv = ta.channel_values(tiny, "brake")
    grid, step = make_grid(0.0, 1000.0, 10.0)
    assert spikes(tiny, tv, grid, step, resample(tiny, tv, grid, False), 100.0, 2) is None


def test_spikes_ignore_samples_outside_the_grid_and_gaps_in_the_data():
    lap = _flat_lap_with_tap(tap_at=995.0, tap_len=5.0)           # partly beyond the grid's end
    values = ta.channel_values(lap, "brake").copy()
    values[100:110] = np.nan
    grid, step = make_grid(0.0, 980.0, 10.0)                       # ends at 980 m
    found = spikes(lap, values, grid, step, resample(lap, values, grid, False), 100.0, 2)
    assert found is None, "a tap at 995–1000 m is not inside a grid that ends at 980 m"


# ---------------------------------------------------------------- corners

def test_corners_are_found_where_they_are_and_measured_from_the_raw_samples():
    a, _ = _lap(3200.0, [C1, C2])
    b, _ = _lap(3200.0, [dict(C1, brake_at=820.0, apex_kmh=104.0), C2])   # Lap 2: brakes 20 m later, 4 km/h faster
    corners = corner_analysis(a, b)
    assert [c["n"] for c in corners] == [1, 2]
    c1, c2 = corners
    assert abs(c1["apex"] - 960.0) <= 4 and abs(c2["apex"] - 2320.0) <= 4, (c1["apex"], c2["apex"])
    # braking points: the first sample with ≥10 % brake, exact to the sample spacing
    assert c1["a"]["brake"] == 800.0 and c1["b"]["brake"] == 820.0
    assert c1["a"]["min"] == pytest.approx(100.0, abs=0.1) and c1["b"]["min"] == pytest.approx(104.0, abs=0.1)
    assert c1["a"]["throttle"] == pytest.approx(1100.0, abs=STEP_M) and c2["a"]["throttle"] == pytest.approx(2400.0, abs=STEP_M)
    assert c1["a"]["entry"] == pytest.approx(260.0, abs=0.5)
    # corner 2 is identical in both laps: no time difference, same numbers
    assert c2["delta"] == pytest.approx(0.0, abs=0.001)
    assert c2["a"]["brake"] == c2["b"]["brake"] == 2200.0


def test_corner_time_differences_are_the_true_ones_and_add_up_to_the_delta():
    a, ta_ = _lap(3200.0, [C1, C2])
    b, tb_ = _lap(3200.0, [dict(C1, brake_at=820.0, apex_kmh=104.0), C2])
    corners = corner_analysis(a, b)
    c1 = corners[0]
    # the truth, straight from the integrated profiles: time from corner start to corner end
    truth = (np.interp(c1["end"], a.dist, ta_) - np.interp(c1["start"], a.dist, ta_)) - (
        np.interp(c1["end"], b.dist, tb_) - np.interp(c1["start"], b.dist, tb_))
    assert c1["delta"] == pytest.approx(truth, abs=0.001)
    assert c1["delta"] > 0, "braking 20 m earlier and 4 km/h slower at the apex loses time"
    grid, _ = make_grid(0.0, 3200.0, 1.0)
    delta = delta_series(a, b, grid)
    over_corners = np.interp(corners[-1]["end"], grid, delta) - np.interp(corners[0]["start"], grid, delta)
    assert sum(c["delta"] for c in corners) == pytest.approx(over_corners, abs=0.002), "the corners cover the lap without gaps"


def test_the_corners_add_up_to_the_lap_time_difference_including_the_opening_straight():
    """The straight from the line to the first braking zone belongs to corner 1 and the run to
    the line to the last corner, so the table's total is the lap-time difference, to the ms."""
    a, ta_ = _lap(3200.0, [C1, C2])
    d, v, brake, throttle = _profile(3200.0, [dict(C1, brake_at=820.0, apex_kmh=104.0), C2])
    v = np.where(d < 500.0, 248.0, v)              # Lap 2 is slower out of the start
    ms = v * KMH
    tb_ = np.concatenate(([0.0], np.cumsum(np.diff(d) / ((ms[1:] + ms[:-1]) / 2))))
    raw = {"lap_dist": d, "t": tb_, "speed": ms, "brake": brake, "throttle": throttle}
    b = LapSeries(dist=d.copy(), raw=raw, orientation=calibrate_orientation(raw), available=set(raw))
    corners = corner_analysis(a, b)
    assert corners[0]["start"] == 0.0 and corners[-1]["end"] == 3200.0
    assert sum(c["delta"] for c in corners) == pytest.approx(ta_[-1] - tb_[-1], abs=0.002)
    # the braking zone still begins where the lift comes, not at the start line
    assert corners[0]["entry"] == pytest.approx(800.0, abs=CORNER_SMOOTH_M)
    assert corners[1]["entry"] == pytest.approx(2200.0, abs=CORNER_SMOOTH_M)
    for c in corners:
        assert c["start"] <= c["entry"] < c["apex"] < c["end"]


def test_delta_facts_are_measured_on_a_fixed_fine_grid_and_match_the_truth():
    """Biggest gain / loss used to come from the chart's grid, so they shifted with the screen
    width. Now: a fixed 0.5 m grid, compared with the integrated profiles directly."""
    a, ta_ = _lap(3200.0, [C1, C2])
    b, tb_ = _lap(3200.0, [dict(C1, brake_at=820.0, apex_kmh=104.0), dict(C2, apex_kmh=144.0)])
    facts = delta_facts(a, b)
    assert facts["window"] == 100.0                     # 3.2 km / 40 = 80 m, clamped to 100 m
    assert facts["net"] == pytest.approx(ta_[-1] - tb_[-1], abs=0.001)
    d = a.dist                                          # same 0.5 m spacing as the fact grid
    truth = (ta_ - tb_)
    span = int(round(100.0 / STEP_M))
    change = truth[span:] - truth[:-span]
    assert facts["loss"]["seconds"] == pytest.approx(change.max(), abs=0.001)
    assert facts["gain"]["seconds"] == pytest.approx(-change.min(), abs=0.001)
    assert facts["loss"]["to"] - facts["loss"]["from"] == pytest.approx(100.0, abs=0.5)
    assert 700.0 <= facts["loss"]["from"] <= 1100.0, "Lap 1 loses in corner 1 (slower apex)"
    assert 2100.0 <= facts["gain"]["from"] <= 2900.0, "and gains in corner 2 (Lap 2 is slower there)"


def test_no_gain_or_loss_for_identical_laps():
    a, _ = _lap(3200.0, [C1, C2])
    facts = delta_facts(a, a)
    assert facts["gain"] is None and facts["loss"] is None and facts["net"] == 0.0


def test_the_whole_lap_delta_matches_the_true_lap_time_difference():
    a, ta_ = _lap(3200.0, [C1, C2])
    b, tb_ = _lap(3200.0, [dict(C1, brake_at=820.0, apex_kmh=104.0), C2])
    grid, _ = make_grid(0.0, 3200.0, 2.0)
    delta = delta_series(a, b, grid)
    assert delta[-1] == pytest.approx(ta_[-1] - tb_[-1], abs=0.001), "the delta at the line is the lap-time difference, to the millisecond"


def test_corner_at_the_finish_and_at_the_start_are_still_found():
    """A lap that ends in a corner (no acceleration after it inside the lap) or starts in one."""
    a, _ = _lap(2500.0, [C1, dict(brake_at=2300.0, apex_at=2480.0, apex_kmh=120.0, full_throttle_at=2490.0)])
    corners = corner_analysis(a, a)
    assert len(corners) == 2 and abs(corners[-1]["apex"] - 2480.0) <= 4


def test_no_corners_on_a_straight_or_for_gentle_lifts():
    flat, _ = _lap(3000.0, [])
    assert corner_analysis(flat, flat) == []
    gentle, _ = _lap(3000.0, [dict(brake_at=1000.0, apex_at=1100.0, apex_kmh=254.0, full_throttle_at=1150.0)])  # −6 km/h
    assert corner_analysis(gentle, gentle) == []


def test_corner_analysis_needs_speed_in_both_laps():
    a, _ = _lap(3200.0, [C1, C2])
    no_speed = LapSeries(dist=a.dist, raw={k: v for k, v in a.raw.items() if k != "speed"}, orientation=a.orientation, available=set())
    assert corner_analysis(a, no_speed) == []


# ---------------------------------------------------------------- transport precision

def test_json_series_rounds_per_precision_and_keeps_gaps():
    values = np.array([1.23456, np.nan, -2.5, np.inf, 1234.5678])
    assert json_series(values, 3) == [1.235, None, -2.5, None, 1234.568]
    assert json_series(values, 1) == [1.2, None, -2.5, None, 1234.6]
    whole = json_series(np.array([3.0, 4.4, np.nan, 7.6]), 0)
    assert whole == [3, 4, None, 8] and all(isinstance(v, int) for v in whole if v is not None), "whole numbers, not 3.0"


def test_every_catalog_unit_has_a_deliberate_precision_not_below_the_display():
    for key, channel in CATALOG.items():
        if channel.discrete:
            assert channel_decimals(channel.unit, True) == 0, key
            continue
        assert channel.unit in ta.DECIMALS_BY_UNIT, f"{key}: unit {channel.unit!r} has no transport precision"
        # the browser shows values >= 10 with 1 decimal and >= 1000 without: transport never coarser,
        # except where the unit itself is whole (rpm)
        if channel.unit != "rpm":
            assert channel_decimals(channel.unit, False) >= 1, key


def test_the_response_uses_per_channel_precision(client):
    from tests.test_analysis import _headers, _register, _upload

    me, rival = _register(client, "Me"), _register(client, "Rival")
    a = _upload(client, me, lap_time=92.0, seed=31)["lap_id"]
    b = _upload(client, rival, lap_time=90.0, seed=32)["lap_id"]
    body = client.get("/telemetry/analysis/compare", params={"lap_a": a, "lap_b": b, "channels": "speed,gear,rpm,lat_g"},
                      headers=_headers(me)).json()
    decimals = lambda xs: max((len(repr(x).split(".")[1]) if isinstance(x, float) and "." in repr(x) else 0) for x in xs if x is not None)
    assert decimals(body["channels"]["speed"]["a"]) <= 1
    assert all(isinstance(x, int) for x in body["channels"]["gear"]["a"] if x is not None)
    assert all(isinstance(x, int) for x in body["channels"]["rpm"]["a"] if x is not None)
    assert decimals(body["channels"]["lat_g"]["a"]) <= 3
    assert decimals(body["delta"]) <= 3 and decimals(body["time"]["a"]) <= 3
    assert body["analysis_version"] == ANALYSIS_CACHE_VERSION == 5  # V0.9.4: corner phases, line offset
    assert isinstance(body["corners"], list)
    assert set(body["delta_facts"]) == {"window", "gain", "loss", "net"}


def test_corners_only_for_the_whole_lap_not_for_a_zoom_stretch(client):
    from tests.test_analysis import _headers, _register, _upload

    me, rival = _register(client, "Me"), _register(client, "Rival")
    a = _upload(client, me, lap_time=92.0, seed=33)["lap_id"]
    b = _upload(client, rival, lap_time=90.0, seed=34)["lap_id"]
    h = _headers(me)
    whole = client.get("/telemetry/analysis/compare", params={"lap_a": a, "lap_b": b, "channels": "speed"}, headers=h).json()
    stretch = client.get("/telemetry/analysis/compare", params={"lap_a": a, "lap_b": b, "channels": "speed", "start": 500, "end": 900}, headers=h).json()
    assert whole["corners"], "the synthetic oval has corners"
    assert stretch["corners"] == []
    single = client.get("/telemetry/analysis/compare", params={"lap_a": a, "channels": "speed"}, headers=h).json()
    assert single["corners"] == [], "one lap: nothing to compare per corner"


# ---------------------------------------------------------------- the prepared-lap cache

def _write(path, length=1500.0):
    lap, _ = _lap(length, [dict(C1, brake_at=500.0, apex_at=620.0, full_throttle_at=700.0)])
    pd.DataFrame(lap.raw).to_parquet(path, index=False)


def test_prepared_laps_are_reused_and_a_changed_file_is_read_again(tmp_path, monkeypatch):
    ta.clear_lap_cache()
    path = tmp_path / "lap.parquet"
    _write(path)
    reads = []
    real = ta._load_lap_uncached
    monkeypatch.setattr(ta, "_load_lap_uncached", lambda p, w: (reads.append(p), real(p, w))[1])
    first = ta.load_lap(path, ["speed"])
    again = ta.load_lap(path, ["speed"])
    assert again is first and len(reads) == 1, "the second request is served from memory"
    ta.load_lap(path, ["speed", "brake"])
    assert len(reads) == 2, "other columns = another entry"
    import os
    import time as _t
    _write(path, length=1800.0)
    os.utime(path, ns=(_t.time_ns() + 10**9, _t.time_ns() + 10**9))
    changed = ta.load_lap(path, ["speed"])
    assert len(reads) == 3 and changed.length > first.length, "a changed file is never answered from the cache"
    ta.clear_lap_cache()


def test_the_lap_cache_stays_within_its_byte_budget(tmp_path, monkeypatch):
    ta.clear_lap_cache()
    monkeypatch.setattr(ta, "LAP_CACHE_MAX_BYTES", 600_000)
    for i in range(12):
        path = tmp_path / f"lap{i}.parquet"
        _write(path)
        ta.load_lap(path, ["speed"])
        assert ta._lap_cache_bytes <= ta.LAP_CACHE_MAX_BYTES
    assert 0 < len(ta._lap_cache) < 12, "the oldest laps were evicted"
    ta.clear_lap_cache()
    assert ta._lap_cache_bytes == 0 and not ta._lap_cache
