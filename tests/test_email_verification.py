"""Tests for V0.8-NAS §2 email verification: server/routers/accounts.py's
verify-email/resend and verify-email/confirm, and the email_verified
gating behavior (or deliberate lack thereof — see login()'s docstring)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest


def _register(client, name="Driver"):
    resp = client.post("/accounts/register", params={"display_name": name})
    assert resp.status_code == 200
    return resp.json()


def _auth(account):
    return {"Authorization": f"Bearer {account['auth_token']}"}


# --- email capture, same pattern as tests/test_password_auth.py's ---

_captured_bodies: dict[str, list] = {}


@pytest.fixture(autouse=True)
def _capture_sent_emails(monkeypatch):
    from server import email_provider
    from server.routers import accounts as accounts_router

    sent = []

    class _CapturingProvider:
        def send(self, to, subject, body):
            sent.append(body)

    monkeypatch.setattr(email_provider, "get_email_provider", lambda: _CapturingProvider())
    monkeypatch.setattr(accounts_router, "get_email_provider", lambda: _CapturingProvider())
    _captured_bodies["sent"] = sent
    yield sent


def _find_raw_token_for_last_email() -> str:
    body = _captured_bodies["sent"][-1]
    marker = "token="
    start = body.index(marker) + len(marker)
    end = body.index("\n", start)
    return body[start:end].strip()


def _set_password(client, account, email="driver@example.com", password="correct horse battery"):
    resp = client.post(
        "/accounts/set-password", json={"email": email, "new_password": password}, headers=_auth(account)
    )
    assert resp.status_code == 200


# --- set-password triggers verification -----------------------------------

def test_setting_email_sends_a_verification_email(client):
    account = _register(client)
    _set_password(client, account)
    assert len(_captured_bodies["sent"]) == 1
    assert "driver@example.com" not in _captured_bodies["sent"][0]  # body is generic, not a leak check per se
    assert "token=" in _captured_bodies["sent"][0]


def test_new_email_starts_unverified(client):
    account = _register(client)
    _set_password(client, account)
    who = client.get("/accounts/me", headers=_auth(account)).json()
    assert who["email"] == "driver@example.com"
    assert who["email_verified"] is False


def test_changing_password_with_same_email_does_not_resend_or_unverify(client):
    """A plain password change must not re-trigger verification — only an
    actual email change should."""
    account = _register(client)
    _set_password(client, account, password="first password here")

    # Manually mark verified, as if they'd clicked the link already.
    from server.database import SessionLocal
    from server.models import Driver
    db = SessionLocal()
    try:
        d = db.query(Driver).filter(Driver.id == account["driver_id"]).one()
        d.email_verified = True
        d.email_verified_at = datetime.now(timezone.utc).replace(tzinfo=None)
        db.add(d)
        db.commit()
    finally:
        db.close()

    sent_before = len(_captured_bodies["sent"])
    change = client.post(
        "/accounts/set-password",
        json={"email": "driver@example.com", "new_password": "second password here", "current_password": "first password here"},
        headers=_auth(account),
    )
    assert change.status_code == 200
    assert len(_captured_bodies["sent"]) == sent_before  # no new verification email

    who = client.get("/accounts/me", headers=_auth(account)).json()
    assert who["email_verified"] is True  # still verified — untouched


def test_changing_to_a_different_email_resets_verification_and_resends(client):
    account = _register(client)
    _set_password(client, account, email="old@example.com", password="first password here")

    from server.database import SessionLocal
    from server.models import Driver
    db = SessionLocal()
    try:
        d = db.query(Driver).filter(Driver.id == account["driver_id"]).one()
        d.email_verified = True
        db.add(d)
        db.commit()
    finally:
        db.close()

    change = client.post(
        "/accounts/set-password",
        json={"email": "new@example.com", "new_password": "second password here", "current_password": "first password here"},
        headers=_auth(account),
    )
    assert change.status_code == 200

    who = client.get("/accounts/me", headers=_auth(account)).json()
    assert who["email"] == "new@example.com"
    assert who["email_verified"] is False


# --- resend ---------------------------------------------------------------

def test_resend_verification_sends_another_email(client):
    account = _register(client)
    _set_password(client, account)
    sent_before = len(_captured_bodies["sent"])

    resp = client.post("/accounts/verify-email/resend", headers=_auth(account))
    assert resp.status_code == 200
    assert len(_captured_bodies["sent"]) == sent_before + 1


def test_resend_without_any_email_set_is_rejected(client):
    account = _register(client)
    resp = client.post("/accounts/verify-email/resend", headers=_auth(account))
    assert resp.status_code == 400


def test_resend_when_already_verified_is_a_harmless_no_op(client):
    account = _register(client)
    _set_password(client, account)

    from server.database import SessionLocal
    from server.models import Driver
    db = SessionLocal()
    try:
        d = db.query(Driver).filter(Driver.id == account["driver_id"]).one()
        d.email_verified = True
        db.add(d)
        db.commit()
    finally:
        db.close()

    sent_before = len(_captured_bodies["sent"])
    resp = client.post("/accounts/verify-email/resend", headers=_auth(account))
    assert resp.status_code == 200
    assert resp.json()["status"] == "already_verified"
    assert len(_captured_bodies["sent"]) == sent_before  # no email sent


def test_resend_requires_auth(client):
    resp = client.post("/accounts/verify-email/resend")
    assert resp.status_code == 401


# --- confirm ----------------------------------------------------------

def test_confirm_verifies_the_email(client):
    account = _register(client)
    _set_password(client, account)
    token = _find_raw_token_for_last_email()

    confirm = client.post("/accounts/verify-email/confirm", json={"token": token})
    assert confirm.status_code == 200

    who = client.get("/accounts/me", headers=_auth(account)).json()
    assert who["email_verified"] is True


def test_confirm_token_is_single_use(client):
    account = _register(client)
    _set_password(client, account)
    token = _find_raw_token_for_last_email()

    client.post("/accounts/verify-email/confirm", json={"token": token})
    replay = client.post("/accounts/verify-email/confirm", json={"token": token})
    assert replay.status_code == 400


def test_confirm_rejects_garbage_token(client):
    resp = client.post("/accounts/verify-email/confirm", json={"token": "not-a-real-token"})
    assert resp.status_code == 400


def test_confirm_rejects_expired_token(client):
    account = _register(client)
    _set_password(client, account)
    token = _find_raw_token_for_last_email()

    from server.database import SessionLocal
    from server.models import EmailVerificationToken
    from server.security import hash_token
    db = SessionLocal()
    try:
        row = db.query(EmailVerificationToken).filter(EmailVerificationToken.token_hash == hash_token(token)).one()
        row.expires_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1)
        db.add(row)
        db.commit()
    finally:
        db.close()

    resp = client.post("/accounts/verify-email/confirm", json={"token": token})
    assert resp.status_code == 400


def test_confirming_a_stale_token_after_a_later_email_change_verifies_nothing(client):
    """The core correctness guarantee from EmailVerificationToken's
    docstring: a token captured for an OLD email must not verify
    whatever email the driver has NOW."""
    account = _register(client)
    _set_password(client, account, email="old@example.com", password="first password here")
    old_token = _find_raw_token_for_last_email()

    client.post(
        "/accounts/set-password",
        json={"email": "new@example.com", "new_password": "second password here", "current_password": "first password here"},
        headers=_auth(account),
    )

    confirm = client.post("/accounts/verify-email/confirm", json={"token": old_token})
    assert confirm.status_code == 400

    who = client.get("/accounts/me", headers=_auth(account)).json()
    assert who["email_verified"] is False


# --- login is NOT gated by email_verified (deliberate — see login()'s docstring) ---

def test_login_works_even_when_email_is_unverified(client):
    account = _register(client)
    _set_password(client, account)
    login = client.post("/accounts/login", json={"email": "driver@example.com", "password": "correct horse battery"})
    assert login.status_code == 200
