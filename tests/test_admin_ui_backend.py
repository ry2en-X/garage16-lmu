"""Tests for the V0.7.2 admin backend additions that enable the Admin UI
(§15): enriched GET /admin/reports, GET /admin/drivers, GET
/admin/laps/{id}."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

ADMIN_HEADERS = {"X-Admin-Token": "test-admin-token"}


def _register(client, name="Driver"):
    resp = client.post("/accounts/register", params={"display_name": name})
    assert resp.status_code == 200
    return resp.json()


def _auth(account):
    return {"Authorization": f"Bearer {account['auth_token']}"}


def _upload_lap(client, account, lap_time=90.0, track="Monza"):
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
        parquet_path = tmp_dir / "lap.parquet"
        df.to_parquet(parquet_path, index=False)
        meta_path = tmp_dir / "lap.json"
        meta_path.write_text(json.dumps({
            "track_name": track, "car_name": "GT3 Custom #12", "car_class": "GT3",
            "car_model": "Porsche 911 GT3 R", "session_type": 10,
            "lap_number": 1, "lap_time": lap_time, "sector_times": [lap_time / 3] * 3,
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
    return resp.json()


# --- enriched reports -------------------------------------------------

def test_admin_reports_include_full_lap_and_reporter_detail(client):
    offender = _register(client, "Offender")
    lap = _upload_lap(client, offender, lap_time=95.0)
    reporter = _register(client, "Reporter")
    client.post(
        f"/telemetry/laps/{lap['lap_id']}/report", json={"reason": "cut the track"}, headers=_auth(reporter)
    )

    resp = client.get("/admin/reports", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    reports = resp.json()
    assert len(reports) == 1
    r = reports[0]
    assert r["lap"]["driver_name"] == "Offender"
    assert r["lap"]["track_name"] == "Monza"
    assert r["lap"]["lap_time"] == 95.0
    assert r["reported_by_display_name"] == "Reporter"
    assert r["reason"] == "cut the track"


def test_driver_facing_report_response_unaffected_by_admin_enrichment(client):
    """The driver's own POST .../report confirmation must keep its
    original, simple shape — the enrichment is admin-only."""
    offender = _register(client, "Offender2")
    lap = _upload_lap(client, offender)
    reporter = _register(client, "Reporter2")
    resp = client.post(
        f"/telemetry/laps/{lap['lap_id']}/report", json={"reason": "suspicious"}, headers=_auth(reporter)
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["lap_id"] == lap["lap_id"]
    assert "lap" not in body
    assert "reported_by_display_name" not in body


def test_admin_reports_requires_admin_token(client):
    resp = client.get("/admin/reports")
    assert resp.status_code == 403


# --- driver listing -----------------------------------------------------

def test_admin_drivers_lists_with_lap_counts(client):
    account = _register(client, "Prolific")
    _upload_lap(client, account, lap_time=90.0, track="Monza")
    _upload_lap(client, account, lap_time=91.0, track="Spa")

    resp = client.get("/admin/drivers", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    rows = {d["display_name"]: d for d in resp.json()}
    assert rows["Prolific"]["total_laps"] == 2
    assert rows["Prolific"]["is_locked"] is False


def test_admin_drivers_search_is_substring_and_case_insensitive(client):
    _register(client, "Nito Racing")
    _register(client, "SomeoneElse")

    resp = client.get("/admin/drivers", params={"search": "nito"}, headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    names = [d["display_name"] for d in resp.json()]
    assert names == ["Nito Racing"]


def test_admin_drivers_includes_email_when_set(client):
    account = _register(client, "HasEmail")
    client.post(
        "/accounts/set-password",
        json={"email": "hasemail@example.com", "new_password": "correct horse battery"},
        headers=_auth(account),
    )
    resp = client.get("/admin/drivers", params={"search": "HasEmail"}, headers=ADMIN_HEADERS)
    assert resp.json()[0]["email"] == "hasemail@example.com"


def test_admin_drivers_requires_admin_token(client):
    resp = client.get("/admin/drivers")
    assert resp.status_code == 403


# --- lap detail -----------------------------------------------------------

def test_admin_get_lap_detail(client):
    account = _register(client, "LapOwner")
    lap = _upload_lap(client, account, lap_time=93.5, track="Le Mans")

    resp = client.get(f"/admin/laps/{lap['lap_id']}", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["driver_name"] == "LapOwner"
    assert body["track_name"] == "Le Mans"
    assert body["lap_time"] == 93.5


def test_admin_get_lap_detail_404_for_unknown_lap(client):
    resp = client.get("/admin/laps/999999", headers=ADMIN_HEADERS)
    assert resp.status_code == 404


def test_admin_get_lap_detail_requires_admin_token(client):
    account = _register(client, "LapOwner2")
    lap = _upload_lap(client, account)
    resp = client.get(f"/admin/laps/{lap['lap_id']}")
    assert resp.status_code == 403
