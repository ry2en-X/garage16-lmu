"""Tests for GET /health, including the V0.7.2 §7 server-migration
signal (migrated_to)."""

from __future__ import annotations

from server.config import settings


def test_health_ok_with_no_migration_configured(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["db_ok"] is True
    assert "migrated_to" not in body


def test_health_reports_migrated_to_when_configured(client, monkeypatch):
    monkeypatch.setattr(settings, "migrated_to_url", "https://garage16.example.com")
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["migrated_to"] == "https://garage16.example.com"


def test_health_still_serves_normally_during_a_migration(client, monkeypatch):
    """A migration signal must not turn the old server into a dead end —
    it keeps answering normally (§7: 'möglichst nichts bemerken', a
    controlled cutover, not an abrupt one)."""
    monkeypatch.setattr(settings, "migrated_to_url", "https://garage16.example.com")
    resp = client.get("/health")
    assert resp.json()["status"] == "ok"
    assert resp.json()["db_ok"] is True
