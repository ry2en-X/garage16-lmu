"""V1.0 — open sign-up with terms and minimum age, the desktop client's email login, the legal
endpoint, and the default download links."""

from __future__ import annotations

import pytest

from server.config import Settings, check_registration_settings, settings

STRONG = "Correct-Horse-42-battery"


def _signup(client, **overrides):
    body = {"display_name": "Mika", "email": "mika@example.com", "password": STRONG,
            "accept_terms": True, "confirm_min_age": True, **overrides}
    return client.post("/accounts/signup", json=body)


def test_signup_creates_a_signed_in_account_with_terms_recorded(client):
    resp = _signup(client)
    assert resp.status_code == 200, resp.text
    assert resp.json()["display_name"] == "Mika"
    # signed in by cookie: /accounts/me answers without any token
    me = client.get("/accounts/me")
    assert me.status_code == 200 and me.json()["email"] == "mika@example.com"
    assert me.json()["email_verified"] is False
    export = client.get("/accounts/me/export").json()
    from server.routers.legal import TERMS_VERSION

    assert export["terms_version"] == TERMS_VERSION and export["terms_accepted_at"]


@pytest.mark.parametrize("field,message", [("accept_terms", "terms"), ("confirm_min_age", "16")])
def test_signup_requires_both_confirmations(client, field, message):
    resp = _signup(client, **{field: False})
    assert resp.status_code == 400 and message in resp.json()["detail"]


def test_signup_rejects_weak_password_bad_email_duplicate(client):
    assert _signup(client, password="short").status_code == 400
    assert _signup(client, email="not-an-email").status_code == 400
    assert _signup(client).status_code == 200
    client.cookies.clear()
    dup = _signup(client, email="MIKA@example.com", display_name="Other")
    assert dup.status_code == 409


def test_signup_needs_the_secret_when_registration_is_not_open(client, monkeypatch):
    monkeypatch.setattr(settings, "open_registration", False)
    monkeypatch.setattr(settings, "registration_secret", "s3cret")
    assert _signup(client).status_code == 403
    ok = client.post("/accounts/signup", headers={"X-Registration-Secret": "s3cret"},
                     json={"display_name": "Mika", "email": "mika@example.com", "password": STRONG,
                           "accept_terms": True, "confirm_min_age": True})
    assert ok.status_code == 200


def test_open_production_server_refuses_the_token_only_register(client, monkeypatch):
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "open_registration", True)
    monkeypatch.setattr(settings, "registration_secret", None)
    assert client.post("/accounts/register", params={"display_name": "X"}).status_code == 403
    assert _signup(client).status_code == 200


def test_desktop_login_issues_working_credentials_and_replaces_the_old_token(client):
    assert _signup(client).status_code == 200
    client.cookies.clear()
    bad = client.post("/accounts/desktop-login", json={"email": "mika@example.com", "password": "wrong-password-1"})
    assert bad.status_code == 401
    first = client.post("/accounts/desktop-login", json={"email": "Mika@Example.com", "password": STRONG})
    assert first.status_code == 200, first.text
    assert first.headers["cache-control"] == "no-store"
    creds = first.json()
    who = client.get("/accounts/me", headers={"Authorization": f"Bearer {creds['auth_token']}"})
    assert who.status_code == 200 and who.json()["display_name"] == "Mika"
    second = client.post("/accounts/desktop-login", json={"email": "mika@example.com", "password": STRONG}).json()
    # one PC at a time: the first token no longer works, the client secret stays the same
    assert client.get("/accounts/me", headers={"Authorization": f"Bearer {creds['auth_token']}"}).status_code == 401
    assert second["client_secret"] == creds["client_secret"]


def test_desktop_login_refuses_locked_accounts(client):
    from server.database import SessionLocal
    from server.models import Driver

    assert _signup(client).status_code == 200
    db = SessionLocal()
    driver = db.query(Driver).filter(Driver.email == "mika@example.com").one()
    driver.is_locked = True
    db.commit()
    db.close()
    resp = client.post("/accounts/desktop-login", json={"email": "mika@example.com", "password": STRONG})
    assert resp.status_code == 403


def test_legal_endpoint_shows_the_operator_only_when_configured(client, monkeypatch):
    monkeypatch.setattr(settings, "operator_name", None)
    body = client.get("/legal").json()
    assert body["operator"] is None and body["min_age"] == 16 and body["registration"] == "development"
    monkeypatch.setattr(settings, "operator_name", "Erika Mustermann")
    monkeypatch.setattr(settings, "operator_address", ["Musterstraße 1", "12345 Musterstadt"])
    monkeypatch.setattr(settings, "operator_email", "erika@example.com")
    monkeypatch.setattr(settings, "open_registration", True)
    resp = client.get("/legal")
    assert resp.headers["x-robots-tag"] == "noindex"
    body = resp.json()
    assert body["operator"] == {"name": "Erika Mustermann", "address": ["Musterstraße 1", "12345 Musterstadt"],
                                "email": "erika@example.com"}
    assert body["registration"] == "open"
    assert client.get("/health").json()["registration"] == "open"


def test_operator_address_is_split_on_pipes(monkeypatch):
    monkeypatch.setenv("LMU_GARAGE_OPERATOR_NAME", "  A  ")
    monkeypatch.setenv("LMU_GARAGE_OPERATOR_ADDRESS", "Street 1 | 12345 Town |")
    cfg = Settings()
    assert cfg.operator_name == "A" and cfg.operator_address == ["Street 1", "12345 Town"]


def test_production_needs_a_secret_or_explicit_open_registration(monkeypatch):
    cfg = Settings()
    cfg.environment = "production"
    cfg.registration_secret = None
    cfg.open_registration = False
    with pytest.raises(RuntimeError, match="LMU_GARAGE_OPEN_REGISTRATION"):
        check_registration_settings(cfg)
    cfg.open_registration = True
    check_registration_settings(cfg)
    cfg.open_registration = False
    cfg.registration_secret = "x"
    check_registration_settings(cfg)


def test_download_links_default_to_the_newest_github_release(monkeypatch):
    for name in ("LMU_GARAGE_CLIENT_DOWNLOAD_URL", "LMU_GARAGE_CLIENT_APP_DOWNLOAD_URL",
                 "LMU_GARAGE_CLIENT_RELEASE_PAGE_URL", "LMU_GARAGE_GITHUB_REPO"):
        monkeypatch.delenv(name, raising=False)
    cfg = Settings()
    base = "https://github.com/ry2en-X/garage16-lmu/releases/latest"
    assert cfg.client_download_url == f"{base}/download/Garage16-Client-Setup.exe"
    assert cfg.client_app_download_url == f"{base}/download/Garage16-Client-Analysis-Setup.exe"
    assert cfg.client_release_page_url == base
    # the installer names are the ones the build produces
    from pathlib import Path

    iss = Path(__file__).resolve().parent.parent.joinpath("packaging", "garage16.iss").read_text(encoding="utf-8")
    assert '"Garage16-Client-Setup"' in iss and '"Garage16-Client-Analysis-Setup"' in iss
    # an explicit .env value wins; an empty repo switches the defaults off
    monkeypatch.setenv("LMU_GARAGE_CLIENT_DOWNLOAD_URL", "https://nas.example/setup.exe")
    monkeypatch.setenv("LMU_GARAGE_GITHUB_REPO", "")
    cfg = Settings()
    assert cfg.client_download_url == "https://nas.example/setup.exe"
    assert cfg.client_app_download_url is None and cfg.client_release_page_url is None
