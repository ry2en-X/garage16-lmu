"""Shared helpers for the V0.9.3 tests: upload an arbitrary lap DataFrame through the real
client Uploader (signature, envelope, field allowlist), with any extra metadata."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from tests.helpers.synthetic_lap import make_lap


def register(client, name):
    resp = client.post("/accounts/register", params={"display_name": name})
    assert resp.status_code == 200, resp.text
    return resp.json()


def headers(account):
    return {"Authorization": f"Bearer {account['auth_token']}"}


def upload_df(client, account, df, *, lap_time=90.0, track="Le Mans", car_model="397_25_499P",
              car_class="Hyper", car_name="499P Custom Team 2025 #397", is_valid=True, lap_number=1,
              recorded_at=1_700_000_000, expect=200, **extra):
    from client.telemetry.recorder import LapRecorder
    from client.uploader.uploader import Uploader

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        df.to_parquet(tmp_dir / "lap.parquet", index=False)
        meta = {
            "track_name": track, "car_name": car_name, "car_class": car_class, "car_model": car_model,
            "session_type": 10, "lap_number": lap_number, "lap_time": lap_time,
            "sector_times": [lap_time / 3] * 3, "is_valid": is_valid, "started_at": 0.0,
            "recorded_at": recorded_at, "ambient_temp": 22.0, "track_temp": 30.0, "sample_count": len(df),
            "telemetry_file": "lap.parquet", "uploaded": False, **extra,
        }
        meta_path = tmp_dir / "lap.json"
        meta_path.write_text(json.dumps(meta), encoding="utf-8")
        uploader = Uploader(recorder=LapRecorder(data_dir=str(tmp_dir)), auth_token=account["auth_token"],
                            client_secret=account["client_secret"])
        payload = uploader._build_payload(meta_path)
        resp = client.post(
            "/telemetry/upload",
            data={"envelope": json.dumps(payload["envelope"]), "signature": payload["signature"]},
            files={"telemetry": ("lap.parquet", payload["telemetry_path"].read_bytes())},
            headers=headers(account),
        )
    assert resp.status_code == expect, resp.text
    return resp.json()


def lap_row(lap_id):
    from server.database import SessionLocal
    from server.models import Lap

    with SessionLocal() as db:
        lap = db.get(Lap, lap_id)
        db.expunge(lap)
        return lap


def clean_lap(**kwargs):
    return make_lap(**kwargs)
