"""Tests for V0.7.2 §4 (GET /drivers/{id}/public) and §5 (driver_id now
present on LeaderboardEntry/TeamLeaderboardEntry, for linking a
leaderboard row to its driver's profile)."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd


def _register(client, name):
    resp = client.post("/accounts/register", params={"display_name": name})
    assert resp.status_code == 200
    return resp.json()


def _auth(account):
    return {"Authorization": f"Bearer {account['auth_token']}"}


def _upload(client, account, track="Monza", car_name="GT3 #1", car_class="GT3", car_model="911 GT3 R", lap_time=90.0):
    from client.telemetry.recorder import LapRecorder
    from client.uploader.uploader import Uploader

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        n = 50
        speed = 4500.0 / lap_time
        df = pd.DataFrame({
            "t": np.linspace(0, lap_time, n), "lap_dist": np.linspace(0, 4500, n),
            "speed": np.full(n, speed), "throttle": np.full(n, 0.8), "brake": np.zeros(n),
            "clutch": np.zeros(n), "steering": np.zeros(n), "gear": np.full(n, 4),
            "rpm": np.full(n, 6000.0), "fuel": np.linspace(50, 48, n),
        })
        pp = tmp_dir / "lap.parquet"
        df.to_parquet(pp, index=False)
        meta = {
            "track_name": track, "car_name": car_name, "car_class": car_class, "car_model": car_model,
            "session_type": 10, "lap_number": 1, "lap_time": lap_time,
            "sector_times": [lap_time / 3] * 3, "is_valid": True, "started_at": 0.0,
            "recorded_at": 1700000000, "sample_count": n, "telemetry_file": "lap.parquet", "uploaded": False,
        }
        mp = tmp_dir / "lap.json"
        mp.write_text(json.dumps(meta), encoding="utf-8")
        uploader = Uploader(recorder=LapRecorder(data_dir=str(tmp_dir)), auth_token=account["auth_token"], client_secret=account["client_secret"])
        payload = uploader._build_payload(mp)
        resp = client.post(
            "/telemetry/upload",
            data={"envelope": json.dumps(payload["envelope"]), "signature": payload["signature"]},
            files={"telemetry": ("lap.parquet", payload["telemetry_path"].read_bytes())},
            headers={"Authorization": f"Bearer {account['auth_token']}"},
        )
    assert resp.status_code == 200
    return resp.json()


# --- §4: public driver profile -------------------------------------------

def test_profile_is_public_no_auth_required(client):
    account = _register(client, "Nito")
    resp = client.get(f"/drivers/{account['driver_id']}/public")
    assert resp.status_code == 200
    assert resp.json()["display_name"] == "Nito"


def test_profile_404s_for_nonexistent_driver(client):
    resp = client.get("/drivers/999999/public")
    assert resp.status_code == 404


def test_profile_counts_reflect_actual_laps(client):
    account = _register(client, "Counter")
    _upload(client, account, track="Monza", lap_time=90.0)
    _upload(client, account, track="Spa", lap_time=95.0)

    profile = client.get(f"/drivers/{account['driver_id']}/public").json()
    assert profile["total_laps"] == 2
    assert profile["tracks_driven"] == 2
    assert profile["cars_driven"] == 1


def test_profile_personal_records_grouped_by_track_and_car_model(client):
    account = _register(client, "PBHolder")
    _upload(client, account, track="Monza", car_model="911 GT3 R", lap_time=100.0)
    _upload(client, account, track="Monza", car_model="911 GT3 R", lap_time=95.0)  # improves the same PR
    _upload(client, account, track="Monza", car_model="Ferrari 296 GT3", lap_time=200.0)  # different car

    profile = client.get(f"/drivers/{account['driver_id']}/public").json()
    records = {r["car_model"]: r["lap_time"] for r in profile["personal_records"]}
    assert records["911 GT3 R"] == 95.0  # the faster of the two, not both listed
    assert records["Ferrari 296 GT3"] == 200.0
    assert len(profile["personal_records"]) == 2


def test_profile_recent_activity_reflects_record_events(client):
    account = _register(client, "ActivityDriver")
    _upload(client, account, lap_time=90.0)

    profile = client.get(f"/drivers/{account['driver_id']}/public").json()
    assert any(a["record_type"] == "PR" for a in profile["recent_activity"])


def test_profile_lists_team_memberships(client):
    owner = _register(client, "TeamOwnerForProfile")
    team = client.post("/teams", json={"name": "Profile Team"}, headers=_auth(owner)).json()

    profile = client.get(f"/drivers/{owner['driver_id']}/public").json()
    assert any(t["team_name"] == "Profile Team" and t["role"] == "owner" for t in profile["teams"])


def test_profile_never_exposes_private_fields(client):
    account = _register(client, "PrivacyCheck")
    resp = client.get(f"/drivers/{account['driver_id']}/public")
    body_text = resp.text
    for secret in [account["auth_token"], account["client_secret"]]:
        assert secret not in body_text
    # None of these keys should exist anywhere in the response at all.
    for forbidden_key in ("email", "password_hash", "auth_token", "client_secret", "is_locked", "token_hash"):
        assert forbidden_key not in resp.json()


def test_profile_with_no_laps_returns_zeros_not_an_error(client):
    account = _register(client, "FreshDriver")
    profile = client.get(f"/drivers/{account['driver_id']}/public").json()
    assert profile["total_laps"] == 0
    assert profile["personal_records"] == []
    assert profile["recent_activity"] == []


# --- §5: leaderboard entries now carry driver_id --------------------------

def test_global_leaderboard_entries_include_driver_id(client):
    account = _register(client, "LeaderboardLinker")
    _upload(client, account, track="Le Mans", car_class="Hypercar")

    entries = client.get("/leaderboard/class/Le%20Mans/Hypercar").json()
    assert len(entries) == 1
    assert entries[0]["driver_id"] == account["driver_id"]


def test_team_leaderboard_entries_include_driver_id(client):
    owner = _register(client, "TeamLeaderboardLinker")
    team = client.post("/teams", json={"name": "Linker Team"}, headers=_auth(owner)).json()
    _upload(client, owner, track="Le Mans", car_class="Hypercar")

    entries = client.get(
        f"/teams/{team['id']}/leaderboard/class/Le%20Mans/Hypercar", headers=_auth(owner)
    ).json()
    assert len(entries) == 1
    assert entries[0]["driver_id"] == owner["driver_id"]
