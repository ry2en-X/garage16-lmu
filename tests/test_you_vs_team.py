"""Tests for V0.7.2 §21 "You vs Team" — GET /teams/{id}/records now
includes each caller's own gap to the team record on every combo."""

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


def _upload(client, account, track="Monza", car_class="GT3", car_model="911 GT3 R", lap_time=90.0):
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
            "track_name": track, "car_name": "car", "car_class": car_class, "car_model": car_model,
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


def test_record_holder_sees_zero_gap_and_you_hold_it_true(client):
    owner = _register(client, "Owner")
    team = client.post("/teams", json={"name": "Holder Team"}, headers=_auth(owner)).json()
    _upload(client, owner, lap_time=90.0)

    records = client.get(f"/teams/{team['id']}/records", headers=_auth(owner)).json()
    assert len(records) == 1
    assert records[0]["you_hold_it"] is True
    assert records[0]["gap"] == 0.0
    assert records[0]["your_lap_time"] == 90.0


def test_non_holder_sees_positive_gap(client):
    owner = _register(client, "Owner")
    team = client.post("/teams", json={"name": "Gap Team"}, headers=_auth(owner)).json()
    mate = _register(client, "Mate")
    client.post("/teams/join", json={"invite_code": team["invite_code"]}, headers=_auth(mate))

    _upload(client, owner, lap_time=90.0)  # team record
    _upload(client, mate, lap_time=92.5)

    records_for_mate = client.get(f"/teams/{team['id']}/records", headers=_auth(mate)).json()
    assert len(records_for_mate) == 1
    row = records_for_mate[0]
    assert row["you_hold_it"] is False
    assert row["your_lap_time"] == 92.5
    assert row["gap"] == 2.5
    assert row["driver_name"] == "Owner"


def test_driver_with_no_lap_on_a_combo_sees_null_gap_not_zero(client):
    """A missing gap must be None, never 0.0 — 0.0 would misleadingly
    read as 'you tied the record'."""
    owner = _register(client, "Owner")
    team = client.post("/teams", json={"name": "No Lap Team"}, headers=_auth(owner)).json()
    mate = _register(client, "MateNoLap")
    client.post("/teams/join", json={"invite_code": team["invite_code"]}, headers=_auth(mate))

    _upload(client, owner, lap_time=90.0)

    records_for_mate = client.get(f"/teams/{team['id']}/records", headers=_auth(mate)).json()
    assert len(records_for_mate) == 1
    row = records_for_mate[0]
    assert row["you_hold_it"] is False
    assert row["your_lap_time"] is None
    assert row["gap"] is None
