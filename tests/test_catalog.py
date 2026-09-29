"""Tests for server/routers/leaderboard.py's catalog endpoints — these
discover real track/class/car names from uploaded laps rather than
requiring a hand-maintained list (see that module's comment)."""

import json
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd


def _register(client, name):
    resp = client.post("/accounts/register", params={"display_name": name})
    assert resp.status_code == 200
    return resp.json()


def _upload(client, account, track, car_name, car_class, car_model, lap_time=90.0):
    from client.telemetry.recorder import LapRecorder
    from client.uploader.uploader import Uploader

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        n = 50
        # Tiny, deterministic perturbation so distinct (track, car, model)
        # combos never produce byte-identical parquet content — otherwise
        # the server's real, correct upload-idempotency check (same
        # driver + same telemetry hash = duplicate, see P0-4b) would
        # silently collapse two genuinely different test uploads into
        # one, which is a test-fixture problem, not a product bug.
        salt = (hash((track, car_name, car_model)) % 1000) / 10000.0
        speed_ms = 50.0 + salt
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


def test_catalog_starts_empty(client):
    assert client.get("/leaderboard/catalog/tracks").json() == []
    assert client.get("/leaderboard/catalog/classes").json() == []
    assert client.get("/leaderboard/catalog/cars").json() == []


def test_catalog_reflects_real_uploads_with_no_manual_config(client):
    """The whole point: a brand-new track/class/car appears here purely
    because someone uploaded a lap on it — nobody edited a list anywhere."""
    account = _register(client, "Catalog Driver")
    _upload(client, account, "Le Mans", "499P #50", "Hyper", "397_25_499P")
    _upload(client, account, "Spa", "911 GT3 R #12", "GT3", "911_gt3_r")

    tracks = client.get("/leaderboard/catalog/tracks").json()
    assert set(tracks) == {"Le Mans", "Spa"}

    classes = client.get("/leaderboard/catalog/classes").json()
    assert set(classes) == {"Hypercar", "GT3"}

    cars = client.get("/leaderboard/catalog/cars").json()
    assert set(cars) == {"397_25_499P", "911_gt3_r"}


def test_catalog_classes_and_cars_scoped_by_track(client):
    """The UI's actual use case: once a track is picked, only offer
    classes/cars that exist AT that track — not a global, irrelevant list."""
    account = _register(client, "Scoped Driver")
    _upload(client, account, "Le Mans", "499P #50", "Hyper", "397_25_499P")
    _upload(client, account, "Spa", "911 GT3 R #12", "GT3", "911_gt3_r")

    le_mans_classes = client.get("/leaderboard/catalog/classes", params={"track_name": "Le Mans"}).json()
    assert le_mans_classes == ["Hypercar"]

    spa_cars = client.get("/leaderboard/catalog/cars", params={"track_name": "Spa"}).json()
    assert spa_cars == ["911_gt3_r"]


def test_catalog_deduplicates_across_multiple_laps_and_drivers(client):
    """Two drivers, two laps, same car — must appear exactly once."""
    a = _register(client, "Driver A")
    b = _register(client, "Driver B")
    _upload(client, a, "Le Mans", "499P Team A #50", "Hyper", "397_25_499P")
    _upload(client, b, "Le Mans", "499P Team B #83", "Hyper", "397_25_499P")

    assert client.get("/leaderboard/catalog/tracks").json() == ["Le Mans"]
    assert client.get("/leaderboard/catalog/classes").json() == ["Hypercar"]
    assert client.get("/leaderboard/catalog/cars").json() == ["397_25_499P"]


def test_catalog_excludes_laps_without_class_or_model(client):
    """Laps from clients older than V0.6.3 have NULL car_class/car_model
    (see server/schemas.py) — they must not show up as a literal "None"
    entry in the catalog."""
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
        # No car_class/car_model key at all — pre-V0.6.3 client.
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
    assert resp.status_code == 200

    # Track shows up (that field is never NULL) but class/car don't.
    assert client.get("/leaderboard/catalog/tracks").json() == ["Nordschleife"]
    assert client.get("/leaderboard/catalog/classes").json() == []
    assert client.get("/leaderboard/catalog/cars").json() == []
