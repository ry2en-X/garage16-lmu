"""
Lap analysis (V0.9.0): the pure analysis functions on a physically consistent
synthetic lap, and the /telemetry/analysis API end to end (upload through the
real client uploader, then compare through the real app).

The synthetic lap (tests/helpers/synthetic_lap.py) is generated with the game's
documented conventions, then re-generated with the unverified ones flipped
(steering sign, mirrored world) — the self-calibration has to get all of them
right, because on a real session nobody can eyeball a mirrored graph.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from server.telemetry_analysis import (
    AnalysisError,
    delta_series,
    load_lap,
    logical_columns,
    make_grid,
    map_mirror,
    read_raw,
    resample,
    sector_distances,
    channel_values,
    common_range,
)
from server.telemetry_channels import CATALOG, calibrate_orientation
from tests.helpers.synthetic_lap import make_lap, write_lap


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

def test_read_raw_expands_wheel_lists_and_reads_only_what_was_asked(tmp_path):
    path = tmp_path / "lap.parquet"
    write_lap(path)
    raw = read_raw(path, ["speed", "tire_pressure_fr", "brake_temp_rl", "tire_temp_mean_fl", "no_such_column"])
    assert set(raw) == {"speed", "tire_pressure_fr", "brake_temp_rl", "tire_temp_mean_fl"}
    assert raw["tire_pressure_fr"][0] == 181.0      # FL, FR, RL, RR order
    assert raw["tire_temp_mean_fl"][0] == 350.0
    assert raw["brake_temp_rl"][0] == 620.0


def test_logical_columns_lists_the_expanded_wheel_columns(tmp_path):
    path = tmp_path / "lap.parquet"
    write_lap(path, with_extended=False)
    offered = logical_columns(path)
    assert "tire_pressure_rr" in offered and "brake_temp_fl" in offered
    assert "tire_pressures" not in offered
    assert "tire_load_fl" not in offered            # an old-format file has no extended channels


def test_unreadable_file_is_an_analysis_error_not_a_crash(tmp_path):
    path = tmp_path / "broken.parquet"
    path.write_bytes(b"this is not parquet")
    with pytest.raises(AnalysisError):
        load_lap(path, ["speed"])


def test_file_without_a_distance_axis_is_rejected(tmp_path):
    path = tmp_path / "nodist.parquet"
    pq.write_table(pa.table({"t": [0.0, 1.0, 2.0], "speed": [1.0, 2.0, 3.0]}), path)
    with pytest.raises(AnalysisError):
        load_lap(path, ["speed"])


def test_distance_axis_survives_jitter_and_standing_frames(tmp_path):
    df = make_lap(n=800, with_extended=False)
    d = df["lap_dist"].to_numpy().copy()
    d[100:110] = d[100]              # car standing still
    d[300] -= 2.0                    # sensor jitter, below the validator's 5 m limit
    df["lap_dist"] = d
    path = tmp_path / "jitter.parquet"
    df.to_parquet(path, index=False)
    series = load_lap(path, ["speed"])
    assert np.all(np.diff(series.dist) > 0)
    assert len(series.dist) < 800


# ---------------------------------------------------------------------------
# Self-calibration
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("steering_right_positive", [True, False])
@pytest.mark.parametrize("world_mirrored", [False, True])
def test_orientation_is_recovered_whatever_the_unverified_conventions_are(tmp_path, steering_right_positive, world_mirrored):
    path = tmp_path / "lap.parquet"
    write_lap(path, steering_right_positive=steering_right_positive, world_mirrored=world_mirrored)
    series = load_lap(path, ["speed", "steering", "lat_g", "long_g", "yaw_rate", "slip_angle_fl"])

    o = series.orientation
    assert all(o.decided_from_data[k] for k in ("steering", "lat_g", "long_g", "yaw", "slip")), o
    assert o.steering == (1.0 if steering_right_positive else -1.0)

    # Right-hand corners: steering, lateral G, yaw rate and slip angle are all POSITIVE.
    steering = channel_values(series, "steering")
    lat_g = channel_values(series, "lat_g")
    yaw = channel_values(series, "yaw_rate")
    slip = channel_values(series, "slip_angle_fl")
    assert steering.max() > 30 and steering.min() > -5
    assert lat_g.max() > 0.5 and lat_g.min() > -0.1
    assert yaw.max() > 0 and yaw.min() > -1
    assert slip.max() > 0 and slip.min() > -0.5

    # The track map is un-mirrored in every case, and it knows it decided from data.
    mirror, decided = map_mirror(series)
    assert decided
    assert mirror == world_mirrored


def test_longitudinal_g_is_positive_when_accelerating(tmp_path):
    path = tmp_path / "lap.parquet"
    write_lap(path)
    series = load_lap(path, ["long_g", "speed"])
    long_g = channel_values(series, "long_g")
    dvdt = np.gradient(series.raw["speed"], series.raw["t"])
    mask = np.abs(dvdt) > 1.0
    assert mask.sum() > 5
    assert np.corrcoef(long_g[mask], dvdt[mask])[0, 1] > 0.9


def test_calibration_without_extended_channels_falls_back_to_documented_defaults(tmp_path):
    path = tmp_path / "old.parquet"
    write_lap(path, with_extended=False)
    series = load_lap(path, ["speed", "steering"])
    o = series.orientation
    assert o.steering == 1.0
    assert not o.decided_from_data["steering"]
    assert not o.decided_from_data["lat_g"]
    # ... and the map can STILL be oriented, from the basic steering column.
    assert map_mirror(series) == (False, True)


def test_a_motionless_lap_does_not_invent_a_sign():
    n = 200
    raw = {
        "t": np.linspace(0, 10, n), "speed": np.zeros(n), "steering": np.zeros(n),
        "accel_x": np.zeros(n), "accel_z": np.zeros(n), "ang_vel_y": np.zeros(n),
    }
    o = calibrate_orientation(raw)
    assert not any(o.decided_from_data.values())


# ---------------------------------------------------------------------------
# Units and channels
# ---------------------------------------------------------------------------

def test_units_are_converted_for_display(tmp_path):
    path = tmp_path / "lap.parquet"
    write_lap(path)
    series = load_lap(path, ["speed", "brake_temp_fl", "tire_temp_fl", "tire_pressure_rr", "susp_travel_fl", "brake_bias_rear"])
    assert channel_values(series, "speed")[5] == pytest.approx(series.raw["speed"][5] * 3.6)
    assert channel_values(series, "brake_temp_rl") is None          # not requested -> not loaded -> None
    assert 300 < channel_values(series, "brake_temp_fl")[5] < 340           # 600 K -> ~330 C
    assert channel_values(series, "tire_temp_fl")[5] == pytest.approx(350.0 - 273.15)
    assert channel_values(series, "tire_pressure_rr")[5] == pytest.approx(183.0)  # kPa, unchanged
    assert channel_values(series, "brake_bias_rear")[5] == pytest.approx(55.0)    # fraction -> %
    assert channel_values(series, "susp_travel_fl")[5] > 40                       # metres -> mm


def test_channel_missing_in_the_file_is_none_not_an_error(tmp_path):
    path = tmp_path / "old.parquet"
    write_lap(path, with_extended=False)
    series = load_lap(path, ["susp_travel_fl", "speed"])
    assert channel_values(series, "susp_travel_fl") is None
    assert channel_values(series, "speed") is not None


def test_tire_pressure_in_bar_is_shown_as_kpa():
    from server.telemetry_channels import _to_kpa
    assert _to_kpa(np.array([1.5, 1.6]))[0] == pytest.approx(150.0)
    assert _to_kpa(np.array([180.0, 190.0]))[0] == pytest.approx(180.0)


def test_temperatures_already_in_celsius_are_not_shifted_again():
    from server.telemetry_channels import _to_celsius
    assert _to_celsius(np.array([80.0, 90.0]))[0] == pytest.approx(80.0)
    assert _to_celsius(np.array([353.15]))[0] == pytest.approx(80.0)


def test_every_catalog_channel_has_a_unique_key_and_known_inputs():
    keys = list(CATALOG)
    assert len(keys) == len(set(keys))
    from client.telemetry.channels import EXTENDED_COLUMNS
    core = {"t", "lap_dist", "speed", "throttle", "brake", "clutch", "steering", "gear", "rpm", "fuel",
            "pos_x", "pos_y", "pos_z"}
    nested = {f"{prefix}_{pos}" for prefix in ("tire_pressure", "tire_temp_mean", "brake_temp") for pos in ("fl", "fr", "rl", "rr")}
    known = core | nested | set(EXTENDED_COLUMNS)
    for key, channel in CATALOG.items():
        for column in channel.needs:
            assert column in known, f"channel {key} needs a column nothing records: {column}"


# ---------------------------------------------------------------------------
# Grid, resampling, delta, sectors
# ---------------------------------------------------------------------------

def test_make_grid_clamps_the_step_and_caps_the_point_count():
    grid, step = make_grid(0.0, 5000.0, 0.01)
    assert step >= 0.5 and len(grid) <= 8000
    grid, step = make_grid(0.0, 100.0, 500.0)
    assert step == 20.0
    with pytest.raises(AnalysisError):
        make_grid(0.0, 5.0, 2.0)


def test_discrete_channels_keep_whole_values_continuous_ones_interpolate(tmp_path):
    path = tmp_path / "lap.parquet"
    write_lap(path)
    series = load_lap(path, ["gear", "speed"])
    grid, _ = make_grid(float(series.dist[0]), float(series.dist[-1]), 1.7)
    gears = resample(series, channel_values(series, "gear"), grid, discrete=True)
    assert set(np.unique(gears)) <= {4.0, 6.0}
    native = channel_values(series, "speed")
    speeds = resample(series, native, grid, discrete=False)
    # Linear interpolation produces values BETWEEN the recorded samples (at the
    # speed transitions); the discrete path above can only ever return recorded ones.
    recorded = set(np.round(native, 6))
    assert any(round(float(v), 6) not in recorded for v in speeds)
    assert speeds.min() >= native.min() - 1e-9 and speeds.max() <= native.max() + 1e-9


def test_delta_end_matches_the_lap_time_difference_and_is_positive_for_the_slower_lap(tmp_path):
    fast, slow = tmp_path / "fast.parquet", tmp_path / "slow.parquet"
    write_lap(fast, lap_time=85.0, seed=1)
    write_lap(slow, lap_time=90.0, seed=2)
    a = load_lap(slow, ["speed"])   # A = the slower one
    b = load_lap(fast, ["speed"])
    lo, hi = common_range(a, b)
    grid, _ = make_grid(lo, hi, 2.0)
    delta = delta_series(a, b, grid)
    assert delta[0] == 0.0
    assert delta[-1] == pytest.approx(5.0, abs=0.3)        # A is ~5 s behind at the line
    assert np.all(np.diff(delta) > -0.05) or delta[-1] > 4  # it loses time over the lap
    assert delta_series(b, a, grid)[-1] == pytest.approx(-5.0, abs=0.3)


def test_sector_distances_follow_the_reported_sector_times(tmp_path):
    path = tmp_path / "lap.parquet"
    write_lap(path, lap_time=90.0)
    series = load_lap(path, ["speed"])
    d1, d2 = sector_distances(series, [30.0, 30.0, 30.0])
    assert 0 < d1 < d2 < series.dist[-1]
    assert sector_distances(series, [0.0, 0.0, 0.0]) == []
    assert sector_distances(series, [30.0]) == []
    assert sector_distances(series, [60.0, 60.0, 60.0]) == []     # does not fit a 90 s lap


# ---------------------------------------------------------------------------
# API, end to end
# ---------------------------------------------------------------------------

def _register(client, name):
    resp = client.post("/accounts/register", params={"display_name": name})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _headers(account):
    return {"Authorization": f"Bearer {account['auth_token']}"}


def _upload(client, account, *, lap_time=90.0, track="Le Mans", car_model="Ferrari 499P", car_class="Hypercar",
            claim_lap_time=None, lap_number=1, client_version=None, **lap_kwargs):
    """Writes a synthetic lap exactly as the recorder would and uploads it through the
    real Uploader (signature, envelope, field allowlist) — returns the upload response JSON.
    `client_version` makes the upload claim to come from that client version (e.g. "0.8.6"
    for an old client; pair it with with_extended=False for the matching old file format)."""
    from unittest import mock

    import client.uploader.uploader as uploader_module
    from client.telemetry.recorder import LapRecorder
    from client.uploader.uploader import Uploader

    version_patch = mock.patch.object(uploader_module, "CLIENT_VERSION", client_version or uploader_module.CLIENT_VERSION)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        df = write_lap(tmp_dir / "lap.parquet", lap_time=lap_time, **lap_kwargs)
        claimed = claim_lap_time if claim_lap_time is not None else lap_time
        meta = {
            "track_name": track, "car_name": f"{car_model} #7", "car_class": car_class, "car_model": car_model,
            "session_type": 10, "lap_number": lap_number, "lap_time": claimed,
            "sector_times": [claimed / 3, claimed / 3, claimed / 3], "is_valid": True, "started_at": 0.0,
            "recorded_at": 1700000000, "ambient_temp": 22.0, "track_temp": 30.0, "sample_count": len(df),
            "telemetry_file": "lap.parquet", "uploaded": False,
        }
        meta_path = tmp_dir / "lap.json"
        meta_path.write_text(json.dumps(meta), encoding="utf-8")
        uploader = Uploader(
            recorder=LapRecorder(data_dir=str(tmp_dir)),
            auth_token=account["auth_token"], client_secret=account["client_secret"],
        )
        with version_patch:
            payload = uploader._build_payload(meta_path)
        assert payload["envelope"]["client_version"] == (client_version or uploader_module.CLIENT_VERSION)
        resp = client.post(
            "/telemetry/upload",
            data={"envelope": json.dumps(payload["envelope"]), "signature": payload["signature"]},
            files={"telemetry": ("lap.parquet", payload["telemetry_path"].read_bytes())},
            headers=_headers(account),
        )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _lap_row(lap_id):
    from server.database import SessionLocal
    from server.models import Lap
    with SessionLocal() as db:
        return db.get(Lap, lap_id)


def test_channel_catalog_is_public(client):
    resp = client.get("/telemetry/analysis/channels")
    assert resp.status_code == 200
    body = resp.json()
    keys = [c["key"] for c in body["channels"]]
    assert "speed" in keys and "susp_travel_fl" in keys and "abs_level" in keys
    assert set(body["default"]) <= set(keys)
    assert all({"key", "label", "unit", "group", "discrete"} <= set(c) for c in body["channels"])


def test_telemetry_endpoints_need_a_signed_in_driver(client):
    assert client.get("/telemetry/analysis/compare", params={"lap_a": 1}).status_code == 401
    assert client.get("/telemetry/analysis/laps/1").status_code == 401
    assert client.get("/telemetry/analysis/drivers/1/laps").status_code == 401
    assert client.get("/telemetry/analysis/circuits/outline", params={"track_name": "Le Mans"}).status_code == 401


def test_compare_against_another_drivers_lap_end_to_end(client):
    me, rival = _register(client, "Me"), _register(client, "Rival")
    mine = _upload(client, me, lap_time=90.0, seed=1)["lap_id"]
    theirs = _upload(client, rival, lap_time=85.0, seed=2)["lap_id"]

    resp = client.get(
        "/telemetry/analysis/compare",
        params={"lap_a": mine, "lap_b": theirs, "channels": "speed,lat_g,steering,susp_travel_fl,tc_level,gear"},
        headers=_headers(me),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    grid = body["grid"]
    assert grid["count"] > 500 and grid["step"] >= 0.5
    assert body["laps"]["a"]["driver_name"] == "Me" and body["laps"]["b"]["driver_name"] == "Rival"
    assert body["unavailable"] == []

    # Every series has exactly one value per grid point, for both laps.
    for key, entry in body["channels"].items():
        assert len(entry["a"]) == grid["count"] and len(entry["b"]) == grid["count"], key
    assert len(body["delta"]) == grid["count"]
    assert len(body["time"]["a"]) == len(body["time"]["b"]) == grid["count"]

    # I am 5 s slower: the delta ends near +5 s (positive = A behind).
    assert body["delta"][0] == 0.0
    assert body["delta"][-1] == pytest.approx(5.0, abs=0.4)

    # Physics came out the right way round.
    assert max(v for v in body["channels"]["lat_g"]["a"] if v is not None) > 0.5
    assert min(v for v in body["channels"]["lat_g"]["a"] if v is not None) > -0.1
    assert set(body["channels"]["gear"]["a"]) <= {4.0, 6.0}
    assert body["channels"]["gear"]["discrete"] is True
    assert body["channels"]["speed"]["unit"] == "km/h"

    assert body["map"]["mirrored"] is False
    assert len(body["map"]["x_a"]) == len(body["map"]["x_b"]) == grid["count"]
    assert body["orientation"]["a"]["decided_from_data"]["lat_g"] is True
    assert len(body["sectors"]["a"]) == 2 and len(body["sectors"]["b"]) == 2


def test_compare_detects_a_mirrored_world_and_reports_it(client):
    me = _register(client, "Me")
    a = _upload(client, me, lap_time=90.0, world_mirrored=True, seed=3)["lap_id"]
    b = _upload(client, me, lap_time=91.0, world_mirrored=True, seed=4, lap_number=2)["lap_id"]
    body = client.get("/telemetry/analysis/compare", params={"lap_a": a, "lap_b": b, "channels": "speed"},
                      headers=_headers(me)).json()
    assert body["map"]["mirrored"] is True
    assert body["map"]["a_decided"] is True


def test_compare_a_single_lap(client):
    me = _register(client, "Me")
    lap = _upload(client, me, seed=5)["lap_id"]
    resp = client.get("/telemetry/analysis/compare", params={"lap_a": lap, "channels": "speed,throttle"}, headers=_headers(me))
    assert resp.status_code == 200
    body = resp.json()
    assert body["delta"] is None and body["laps"]["b"] is None
    assert body["channels"]["speed"]["b"] is None
    assert body["channels"]["speed"]["a"] is not None


def test_old_format_laps_report_missing_channels_instead_of_failing(client):
    me = _register(client, "Me")
    old = _upload(client, me, with_extended=False, seed=6)["lap_id"]
    new = _upload(client, me, with_extended=True, seed=7, lap_number=2)["lap_id"]
    body = client.get("/telemetry/analysis/compare",
                      params={"lap_a": old, "lap_b": new, "channels": "speed,susp_travel_fl,brake_temp_fl"},
                      headers=_headers(me)).json()
    assert body["unavailable"] == ["susp_travel_fl"]
    assert body["channels"]["susp_travel_fl"]["a"] is None
    assert body["channels"]["susp_travel_fl"]["b"] is not None
    assert body["channels"]["brake_temp_fl"]["a"] is not None      # present in the basic format too
    info = client.get(f"/telemetry/analysis/laps/{old}", headers=_headers(me)).json()
    assert "speed" in info["available_channels"] and "susp_travel_fl" not in info["available_channels"]


def test_other_drivers_invalid_lap_is_hidden_but_my_own_is_not(client):
    me, rival = _register(client, "Me"), _register(client, "Rival")
    invalid = _upload(client, rival, lap_time=90.0, claim_lap_time=60.0, seed=8)["lap_id"]     # claims 60 s, telemetry says 90
    assert _lap_row(invalid).is_valid is False
    mine_ok = _upload(client, me, seed=9)["lap_id"]

    assert client.get("/telemetry/analysis/compare", params={"lap_a": mine_ok, "lap_b": invalid, "channels": "speed"},
                      headers=_headers(me)).status_code == 404
    assert client.get(f"/telemetry/analysis/laps/{invalid}", headers=_headers(me)).status_code == 404
    assert client.get(f"/telemetry/analysis/laps/{invalid}", headers=_headers(rival)).status_code == 200


def test_laps_from_different_tracks_cannot_be_compared(client):
    me = _register(client, "Me")
    a = _upload(client, me, track="Le Mans", seed=10)["lap_id"]
    b = _upload(client, me, track="Monza", seed=11, lap_number=2)["lap_id"]
    resp = client.get("/telemetry/analysis/compare", params={"lap_a": a, "lap_b": b, "channels": "speed"}, headers=_headers(me))
    assert resp.status_code == 400
    assert "different tracks" in resp.json()["detail"]


def test_bad_channel_requests_are_rejected(client):
    me = _register(client, "Me")
    lap = _upload(client, me, seed=12)["lap_id"]
    h = _headers(me)
    assert client.get("/telemetry/analysis/compare", params={"lap_a": lap, "channels": "speed,warp_drive"}, headers=h).status_code == 400
    too_many = ",".join(list(CATALOG)[:25])
    assert client.get("/telemetry/analysis/compare", params={"lap_a": lap, "channels": too_many}, headers=h).status_code == 400
    assert client.get("/telemetry/analysis/compare", params={"lap_a": 999999}, headers=h).status_code == 404
    assert client.get("/telemetry/analysis/compare", params={"lap_a": 0}, headers=h).status_code == 422


def test_a_lap_whose_file_is_gone_says_so(client):
    me = _register(client, "Me")
    lap_id = _upload(client, me, seed=13)["lap_id"]
    Path(_lap_row(lap_id).telemetry_path).unlink()
    resp = client.get("/telemetry/analysis/compare", params={"lap_a": lap_id}, headers=_headers(me))
    assert resp.status_code == 404
    assert "no longer stored" in resp.json()["detail"]


def test_a_telemetry_path_outside_the_storage_directory_is_never_read(client, tmp_path):
    me = _register(client, "Me")
    lap_id = _upload(client, me, seed=14)["lap_id"]
    outside = tmp_path / "outside.parquet"
    write_lap(outside)
    from server.database import SessionLocal
    from server.models import Lap
    with SessionLocal() as db:
        db.get(Lap, lap_id).telemetry_path = str(outside)
        db.commit()
    resp = client.get("/telemetry/analysis/compare", params={"lap_a": lap_id}, headers=_headers(me))
    assert resp.status_code == 404


def test_leaderboard_rows_carry_the_lap_id_that_can_be_compared(client):
    me, rival = _register(client, "Me"), _register(client, "Rival")
    mine = _upload(client, me, lap_time=90.0, seed=15)["lap_id"]
    theirs = _upload(client, rival, lap_time=85.0, seed=16)["lap_id"]
    board = client.get("/leaderboard/class/Le%20Mans/Hypercar").json()
    assert [row["driver_name"] for row in board] == ["Rival", "Me"]
    assert board[0]["lap_id"] == theirs and board[1]["lap_id"] == mine
    car_board = client.get("/leaderboard/car/Le%20Mans/Ferrari%20499P").json()
    assert car_board[0]["lap_id"] == theirs
    # "Compare me against the number one" — the whole feature in two calls.
    top = board[0]["lap_id"]
    resp = client.get("/telemetry/analysis/compare", params={"lap_a": mine, "lap_b": top, "channels": "speed"}, headers=_headers(me))
    assert resp.status_code == 200
    assert resp.json()["laps"]["b"]["driver_name"] == "Rival"


def test_a_drivers_lap_list_hides_invalid_laps_from_others(client):
    me, rival = _register(client, "Me"), _register(client, "Rival")
    good = _upload(client, rival, lap_time=90.0, seed=17)["lap_id"]
    _upload(client, rival, lap_time=90.0, claim_lap_time=50.0, seed=18, lap_number=2)
    seen_by_me = client.get(f"/telemetry/analysis/drivers/{rival['driver_id']}/laps", headers=_headers(me)).json()
    assert [lap["id"] for lap in seen_by_me["laps"]] == [good]
    seen_by_owner = client.get(f"/telemetry/analysis/drivers/{rival['driver_id']}/laps", headers=_headers(rival)).json()
    assert len(seen_by_owner["laps"]) == 2
    assert client.get("/telemetry/analysis/drivers/99999/laps", headers=_headers(me)).status_code == 404
    filtered = client.get(f"/telemetry/analysis/drivers/{rival['driver_id']}/laps",
                          params={"track_name": "Nowhere"}, headers=_headers(me)).json()
    assert filtered["laps"] == []


def test_circuit_cards_and_outline(client):
    me, rival = _register(client, "Me"), _register(client, "Rival")
    _upload(client, me, lap_time=90.0, seed=19)
    fastest = _upload(client, rival, lap_time=85.0, seed=20)["lap_id"]

    cards = client.get("/telemetry/analysis/circuits", params={"q": "le mans"}).json()["venues"]
    card = next(c for c in cards if any(l["track_name"] == "Le Mans" for l in c["layouts"]))
    layout = next(l for l in card["layouts"] if l["track_name"] == "Le Mans")
    assert layout["lap_count"] == 2 and layout["driver_count"] == 2
    assert layout["classes"] == ["Hypercar"]
    assert layout["fastest"]["lap_id"] == fastest
    assert layout["fastest"]["driver_name"] == "Rival" and layout["fastest"]["lap_time"] == 85.0
    assert client.get("/telemetry/analysis/circuits", params={"q": "zzz-nothing"}).json()["venues"] == []

    outline = client.get("/telemetry/analysis/circuits/outline", params={"track_name": "Le Mans"}, headers=_headers(me))
    assert outline.status_code == 200
    body = outline.json()
    assert body["source_lap_id"] == fastest
    assert body["length_m"] == pytest.approx(2 * 900 + 2 * np.pi * 120, rel=0.01)
    assert 100 < len(body["x"]) <= 700 and len(body["x"]) == len(body["y"])
    assert body["mirrored"] is False
    assert len(body["sectors"]) == 2
    again = client.get("/telemetry/analysis/circuits/outline", params={"track_name": "Le Mans"}, headers=_headers(me))
    assert again.json() == body                                       # served from the cache, identical

    assert client.get("/telemetry/analysis/circuits/outline", params={"track_name": "Unknown"}, headers=_headers(me)).status_code == 404


def test_analysis_is_rate_limited(client, monkeypatch):
    from server.config import settings
    me = _register(client, "Me")
    lap = _upload(client, me, seed=21)["lap_id"]
    monkeypatch.setitem(settings.rate_limits, "analysis", (2, 3600.0))
    h = _headers(me)
    assert client.get("/telemetry/analysis/compare", params={"lap_a": lap, "channels": "speed"}, headers=h).status_code == 200
    assert client.get("/telemetry/analysis/compare", params={"lap_a": lap, "channels": "speed"}, headers=h).status_code == 200
    assert client.get("/telemetry/analysis/compare", params={"lap_a": lap, "channels": "speed"}, headers=h).status_code == 429


# ---------------------------------------------------------------------------
# Old clients (< 0.9.0): still accepted, still comparable — and the UI is told
# that their laps carry fewer channels.
# ---------------------------------------------------------------------------

def test_data_level_from_client_version():
    from server.versioning import FULL_DATA_CLIENT_VERSION, data_level_for_client_version
    assert FULL_DATA_CLIENT_VERSION == "0.9.0"
    assert data_level_for_client_version("0.8.6") == "basic"
    assert data_level_for_client_version("0.8.10") == "basic"
    assert data_level_for_client_version("0.9.0") == "full"
    assert data_level_for_client_version("0.9.1") == "full"
    assert data_level_for_client_version("1.0.0") == "full"
    assert data_level_for_client_version("0.0.0") == "basic"
    assert data_level_for_client_version("garbage") == "basic"
    assert data_level_for_client_version(None) == "basic"


def test_old_client_laps_are_accepted_and_labelled_basic_everywhere(client):
    old_driver, new_driver = _register(client, "OldClient"), _register(client, "NewClient")
    old = _upload(client, old_driver, lap_time=90.0, seed=30, client_version="0.8.6", with_extended=False)
    new = _upload(client, new_driver, lap_time=91.0, seed=31)
    assert old["status"] == "ok" and old["duplicate"] is False          # the old wire format is still accepted
    assert _lap_row(old["lap_id"]).is_valid is True                      # ... and validated like before
    assert _lap_row(old["lap_id"]).client_version == "0.8.6"

    mine = client.get("/telemetry/laps", headers=_headers(old_driver)).json()
    assert mine[0]["data_level"] == "basic"
    assert client.get("/telemetry/laps", headers=_headers(new_driver)).json()[0]["data_level"] == "full"

    board = {row["driver_name"]: row for row in client.get("/leaderboard/class/Le%20Mans/Hypercar").json()}
    assert board["OldClient"]["data_level"] == "basic" and board["NewClient"]["data_level"] == "full"
    car_board = {row["driver_name"]: row for row in client.get("/leaderboard/car/Le%20Mans/Ferrari%20499P").json()}
    assert car_board["OldClient"]["data_level"] == "basic"

    lap_list = client.get(f"/telemetry/analysis/drivers/{old_driver['driver_id']}/laps", headers=_headers(new_driver)).json()
    assert [lap["data_level"] for lap in lap_list["laps"]] == ["basic"]


def test_compare_reports_what_each_lap_was_recorded_with(client):
    me = _register(client, "Me")
    old = _upload(client, me, lap_time=90.0, seed=32, client_version="0.8.6", with_extended=False)["lap_id"]
    new = _upload(client, me, lap_time=89.0, seed=33, lap_number=2)["lap_id"]

    body = client.get("/telemetry/analysis/compare", params={"lap_a": old, "lap_b": new, "channels": "speed,susp_travel_fl"},
                      headers=_headers(me)).json()
    a, b = body["recording"]["a"], body["recording"]["b"]
    assert a["level"] == "basic" and a["client_version"] == "0.8.6"
    # the lap was uploaded by the current client (V0.9.1: no longer a literal "0.9.0", which
    # only ever meant "the version this test ran under")
    from client.uploader.uploader import CLIENT_VERSION
    assert b["level"] == "full" and b["client_version"] == CLIENT_VERSION
    assert a["channels_total"] == b["channels_total"] == len(CATALOG)
    assert 0 < a["channels_available"] < b["channels_available"] <= b["channels_total"]
    assert body["unavailable"] == ["susp_travel_fl"]

    single = client.get("/telemetry/analysis/compare", params={"lap_a": old, "channels": "speed"}, headers=_headers(me)).json()
    assert single["recording"]["a"]["level"] == "basic" and single["recording"]["b"] is None

    info = client.get(f"/telemetry/analysis/laps/{old}", headers=_headers(me)).json()
    assert info["recording"]["level"] == "basic"
    assert client.get(f"/telemetry/analysis/laps/{new}", headers=_headers(me)).json()["recording"]["level"] == "full"


def test_the_file_decides_not_the_version_label(client):
    """The version is only a label sent by the client; what the charts are drawn from is the
    file, so the verdict in the analysis follows the file."""
    me = _register(client, "Me")
    mislabelled_old = _upload(client, me, seed=34, client_version="0.8.6", with_extended=True)["lap_id"]
    mislabelled_new = _upload(client, me, seed=35, lap_number=2, client_version="0.9.0", with_extended=False)["lap_id"]
    h = _headers(me)
    assert client.get(f"/telemetry/analysis/laps/{mislabelled_old}", headers=h).json()["recording"]["level"] == "full"
    assert client.get(f"/telemetry/analysis/laps/{mislabelled_new}", headers=h).json()["recording"]["level"] == "basic"
    # ... while the cheap list view can only go by the label:
    laps = {lap["id"]: lap["data_level"] for lap in client.get("/telemetry/laps", headers=h).json()}
    assert laps[mislabelled_old] == "basic" and laps[mislabelled_new] == "full"


def test_two_old_client_laps_can_still_be_compared_on_the_basic_channels(client):
    me, rival = _register(client, "Me"), _register(client, "Rival")
    a = _upload(client, me, lap_time=90.0, seed=36, client_version="0.8.6", with_extended=False)["lap_id"]
    b = _upload(client, rival, lap_time=86.0, seed=37, client_version="0.8.6", with_extended=False)["lap_id"]
    body = client.get("/telemetry/analysis/compare",
                      params={"lap_a": a, "lap_b": b, "channels": "speed,throttle,brake,steering,gear,rpm,fuel,tire_pressure_fl,brake_temp_fl"},
                      headers=_headers(me)).json()
    assert body["unavailable"] == []
    assert body["delta"][-1] == pytest.approx(4.0, abs=0.4)
    assert body["recording"]["a"]["level"] == body["recording"]["b"]["level"] == "basic"
    # Position data is part of the basic format, so the map works too.
    assert body["map"]["x_a"] is not None and body["map"]["mirrored"] is False

