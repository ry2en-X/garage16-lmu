"""V0.9.7 — the telemetry health check: each check reacts to the fault it is there for, and only to
that, on reference recordings (tests/helpers/reference_lap.py) and on full synthetic laps."""

from __future__ import annotations

import pytest

from server.telemetry_health import file_health, report
from tests.helpers import reference_lap as R
from tests.helpers.synthetic_lap import make_lap
from tests.helpers.v093 import headers, register, upload_df


@pytest.fixture(scope="module")
def ref():
    return R.simulate(R.TRACK_M, R.LAP_A)


def _health(tmp_path, df, lap_time):
    path = tmp_path / "lap.parquet"
    df.to_parquet(path, index=False)
    out = file_health(path, lap_time)
    return {c["key"]: c for c in out["checks"]}


def test_a_clean_recording_passes_every_data_check(tmp_path, ref):
    checks = _health(tmp_path, R.record(ref), ref.lap_time)
    for key in ("nan", "samples", "gaps", "time", "distance", "axis", "speed_distance", "jumps", "resolution", "duration"):
        assert checks[key]["status"] == "ok", (key, checks[key])
    s = checks["samples"]["values"]
    assert 49 <= s["frame_rate_hz"] <= 51, "50 Hz frames"
    assert 0.15 <= s["duplicate_share"] <= 0.2, "60 Hz polls of 50 Hz frames repeat every 6th row"
    assert 4.5 <= checks["axis"]["values"]["anchor_rate_hz"] <= 5.5, "lap_dist refreshed at 5 Hz"
    assert abs(checks["speed_distance"]["values"]["ratio"] - 1.0) < 0.005
    assert abs(checks["duration"]["values"]["difference_s"]) < 0.05
    # 260 km/h / 50 Hz = 1.44 m between frames at top speed
    assert 0.9 <= checks["resolution"]["values"]["metres"] <= 1.5
    # a basic recording: the channel check says so (only a part of the catalog is in the file)
    assert checks["channels"]["values"]["level"] == "basic" and checks["channels"]["status"] == "warn"


@pytest.mark.parametrize("fault,kw,key,status", [
    ("drop", dict(at=1500.0, seconds=0.6), "gaps", "warn"),
    ("drop", dict(at=1500.0, seconds=2.0), "gaps", "bad"),
    ("nan", dict(at=1500.0, rows=200, columns=["speed"]), "nan", "bad"),
    ("restart", dict(at=2500.0), "time", "bad"),
    ("spike", dict(at=1500.0, value=300.0), "jumps", "warn"),
])
def test_each_fault_is_reported_by_its_check(tmp_path, ref, fault, kw, key, status):
    checks = _health(tmp_path, R.inject(R.record(ref), fault, **kw), ref.lap_time)
    assert checks[key]["status"] == status, checks[key]
    if fault == "drop":
        assert abs(checks["gaps"]["values"]["at_m"] - 1500.0) < 50.0, "where the samples are missing"
        assert checks["time"]["status"] == "ok" and checks["distance"]["status"] == "ok"


def test_a_lap_time_that_does_not_fit_the_recording_and_a_late_start_are_flagged(tmp_path, ref):
    checks = _health(tmp_path, R.record(ref), ref.lap_time + 2.0)
    assert checks["duration"]["status"] == "warn"
    assert abs(checks["duration"]["values"]["difference_s"] + 2.0) < 0.05


def test_speed_and_lap_distance_disagreeing_is_flagged(tmp_path, ref):
    df = R.record(ref)
    df["speed"] = df["speed"] * 1.08           # a speed channel 8 % off (wrong unit, wrong wheel radius …)
    assert _health(tmp_path, df, ref.lap_time)["speed_distance"]["status"] == "bad"


def test_a_coarse_recording_has_a_coarse_resolution(tmp_path):
    lap = R.simulate(R.TRACK_M, R.LAP_A)
    df = R.record(lap, poll_hz=None)
    coarse = df.iloc[::6].reset_index(drop=True)          # ~8 frames per second
    checks = _health(tmp_path, coarse, lap.lap_time)
    assert checks["samples"]["status"] == "bad"
    assert checks["resolution"]["status"] == "bad" and checks["resolution"]["values"]["metres"] > 5


def test_a_full_recording_reports_its_channels_complete(tmp_path):
    df = make_lap()
    checks = _health(tmp_path, df, 90.0)
    assert checks["channels"]["values"]["level"] == "full"
    assert checks["channels"]["values"]["missing_count"] == 0


def test_the_endpoint_combines_the_file_check_with_the_lap_verdict_and_respects_access(client):
    owner, other = register(client, "Health Owner"), register(client, "Health Other")
    valid = upload_df(client, owner, make_lap(seed=1), lap_time=90.0)["lap_id"]
    invalid = upload_df(client, owner, make_lap(seed=2), lap_time=90.0, is_valid=False, lap_number=2,
                        recorded_at=1_700_000_100)["lap_id"]
    resp = client.get(f"/telemetry/analysis/laps/{valid}/health", headers=headers(owner))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["lap_id"] == valid and body["status"] in ("good", "limited", "unreliable")
    assert {c["key"] for c in body["checks"]} >= {"channels", "samples", "time", "distance", "axis"}
    assert body["validation"]["is_valid"] is True
    # an invalid lap: only its owner sees it (same rule as every analysis endpoint)
    assert client.get(f"/telemetry/analysis/laps/{invalid}/health", headers=headers(owner)).json()["validation"]["is_valid"] is False
    assert client.get(f"/telemetry/analysis/laps/{invalid}/health", headers=headers(other)).status_code == 404
    assert client.get(f"/telemetry/analysis/laps/{valid}/health").status_code == 401
    # cached: the second answer is the same
    assert client.get(f"/telemetry/analysis/laps/{valid}/health", headers=headers(owner)).json() == body


def test_report_status_is_the_worst_data_check_and_ignores_validity():
    class Lap:
        validation, invalid_reason, is_valid, client_claimed_valid, review_status, client_version = None, ["x"], False, True, None, "0.9.4"
    part = {"checks": [{"key": "a", "status": "ok", "values": {}}, {"key": "b", "status": "warn", "values": {}}]}
    assert report(part, Lap())["status"] == "limited"
    part["checks"][1]["status"] = "ok"
    assert report(part, Lap())["status"] == "good", "an invalid lap can still be a perfect recording"
