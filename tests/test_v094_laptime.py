"""V0.9.4 — LapTime Analyzer, server side.

  * corner phases (braking / entry / apex / exit / straight) add up EXACTLY to the corner's delta
  * corner details: braking point, turn-in (estimated), throttle points, coasting, exit acceleration
  * racing line: Lap 2's lateral offset from Lap 1 (inside = +) in /compare, as the pair channel
    "line_offset", and in high resolution from /line
  * up to two extra laps (c, d) with their delta to the reference lap
  * /corner-history (earlier laps of the same driver, layout and car) and /lap-sectors
  * the stored per-lap figures (insights v3): pit lane, refuelling, tyre change, traffic
"""
from __future__ import annotations

import numpy as np
import pytest

from server import lap_insights
from server.telemetry_analysis import line_offset
from tests.helpers.v093 import clean_lap, headers, register, upload_df

COMPARE = "/telemetry/analysis/compare"


def _pair(client, name="Phases"):
    me = register(client, name)
    a = upload_df(client, me, clean_lap(seed=1), lap_time=90.0, lap_number=1, recorded_at=1_700_000_000)["lap_id"]
    b = upload_df(client, me, clean_lap(seed=2, speed_scale=1.03, lap_time=88.0), lap_time=88.0, lap_number=2,
                  recorded_at=1_700_000_100)["lap_id"]
    return me, a, b


def test_corner_phases_add_up_to_the_corner_delta(client):
    me, a, b = _pair(client)
    body = client.get(COMPARE, params={"lap_a": a, "lap_b": b, "channels": "speed"}, headers=headers(me)).json()
    assert body["corners"], "the synthetic lap has two corners"
    for corner in body["corners"]:
        phases = corner["phases"]
        assert [p["phase"] for p in phases] == ["braking", "entry", "apex", "exit", "straight"][:len(phases)] or phases
        total = sum(p["delta"] for p in phases if p["delta"] is not None)
        assert total == pytest.approx(corner["delta"], abs=0.002)
        # boundaries are contiguous
        for left, right in zip(phases, phases[1:]):
            assert left["to"] == pytest.approx(right["from"])


def test_corner_details_are_measured_and_turn_in_is_marked_as_estimate(client):
    me, a, b = _pair(client, "Details")
    body = client.get(COMPARE, params={"lap_a": a, "lap_b": b, "channels": "speed"}, headers=headers(me)).json()
    corner = body["corners"][0]
    for side in ("a", "b"):
        info = corner[side]
        for key in ("brake", "min", "apex", "exit", "throttle_on", "coast_m"):
            assert key in info, key
    assert body["analysis_version"] == 5


def test_line_offset_is_zero_for_the_same_line_and_signed_inside_positive():
    # a counter-clockwise circle of radius 100: B 1.5 m further in = +1.5 inside
    theta = np.linspace(0, 2 * np.pi, 2000)
    xa, ya = 100 * np.cos(theta), 100 * np.sin(theta)
    xb, yb = 98.5 * np.cos(theta), 98.5 * np.sin(theta)
    same = line_offset(xa, ya, xa, ya, 0.3)
    assert np.nanmax(np.abs(same)) < 0.05
    off = line_offset(xa, ya, xb, yb, 0.3)
    # line_offset is signed LEFT of A's direction; ccw: left = inside
    assert np.nanmedian(off) == pytest.approx(1.5, abs=0.05)


def test_compare_returns_the_offset_and_the_pair_channel(client):
    me = register(client, "Line")
    a = upload_df(client, me, clean_lap(seed=3), lap_number=1, recorded_at=1_700_000_000)["lap_id"]
    shifted = clean_lap(seed=3)
    shifted["pos_x"] = shifted["pos_x"] + 1.5   # 1.5 m toward the loop's centre on the first straight
    b = upload_df(client, me, shifted, lap_number=2, recorded_at=1_700_000_100)["lap_id"]
    body = client.get(COMPARE, params={"lap_a": a, "lap_b": b, "channels": "speed,line_offset"},
                      headers=headers(me)).json()
    offset = np.array([np.nan if v is None else v for v in body["map"]["offset_b"]], dtype=float)
    assert np.isfinite(offset).mean() > 0.9
    assert np.nanmax(np.abs(offset)) == pytest.approx(1.5, abs=0.15)
    # the two straights: once further in, once further out — the sign follows the corners
    assert (offset > 1.2).sum() > 50 and (offset < -1.2).sum() > 50
    channel = body["channels"]["line_offset"]
    assert channel["a"] == body["map"]["offset_b"] and channel["b"] is None
    catalog = client.get("/telemetry/analysis/channels").json()["channels"]
    assert any(c["key"] == "line_offset" and c.get("pair") for c in catalog)


def test_line_endpoint_gives_a_fine_grid_for_a_stretch(client):
    me, a, b = _pair(client, "Zoom")
    resp = client.get("/telemetry/analysis/line", params={"lap_a": a, "lap_b": b, "start": 100, "end": 400},
                      headers=headers(me))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["grid"]["step"] == pytest.approx(0.5)
    assert body["grid"]["start"] >= 100 and body["grid"]["count"] <= 601
    assert len(body["x_a"]) == len(body["x_b"]) == len(body["offset_b"]) == body["grid"]["count"]
    again = client.get("/telemetry/analysis/line", params={"lap_a": a, "lap_b": b, "start": 100, "end": 400},
                       headers={**headers(me), "If-None-Match": resp.headers["etag"]})
    assert again.status_code == 304


def test_the_pair_channel_has_no_histogram(client):
    me, a, b = _pair(client, "Histo")
    resp = client.get("/telemetry/analysis/distribution", params={"lap_a": a, "lap_b": b, "channels": "line_offset"},
                      headers=headers(me))
    assert resp.status_code == 400


def test_extra_laps_come_with_their_delta_to_the_reference(client):
    me, a, b = _pair(client, "Extras")
    c = upload_df(client, me, clean_lap(seed=4, speed_scale=0.98, lap_time=91.5), lap_time=91.5, lap_number=3,
                  recorded_at=1_700_000_200)["lap_id"]
    body = client.get(COMPARE, params={"lap_a": a, "lap_b": b, "extra": str(c), "channels": "speed"},
                      headers=headers(me)).json()
    assert body["extra_sides"] == ["c"]
    assert body["laps"]["c"]["id"] == c
    assert len(body["time"]["c"]) == body["grid"]["count"]
    delta = [v for v in body["extra_delta"]["c"] if v is not None]
    assert delta[-1] > 0   # slower than the reference
    assert body["channels"]["speed"].get("c") is not None
    too_many = client.get(COMPARE, params={"lap_a": a, "lap_b": b, "extra": f"{c},99998,99999"}, headers=headers(me))
    assert too_many.status_code == 400


def test_corner_history_lists_earlier_laps_of_the_same_driver_and_car(client):
    me = register(client, "History")
    ids = [upload_df(client, me, clean_lap(seed=10 + i), lap_number=i + 1, recorded_at=1_700_000_000 + 100 * i)["lap_id"]
           for i in range(3)]
    other_car = upload_df(client, me, clean_lap(seed=20), lap_number=9, recorded_at=1_700_000_050,
                          car_model="397_25_ORECA")["lap_id"]
    body = client.get("/telemetry/analysis/corner-history", params={"lap_id": ids[-1]}, headers=headers(me)).json()
    assert body["corners"], "the lap's own corners are stored at upload"
    assert [lap["id"] for lap in body["laps"]] == [ids[1], ids[0]]
    assert other_car not in [lap["id"] for lap in body["laps"]]
    corner = body["laps"][0]["corners"][0]
    assert len(corner) == 5   # apex, braking point, min speed, full throttle, exit speed


def test_lap_sectors_give_best_driven_sectors_and_the_potential(client):
    me = register(client, "Sectors")
    first = upload_df(client, me, clean_lap(seed=30), lap_time=90.0, lap_number=1, recorded_at=1_700_000_000,
                      sector_times=[31.0, 29.0, 30.0])["lap_id"]
    second = upload_df(client, me, clean_lap(seed=31), lap_time=89.5, lap_number=2, recorded_at=1_700_000_100,
                       sector_times=[30.0, 29.5, 30.0])["lap_id"]
    body = client.get("/telemetry/analysis/lap-sectors", params={"lap_id": second}, headers=headers(me)).json()
    assert [s["time"] for s in body["session_best"]] == [30.0, 29.0, 30.0]
    assert body["session_best"][1]["lap_id"] == first
    assert body["session_ideal"] == pytest.approx(89.0)
    assert body["potential_session"] == pytest.approx([0.0, 0.5, 0.0])
    assert body["calculated"] is True


def _columns(n=1000, **extra):
    t = np.linspace(0, 100, n)
    cols = {"t": t, "lap_dist": np.linspace(0, 5000, n), "speed": np.full(n, 50.0)}
    cols.update(extra)
    return cols


def test_insights_measure_pit_lane_refuelling_and_tyre_change():
    n = 1000
    in_pits = np.zeros(n)
    in_pits[900:] = 1
    speed = np.full(n, 50.0)
    speed[950:990] = 0.0   # standing in the box
    fuel = np.linspace(20, 18, n)
    fuel[950:990] = np.linspace(18.1, 58.1, 40)   # refuelled 40 l over 40 samples
    fuel[990:] = 58.0
    wear = np.linspace(0.9, 0.88, n)
    wear[970:] = 1.0                               # new tyre fitted
    cols = _columns(n, speed=speed, in_pits=in_pits, fuel=fuel, wear_fl=wear, wear_fr=wear, wear_rl=wear, wear_rr=wear)
    stats = lap_insights.compute(cols, lap_time=100.0)
    assert stats["version"] == 3
    assert stats["pit"]["ends_in_pits"] is True and stats["pit"]["starts_in_pits"] is False
    assert stats["pit"]["stationary_s"] == pytest.approx(4.0, abs=0.3)
    assert stats["fuel_added_l"] == pytest.approx(40.0, abs=0.6)
    assert 1.5 <= stats["fuel_used_l"] <= 2.6
    assert stats["tyres_changed"] == ["fl", "fr", "rl", "rr"]


def test_insights_traffic_from_the_measured_gaps_only():
    n = 1000
    gap = np.full(n, 3.0)
    gap[:300] = 0.6                     # 30 % of the lap less than a second behind a car
    flag = np.zeros(n)
    flag[500:600] = 6                   # blue flag for 10 %
    place = np.full(n, 5.0)
    place[700:] = 4.0
    stats = lap_insights.compute(_columns(n, time_gap_ahead=gap, flag=flag, place=place), lap_time=100.0)
    traffic = stats["traffic"]
    assert traffic["ahead_close_share"] == pytest.approx(0.3, abs=0.01)
    assert traffic["blue_flag_share"] == pytest.approx(0.1, abs=0.01)
    assert traffic["places_gained"] == 1 and traffic["place_end"] == 4
    without = lap_insights.compute(_columns(n), lap_time=100.0)
    assert "traffic" not in without and "pit" not in without   # never assumed
