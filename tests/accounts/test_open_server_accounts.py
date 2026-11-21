"""Account rules for an open server: the email must be confirmed before the desktop client
connects, terms are accepted again when they change, one token per PC, sign-up and sign-in do not
reveal which addresses have accounts, throttling per address, unique driver names, unconfirmed
sign-ups are removed, and GET /health names the newest client."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from server.core.config import settings
from server.core.database import SessionLocal
from server.core.models import DeviceToken, Driver

STRONG = "Correct-Horse-42-battery"


@pytest.fixture(autouse=True)
def sent_emails(monkeypatch):
    from server.accounts import email_provider
    from server.api.v1 import accounts as accounts_router

    sent = []

    class _Capture:
        def send(self, to, subject, body):
            sent.append({"to": to, "subject": subject, "body": body})

    monkeypatch.setattr(email_provider, "get_email_provider", lambda: _Capture())
    monkeypatch.setattr(accounts_router, "get_email_provider", lambda: _Capture())
    return sent


def _signup(client, name="Mika", email="mika@example.com", **extra):
    return client.post("/accounts/signup", json={"display_name": name, "email": email, "password": STRONG,
                                                  "accept_terms": True, "confirm_min_age": True, **extra})


def _update(email, **fields):
    db = SessionLocal()
    try:
        db.query(Driver).filter(Driver.email == email).update(fields)
        db.commit()
    finally:
        db.close()


def _desktop(client, email="mika@example.com", **extra):
    return client.post("/accounts/desktop-login", json={"email": email, "password": STRONG, **extra})


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


# --------------------------------------------------------------- email confirmation before connecting

def test_desktop_login_waits_for_the_confirmed_email(client, sent_emails):
    assert _signup(client).status_code == 202
    assert "verify" in sent_emails[-1]["subject"]
    refused = _desktop(client)
    assert refused.status_code == 409
    assert refused.json()["detail"]["code"] == "email_not_verified"
    token = sent_emails[-1]["body"].split("token=")[1].split()[0]
    assert client.post("/accounts/verify-email/confirm", json={"token": token}).status_code == 200
    assert _desktop(client).status_code == 200


def test_email_confirmation_can_be_switched_off_for_a_closed_circle(client, monkeypatch):
    monkeypatch.setattr(settings, "require_email_verification", False)
    assert _signup(client).status_code == 202
    assert _desktop(client).status_code == 200


# --------------------------------------------------------------- terms accepted again

def _token_account_with_password(client, name="Old", email="old@example.com"):
    account = client.post("/accounts/register", params={"display_name": name}).json()
    assert client.post("/accounts/set-password", json={"email": email, "new_password": STRONG},
                       headers=_bearer(account["auth_token"])).status_code == 200
    _update(email, email_verified=True)
    return account


def test_an_account_from_before_v1_0_accepts_the_terms_when_connecting(client):
    account = _token_account_with_password(client)
    me = client.get("/accounts/me", headers=_bearer(account["auth_token"])).json()
    assert me["terms_required"] is True and me["terms_version"] is None
    refused = _desktop(client, email="old@example.com")
    assert refused.status_code == 409 and refused.json()["detail"]["code"] == "terms_required"
    ok = _desktop(client, email="old@example.com", accept_terms=True)
    assert ok.status_code == 200
    me = client.get("/accounts/me", headers=_bearer(account["auth_token"])).json()
    assert me["terms_required"] is False and me["terms_version"] == me["current_terms_version"]


def test_the_website_asks_for_the_terms_and_records_them(client):
    account = _token_account_with_password(client)
    headers = _bearer(account["auth_token"])
    assert client.post("/accounts/accept-terms", json={"accept_terms": True}, headers=headers).status_code == 400
    assert client.post("/accounts/accept-terms", json={"confirm_min_age": True}, headers=headers).status_code == 400
    resp = client.post("/accounts/accept-terms", json={"accept_terms": True, "confirm_min_age": True}, headers=headers)
    assert resp.status_code == 200 and resp.json()["terms_required"] is False
    login = client.post("/accounts/login", json={"email": "old@example.com", "password": STRONG}).json()
    assert login["terms_required"] is False and login["email_verified"] is True


def test_changed_terms_must_be_accepted_again(client, monkeypatch):
    assert _signup(client).status_code == 202
    _update("mika@example.com", email_verified=True)
    from server.api.v1 import legal

    monkeypatch.setattr(legal, "TERMS_VERSION", "2099-01-01")
    assert _desktop(client).json()["detail"]["code"] == "terms_required"
    assert _desktop(client, accept_terms=True).status_code == 200


# --------------------------------------------------------------- one token per PC

def _connected(client):
    assert _signup(client).status_code == 202
    _update("mika@example.com", email_verified=True)


def test_two_pcs_stay_connected_and_each_can_be_disconnected(client):
    _connected(client)
    rig = _desktop(client, device_name="SIM-RIG").json()
    laptop = _desktop(client, device_name="Laptop").json()
    assert rig["client_secret"] == laptop["client_secret"]
    for creds in (rig, laptop):
        assert client.get("/accounts/me", headers=_bearer(creds["auth_token"])).status_code == 200
    devices = client.get("/accounts/devices", headers=_bearer(rig["auth_token"])).json()
    assert {d["name"] for d in devices} == {"SIM-RIG", "Laptop"}
    assert [d["is_current"] for d in devices if d["name"] == "SIM-RIG"] == [True]
    laptop_id = next(d["id"] for d in devices if d["name"] == "Laptop")
    assert client.delete(f"/accounts/devices/{laptop_id}", headers=_bearer(rig["auth_token"])).json()["status"] == "revoked"
    assert client.get("/accounts/me", headers=_bearer(laptop["auth_token"])).status_code == 401
    assert client.get("/accounts/me", headers=_bearer(rig["auth_token"])).status_code == 200


def test_a_device_of_another_driver_cannot_be_disconnected(client):
    _connected(client)
    rig = _desktop(client).json()
    other = client.post("/accounts/register", params={"display_name": "Other"}).json()
    device_id = client.get("/accounts/devices", headers=_bearer(rig["auth_token"])).json()[0]["id"]
    assert client.delete(f"/accounts/devices/{device_id}", headers=_bearer(other["auth_token"])).status_code == 404


def test_the_pc_used_longest_ago_is_disconnected_above_the_limit(client, monkeypatch):
    monkeypatch.setattr(settings, "max_devices", 2)
    _connected(client)
    first = _desktop(client, device_name="One").json()
    db = SessionLocal()
    try:   # "One" was used a while ago
        db.query(DeviceToken).filter(DeviceToken.name == "One").update(
            {"last_used_at": datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=3)})
        db.commit()
    finally:
        db.close()
    second = _desktop(client, device_name="Two").json()
    third = _desktop(client, device_name="Three").json()
    assert client.get("/accounts/me", headers=_bearer(first["auth_token"])).status_code == 401
    for creds in (second, third):
        assert client.get("/accounts/me", headers=_bearer(creds["auth_token"])).status_code == 200


def test_revoke_with_a_pc_token_disconnects_only_that_pc(client):
    _connected(client)
    rig = _desktop(client, device_name="Rig").json()
    laptop = _desktop(client, device_name="Laptop").json()
    assert client.post("/accounts/revoke", headers=_bearer(rig["auth_token"])).status_code == 204
    assert client.get("/accounts/me", headers=_bearer(rig["auth_token"])).status_code == 401
    assert client.get("/accounts/me", headers=_bearer(laptop["auth_token"])).status_code == 200


def test_a_pc_connected_before_v1_0_1_keeps_its_token(client):
    account = _token_account_with_password(client)
    _desktop(client, email="old@example.com", accept_terms=True)
    # the old per-driver token is untouched by a new PC connecting
    assert client.get("/accounts/me", headers=_bearer(account["auth_token"])).status_code == 200


def test_devices_are_in_the_export_and_names_are_cleaned(client):
    _connected(client)
    rig = _desktop(client, device_name="  Rig\x00  ​ PC  ").json()
    export = client.get("/accounts/me/export", headers=_bearer(rig["auth_token"])).json()
    assert [d["name"] for d in export["devices"]] == ["Rig ​ PC"] or [d["name"] for d in export["devices"]] == ["Rig PC"]
    assert "token" not in str(export["devices"]).lower()


def test_a_locked_account_cannot_use_its_pc_token(client):
    _connected(client)
    rig = _desktop(client).json()
    _update("mika@example.com", is_locked=True)
    assert client.get("/accounts/me", headers=_bearer(rig["auth_token"])).status_code == 403


def test_pc_tokens_upload_laps(client):
    from tests.laps.test_moderation import _upload_lap

    _connected(client)
    creds = _desktop(client).json()
    assert _upload_lap(client, creds)


# --------------------------------------------------------------- no hint which addresses exist

def test_signup_with_a_registered_address_answers_the_same_and_tells_the_owner(client, sent_emails):
    first = _signup(client)
    second = _signup(client, name="Someone Else")
    assert first.status_code == second.status_code == 202 and first.json() == second.json()
    assert sent_emails[-1]["to"] == "mika@example.com" and "already have an account" in sent_emails[-1]["subject"]
    db = SessionLocal()
    try:
        assert db.query(Driver).filter(Driver.email == "mika@example.com").count() == 1
        assert db.query(Driver).filter(Driver.display_name == "Someone Else").count() == 0
    finally:
        db.close()


def test_an_unknown_address_costs_a_password_check_too(monkeypatch, client):
    from server.api.v1 import accounts

    calls = []
    real = accounts.verify_password
    monkeypatch.setattr(accounts, "verify_password", lambda pw, h: calls.append(h) or real(pw, h))
    assert client.post("/accounts/login", json={"email": "nobody@example.com", "password": STRONG}).status_code == 401
    assert client.post("/accounts/desktop-login", json={"email": "nobody@example.com", "password": STRONG}).status_code == 401
    assert len(calls) == 2


# --------------------------------------------------------------- throttling per address

def test_failed_sign_ins_are_counted_per_account(client, monkeypatch):
    monkeypatch.setitem(settings.rate_limits, "login", (1000, 900))
    monkeypatch.setitem(settings.rate_limits, "login_account", (3, 900))
    _connected(client)
    for _ in range(5):      # correct passwords never use up the limit
        assert client.post("/accounts/login", json={"email": "mika@example.com", "password": STRONG}).status_code == 200
    for _ in range(3):
        assert client.post("/accounts/login", json={"email": "mika@example.com", "password": "wrong-pass-1"}).status_code == 401
    # used up: even the right password waits, for the web and the desktop sign-in alike
    assert client.post("/accounts/login", json={"email": "MIKA@example.com", "password": STRONG}).status_code == 429
    assert _desktop(client).status_code == 429
    assert client.post("/accounts/login", json={"email": "b@example.com", "password": "wrong-pass-1"}).status_code == 401


def test_emails_to_one_address_are_limited(client, monkeypatch, sent_emails):
    monkeypatch.setitem(settings.rate_limits, "password_reset", (1000, 3600))
    monkeypatch.setitem(settings.rate_limits, "email_to_address", (2, 3600))
    for _ in range(2):
        assert client.post("/accounts/password-reset/request", json={"email": "x@example.com"}).status_code == 200
    assert client.post("/accounts/password-reset/request", json={"email": "x@example.com"}).status_code == 429


def test_expired_rate_limit_windows_are_dropped():
    from server.core import rate_limit

    rate_limit.reset_all()
    rate_limit._buckets[("old", "ip:1.2.3.4")] = (0.0, 5, 1.0)      # window ran out long ago
    for i in range(rate_limit._PRUNE_EVERY):
        rate_limit._check("live", f"ip:{i % 3}", 10_000, 3600.0)
    assert ("old", "ip:1.2.3.4") not in rate_limit._buckets
    assert ("live", "ip:0") in rate_limit._buckets
    rate_limit.reset_all()


# --------------------------------------------------------------- unique driver names

@pytest.mark.parametrize("a,b", [
    ("Max Mustermann", "max  mustermann"),
    ("Max", "ＭＡＸ"),                 # full-width letters
    ("Max", "Ma​x"),             # zero-width space
    ("Strauß", "STRAUSS"),
])
def test_names_are_compared_in_one_form(a, b):
    from server.accounts.names import name_key

    assert name_key(a) == name_key(b)


def test_reserved_and_invisible_names_are_refused():
    from server.accounts.names import InvalidName, clean_display_name

    for bad in ("admin", "  GARAGE16 ", "​​", "x" * 61):
        with pytest.raises(InvalidName):
            clean_display_name(bad)
    assert clean_display_name("  Mika​  H. ") == "Mika H."


def test_a_taken_name_cannot_be_registered_or_renamed_to(client):
    a = client.post("/accounts/register", params={"display_name": "Mika"}).json()
    assert client.post("/accounts/register", params={"display_name": "MIKA"}).status_code == 409
    b = client.post("/accounts/register", params={"display_name": "Rita"}).json()
    resp = client.patch("/accounts/me", json={"display_name": "mika"}, headers=_bearer(b["auth_token"]))
    assert resp.status_code == 409 and "taken" in resp.json()["detail"]
    # changing only the capitals of your own name is fine
    resp = client.patch("/accounts/me", json={"display_name": "MIKA"}, headers=_bearer(a["auth_token"]))
    assert resp.status_code == 200 and resp.json()["display_name"] == "MIKA"


# --------------------------------------------------------------- unconfirmed sign-ups removed

def test_unconfirmed_signups_without_laps_are_removed_after_the_grace_period(client):
    from server.accounts.driver_lifecycle import prune_unverified_signups
    from tests.laps.test_moderation import _upload_lap

    for name, email in (("Ghost", "ghost@example.com"), ("Kept", "kept@example.com"), ("Racer", "racer@example.com")):
        assert _signup(client, name=name, email=email).status_code == 202
    _update("kept@example.com", email_verified=True)
    _update("racer@example.com", email_verified=True)
    racer = _desktop(client, email="racer@example.com").json()
    assert _upload_lap(client, racer)
    _update("racer@example.com", email_verified=False)             # laps: kept even unconfirmed
    old = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=8)
    for email in ("ghost@example.com", "kept@example.com", "racer@example.com"):
        _update(email, created_at=old)
    token_only = client.post("/accounts/register", params={"display_name": "Token"}).json()   # no terms: never pruned

    db = SessionLocal()
    try:
        assert prune_unverified_signups(db, 7) == 1
        left = {d.display_name for d in db.query(Driver).all()}
    finally:
        db.close()
    assert left == {"Kept", "Racer", "Token"} and token_only
    # the address is free again
    assert _signup(client, name="Ghost", email="ghost@example.com").status_code == 202


# --------------------------------------------------------------- newest client in /health

def test_health_names_the_newest_client(client):
    from server.core.versioning import LATEST_CLIENT_VERSION

    body = client.get("/health", params={"client_version": "1.0.0"}).json()
    assert body["latest_client_version"] == LATEST_CLIENT_VERSION
    assert body["update_available"] is True and body["update_required"] is False
    assert client.get("/health", params={"client_version": LATEST_CLIENT_VERSION}).json()["update_available"] is False


# --------------------------------------------------------------- unit edges (coverage floors 100 %)

class _FakeQuery:
    def __init__(self, result):
        self._result = result

    def filter(self, *_a, **_k):
        return self

    def one_or_none(self):
        return self._result


class _FakeDb:
    def __init__(self, driver=None, device=None, fail_commit=False):
        self.driver, self.device, self.fail_commit = driver, device, fail_commit
        self.commits = self.rollbacks = 0

    def query(self, model):
        return _FakeQuery(self.driver if model is Driver else self.device)

    def commit(self):
        self.commits += 1
        if self.fail_commit:
            raise RuntimeError("database is busy")

    def rollback(self):
        self.rollbacks += 1


def test_last_use_of_a_pc_is_written_at_most_every_ten_minutes():
    from types import SimpleNamespace

    from server.accounts.auth import _touch

    now = datetime.now(timezone.utc)
    recent = SimpleNamespace(last_used_at=now - timedelta(minutes=2))          # aware, recent: no write
    db = _FakeDb()
    _touch(db, recent)
    assert db.commits == 0
    old = SimpleNamespace(last_used_at=(now - timedelta(hours=1)).replace(tzinfo=None))
    _touch(db, old)
    assert db.commits == 1 and old.last_used_at > (now - timedelta(minutes=1)).replace(tzinfo=None)
    busy = _FakeDb(fail_commit=True)
    _touch(busy, SimpleNamespace(last_used_at=None))      # a busy database never fails the request
    assert busy.rollbacks == 1


def test_a_pc_token_whose_driver_is_gone_is_refused():
    from types import SimpleNamespace

    from fastapi import HTTPException

    from server.accounts.auth import get_current_driver

    device = SimpleNamespace(id=7, revoked_at=None, driver=None, last_used_at=datetime.now(timezone.utc))
    request = SimpleNamespace(state=SimpleNamespace())
    with pytest.raises(HTTPException) as exc:
        get_current_driver(request, authorization="Bearer orphan", session_cookie=None, db=_FakeDb(device=device))
    assert exc.value.status_code == 401


def test_pruning_with_zero_days_does_nothing():
    from server.accounts.driver_lifecycle import prune_unverified_signups

    assert prune_unverified_signups(None, 0) == 0


def test_a_used_up_key_is_reported_until_its_window_ends(monkeypatch):
    from server.core import rate_limit

    rate_limit.reset_all()
    monkeypatch.setitem(settings.rate_limits, "login_account", (2, 900))
    assert rate_limit.key_blocked("login_account", "x@example.com") is False
    rate_limit.count_key("login_account", "x@example.com")
    rate_limit.count_key("login_account", "x@example.com")
    assert rate_limit.key_blocked("login_account", "x@example.com") is True
    with pytest.raises(Exception):
        rate_limit.limit_key("login_account", "x@example.com")
    rate_limit.reset_all()
