"""
Integration test: the full path client envelope -> HTTP upload -> DB ->
leaderboard/records, using a real TestClient (not mocked HTTP) and the
real Uploader._build_payload() so a regression like V0.5.2's P0-1 bug
(client sends a field the server's schema rejects) would fail here, not
just in test_upload_contract.py's narrower schema-only check.
"""

import json
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd


def _write_recorded_lap(tmp_dir: Path, lap_time: float = 90.0, lap_number: int = 1) -> Path:
    """Writes a lap exactly as client/telemetry/recorder.py would: a
    parquet file with the columns validate_dataframe() requires, plus the
    metadata JSON recorder.save() produces (including the local-only
    `uploaded` field the client must strip before sending)."""
    n = 100
    df = pd.DataFrame({
        "t": np.linspace(0, lap_time, n),
        "lap_dist": np.linspace(0, 4500, n),
        "speed": np.full(n, 50.0),
        "throttle": np.full(n, 0.8),
        "brake": np.zeros(n),
        "clutch": np.zeros(n),
        "steering": np.zeros(n),
        "gear": np.full(n, 4),
        "rpm": np.full(n, 6000.0),
        "fuel": np.linspace(50, 48, n),
    })
    parquet_path = tmp_dir / "lap.parquet"
    df.to_parquet(parquet_path, index=False)

    meta_path = tmp_dir / "lap.json"
    meta_path.write_text(json.dumps({
        "track_name": "Le Mans",
        "car_name": "499P Custom Team 2025 #397",
        "car_class": "Hypercar",
        "car_model": "Ferrari 499P",
        "session_type": 10,
        "lap_number": lap_number,
        "lap_time": lap_time,
        "sector_times": [30.0, 30.0, 30.0],
        "is_valid": True,
        "started_at": 0.0,
        "recorded_at": 1700000000,
        "ambient_temp": 22.0,
        "track_temp": 30.0,
        "sample_count": n,
        "telemetry_file": "lap.parquet",
        "uploaded": False,
    }), encoding="utf-8")
    return meta_path


def _register(client) -> dict:
    resp = client.post("/accounts/register", params={"display_name": "Test Driver"})
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_full_upload_flow_via_real_client_envelope(client):
    """Register -> build a real envelope via Uploader._build_payload ->
    POST it -> verify the lap landed in the DB and appears on the
    leaderboard. This is the end-to-end check that would have caught
    P0-1 (the client sending a field the server's schema rejects)."""
    from client.telemetry.recorder import LapRecorder
    from client.uploader.uploader import Uploader

    account = _register(client)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        meta_path = _write_recorded_lap(tmp_dir, lap_time=90.0)

        uploader = Uploader(
            recorder=LapRecorder(data_dir=str(tmp_dir)),
            auth_token=account["auth_token"],
            client_secret=account["client_secret"],
        )
        payload = uploader._build_payload(meta_path)

        resp = client.post(
            "/telemetry/upload",
            data={
                "envelope": json.dumps(payload["envelope"]),
                "signature": payload["signature"],
            },
            files={"telemetry": ("lap.parquet", payload["telemetry_path"].read_bytes())},
            headers={"Authorization": f"Bearer {account['auth_token']}"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "ok"
        assert body["duplicate"] is False

    # Shows up in the driver's own lap list.
    resp = client.get("/telemetry/laps", headers={"Authorization": f"Bearer {account['auth_token']}"})
    assert resp.status_code == 200
    laps = resp.json()
    assert len(laps) == 1
    assert laps[0]["is_valid"] is True
    assert laps[0]["lap_time"] == 90.0

    # Shows up on the class leaderboard (main) as the first entry.
    resp = client.get("/leaderboard/class/Le%20Mans/Hypercar")
    assert resp.status_code == 200
    board = resp.json()
    assert len(board) == 1
    assert board[0]["driver_name"] == "Test Driver"

    # Also shows up on the exact-car sub-leaderboard.
    resp = client.get("/leaderboard/car/Le%20Mans/Ferrari%20499P")
    assert resp.status_code == 200
    board = resp.json()
    assert len(board) == 1
    assert board[0]["driver_name"] == "Test Driver"


def test_duplicate_upload_is_idempotent(client):
    from client.telemetry.recorder import LapRecorder
    from client.uploader.uploader import Uploader

    account = _register(client)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        meta_path = _write_recorded_lap(tmp_dir)
        uploader = Uploader(
            recorder=LapRecorder(data_dir=str(tmp_dir)),
            auth_token=account["auth_token"],
            client_secret=account["client_secret"],
        )
        payload = uploader._build_payload(meta_path)
        req_kwargs = dict(
            data={"envelope": json.dumps(payload["envelope"]), "signature": payload["signature"]},
            files={"telemetry": ("lap.parquet", payload["telemetry_path"].read_bytes())},
            headers={"Authorization": f"Bearer {account['auth_token']}"},
        )

        first = client.post("/telemetry/upload", **req_kwargs)
        second = client.post("/telemetry/upload", **req_kwargs)

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["lap_id"] == second.json()["lap_id"]
    assert second.json()["duplicate"] is True


def test_invalid_lap_has_reason_recorded(client):
    """A lap that fails server validation must carry a human-readable
    invalid_reason (V0.6.0 fix — previously reasons were computed then
    discarded, driver only ever saw 'invalid')."""
    from client.telemetry.recorder import LapRecorder
    from client.uploader.uploader import Uploader

    account = _register(client)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        # Claim a lap_time wildly inconsistent with the telemetry duration.
        meta_path = _write_recorded_lap(tmp_dir, lap_time=90.0)
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["lap_time"] = 5.0  # telemetry actually spans 90s
        meta["sector_times"] = [1.5, 1.5, 2.0]
        meta_path.write_text(json.dumps(meta), encoding="utf-8")

        uploader = Uploader(
            recorder=LapRecorder(data_dir=str(tmp_dir)),
            auth_token=account["auth_token"],
            client_secret=account["client_secret"],
        )
        payload = uploader._build_payload(meta_path)
        resp = client.post(
            "/telemetry/upload",
            data={"envelope": json.dumps(payload["envelope"]), "signature": payload["signature"]},
            files={"telemetry": ("lap.parquet", payload["telemetry_path"].read_bytes())},
            headers={"Authorization": f"Bearer {account['auth_token']}"},
        )
        assert resp.status_code == 200

    resp = client.get("/telemetry/laps", headers={"Authorization": f"Bearer {account['auth_token']}"})
    lap = resp.json()[0]
    assert lap["is_valid"] is False
    assert lap["invalid_reason"] is not None
    assert any("lap_time" in r for r in lap["invalid_reason"])


def test_min_client_version_rejects_old_client(client):
    from client.telemetry.recorder import LapRecorder
    from client.uploader.uploader import Uploader

    account = _register(client)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        meta_path = _write_recorded_lap(tmp_dir)
        uploader = Uploader(
            recorder=LapRecorder(data_dir=str(tmp_dir)),
            auth_token=account["auth_token"],
            client_secret=account["client_secret"],
        )
        payload = uploader._build_payload(meta_path)
        payload["envelope"]["client_version"] = "0.1.0"  # ancient, pre-P0-1-fix
        # Re-sign since we mutated the envelope after signing.
        envelope_bytes = json.dumps(payload["envelope"], sort_keys=True).encode()
        signature = uploader._sign(envelope_bytes)

        resp = client.post(
            "/telemetry/upload",
            data={"envelope": json.dumps(payload["envelope"]), "signature": signature},
            files={"telemetry": ("lap.parquet", payload["telemetry_path"].read_bytes())},
            headers={"Authorization": f"Bearer {account['auth_token']}"},
        )
    # V0.8.6: 426 Upgrade Required with a machine-readable UPDATE_REQUIRED
    # body (was a generic 400) — see server/routers/telemetry.py.
    assert resp.status_code == 426
    assert "too old" in resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == "UPDATE_REQUIRED"
    assert detail["min_client_version"]
