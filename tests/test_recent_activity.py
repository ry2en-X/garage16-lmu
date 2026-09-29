"""Tests for GET /leaderboard/recent — the landing page's recent-activity
feed (V0.7.2)."""

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


def _upload(client, account, track="Monza", lap_time=90.0, is_valid=True):
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
            "track_name": track, "car_name": "car", "car_class": "GT3", "car_model": "911 GT3 R",
            "session_type": 10, "lap_number": 1, "lap_time": lap_time,
            "sector_times": [lap_time / 3] * 3, "is_valid": is_valid, "started_at": 0.0,
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
    return resp


def test_recent_activity_is_public_no_auth(client):
    resp = client.get("/leaderboard/recent")
    assert resp.status_code == 200


def test_recent_activity_shows_uploaded_laps_newest_first(client):
    account = _register(client, "RecentDriver")
    _upload(client, account, track="Monza", lap_time=90.0)
    _upload(client, account, track="Spa", lap_time=95.0)

    entries = client.get("/leaderboard/recent").json()
    tracks = [e["track_name"] for e in entries[:2]]
    assert "Spa" in tracks and "Monza" in tracks
    assert entries[0]["driver_name"] == "RecentDriver"


def test_recent_activity_excludes_invalid_laps(client):
    account = _register(client, "InvalidDriver")
    _upload(client, account, track="Nurburgring", lap_time=90.0)

    from server.database import SessionLocal
    from server.models import Lap
    db = SessionLocal()
    try:
        lap = db.query(Lap).filter(Lap.driver_id == account["driver_id"]).one()
        lap.is_valid = False
        db.add(lap)
        db.commit()
    finally:
        db.close()

    entries = client.get("/leaderboard/recent").json()
    assert not any(e["driver_name"] == "InvalidDriver" for e in entries)


def test_recent_activity_respects_limit(client):
    account = _register(client, "LimitDriver")
    for i in range(5):
        _upload(client, account, track=f"Track{i}", lap_time=80.0 + i)
    entries = client.get("/leaderboard/recent", params={"limit": 3}).json()
    assert len(entries) <= 3
