"""Open sign-up with terms and minimum age, the desktop client's email login, the legal
endpoint, and the default download links."""

from __future__ import annotations

import pytest

from server.core.config import Settings, check_registration_settings, settings

STRONG = "Correct-Horse-42-battery"


def _signup(client, **overrides):
    body = {"display_name": "Mika", "email": "mika@example.com", "password": STRONG,
            "accept_terms": True, "confirm_min_age": True, **overrides}
    return client.post("/accounts/signup", json=body)


def _verify(email):
    """Marks the email confirmed, as clicking the link would (tests/accounts/test_email_verification.py
    covers the link itself)."""
    from server.core.database import SessionLocal
    from server.core.models import Driver

    db = SessionLocal()
    try:
        db.query(Driver).filter(Driver.email == email).update({"email_verified": True})
        db.commit()
    finally:
        db.close()


def _drivers_with(email):
    from server.core.database import SessionLocal
    from server.core.models import Driver

    db = SessionLocal()
    try:
        return db.query(Driver).filter(Driver.email == email).all()
    finally:
        db.close()


def test_signup_creates_the_account_with_terms_recorded_and_asks_to_confirm(client):
    resp = _signup(client)
    # 202 "check_email", the browser is NOT signed in (confirm first, then sign in)
    assert resp.status_code == 202, resp.text
    assert resp.json() == {"status": "check_email"}
    assert "garage16_session" not in resp.cookies
    assert client.get("/accounts/me").status_code == 401
    login = client.post("/accounts/login", json={"email": "mika@example.com", "password": STRONG})
    assert login.status_code == 200 and login.json()["email_verified"] is False and login.json()["terms_required"] is False
    me = client.get("/accounts/me").json()
    assert me["email"] == "mika@example.com" and me["email_verified"] is False
    export = client.get("/accounts/me/export").json()
    from server.api.v1.legal import TERMS_VERSION

    assert export["terms_version"] == TERMS_VERSION and export["terms_accepted_at"]


@pytest.mark.parametrize("field,message", [("accept_terms", "terms"), ("confirm_min_age", "16")])
def test_signup_requires_both_confirmations(client, field, message):
    resp = _signup(client, **{field: False})
    assert resp.status_code == 400 and message in resp.json()["detail"]


def test_signup_rejects_weak_password_bad_email_and_a_taken_name(client):
    assert _signup(client, password="short").status_code == 400
    assert _signup(client, email="not-an-email").status_code == 400
    assert _signup(client).status_code == 202
    # An address that already has an account gets the SAME answer (no enumeration) …
    dup = _signup(client, email="MIKA@example.com", display_name="Other")
    assert dup.status_code == 202 and dup.json() == {"status": "check_email"}
    assert len(_drivers_with("mika@example.com")) == 1
    # … while a taken driver name is still refused (names are public anyway)
    assert _signup(client, email="someone@example.com", display_name="mika").status_code == 409


def test_signup_needs_the_secret_when_registration_is_not_open(client, monkeypatch):
    monkeypatch.setattr(settings, "open_registration", False)
    monkeypatch.setattr(settings, "registration_secret", "s3cret")
    assert _signup(client).status_code == 403
    ok = client.post("/accounts/signup", headers={"X-Registration-Secret": "s3cret"},
                     json={"display_name": "Mika", "email": "mika@example.com", "password": STRONG,
                           "accept_terms": True, "confirm_min_age": True})
    assert ok.status_code == 202


def test_open_production_server_refuses_the_token_only_register(client, monkeypatch):
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "open_registration", True)
    monkeypatch.setattr(settings, "registration_secret", None)
    assert client.post("/accounts/register", params={"display_name": "X"}).status_code == 403
    assert _signup(client).status_code == 202


def test_desktop_login_issues_working_credentials(client):
    assert _signup(client).status_code == 202
    _verify("mika@example.com")
    bad = client.post("/accounts/desktop-login", json={"email": "mika@example.com", "password": "wrong-password-1"})
    assert bad.status_code == 401
    first = client.post("/accounts/desktop-login", json={"email": "Mika@Example.com", "password": STRONG})
    assert first.status_code == 200, first.text
    assert first.headers["cache-control"] == "no-store"
    creds = first.json()
    who = client.get("/accounts/me", headers={"Authorization": f"Bearer {creds['auth_token']}"})
    assert who.status_code == 200 and who.json()["display_name"] == "Mika"
    # A second PC gets its own token; the first one keeps working (tests/accounts/test_open_server_accounts.py)
    second = client.post("/accounts/desktop-login", json={"email": "mika@example.com", "password": STRONG}).json()
    assert client.get("/accounts/me", headers={"Authorization": f"Bearer {creds['auth_token']}"}).status_code == 200
    assert second["auth_token"] != creds["auth_token"] and second["client_secret"] == creds["client_secret"]


def test_desktop_login_refuses_locked_accounts(client):
    from server.core.database import SessionLocal
    from server.core.models import Driver

    assert _signup(client).status_code == 202
    _verify("mika@example.com")
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
    monkeypatch.setenv("GARAGE16_OPERATOR_NAME", "  A  ")
    monkeypatch.setenv("GARAGE16_OPERATOR_ADDRESS", "Street 1 | 12345 Town |")
    cfg = Settings()
    assert cfg.operator_name == "A" and cfg.operator_address == ["Street 1", "12345 Town"]


def test_production_needs_a_secret_or_explicit_open_registration(monkeypatch):
    cfg = Settings()
    cfg.environment = "production"
    cfg.registration_secret = None
    cfg.open_registration = False
    with pytest.raises(RuntimeError, match="GARAGE16_OPEN_REGISTRATION"):
        check_registration_settings(cfg)
    cfg.open_registration = True
    check_registration_settings(cfg)
    cfg.open_registration = False
    cfg.registration_secret = "x"
    check_registration_settings(cfg)


def test_download_links_default_to_the_newest_github_release(monkeypatch):
    for name in ("GARAGE16_CLIENT_DOWNLOAD_URL", "GARAGE16_CLIENT_APP_DOWNLOAD_URL",
                 "GARAGE16_CLIENT_RELEASE_PAGE_URL", "GARAGE16_GITHUB_REPO"):
        monkeypatch.delenv(name, raising=False)
    cfg = Settings()
    base = "https://github.com/ry2en-X/garage16-lmu/releases/latest"
    assert cfg.client_download_url == f"{base}/download/Garage16-Client-Setup.exe"
    assert cfg.client_app_download_url == f"{base}/download/Garage16-Client-Analysis-Setup.exe"
    assert cfg.client_release_page_url == base
    # the installer names are the ones the build produces
    from pathlib import Path

    iss = Path(__file__).resolve().parents[2].joinpath("packaging", "garage16.iss").read_text(encoding="utf-8")
    assert '"Garage16-Client-Setup"' in iss and '"Garage16-Client-Analysis-Setup"' in iss
    # an explicit .env value wins; an empty repo switches the defaults off
    monkeypatch.setenv("GARAGE16_CLIENT_DOWNLOAD_URL", "https://nas.example/setup.exe")
    monkeypatch.setenv("GARAGE16_GITHUB_REPO", "")
    cfg = Settings()
    assert cfg.client_download_url == "https://nas.example/setup.exe"
    assert cfg.client_app_download_url is None and cfg.client_release_page_url is None
