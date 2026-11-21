"""Settings are GARAGE16_*, the old LMU_GARAGE_* names keep working; the visitor's address
behind the Cloudflare Pages function."""

from __future__ import annotations

from server.core.client_ip import edge_client_ip
from server.core.env import env


def test_the_new_name_wins_and_the_old_one_still_counts(monkeypatch):
    monkeypatch.delenv("GARAGE16_SOME_SETTING", raising=False)
    monkeypatch.delenv("LMU_GARAGE_SOME_SETTING", raising=False)
    assert env("SOME_SETTING", "default") == "default"
    monkeypatch.setenv("LMU_GARAGE_SOME_SETTING", "old")
    assert env("SOME_SETTING", "default") == "old"
    monkeypatch.setenv("GARAGE16_SOME_SETTING", "new")
    assert env("SOME_SETTING", "default") == "new"
    monkeypatch.setenv("GARAGE16_SOME_SETTING", "")
    assert env("SOME_SETTING", "default") == "", "an empty value is a value (some settings are switched off so)"


def test_an_old_env_file_still_configures_the_server(monkeypatch):
    from server.core.config import Settings

    for name in ("MIN_AGE", "MAX_DEVICES"):
        monkeypatch.delenv("GARAGE16_" + name, raising=False)
    monkeypatch.setenv("LMU_GARAGE_MIN_AGE", "18")
    monkeypatch.setenv("LMU_GARAGE_MAX_DEVICES", "2")
    cfg = Settings()
    assert cfg.min_age == 18 and cfg.max_devices == 2


def test_compose_reads_the_old_names_too():
    import re
    from pathlib import Path

    compose = (Path(__file__).resolve().parents[2] / "docker-compose.yml").read_text(encoding="utf-8")
    entries = re.findall(r"^\s+(GARAGE16_[A-Z0-9_]+): (\$\{[^\n]+)$", compose, re.M)
    assert len(entries) > 30
    for name, value in entries:
        old = "LMU_GARAGE_" + name[len("GARAGE16_"):]
        assert value.startswith("${" + name) and "${" + old in value, (name, value)


def test_the_edge_address_is_believed_only_with_the_key():
    h = {"x-garage16-client-ip": "203.0.113.7", "x-garage16-edge-key": "s3cret"}
    assert edge_client_ip(h, "s3cret") == "203.0.113.7"
    assert edge_client_ip(h, None) is None, "no key configured: never believed"
    assert edge_client_ip({**h, "x-garage16-edge-key": "wrong"}, "s3cret") is None
    assert edge_client_ip({**h, "x-garage16-client-ip": "not-an-ip"}, "s3cret") is None


def test_rate_limits_count_the_visitor_behind_the_pages_function(client, monkeypatch):
    from server.core.config import settings

    monkeypatch.setattr(settings, "edge_key", "edge-secret")
    monkeypatch.setitem(settings.rate_limits, "login", (2, 900.0))
    body = {"email": "nobody@example.com", "password": "wrong-password-123"}
    for visitor in ("198.51.100.1", "198.51.100.2"):
        h = {"X-Garage16-Client-IP": visitor, "X-Garage16-Edge-Key": "edge-secret"}
        codes = [client.post("/api/v1/accounts/login", json={**body, "email": f"{visitor}@example.com"}, headers=h).status_code for _ in range(3)]
        assert codes[-1] == 429 and 429 not in codes[:2], (visitor, codes)
