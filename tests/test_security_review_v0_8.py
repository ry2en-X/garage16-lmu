"""Tests for the V0.8-NAS §9 security-review findings: previously-missing
length bounds on several request schemas (real DoS/Argon2-cost concerns,
not just tidiness), and empirical confirmation that telemetry storage
path construction neutralizes path traversal."""

from __future__ import annotations


def _register(client, name="Driver"):
    resp = client.post("/accounts/register", params={"display_name": name})
    assert resp.status_code == 200
    return resp.json()


def _auth(account):
    return {"Authorization": f"Bearer {account['auth_token']}"}


# --- previously-unbounded fields now rejected when oversized --------------

def test_team_name_rejects_absurdly_long_input(client):
    account = _register(client)
    resp = client.post("/teams", json={"name": "x" * 5000}, headers=_auth(account))
    assert resp.status_code == 422


def test_team_join_code_rejects_absurdly_long_input(client):
    account = _register(client)
    resp = client.post("/teams/join", json={"invite_code": "x" * 5000}, headers=_auth(account))
    assert resp.status_code == 422


def test_change_password_current_password_rejects_absurdly_long_input(client):
    """The real concern: current_password goes straight into Argon2's
    verify_password() — an unbounded string here would let a caller force
    an expensive hash over an arbitrarily large payload."""
    account = _register(client)
    client.post(
        "/accounts/set-password",
        json={"email": "driver@example.com", "new_password": "correct horse battery"},
        headers=_auth(account),
    )
    resp = client.post(
        "/accounts/change-password",
        json={"current_password": "x" * 100_000, "new_password": "another password here"},
        headers=_auth(account),
    )
    assert resp.status_code == 422


def test_set_password_current_password_rejects_absurdly_long_input(client):
    account = _register(client)
    client.post(
        "/accounts/set-password",
        json={"email": "driver@example.com", "new_password": "correct horse battery"},
        headers=_auth(account),
    )
    resp = client.post(
        "/accounts/set-password",
        json={"email": "driver@example.com", "new_password": "another password here", "current_password": "x" * 100_000},
        headers=_auth(account),
    )
    assert resp.status_code == 422


def test_password_reset_confirm_token_rejects_absurdly_long_input(client):
    resp = client.post(
        "/accounts/password-reset/confirm", json={"token": "x" * 5000, "new_password": "whatever password"}
    )
    assert resp.status_code == 422


def test_verify_email_confirm_token_rejects_absurdly_long_input(client):
    resp = client.post("/accounts/verify-email/confirm", json={"token": "x" * 5000})
    assert resp.status_code == 422


def test_admin_driver_search_rejects_absurdly_long_input(client):
    resp = client.get(
        "/admin/drivers", params={"search": "x" * 5000}, headers={"X-Admin-Token": "test-admin-token"}
    )
    assert resp.status_code == 422


# --- reasonable-length inputs still work (bounds aren't too tight) --------

def test_team_name_at_the_limit_still_works(client):
    account = _register(client)
    resp = client.post("/teams", json={"name": "x" * 100}, headers=_auth(account))
    assert resp.status_code == 200


def test_change_password_with_a_normal_length_current_password_still_works(client):
    account = _register(client)
    client.post(
        "/accounts/set-password",
        json={"email": "driver@example.com", "new_password": "correct horse battery"},
        headers=_auth(account),
    )
    resp = client.post(
        "/accounts/change-password",
        json={"current_password": "correct horse battery", "new_password": "another password here"},
        headers=_auth(account),
    )
    assert resp.status_code == 200


# --- path traversal: empirical confirmation (§9) ---------------------------

def test_slugify_neutralizes_path_traversal_payloads():
    from server.storage import _slugify

    assert ".." not in _slugify("../../etc/passwd")
    assert "/" not in _slugify("../../etc/passwd")
    assert "\\" not in _slugify("..\\..\\windows\\system32")
    assert _slugify("../../etc/passwd") == "etc_passwd"
    assert _slugify("") == "unknown"


def test_uploading_a_lap_with_traversal_payloads_in_track_and_car_name_stays_sandboxed(client):
    """End-to-end, not just the unit-level _slugify check: a real upload
    with malicious track_name/car_name must still land safely inside the
    configured storage root, never escaping it."""
    import json
    import tempfile
    from pathlib import Path

    import numpy as np
    import pandas as pd

    from client.telemetry.recorder import LapRecorder
    from client.uploader.uploader import Uploader
    from server.config import settings

    account = _register(client)
    lap_time = 90.0
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        n = 50
        df = pd.DataFrame({
            "t": np.linspace(0, lap_time, n), "lap_dist": np.linspace(0, 4500, n),
            "speed": np.full(n, 4500.0 / lap_time), "throttle": np.full(n, 0.8), "brake": np.zeros(n),
            "clutch": np.zeros(n), "steering": np.zeros(n), "gear": np.full(n, 4),
            "rpm": np.full(n, 6000.0), "fuel": np.linspace(50, 48, n),
        })
        df.to_parquet(tmp_dir / "lap.parquet", index=False)
        meta_path = tmp_dir / "lap.json"
        meta_path.write_text(json.dumps({
            "track_name": "../../../../etc/passwd", "car_name": "..\\..\\windows\\system32",
            "car_class": "GT3", "session_type": 10, "lap_number": 1, "lap_time": lap_time,
            "sector_times": [30, 30, 30], "is_valid": True, "started_at": 0.0,
            "recorded_at": 1700000000, "sample_count": n, "telemetry_file": "lap.parquet", "uploaded": False,
        }), encoding="utf-8")
        uploader = Uploader(recorder=LapRecorder(data_dir=str(tmp_dir)), auth_token=account["auth_token"], client_secret=account["client_secret"])
        payload = uploader._build_payload(meta_path)
        resp = client.post(
            "/telemetry/upload",
            data={"envelope": json.dumps(payload["envelope"]), "signature": payload["signature"]},
            files={"telemetry": ("lap.parquet", payload["telemetry_path"].read_bytes())},
            headers={"Authorization": f"Bearer {account['auth_token']}"},
        )
    assert resp.status_code == 200

    from server.database import SessionLocal
    from server.models import Lap
    db = SessionLocal()
    try:
        lap = db.query(Lap).filter(Lap.driver_id == account["driver_id"]).one()
        stored_path = Path(lap.telemetry_path).resolve()
    finally:
        db.close()

    storage_root = settings.telemetry_storage_dir.resolve()
    assert storage_root in stored_path.parents
    assert ".." not in stored_path.parts
