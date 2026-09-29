"""Server side of V0.8.6 UPDATE_REQUIRED: GET /health version negotiation
(?client_version=) and the structured 426 on upload."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from server.config import settings
from server.versioning import is_client_outdated, parse_version


@pytest.mark.parametrize(
    "client,minimum,outdated",
    [
        ("0.8.6", "0.8.6", False),
        ("0.9.0", "0.8.6", False),
        ("0.8.5", "0.8.6", True),
        ("0.8", "0.8.6", True),
        ("0.10.0", "0.9.0", False),  # numeric, not lexicographic
        ("garbage", "0.5.3", True),
        ("", "0.5.3", True),
    ],
)
def test_is_client_outdated(client, minimum, outdated):
    assert is_client_outdated(client, minimum) is outdated


def test_parse_version_never_raises():
    assert parse_version(None) == (0,)  # type: ignore[arg-type]
    assert parse_version("1.2.3.4") == (1, 2, 3)


def test_health_without_client_version_has_no_verdict(client):
    assert "update_required" not in client.get("/health").json()


def test_health_says_update_required_for_an_old_client(client, monkeypatch):
    monkeypatch.setattr(settings, "min_client_version", "0.8.6")
    body = client.get("/health", params={"client_version": "0.8.1"}).json()
    assert body["update_required"] is True
    assert body["min_client_version"] == "0.8.6"


def test_health_says_no_update_for_a_current_client(client, monkeypatch):
    monkeypatch.setattr(settings, "min_client_version", "0.8.6")
    assert client.get("/health", params={"client_version": "0.8.6"}).json()["update_required"] is False


def test_health_advertises_the_download_url_only_when_configured(client, monkeypatch):
    monkeypatch.setattr(settings, "client_download_url", None)
    assert "client_download_url" not in client.get("/health").json()
    monkeypatch.setattr(settings, "client_download_url", "https://example.com/garage16")
    assert client.get("/health").json()["client_download_url"] == "https://example.com/garage16"


def test_upload_from_an_old_client_gets_a_structured_426(client, monkeypatch):
    from client.telemetry.recorder import LapRecorder
    from client.uploader.uploader import Uploader
    from tests.test_integration_upload_flow import _register, _write_recorded_lap

    monkeypatch.setattr(settings, "min_client_version", "9.9.9")
    monkeypatch.setattr(settings, "client_download_url", "https://example.com/garage16")
    account = _register(client)
    with tempfile.TemporaryDirectory() as tmp:
        meta_path = _write_recorded_lap(Path(tmp))
        uploader = Uploader(recorder=LapRecorder(data_dir=tmp), auth_token=account["auth_token"], client_secret=account["client_secret"])
        payload = uploader._build_payload(meta_path)
        resp = client.post(
            "/telemetry/upload",
            data={"envelope": json.dumps(payload["envelope"]), "signature": payload["signature"]},
            files={"telemetry": ("lap.parquet", payload["telemetry_path"].read_bytes())},
            headers={"Authorization": f"Bearer {account['auth_token']}"},
        )
    assert resp.status_code == 426
    detail = resp.json()["detail"]
    assert detail == {
        "code": "UPDATE_REQUIRED",
        "message": detail["message"],
        "min_client_version": "9.9.9",
        "download_url": "https://example.com/garage16",
    }
    assert "too old" in detail["message"]


def test_health_rejects_an_absurdly_long_client_version(client):
    assert client.get("/health", params={"client_version": "1" * 500}).status_code == 422


def test_startup_warns_about_a_migration_target_clients_would_ignore():
    from server.config import Settings, warn_about_questionable_client_facing_urls

    cfg = Settings()
    cfg.migrated_to_url = "garage16.example.com"  # forgot the https://
    cfg.client_download_url = "javascript:alert(1)"
    messages = warn_about_questionable_client_facing_urls(cfg)
    assert any("MIGRATED_TO" in m and "https://" in m for m in messages)
    assert any("CLIENT_DOWNLOAD_URL" in m for m in messages)


def test_startup_stays_quiet_for_valid_client_facing_urls():
    from server.config import Settings, warn_about_questionable_client_facing_urls

    cfg = Settings()
    cfg.migrated_to_url = "https://new.example.com"
    cfg.client_download_url = "https://example.com/dl"
    assert warn_about_questionable_client_facing_urls(cfg) == []
    cfg.migrated_to_url = None
    cfg.client_download_url = None
    assert warn_about_questionable_client_facing_urls(cfg) == []
