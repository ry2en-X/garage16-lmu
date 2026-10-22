"""V0.9.3 — per-lap figures, theoretical best, sessions (server/lap_insights.py) and the new
analysis data: damper velocity, wheel slip / lock-ups, tread zones, histograms, events.

The checks are about definitions that can be verified by hand: segment times add up to the
lap time exactly, the theoretical best is the sum of the fastest real segments, a damper
velocity is the time derivative of the travel with "bump = +" decided from braking, a wheel
locked on purpose in the data is found where it was put."""
from __future__ import annotations

import numpy as np
import pytest

from server import lap_insights as li
from server.telemetry_analysis import distribution, histogram_edges, load_lap, wheel_events, channel_values
from tests.helpers.synthetic_lap import make_lap
from tests.helpers.v093 import clean_lap, headers, register, upload_df


def _cols(df):
    out = {}
    for c in df.columns:
        if df[c].dtype == object:
            continue
        out[c] = np.array(df[c].to_numpy(dtype=float), copy=True)
    for nested, prefix in (("tire_pressures", "tire_pressure"), ("tire_temps_outer", "tire_temp_mean"), ("brake_temps", "brake_temp")):
        arr = np.array(df[nested].tolist(), dtype=float)
        for i, w in enumerate(("fl", "fr", "rl", "rr")):
            out[f"{prefix}_{w}"] = arr[:, i]
    return out


# ---------------------------------------------------------------- per-lap figures

def test_figures_of_a_synthetic_lap_are_what_the_samples_say():
    df = make_lap(lap_time=90.0)
    df["virtual_energy"] = np.linspace(0.80, 0.77, len(df)).astype(np.float32)
    df["wear_fl"] = np.linspace(0.99, 0.985, len(df)).astype(np.float32)
    stats = li.compute(_cols(df), lap_time=90.0)
    assert stats["fuel_used_l"] == pytest.approx(2.0)           # 60 -> 58 L in the synthetic lap
    assert stats["energy_used_pct"] == pytest.approx(3.0, abs=0.01)
    assert stats["wheels"]["fl"]["wear_used_pct"] == pytest.approx(0.5, abs=0.01)
    assert stats["speed_max_kmh"] == pytest.approx(float(df["speed"].max()) * 3.6, abs=0.1)
    shares = [stats[k] for k in ("full_throttle_pct", "braking_pct", "coasting_pct")]
    assert all(0 <= s <= 100 for s in shares)
    assert stats["driven_m"] == pytest.approx(stats["distance_m"], rel=0.02)
    assert stats["wheels"]["fl"]["brake_max_c"] > 300   # Kelvin converted


def test_segment_times_add_up_to_the_lap_time_exactly():
    df = make_lap(lap_time=90.0)
    profile = li.time_profile(df["t"].to_numpy(float), df["lap_dist"].to_numpy(float), 90.0)
    segments = li.segment_times(profile, 90.0)
    assert profile["step"] == 50.0 and len(segments) == len(profile["cum"]) + 1
    assert sum(segments) == pytest.approx(90.0, abs=1e-9)
    assert all(s > 0 for s in segments)


def test_a_lap_that_does_not_start_at_the_line_gets_no_profile():
    df = make_lap(lap_time=90.0)
    t, d = df["t"].to_numpy(float), df["lap_dist"].to_numpy(float) + 200.0
    assert li.time_profile(t, d, 90.0) is None


def test_the_tail_after_the_lap_number_flip_is_left_out_of_the_figures():
    df = make_lap(lap_time=90.0)
    cols = _cols(df)
    cols["fuel"][-5:] = 10.0     # nonsense in the last 5 samples (the next lap's frames)
    assert li.compute(cols, lap_time=90.0, tail_samples=5)["fuel_used_l"] == pytest.approx(2.0, abs=0.01)


# ---------------------------------------------------------------- theoretical best

def _lap(id_, lap_time, segments, step=50.0):
    cum, total = [], 0.0
    for s in segments[:-1]:
        total += s
        cum.append(round(total, 3))
    return {"id": id_, "lap_time": lap_time, "sector_times": [lap_time / 3] * 3,
            "profile": {"step": step, "start": 0.0, "end": step * len(segments), "cum": cum}}


def test_theoretical_best_takes_the_fastest_real_segment_from_each_lap():
    a = _lap(1, 10.0, [2.0, 3.0, 2.5, 2.5])
    b = _lap(2, 10.2, [2.2, 2.6, 2.7, 2.7])
    r = li.theoretical_best([a, b])
    assert r["time"] == pytest.approx(2.0 + 2.6 + 2.5 + 2.5)
    assert r["gain"] == pytest.approx(10.0 - 9.6)
    assert r["from_laps"] == {"1": 3, "2": 1}
    assert [s[0] for s in r["segment_best"]] == [1, 2, 1, 1]
    assert r["best_lap_id"] == 1 and r["laps_used"] == 2


def test_theoretical_best_never_beats_the_best_lap_with_a_single_lap():
    a = _lap(1, 10.0, [2.0, 3.0, 2.5, 2.5])
    assert li.theoretical_best([a])["time"] == pytest.approx(10.0)
    assert li.theoretical_best([a])["gain"] == pytest.approx(0.0)


def test_laps_of_a_different_length_are_not_mixed_in():
    a = _lap(1, 10.0, [2.0, 3.0, 2.5, 2.5])
    b = _lap(2, 10.1, [2.0, 3.0, 2.5, 2.5])
    odd = _lap(3, 9.0, [1.0, 1.0, 7.0])           # one segment fewer: another layout length
    r = li.theoretical_best([a, b, odd])
    assert r["segments"] == 4 and r["laps_used"] == 2 and r["time"] == pytest.approx(10.0)


def test_sector_ideal_is_the_sum_of_the_best_sectors():
    a = {"id": 1, "lap_time": 90.0, "sector_times": [30.0, 31.0, 29.0], "profile": None}
    b = {"id": 2, "lap_time": 90.5, "sector_times": [29.5, 31.5, 29.5], "profile": None}
    r = li.theoretical_best([a, b])
    assert r["sector_ideal"]["time"] == pytest.approx(29.5 + 31.0 + 29.0)
    assert "time" not in r, "no stretch-by-stretch best without profiles"


# ---------------------------------------------------------------- sessions and consistency

def test_consistency_uses_clean_laps_and_reports_the_trend():
    times = [90.0, 90.2, 90.4, 90.6, 90.8, 120.0]     # +0.2 s per lap, one lap in traffic
    c = li.consistency(times)
    assert c["laps"] == 6 and c["clean_laps"] == 5 and c["best"] == 90.0
    assert c["trend_per_lap"] == pytest.approx(0.2)
    assert c["within_half_second"] == 3
    assert c["stdev"] == pytest.approx(np.std([90.0, 90.2, 90.4, 90.6, 90.8], ddof=1), abs=1e-3)


def test_sessions_split_on_pauses_and_on_track_or_car_changes():
    def lap(i, at, track="A", car="X"):
        return {"id": i, "lap_number": i, "lap_time": 90 + i * 0.1, "is_valid": True, "recorded_at": at,
                "session_type": 10, "track_name": track, "car_model": car, "stats": {"fuel_used_l": 2.0}}
    laps = [lap(1, 0), lap(2, 100), lap(3, 200), lap(4, 200 + 16 * 60), lap(5, 2000, car="Y")]
    sessions = li.group_sessions(laps)
    assert [len(s["laps"]) for s in sessions] == [1, 1, 3], "newest first"
    assert sessions[2]["fuel_per_lap_l"] == 2.0 and sessions[0]["car_model"] == "Y"


# ---------------------------------------------------------------- endpoints

def test_theoretical_best_and_sessions_endpoints(client):
    me = register(client, "Ideal")
    ids = [upload_df(client, me, clean_lap(seed=s, lap_time=lt), lap_time=lt, lap_number=i + 1, recorded_at=1_700_000_000 + 100 * i)["lap_id"]
           for i, (s, lt) in enumerate([(1, 90.0), (2, 90.6), (3, 91.0)])]
    res = client.get("/telemetry/analysis/theoretical-best", params={"track_name": "Le Mans", "car_model": "499P (2025)"},
                     headers=headers(me)).json()
    assert res["found"] and res["result"]["laps_used"] == 3
    r = res["result"]
    assert r["time"] <= r["reference_lap_time"] + 1e-9 and r["reference_lap_id"] == ids[0]
    assert sum(seg[1] for seg in r["segment_best"]) == pytest.approx(r["time"], abs=0.01)

    sessions = client.get("/telemetry/analysis/sessions", headers=headers(me)).json()
    assert sessions["total"] == 1
    s = sessions["sessions"][0]
    assert [lap["id"] for lap in s["laps"]] == ids and s["consistency"]["best"] == 90.0
    assert s["fuel_per_lap_l"] == pytest.approx(2.0, abs=0.05)

    other = register(client, "Other")
    seen = client.get("/telemetry/analysis/theoretical-best",
                      params={"track_name": "Le Mans", "car_model": "499P (2025)", "driver_id": me["driver_id"]},
                      headers=headers(other)).json()
    assert seen["found"] and seen["driver_name"] == "Ideal"
    assert client.get("/telemetry/analysis/sessions", headers=headers(other)).json()["sessions"] == [], "only your own sessions"


# ---------------------------------------------------------------- damper velocity and wheel slip

def _dynamic_lap(tmp_path, *, defl_sign=1.0, lock_at=None):
    """A lap whose suspension DIVES under braking (front compresses relative to the rear)
    and, optionally, a front-left wheel that locks for 0.3 s while braking."""
    df = make_lap(lap_time=90.0)
    n = len(df)
    t = df["t"].to_numpy(float)
    v = df["speed"].to_numpy(float)
    decel = np.clip(-np.gradient(v, t), 0, None)
    bump = 0.004 * np.sin(np.linspace(0, 200, n))          # road bumps, same on all wheels
    front = 0.05 + 0.002 * decel + bump
    rear = 0.05 - 0.001 * decel + bump
    for w, base in (("fl", front), ("fr", front), ("rl", rear), ("rr", rear)):
        df[f"susp_deflection_{w}"] = (defl_sign * base).astype(np.float32)
    radius = 0.34
    for w in ("fl", "fr", "rl", "rr"):
        df[f"long_ground_vel_{w}"] = v.astype(np.float32)
        df[f"rotation_{w}"] = (v / radius).astype(np.float32)
    df["brake"] = np.clip(decel / 8.0, 0, 1)
    if lock_at is not None:
        i0 = int(np.argmin(np.abs(t - lock_at)))
        i1 = i0 + 20                                       # 20 samples ~ 0.6 s at 3000/90 Hz
        df.loc[i0:i1, "rotation_fl"] = (v[i0:i1 + 1] * 0.5 / radius).astype(np.float32)   # 50 % slower than the ground
        df.loc[i0:i1, "brake"] = 0.8
    path = tmp_path / "dyn.parquet"
    df.to_parquet(path, index=False)
    return path, df


@pytest.mark.parametrize("sign", [1.0, -1.0])
def test_damper_velocity_is_bump_positive_whatever_the_raw_sign(tmp_path, sign):
    path, df = _dynamic_lap(tmp_path, defl_sign=sign)
    series = load_lap(path, ["damper_vel_fl"])
    assert series.orientation.susp == sign and series.orientation.decided_from_data["susp"]
    vel = channel_values(series, "damper_vel_fl")
    raw = np.gradient(df["susp_deflection_fl"].to_numpy(float), df["t"].to_numpy(float)) * 1000 * sign
    keep = np.isfinite(vel)
    assert np.corrcoef(vel[keep][5:-5], raw[: len(vel)][keep][5:-5])[0, 1] > 0.9


def test_damper_histogram_splits_bump_rebound_and_low_high_speed(tmp_path):
    path, _ = _dynamic_lap(tmp_path)
    series = load_lap(path, ["damper_vel_fl"])
    values = channel_values(series, "damper_vel_fl")
    edges = histogram_edges([values], discrete=False, symmetric=True, bins=30)
    assert edges[0] == -edges[-1] and 0.0 in np.round(edges, 9), "symmetric with an edge at zero"
    d = distribution(series, "damper_vel_fl", edges)
    total = sum(d["pct"]) + d["below"] + d["above"]
    assert total == pytest.approx(100.0, abs=0.05)
    damper = d["damper"]
    assert damper["bump"] + damper["rebound"] == pytest.approx(100.0, abs=0.5)
    assert damper["low_bump"] + damper["high_bump"] == pytest.approx(damper["bump"], abs=0.2)


def test_a_locked_wheel_is_found_where_it_was_put(tmp_path):
    path, df = _dynamic_lap(tmp_path, lock_at=50.0)
    series = load_lap(path, ["slip_ratio_fl", "slip_ratio_fr", "slip_ratio_rl", "slip_ratio_rr"])
    slip = channel_values(series, "slip_ratio_fl")
    assert np.nanmedian(np.abs(slip)) < 1.0, "free-rolling wheels show ~0 % slip (radius estimated from the lap)"
    events = wheel_events(series)
    locks = [e for e in events if e["type"] == "lockup"]
    assert len(locks) == 1 and locks[0]["wheel"] == "fl"
    t = df["t"].to_numpy(float)
    i0 = int(np.argmin(np.abs(t - 50.0)))
    assert locks[0]["at"] == pytest.approx(float(df["lap_dist"].iloc[i0]), abs=5.0)
    assert locks[0]["peak"] == pytest.approx(-50.0, abs=2.0)


def test_slip_is_declined_when_the_tyre_radius_cannot_be_estimated(tmp_path):
    df = make_lap(lap_time=90.0)
    df["rotation_fl"] = np.float32(0.0)      # a file without wheel rotation
    path = tmp_path / "x.parquet"
    df.to_parquet(path, index=False)
    series = load_lap(path, ["slip_ratio_fl"])
    assert channel_values(series, "slip_ratio_fl") is None


def test_tread_zones_follow_the_wheel_side(tmp_path):
    df = make_lap(lap_time=90.0)
    for w in ("fl", "fr"):
        df[f"temp_l_{w}"] = np.float32(373.15)   # 100 °C on the car's left side of the tread
        df[f"temp_c_{w}"] = np.float32(363.15)
        df[f"temp_r_{w}"] = np.float32(353.15)   # 80 °C on the right side
    path = tmp_path / "t.parquet"
    df.to_parquet(path, index=False)
    series = load_lap(path, ["tire_temp_outer_fl", "tire_temp_inner_fl", "tire_temp_outer_fr", "tire_temp_inner_fr"])
    assert channel_values(series, "tire_temp_outer_fl")[0] == pytest.approx(100.0, abs=0.01)   # left wheel: left = outer
    assert channel_values(series, "tire_temp_inner_fl")[0] == pytest.approx(80.0, abs=0.01)
    assert channel_values(series, "tire_temp_outer_fr")[0] == pytest.approx(80.0, abs=0.01)    # right wheel: right = outer
    assert channel_values(series, "tire_temp_inner_fr")[0] == pytest.approx(100.0, abs=0.01)


def test_distribution_and_events_endpoints(client, tmp_path):
    me = register(client, "Dampers")
    _, df = _dynamic_lap(tmp_path, lock_at=40.0)
    a = upload_df(client, me, df)["lap_id"]
    b = upload_df(client, me, clean_lap(seed=21), lap_number=2)["lap_id"]

    resp = client.get("/telemetry/analysis/distribution", params={
        "lap_a": a, "lap_b": b, "channels": "damper_vel_fl,gear"}, headers=headers(me))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    fl = body["channels"]["damper_vel_fl"]
    assert fl["edges"][0] == -fl["edges"][-1] and fl["a"]["damper"]["bump"] > 0
    gear = body["channels"]["gear"]
    assert gear["discrete"] and sum(gear["a"]["pct"]) == pytest.approx(100.0, abs=0.1)
    etag = resp.headers["etag"]
    assert client.get("/telemetry/analysis/distribution", params={"lap_a": a, "lap_b": b, "channels": "damper_vel_fl,gear"},
                      headers={**headers(me), "If-None-Match": etag}).status_code == 304
    too_many = ",".join(["speed", "rpm", "gear", "throttle", "brake", "fuel", "lat_g", "long_g", "steering"])
    assert client.get("/telemetry/analysis/distribution", params={"lap_a": a, "channels": too_many}, headers=headers(me)).status_code == 400

    events = client.get("/telemetry/analysis/events", params={"lap_a": a, "lap_b": b}, headers=headers(me)).json()
    assert events["track_limits"]["a"]["status"] == "valid"
    assert [e["wheel"] for e in events["wheels"]["a"] if e["type"] == "lockup"] == ["fl"]
    assert events["wheels"]["b"] == []

    stranger = register(client, "Stranger")
    assert client.get("/telemetry/analysis/events", params={"lap_a": a}, headers=headers(stranger)).status_code == 200, "valid laps are open to everyone"
    assert client.get("/telemetry/analysis/events", params={"lap_a": a}).status_code == 401


def test_the_lap_header_carries_figures_but_not_the_profile(client):
    me = register(client, "Header")
    lap_id = upload_df(client, me, clean_lap(seed=22))["lap_id"]
    info = client.get(f"/telemetry/analysis/laps/{lap_id}", headers=headers(me)).json()
    assert info["lap"]["stats"]["fuel_used_l"] == pytest.approx(2.0, abs=0.05)
    assert "profile" not in info["lap"]["stats"]
    assert info["lap"]["validation"]["status"] == "valid"
    assert "damper_vel_fl" in info["available_channels"] and "slip_ratio_fl" in info["available_channels"]
