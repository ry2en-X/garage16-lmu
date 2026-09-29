"""Tests for server/routers/accounts.py's V0.6.9 email/password additions:
set-password, login, change-password, and the password-reset flow.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest


def _register(client, name="Driver"):
    resp = client.post("/accounts/register", params={"display_name": name})
    assert resp.status_code == 200
    return resp.json()


def _auth(account_or_token):
    token = account_or_token if isinstance(account_or_token, str) else account_or_token["auth_token"]
    return {"Authorization": f"Bearer {token}"}


# --- set-password -----------------------------------------------------

def test_set_password_on_fresh_token_only_driver_requires_no_current_password(client):
    account = _register(client)
    resp = client.post(
        "/accounts/set-password",
        json={"email": "Driver@Example.com", "new_password": "correct horse battery"},
        headers=_auth(account),
    )
    assert resp.status_code == 200


def test_set_password_normalizes_and_deduplicates_email_case_insensitively(client):
    account = _register(client)
    ok = client.post(
        "/accounts/set-password",
        json={"email": "Same@Example.com", "new_password": "correct horse battery"},
        headers=_auth(account),
    )
    assert ok.status_code == 200

    other = _register(client, "Other")
    conflict = client.post(
        "/accounts/set-password",
        json={"email": "same@example.com", "new_password": "another password here"},
        headers=_auth(other),
    )
    assert conflict.status_code == 409


def test_set_password_rejects_invalid_email(client):
    account = _register(client)
    resp = client.post(
        "/accounts/set-password",
        json={"email": "not-an-email", "new_password": "correct horse battery"},
        headers=_auth(account),
    )
    assert resp.status_code == 400


def test_set_password_rejects_weak_password(client):
    account = _register(client)
    resp = client.post(
        "/accounts/set-password",
        json={"email": "driver@example.com", "new_password": "short"},
        headers=_auth(account),
    )
    assert resp.status_code == 400


def test_changing_password_requires_current_password(client):
    account = _register(client)
    client.post(
        "/accounts/set-password",
        json={"email": "driver@example.com", "new_password": "first password here"},
        headers=_auth(account),
    )

    wrong_current = client.post(
        "/accounts/set-password",
        json={
            "email": "driver@example.com",
            "new_password": "second password here",
            "current_password": "totally wrong",
        },
        headers=_auth(account),
    )
    assert wrong_current.status_code == 401

    right_current = client.post(
        "/accounts/set-password",
        json={
            "email": "driver@example.com",
            "new_password": "second password here",
            "current_password": "first password here",
        },
        headers=_auth(account),
    )
    assert right_current.status_code == 200


# --- login --------------------------------------------------------------

def test_login_with_correct_credentials_issues_a_working_session_cookie(client):
    account = _register(client)
    client.post(
        "/accounts/set-password",
        json={"email": "driver@example.com", "new_password": "correct horse battery"},
        headers=_auth(account),
    )

    login = client.post(
        "/accounts/login", json={"email": "driver@example.com", "password": "correct horse battery"}
    )
    assert login.status_code == 200
    body = login.json()
    assert body["driver_id"] == account["driver_id"]
    assert "auth_token" not in body  # V0.7.2: cookie-only, never in the response body
    assert "garage16_session" in login.cookies

    # The session cookie (held automatically by the test client, exactly
    # like a browser would) must actually work with no Authorization
    # header at all.
    me = client.get("/telemetry/laps")
    assert me.status_code == 200

    # And it must NOT have touched the desktop client's auth_token — the
    # original registration token still works independently.
    still_works = client.get("/telemetry/laps", headers=_auth(account))
    assert still_works.status_code == 200


def test_login_is_case_insensitive_on_email(client):
    account = _register(client)
    client.post(
        "/accounts/set-password",
        json={"email": "driver@example.com", "new_password": "correct horse battery"},
        headers=_auth(account),
    )
    login = client.post(
        "/accounts/login", json={"email": "DRIVER@EXAMPLE.COM", "password": "correct horse battery"}
    )
    assert login.status_code == 200


def test_login_rejects_wrong_password_with_generic_message(client):
    account = _register(client)
    client.post(
        "/accounts/set-password",
        json={"email": "driver@example.com", "new_password": "correct horse battery"},
        headers=_auth(account),
    )
    resp = client.post("/accounts/login", json={"email": "driver@example.com", "password": "wrong password"})
    assert resp.status_code == 401
    assert "invalid" in resp.json()["detail"].lower()


def test_login_rejects_unknown_email_with_same_generic_message_as_wrong_password(client):
    """No user enumeration: an unknown email and a wrong password must be
    indistinguishable from the response."""
    unknown = client.post("/accounts/login", json={"email": "nobody@example.com", "password": "whatever"})

    account = _register(client)
    client.post(
        "/accounts/set-password",
        json={"email": "driver@example.com", "new_password": "correct horse battery"},
        headers=_auth(account),
    )
    wrong_pw = client.post("/accounts/login", json={"email": "driver@example.com", "password": "wrong password"})

    assert unknown.status_code == wrong_pw.status_code == 401
    assert unknown.json()["detail"] == wrong_pw.json()["detail"]


def test_login_rejects_email_with_no_password_set_yet(client):
    """A driver that exists but never called set-password has no
    password_hash — login must fail the same generic way, not 500."""
    resp = client.post("/accounts/login", json={"email": "nopassword@example.com", "password": "whatever"})
    assert resp.status_code == 401


def test_login_still_grants_web_access_after_the_desktop_token_was_revoked(client):
    """The whole point of adding a password: POST /accounts/revoke is no
    longer a dead end for a driver that has one set — they can still get
    back into the WEB app via login. Revoke only ever killed the desktop
    client's auth_token, and (V0.7.2) login never touches that credential
    at all anymore — it's a fully separate session, not a replacement
    token. So the old auth_token stays dead forever; login grants access
    through its own, independent cookie session instead."""
    account = _register(client)
    client.post(
        "/accounts/set-password",
        json={"email": "driver@example.com", "new_password": "correct horse battery"},
        headers=_auth(account),
    )
    client.post("/accounts/revoke", headers=_auth(account))

    # The old auth_token is now dead — permanently; login does not revive it.
    dead = client.get("/telemetry/laps", headers=_auth(account))
    assert dead.status_code == 401

    # But login still works and grants web access via its own session cookie.
    login = client.post(
        "/accounts/login", json={"email": "driver@example.com", "password": "correct horse battery"}
    )
    assert login.status_code == 200
    alive = client.get("/telemetry/laps")
    assert alive.status_code == 200


def test_locked_driver_cannot_log_in(client):
    account = _register(client)
    client.post(
        "/accounts/set-password",
        json={"email": "driver@example.com", "new_password": "correct horse battery"},
        headers=_auth(account),
    )
    lock = client.post(
        "/admin/drivers/{}/lock".format(account["driver_id"]),
        json={"reason": "test"},
        headers={"X-Admin-Token": "test-admin-token"},
    )
    assert lock.status_code == 200

    login = client.post(
        "/accounts/login", json={"email": "driver@example.com", "password": "correct horse battery"}
    )
    assert login.status_code == 403


# --- password reset -------------------------------------------------------

def test_password_reset_request_always_returns_ok_even_for_unknown_email(client):
    resp = client.post("/accounts/password-reset/request", json={"email": "nobody@example.com"})
    assert resp.status_code == 200


def test_password_reset_full_flow(client):
    account = _register(client)
    client.post(
        "/accounts/set-password",
        json={"email": "driver@example.com", "new_password": "old password here"},
        headers=_auth(account),
    )

    req = client.post("/accounts/password-reset/request", json={"email": "driver@example.com"})
    assert req.status_code == 200

    from server.database import SessionLocal
    from server.models import PasswordResetToken

    db = SessionLocal()
    try:
        row = db.query(PasswordResetToken).filter(PasswordResetToken.driver_id == account["driver_id"]).one()
        raw_token = _find_raw_token_for(row)
    finally:
        db.close()

    confirm = client.post(
        "/accounts/password-reset/confirm",
        json={"token": raw_token, "new_password": "brand new password"},
    )
    assert confirm.status_code == 200

    # New password logs in...
    login_new = client.post(
        "/accounts/login", json={"email": "driver@example.com", "password": "brand new password"}
    )
    assert login_new.status_code == 200

    # ...old one no longer does.
    login_old = client.post(
        "/accounts/login", json={"email": "driver@example.com", "password": "old password here"}
    )
    assert login_old.status_code == 401

    # Token is single-use — replaying it must fail.
    replay = client.post(
        "/accounts/password-reset/confirm",
        json={"token": raw_token, "new_password": "yet another password"},
    )
    assert replay.status_code == 400


def test_password_reset_confirm_rejects_expired_token(client):
    account = _register(client)
    client.post(
        "/accounts/set-password",
        json={"email": "driver@example.com", "new_password": "old password here"},
        headers=_auth(account),
    )
    client.post("/accounts/password-reset/request", json={"email": "driver@example.com"})

    from server.database import SessionLocal
    from server.models import PasswordResetToken

    db = SessionLocal()
    try:
        row = db.query(PasswordResetToken).filter(PasswordResetToken.driver_id == account["driver_id"]).one()
        raw_token = _find_raw_token_for(row)
        row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        db.add(row)
        db.commit()
    finally:
        db.close()

    confirm = client.post(
        "/accounts/password-reset/confirm",
        json={"token": raw_token, "new_password": "brand new password"},
    )
    assert confirm.status_code == 400


def test_password_reset_confirm_rejects_garbage_token(client):
    resp = client.post(
        "/accounts/password-reset/confirm",
        json={"token": "not-a-real-token", "new_password": "brand new password"},
    )
    assert resp.status_code == 400


def test_password_reset_does_not_touch_the_desktop_clients_auth_token(client):
    """Resetting a forgotten web password must not disconnect a working
    desktop client — auth_token/client_secret are a separate credential
    domain (see routers/accounts.py's confirm_password_reset docstring)."""
    account = _register(client)
    client.post(
        "/accounts/set-password",
        json={"email": "driver@example.com", "new_password": "old password here"},
        headers=_auth(account),
    )
    client.post("/accounts/password-reset/request", json={"email": "driver@example.com"})

    from server.database import SessionLocal
    from server.models import PasswordResetToken

    db = SessionLocal()
    try:
        row = db.query(PasswordResetToken).filter(PasswordResetToken.driver_id == account["driver_id"]).one()
        raw_token = _find_raw_token_for(row)
    finally:
        db.close()

    client.post(
        "/accounts/password-reset/confirm",
        json={"token": raw_token, "new_password": "brand new password"},
    )

    # The ORIGINAL desktop-client auth_token from registration still works.
    still_alive = client.get("/telemetry/laps", headers=_auth(account))
    assert still_alive.status_code == 200


# --- helper: recover the raw (pre-hash) reset token for test purposes ----
#
# The DB only ever stores the hash (see PasswordResetToken's docstring) —
# by design, tests can't read the plaintext back out of it either. So this
# monkeypatches the email provider for the duration of one call to capture
# what would have been emailed, which is the only place the raw token
# exists outside the requester's own process memory.

_captured_bodies: dict[int, str] = {}


@pytest.fixture(autouse=True)
def _capture_sent_emails(monkeypatch):
    from server import email_provider

    sent = []

    class _CapturingProvider:
        def send(self, to, subject, body):
            sent.append(body)

    monkeypatch.setattr(email_provider, "get_email_provider", lambda: _CapturingProvider())
    # accounts.py imported get_email_provider directly at module load time,
    # so the router's own reference must be patched too.
    from server.routers import accounts as accounts_router

    monkeypatch.setattr(accounts_router, "get_email_provider", lambda: _CapturingProvider())
    _captured_bodies["sent"] = sent
    yield sent


def _find_raw_token_for(_row) -> str:
    body = _captured_bodies["sent"][-1]
    # body contains "...token=<TOKEN>\n\n..." — extract it without
    # depending on exact surrounding wording.
    marker = "token="
    start = body.index(marker) + len(marker)
    end = body.index("\n", start)
    return body[start:end].strip()
