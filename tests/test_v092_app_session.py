"""V0.9.2: the desktop app's analysis window signs itself in.

POST /accounts/app-session (desktop client, Bearer auth token only) creates an ordinary
web session and returns its token; POST /accounts/app-session/activate (the window,
same origin only) turns it into the usual HttpOnly cookie. See server/routers/accounts.py.
"""

from __future__ import annotations

from server.config import settings

SAME_ORIGIN = {"Origin": "http://testserver", "Sec-Fetch-Site": "same-origin"}


def _register(client, name="Driver"):
    resp = client.post("/accounts/register", params={"display_name": name})
    assert resp.status_code == 200
    return resp.json()


def _auth(account):
    return {"Authorization": f"Bearer {account['auth_token']}"}


def _app_session(client, account):
    resp = client.post("/accounts/app-session", headers=_auth(account))
    assert resp.status_code == 200, resp.text
    return resp


def test_the_desktop_client_gets_a_web_session_for_its_window(client):
    account = _register(client)
    resp = _app_session(client, account)
    body = resp.json()
    assert body["driver_id"] == account["driver_id"] and body["display_name"] == "Driver"
    assert body["expires_in"] == 30 * 24 * 3600
    assert len(body["session_token"]) >= 32
    assert resp.headers["cache-control"] == "no-store"
    assert "garage16_session" not in resp.cookies, "the client's own HTTP session gets no cookie"
    # it is an ordinary web session: listed (and revocable) under the account's sessions
    sessions = client.get("/accounts/sessions", headers=_auth(account)).json()
    assert any("Garage16 desktop app" in (s.get("user_agent") or "") for s in sessions)


def test_the_desktop_token_keeps_working(client):
    account = _register(client)
    _app_session(client, account)
    _app_session(client, account)
    assert client.get("/telemetry/laps", headers=_auth(account)).status_code == 200


def test_activation_sets_the_cookie_and_the_window_is_signed_in(client):
    account = _register(client)
    token = _app_session(client, account).json()["session_token"]
    resp = client.post("/accounts/app-session/activate", json={"session_token": token}, headers=SAME_ORIGIN)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"driver_id": account["driver_id"], "display_name": "Driver"}
    assert resp.cookies.get("garage16_session") == token
    client.cookies.set("garage16_session", token)
    me = client.get("/accounts/me")
    assert me.status_code == 200 and me.json()["driver_id"] == account["driver_id"]


def test_a_browser_cannot_mint_app_sessions_with_its_cookie(client):
    """Only the desktop client's Bearer token may create one — a cookie session (any
    browser, any page that manages a same-site request) must not."""
    account = _register(client)
    token = _app_session(client, account).json()["session_token"]
    client.cookies.set("garage16_session", token)
    resp = client.post("/accounts/app-session")
    assert resp.status_code == 403


def test_app_session_needs_a_valid_desktop_token(client):
    assert client.post("/accounts/app-session").status_code == 401
    assert client.post("/accounts/app-session", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_activation_refuses_cross_site_requests(client):
    """Login CSRF: another site must not be able to sign a visitor's browser into an
    account whose session token the attacker owns."""
    account = _register(client)
    token = _app_session(client, account).json()["session_token"]
    for headers in (
        {"Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"},
        {"Origin": "https://evil.example"},
        {"Sec-Fetch-Site": "cross-site"},
        {"Sec-Fetch-Site": "same-site", "Origin": "https://sub.testserver"},
        {"Origin": "null"},                     # a local file or sandboxed frame
        {},                                     # no origin information at all
    ):
        resp = client.post("/accounts/app-session/activate", json={"session_token": token}, headers=headers)
        assert resp.status_code == 403, headers
        assert "garage16_session" not in resp.cookies


def test_activation_accepts_a_configured_frontend_origin(client, monkeypatch):
    """Split deployments (web frontend on its own domain, listed in CORS origins)."""
    account = _register(client)
    token = _app_session(client, account).json()["session_token"]
    monkeypatch.setattr(settings, "cors_origins", ["https://web.example"])
    resp = client.post("/accounts/app-session/activate", json={"session_token": token},
                       headers={"Origin": "https://web.example"})
    assert resp.status_code == 200


def test_activation_rejects_unknown_revoked_and_short_tokens(client):
    account = _register(client)
    resp = client.post("/accounts/app-session/activate", json={"session_token": "x" * 43}, headers=SAME_ORIGIN)
    assert resp.status_code == 401
    assert client.post("/accounts/app-session/activate", json={"session_token": "short"}, headers=SAME_ORIGIN).status_code == 422
    token = _app_session(client, account).json()["session_token"]
    # sign out everywhere (as the account page does) -> the app session is gone too
    assert client.post("/accounts/logout-all", headers=_auth(account)).status_code == 200
    resp = client.post("/accounts/app-session/activate", json={"session_token": token}, headers=SAME_ORIGIN)
    assert resp.status_code == 401


def test_a_locked_account_gets_no_app_session(client):
    from server.database import SessionLocal
    from server.models import Driver

    account = _register(client)
    token = _app_session(client, account).json()["session_token"]
    db = SessionLocal()
    try:
        driver = db.get(Driver, account["driver_id"])
        driver.is_locked = True
        db.commit()
    finally:
        db.close()
    assert client.post("/accounts/app-session", headers=_auth(account)).status_code == 403
    resp = client.post("/accounts/app-session/activate", json={"session_token": token}, headers=SAME_ORIGIN)
    assert resp.status_code in (401, 403)
    assert "garage16_session" not in resp.cookies


def test_app_sessions_are_rate_limited(client, monkeypatch):
    monkeypatch.setitem(settings.rate_limits, "app_session", (3, 3600.0))
    account = _register(client)
    codes = [client.post("/accounts/app-session", headers=_auth(account)).status_code for _ in range(5)]
    assert codes[:3] == [200, 200, 200] and codes[3:] == [429, 429]


def test_health_offers_both_downloads_only_when_configured(client, monkeypatch):
    monkeypatch.setattr(settings, "client_download_url", None)
    monkeypatch.setattr(settings, "client_app_download_url", None)
    body = client.get("/health").json()
    assert "client_download_url" not in body and "client_app_download_url" not in body
    monkeypatch.setattr(settings, "client_download_url", "https://dl.example/Garage16-Client-Setup.exe")
    monkeypatch.setattr(settings, "client_app_download_url", "https://dl.example/Garage16-Client-Analysis-Setup.exe")
    body = client.get("/health").json()
    assert body["client_download_url"].endswith("Garage16-Client-Setup.exe")
    assert body["client_app_download_url"].endswith("Garage16-Client-Analysis-Setup.exe")


def test_startup_warns_about_a_broken_app_download_url():
    from server.config import Settings, warn_about_questionable_client_facing_urls

    cfg = Settings()
    cfg.migrated_to_url = None
    cfg.client_download_url = None
    cfg.client_app_download_url = "ftp://dl.example/x.exe"
    assert any("CLIENT_APP_DOWNLOAD_URL" in m for m in warn_about_questionable_client_facing_urls(cfg))
    cfg.client_app_download_url = "https://dl.example/x.exe"
    assert warn_about_questionable_client_facing_urls(cfg) == []


def test_an_outdated_client_learns_the_download_of_both_editions(client, monkeypatch):
    """426 UPDATE_REQUIRED keeps its V0.8.6 shape; app_download_url is added only when set."""
    import json
    import tempfile
    from pathlib import Path

    from client.telemetry.recorder import LapRecorder
    from client.uploader.uploader import Uploader
    from tests.test_integration_upload_flow import _register as register, _write_recorded_lap

    monkeypatch.setattr(settings, "min_client_version", "9.9.9")
    monkeypatch.setattr(settings, "client_download_url", "https://dl.example/standard")
    monkeypatch.setattr(settings, "client_app_download_url", "https://dl.example/analysis")
    account = register(client)
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
    detail = resp.json()["detail"]
    assert resp.status_code == 426
    assert detail["download_url"] == "https://dl.example/standard"
    assert detail["app_download_url"] == "https://dl.example/analysis"
