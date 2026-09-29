"""Tests for V0.7.2 §9.4 (GET /teams/{id}/stats windows) and §9.5
(catalog/cars car_class filter — the third cascade level)."""

from __future__ import annotations

import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd


def _register(client, name):
    resp = client.post("/accounts/register", params={"display_name": name})
    assert resp.status_code == 200
    return resp.json()


def _auth(account):
    return {"Authorization": f"Bearer {account['auth_token']}"}


def _create_team(client, owner, name):
    resp = client.post("/teams", json={"name": name}, headers=_auth(owner))
    assert resp.status_code == 200
    return resp.json()


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


# --- §9.4: team stats windows -------------------------------------------

def test_team_stats_all_time_counts_everything(client):
    owner = _register(client, "Owner")
    team = _create_team(client, owner, "Stats Team")
    _upload(client, owner, lap_time=90.0)
    _upload(client, owner, lap_time=88.0)

    resp = client.get(f"/teams/{team['id']}/stats", params={"window": "all"}, headers=_auth(owner))
    assert resp.status_code == 200
    body = resp.json()
    assert body["window"] == "all"
    assert body["laps"] == 2
    assert body["active_drivers"] == 1


def test_team_stats_defaults_to_all_time(client):
    owner = _register(client, "Owner")
    team = _create_team(client, owner, "Default Window Team")
    _upload(client, owner)

    resp = client.get(f"/teams/{team['id']}/stats", headers=_auth(owner))
    assert resp.status_code == 200
    assert resp.json()["window"] == "all"


def test_team_stats_today_excludes_old_laps(client):
    owner = _register(client, "Owner")
    team = _create_team(client, owner, "Old Laps Team")
    _upload(client, owner, lap_time=90.0)

    # Backdate the lap's uploaded_at (the field stats actually filters on)
    # well outside the "today" window.
    from server.database import SessionLocal
    from server.models import Lap
    db = SessionLocal()
    try:
        lap = db.query(Lap).filter(Lap.driver_id == owner["driver_id"]).one()
        lap.uploaded_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=5)
        db.add(lap)
        db.commit()
    finally:
        db.close()

    today = client.get(f"/teams/{team['id']}/stats", params={"window": "today"}, headers=_auth(owner)).json()
    assert today["laps"] == 0

    all_time = client.get(f"/teams/{team['id']}/stats", params={"window": "all"}, headers=_auth(owner)).json()
    assert all_time["laps"] == 1


def test_team_stats_rejects_invalid_window(client):
    owner = _register(client, "Owner")
    team = _create_team(client, owner, "Invalid Window Team")
    resp = client.get(f"/teams/{team['id']}/stats", params={"window": "last-week"}, headers=_auth(owner))
    assert resp.status_code == 422


def test_team_stats_requires_membership(client):
    owner = _register(client, "Owner")
    team = _create_team(client, owner, "Private Stats Team")
    outsider = _register(client, "Outsider")
    resp = client.get(f"/teams/{team['id']}/stats", headers=_auth(outsider))
    assert resp.status_code == 403


def test_team_stats_pbs_and_records_reflect_actual_events(client):
    owner = _register(client, "Owner")
    team = _create_team(client, owner, "PB Team")
    mate = _register(client, "Mate")
    join = client.post("/teams/join", json={"invite_code": team["invite_code"]}, headers=_auth(mate))
    assert join.status_code == 200

    _upload(client, owner, lap_time=100.0)  # PR + TEAM_BEST
    stats = client.get(f"/teams/{team['id']}/stats", headers=_auth(owner)).json()
    assert stats["pbs"] == 1
    assert stats["records"] == 1

    _upload(client, mate, lap_time=95.0)  # PR + new TEAM_BEST
    stats2 = client.get(f"/teams/{team['id']}/stats", headers=_auth(owner)).json()
    assert stats2["pbs"] == 2
    assert stats2["records"] == 2


# --- §9.5: catalog/cars car_class filter (cascade third level) ----------

def test_catalog_cars_scoped_to_class_excludes_other_classes_same_track(client):
    owner = _register(client, "Owner")
    _upload(client, owner, track="Le Mans", car_class="Hypercar", car_model="Ferrari 499P", lap_time=200.0)
    _upload(client, owner, track="Le Mans", car_class="GT3", car_model="Porsche 911 GT3 R", lap_time=210.0)

    hypercar_models = client.get(
        "/leaderboard/catalog/cars", params={"track_name": "Le Mans", "car_class": "Hypercar"}
    ).json()
    assert "Ferrari 499P" in hypercar_models
    assert "Porsche 911 GT3 R" not in hypercar_models

    gt3_models = client.get(
        "/leaderboard/catalog/cars", params={"track_name": "Le Mans", "car_class": "GT3"}
    ).json()
    assert "Porsche 911 GT3 R" in gt3_models
    assert "Ferrari 499P" not in gt3_models


def test_catalog_cars_without_class_filter_still_returns_all_track_models(client):
    """Backward compatible: car_class is optional, omitting it keeps the
    pre-V0.7.2 track-only behavior (used by the public leaderboard page,
    which doesn't cascade through class)."""
    owner = _register(client, "Owner")
    _upload(client, owner, track="Spa", car_class="Hypercar", car_model="Toyota GR010", lap_time=150.0)
    _upload(client, owner, track="Spa", car_class="GT3", car_model="BMW M4 GT3", lap_time=160.0)

    models = client.get("/leaderboard/catalog/cars", params={"track_name": "Spa"}).json()
    assert "Toyota GR010" in models
    assert "BMW M4 GT3" in models
