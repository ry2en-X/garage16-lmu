"""Changing the driver's display name (V0.8.9). Until now the name was whatever
was typed into the 'New driver' box at registration — even a throwaway "Test"
— and no page or endpoint could ever change it."""

from __future__ import annotations


def _register(client, name="Test"):
    resp = client.post("/accounts/register", params={"display_name": name})
    assert resp.status_code == 200
    return resp.json()


def _auth(account):
    return {"Authorization": f"Bearer {account['auth_token']}"}


def _rename(client, account, name):
    return client.patch("/accounts/me", json={"display_name": name}, headers=_auth(account))


def test_a_driver_can_rename_themselves(client):
    account = _register(client, "Test")
    resp = _rename(client, account, "Jordan Example")
    assert resp.status_code == 200 and resp.json()["display_name"] == "Jordan Example"
    assert client.get("/accounts/me", headers=_auth(account)).json()["display_name"] == "Jordan Example"


def test_the_new_name_shows_up_everywhere_at_once_including_old_laps(client):
    from tests.test_catalog import _upload

    account = _register(client, "Test")
    _upload(client, account, "Monza", "Car #1", "GT3", "car_a", lap_time=90.0)
    team = client.post("/teams", json={"name": "Renamers"}, headers=_auth(account)).json()

    _rename(client, account, "Jordan Example")

    board = client.get("/leaderboard/class/Monza/GT3").json()
    assert [e["driver_name"] for e in board] == ["Jordan Example"]
    assert client.get(f"/drivers/{account['driver_id']}/public").json()["display_name"] == "Jordan Example"
    members = client.get(f"/teams/{team['id']}/members", headers=_auth(account)).json()
    assert members[0]["display_name"] == "Jordan Example"
    assert any(d["display_name"] == "Jordan Example" for d in client.get("/drivers/lookup", params={"display_name": "Jordan Example"}, headers=_auth(account)).json())


def test_whitespace_is_trimmed_and_collapsed(client):
    account = _register(client)
    assert _rename(client, account, "   Jordan     Example  ").json()["display_name"] == "Jordan Example"


def test_blank_names_are_rejected_and_the_old_name_stays(client):
    account = _register(client, "Keep Me")
    assert _rename(client, account, "     ").status_code == 400
    assert _rename(client, account, "").status_code == 422
    assert client.get("/accounts/me", headers=_auth(account)).json()["display_name"] == "Keep Me"


def test_overlong_names_are_rejected(client):
    account = _register(client)
    assert _rename(client, account, "x" * 61).status_code == 422
    assert _rename(client, account, "x" * 60).status_code == 200


def test_renaming_requires_being_signed_in_and_only_changes_yourself(client):
    a, b = _register(client, "Alice"), _register(client, "Bob")
    assert client.patch("/accounts/me", json={"display_name": "Nope"}).status_code == 401
    _rename(client, a, "Alice Renamed")
    assert client.get("/accounts/me", headers=_auth(b)).json()["display_name"] == "Bob"


def test_renaming_works_for_a_web_login_session_too_and_leaves_email_and_desktop_token_alone(client):
    account = _register(client, "Test")
    client.post("/accounts/set-password", json={"email": "d@example.com", "new_password": "correct horse battery"}, headers=_auth(account))
    assert client.post("/accounts/login", json={"email": "d@example.com", "password": "correct horse battery"}).status_code == 200
    resp = client.patch("/accounts/me", json={"display_name": "Cookie Rename"})  # session cookie only, no bearer
    assert resp.status_code == 200 and resp.json()["email"] == "d@example.com"
    assert client.get("/accounts/me", headers=_auth(account)).status_code == 200  # the desktop token still works
