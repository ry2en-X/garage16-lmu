"""Tests for server/routers/admin.py — moderation endpoints."""

import json
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

ADMIN_HEADERS = {"X-Admin-Token": "test-admin-token"}  # matches conftest.py's LMU_GARAGE_ADMIN_TOKEN


def _register(client, name="Driver"):
    resp = client.post("/accounts/register", params={"display_name": name})
    assert resp.status_code == 200
    return resp.json()


def _upload_lap(client, account, lap_time=90.0):
    from client.telemetry.recorder import LapRecorder
    from client.uploader.uploader import Uploader

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        n = 50
        df = pd.DataFrame({
            "t": np.linspace(0, lap_time, n), "lap_dist": np.linspace(0, 4500, n),
            "speed": np.full(n, 50.0), "throttle": np.full(n, 0.8), "brake": np.zeros(n),
            "clutch": np.zeros(n), "steering": np.zeros(n), "gear": np.full(n, 4),
            "rpm": np.full(n, 6000.0), "fuel": np.linspace(50, 48, n),
        })
        parquet_path = tmp_dir / "lap.parquet"
        df.to_parquet(parquet_path, index=False)
        meta_path = tmp_dir / "lap.json"
        meta_path.write_text(json.dumps({
            "track_name": "Monza", "car_name": "GT3 Custom #12", "car_class": "GT3",
            "car_model": "Porsche 911 GT3 R", "session_type": 10,
            "lap_number": 1, "lap_time": lap_time, "sector_times": [30.0, 30.0, 30.0],
            "is_valid": True, "started_at": 0.0, "recorded_at": 1700000000,
            "sample_count": n, "telemetry_file": "lap.parquet", "uploaded": False,
        }), encoding="utf-8")
        uploader = Uploader(
            recorder=LapRecorder(data_dir=str(tmp_dir)),
            auth_token=account["auth_token"], client_secret=account["client_secret"],
        )
        payload = uploader._build_payload(meta_path)
        resp = client.post(
            "/telemetry/upload",
            data={"envelope": json.dumps(payload["envelope"]), "signature": payload["signature"]},
            files={"telemetry": ("lap.parquet", payload["telemetry_path"].read_bytes())},
            headers={"Authorization": f"Bearer {account['auth_token']}"},
        )
    assert resp.status_code == 200
    return resp.json()["lap_id"]


def test_admin_endpoints_require_token(client):
    resp = client.get("/admin/reports")
    assert resp.status_code == 403


def test_admin_endpoints_503_when_no_admin_token_configured(client):
    from server import config
    original = config.settings.admin_token
    config.settings.admin_token = None
    try:
        resp = client.get("/admin/reports", headers={"X-Admin-Token": "anything"})
        assert resp.status_code == 503
    finally:
        config.settings.admin_token = original


def test_lock_driver_blocks_further_authenticated_requests(client):
    account = _register(client)
    resp = client.post(f"/admin/drivers/{account['driver_id']}/lock", json={"reason": "suspicious activity"}, headers=ADMIN_HEADERS)
    assert resp.status_code == 200

    resp = client.get("/telemetry/laps", headers={"Authorization": f"Bearer {account['auth_token']}"})
    assert resp.status_code == 403


def test_unlock_restores_access(client):
    account = _register(client)
    client.post(f"/admin/drivers/{account['driver_id']}/lock", json={"reason": "test"}, headers=ADMIN_HEADERS)
    resp = client.post(f"/admin/drivers/{account['driver_id']}/unlock", headers=ADMIN_HEADERS)
    assert resp.status_code == 200

    resp = client.get("/telemetry/laps", headers={"Authorization": f"Bearer {account['auth_token']}"})
    assert resp.status_code == 200


def test_invalidate_lap_removes_it_from_leaderboard(client):
    account = _register(client)
    lap_id = _upload_lap(client, account)

    resp = client.get("/leaderboard/car/Monza/Porsche%20911%20GT3%20R")
    assert len(resp.json()) == 1

    resp = client.patch(f"/admin/laps/{lap_id}/invalidate", json={"reason": "cut track"}, headers=ADMIN_HEADERS)
    assert resp.status_code == 200

    resp = client.get("/leaderboard/car/Monza/Porsche%20911%20GT3%20R")
    assert len(resp.json()) == 0  # is_valid=False laps are excluded — no recomputation needed

    resp = client.get("/telemetry/laps", headers={"Authorization": f"Bearer {account['auth_token']}"})
    lap = resp.json()[0]
    assert lap["is_valid"] is False
    assert any("cut track" in r for r in lap["invalid_reason"])


def test_delete_driver_removes_lap_and_file(client):
    from server.database import SessionLocal
    from server.models import Lap

    account = _register(client)
    lap_id = _upload_lap(client, account)

    db = SessionLocal()
    telemetry_path = db.query(Lap).filter(Lap.id == lap_id).one().telemetry_path
    db.close()
    assert Path(telemetry_path).exists()

    resp = client.delete(f"/admin/drivers/{account['driver_id']}", headers=ADMIN_HEADERS)
    assert resp.status_code == 200

    db = SessionLocal()
    assert db.query(Lap).filter(Lap.id == lap_id).one_or_none() is None
    db.close()
    assert not Path(telemetry_path).exists()


def test_report_and_resolve_flow(client):
    account = _register(client)
    lap_id = _upload_lap(client, account)

    resp = client.post(
        f"/telemetry/laps/{lap_id}/report",
        json={"reason": "looks like a cut track lap"},
        headers={"Authorization": f"Bearer {account['auth_token']}"},
    )
    assert resp.status_code == 201
    report_id = resp.json()["id"]

    resp = client.get("/admin/reports", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    assert len(resp.json()) == 1

    resp = client.post(f"/admin/reports/{report_id}/resolve", params={"resolution": "reviewed, lap looks fine"}, headers=ADMIN_HEADERS)
    assert resp.status_code == 200

    resp = client.get("/admin/reports", headers=ADMIN_HEADERS)  # unresolved_only=True default
    assert len(resp.json()) == 0


def test_self_service_export_and_delete(client):
    account = _register(client)
    _upload_lap(client, account)

    resp = client.get("/accounts/me/export", headers={"Authorization": f"Bearer {account['auth_token']}"})
    assert resp.status_code == 200
    export = resp.json()
    assert export["display_name"] == "Driver"
    assert len(export["laps"]) == 1

    resp = client.delete("/accounts/me", headers={"Authorization": f"Bearer {account['auth_token']}"})
    assert resp.status_code == 204

    # Token is gone along with the driver.
    resp = client.get("/telemetry/laps", headers={"Authorization": f"Bearer {account['auth_token']}"})
    assert resp.status_code == 401
