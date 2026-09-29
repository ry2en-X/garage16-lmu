"""
Tests for the V0.6.3 leaderboard architecture: a class-based main
leaderboard (track + class, e.g. every Hypercar together) and a
model-based sub-leaderboard (track + exact car model, ignoring
team/livery/car number).
"""

import json
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd


def _register(client, name):
    resp = client.post("/accounts/register", params={"display_name": name})
    assert resp.status_code == 200
    return resp.json()


def _upload(client, account, track, car_name, car_class, car_model, lap_time):
    from client.telemetry.recorder import LapRecorder
    from client.uploader.uploader import Uploader

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        n = 50
        speed_ms = 50.0
        df = pd.DataFrame({
            "t": np.linspace(0, lap_time, n), "lap_dist": np.linspace(0, speed_ms * lap_time, n),
            "speed": np.full(n, speed_ms), "throttle": np.full(n, 0.8), "brake": np.zeros(n),
            "clutch": np.zeros(n), "steering": np.zeros(n), "gear": np.full(n, 4),
            "rpm": np.full(n, 6000.0), "fuel": np.linspace(50, 48, n),
        })
        parquet_path = tmp_dir / "lap.parquet"
        df.to_parquet(parquet_path, index=False)
        meta_path = tmp_dir / "lap.json"
        meta_path.write_text(json.dumps({
            "track_name": track, "car_name": car_name, "car_class": car_class,
            "car_model": car_model, "session_type": 10, "lap_number": 1,
            "lap_time": lap_time, "sector_times": [lap_time / 3] * 3, "is_valid": True,
            "started_at": 0.0, "recorded_at": 1700000000, "sample_count": n,
            "telemetry_file": "lap.parquet", "uploaded": False,
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
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_class_leaderboard_groups_different_models_together(client):
    """Two different Hypercar models (Ferrari 499P, Porsche 963) at the
    same track must BOTH appear on the class leaderboard together."""
    ferrari_driver = _register(client, "Ferrari Driver")
    porsche_driver = _register(client, "Porsche Driver")

    _upload(client, ferrari_driver, "Le Mans", "499P Team A #50", "Hypercar", "Ferrari 499P", 210.0)
    _upload(client, porsche_driver, "Le Mans", "963 Team B #6", "Hypercar", "Porsche 963", 211.0)

    resp = client.get("/leaderboard/class/Le%20Mans/Hypercar")
    assert resp.status_code == 200
    board = resp.json()
    assert len(board) == 2
    names = {e["driver_name"] for e in board}
    assert names == {"Ferrari Driver", "Porsche Driver"}
    # Fastest first.
    assert board[0]["driver_name"] == "Ferrari Driver"


def test_car_leaderboard_excludes_different_models_in_same_class(client):
    """The exact-car sub-leaderboard for 'Ferrari 499P' must NOT include a
    Porsche 963 lap, even though both are Hypercars at the same track."""
    ferrari_driver = _register(client, "Ferrari Driver 2")
    porsche_driver = _register(client, "Porsche Driver 2")

    _upload(client, ferrari_driver, "Le Mans", "499P Team A #50", "Hypercar", "Ferrari 499P", 210.0)
    _upload(client, porsche_driver, "Le Mans", "963 Team B #6", "Hypercar", "Porsche 963", 211.0)

    resp = client.get("/leaderboard/car/Le%20Mans/Ferrari%20499P")
    assert resp.status_code == 200
    board = resp.json()
    assert len(board) == 1
    assert board[0]["driver_name"] == "Ferrari Driver 2"


def test_car_leaderboard_ignores_team_livery_and_number(client):
    """Two drivers in the SAME model but different team/livery/car number
    (car_name differs, car_model is identical) must both appear together
    on the exact-car sub-leaderboard — per the explicit requirement that
    team/number/year don't matter, only the model does."""
    driver_a = _register(client, "Team A Driver")
    driver_b = _register(client, "Team B Driver")

    _upload(client, driver_a, "Spa", "499P Team A #50", "Hypercar", "Ferrari 499P", 130.0)
    _upload(client, driver_b, "Spa", "499P Team C #83 Custom Livery", "Hypercar", "Ferrari 499P", 129.5)

    resp = client.get("/leaderboard/car/Spa/Ferrari%20499P")
    assert resp.status_code == 200
    board = resp.json()
    assert len(board) == 2
    assert board[0]["driver_name"] == "Team B Driver"  # 129.5 < 130.0


def test_leaderboards_are_scoped_per_track(client):
    """The same class/model at a DIFFERENT track must not leak in."""
    driver = _register(client, "Traveling Driver")
    _upload(client, driver, "Le Mans", "499P #50", "Hypercar", "Ferrari 499P", 210.0)

    resp = client.get("/leaderboard/class/Spa/Hypercar")
    assert resp.status_code == 200
    assert resp.json() == []

    resp = client.get("/leaderboard/car/Spa/Ferrari%20499P")
    assert resp.status_code == 200
    assert resp.json() == []


def test_laps_from_pre_v0_6_3_clients_have_null_class_and_model(client):
    """A lap uploaded without car_class/car_model (old client) must not
    crash and must simply not appear in either leaderboard view — it's
    still visible in the driver's own /telemetry/laps."""
    from client.telemetry.recorder import LapRecorder
    from client.uploader.uploader import Uploader

    account = _register(client, "Old Client Driver")
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        n = 50
        df = pd.DataFrame({
            "t": np.linspace(0, 90.0, n), "lap_dist": np.linspace(0, 4500, n),
            "speed": np.full(n, 50.0), "throttle": np.full(n, 0.8), "brake": np.zeros(n),
            "clutch": np.zeros(n), "steering": np.zeros(n), "gear": np.full(n, 4),
            "rpm": np.full(n, 6000.0), "fuel": np.linspace(50, 48, n),
        })
        parquet_path = tmp_dir / "lap.parquet"
        df.to_parquet(parquet_path, index=False)
        meta_path = tmp_dir / "lap.json"
        # Deliberately NO car_class/car_model key at all — simulates a
        # pre-V0.6.3 client's metadata JSON.
        meta_path.write_text(json.dumps({
            "track_name": "Nordschleife", "car_name": "Old Car", "session_type": 10,
            "lap_number": 1, "lap_time": 90.0, "sector_times": [30.0, 30.0, 30.0],
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
    assert resp.status_code == 200, resp.text

    resp = client.get("/telemetry/laps", headers={"Authorization": f"Bearer {account['auth_token']}"})
    assert len(resp.json()) == 1  # still visible to the driver themselves

    resp = client.get("/leaderboard/class/Nordschleife/GT3")
    assert resp.json() == []
