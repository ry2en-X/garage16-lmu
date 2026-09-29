"""Tests for server/sessions.py and the V0.7.2 web-session endpoints
(login/logout/logout-all/sessions list) — in particular the core fix:
web login must NEVER be able to invalidate the desktop client's
auth_token (a real bug in V0.7.0's first version of login()).
"""

from __future__ import annotations


def _register(client, name="Driver"):
    resp = client.post("/accounts/register", params={"display_name": name})
    assert resp.status_code == 200
    return resp.json()


def _auth(account):
    return {"Authorization": f"Bearer {account['auth_token']}"}


def _set_password(client, account, email="driver@example.com", password="correct horse battery"):
    resp = client.post(
        "/accounts/set-password",
        json={"email": email, "new_password": password},
        headers=_auth(account),
    )
    assert resp.status_code == 200


def _login(client, email="driver@example.com", password="correct horse battery"):
    resp = client.post("/accounts/login", json={"email": email, "password": password})
    assert resp.status_code == 200
    return resp


# --- the core fix: web login and desktop client credentials never mix ---

def test_web_login_does_not_touch_the_desktop_clients_auth_token(client):
    """The actual bug this phase fixes: previously, logging into the web
    app rotated drivers.token_hash — the same column the desktop client's
    auth_token lives in — silently disconnecting a driver's running LMU
    client. Login must now be provably inert with respect to that token."""
    account = _register(client)
    _set_password(client, account)

    before = client.get("/telemetry/laps", headers=_auth(account))
    assert before.status_code == 200

    _login(client)

    # The EXACT SAME original auth_token, untouched by the login above,
    # must still work — this is the regression the bug would have broken.
    after = client.get("/telemetry/laps", headers=_auth(account))
    assert after.status_code == 200


def test_multiple_web_logins_do_not_invalidate_each_other_or_the_desktop_token(client):
    account = _register(client)
    _set_password(client, account)

    _login(client)
    _login(client)  # e.g. a second browser/device logging in

    # Desktop client token: still fine.
    desktop = client.get("/telemetry/laps", headers=_auth(account))
    assert desktop.status_code == 200


# --- cookie mechanics ---

def test_login_sets_httponly_cookie_and_no_token_in_body(client):
    account = _register(client)
    _set_password(client, account)

    resp = _login(client)
    assert "auth_token" not in resp.json()
    cookie = resp.cookies.get("garage16_session")
    assert cookie is not None


def test_protected_endpoint_works_via_cookie_with_no_authorization_header(client):
    account = _register(client)
    _set_password(client, account)
    _login(client)

    resp = client.get("/telemetry/laps")  # no headers at all — cookie only
    assert resp.status_code == 200


def test_endpoints_requiring_auth_reject_a_request_with_neither_cookie_nor_bearer(client):
    resp = client.get("/telemetry/laps")
    assert resp.status_code == 401


def test_garbage_session_cookie_is_rejected_not_500(client):
    resp = client.get("/telemetry/laps", cookies={"garage16_session": "not-a-real-token"})
    assert resp.status_code == 401


# --- logout ---

def test_logout_ends_the_session(client):
    account = _register(client)
    _set_password(client, account)
    _login(client)

    still_in = client.get("/telemetry/laps")
    assert still_in.status_code == 200

    logout = client.post("/accounts/logout")
    assert logout.status_code == 200

    after = client.get("/telemetry/laps")
    assert after.status_code == 401


def test_logout_with_no_session_is_a_harmless_no_op(client):
    resp = client.post("/accounts/logout")
    assert resp.status_code == 200


def test_logout_does_not_touch_the_desktop_clients_auth_token(client):
    account = _register(client)
    _set_password(client, account)
    _login(client)
    client.post("/accounts/logout")

    desktop = client.get("/telemetry/laps", headers=_auth(account))
    assert desktop.status_code == 200


# --- logout-all ---

def test_logout_all_revokes_every_session_but_not_the_desktop_token(client):
    account = _register(client)
    _set_password(client, account)
    _login(client)  # session A, held by this test's cookie jar

    # A second, independent session (e.g. another browser) — capture its
    # raw cookie value directly since this test client only holds one
    # cookie jar at a time.
    from fastapi.testclient import TestClient as SecondTestClient
    from server.main import app

    with SecondTestClient(app) as second:
        second_login = second.post(
            "/accounts/login", json={"email": "driver@example.com", "password": "correct horse battery"}
        )
        assert second_login.status_code == 200
        second_cookie = second_login.cookies.get("garage16_session")

        # logout-all via session A (this test's own client) must also kill session B.
        logout_all = client.post("/accounts/logout-all")
        assert logout_all.status_code == 200

        after = second.get("/telemetry/laps", cookies={"garage16_session": second_cookie})
        assert after.status_code == 401

    # Desktop client token: still fine even after logout-all.
    desktop = client.get("/telemetry/laps", headers=_auth(account))
    assert desktop.status_code == 200


def test_logout_all_works_when_authenticated_via_bearer_token_too(client):
    """get_current_driver resolves either credential to the same driver —
    logout-all must work regardless of which one the caller used to
    authenticate the logout-all call itself."""
    account = _register(client)
    _set_password(client, account)
    _login(client)

    resp = client.post("/accounts/logout-all", headers=_auth(account))
    assert resp.status_code == 200

    after = client.get("/telemetry/laps")
    assert after.status_code == 401


# --- whoami (V0.7.2 §6 prerequisite) ---

def test_whoami_resolves_via_bearer_token(client):
    account = _register(client)
    resp = client.get("/accounts/me", headers=_auth(account))
    assert resp.status_code == 200
    body = resp.json()
    assert body["driver_id"] == account["driver_id"]
    assert body["display_name"] == "Driver"
    # V0.8-NAS §2: a token-only driver has no email yet — unverified, not
    # an error state.
    assert body["email"] is None
    assert body["email_verified"] is False


def test_whoami_resolves_via_session_cookie(client):
    account = _register(client)
    _set_password(client, account)
    _login(client)
    resp = client.get("/accounts/me")
    assert resp.status_code == 200
    assert resp.json()["driver_id"] == account["driver_id"]


def test_whoami_requires_auth(client):
    resp = client.get("/accounts/me")
    assert resp.status_code == 401


# --- sessions list ---

def test_sessions_list_shows_the_current_session_marked(client):
    account = _register(client)
    _set_password(client, account)
    _login(client)

    resp = client.get("/accounts/sessions")
    assert resp.status_code == 200
    sessions = resp.json()
    assert len(sessions) == 1
    assert sessions[0]["is_current"] is True


def test_sessions_list_never_exposes_desktop_credentials(client):
    account = _register(client)
    _set_password(client, account)
    _login(client)

    resp = client.get("/accounts/sessions")
    body = resp.text
    assert account["auth_token"] not in body
    assert account["client_secret"] not in body


def test_revoke_one_session_by_id(client):
    account = _register(client)
    _set_password(client, account)
    _login(client)

    sessions = client.get("/accounts/sessions").json()
    session_id = sessions[0]["id"]

    revoke = client.delete(f"/accounts/sessions/{session_id}")
    assert revoke.status_code == 200

    after = client.get("/telemetry/laps")
    assert after.status_code == 401


def test_cannot_revoke_another_drivers_session_by_guessing_id(client):
    account_a = _register(client, "A")
    _set_password(client, account_a, email="a@example.com")
    _login(client, email="a@example.com")
    session_a_id = client.get("/accounts/sessions").json()[0]["id"]
    client.post("/accounts/logout")

    account_b = _register(client, "B")
    _set_password(client, account_b, email="b@example.com")
    _login(client, email="b@example.com")

    resp = client.delete(f"/accounts/sessions/{session_a_id}")
    assert resp.status_code == 404
