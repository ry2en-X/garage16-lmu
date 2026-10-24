"""V0.9.7 — the permission matrix, derived from the app itself (its OpenAPI description), so a NEW
endpoint is covered the day it is added:

  * without credentials every operation is refused (401/403) — except the documented public ones
    below; an endpoint that becomes public by accident fails this test;
  * every /admin operation refuses a signed-in driver without the admin token, and a wrong token;
  * every team operation refuses a driver outside the team, and the management operations refuse
    a plain member (no 2xx; never a 500);
  * upload limits: an oversized file is refused while streaming (413), a non-JSON or non-object
    envelope is a 400 — before any file is kept.
"""

from __future__ import annotations

import json
import re

import pytest

from tests.helpers.v093 import headers, register

# What anybody may call without signing in — and why.
PUBLIC = {
    ("GET", "/health"),                                           # reverse proxy / orchestrator check
    ("GET", "/health/live"),                                      # V0.9.8: liveness (process answers)
    ("GET", "/health/ready"),                                     # V0.9.8: readiness (DB, storage) — no details
    ("GET", "/leaderboard/car/{track_name}/{car_model}"),         # the public leaderboards
    ("GET", "/leaderboard/class/{track_name}/{car_class}"),
    ("GET", "/leaderboard/recent"),
    ("GET", "/leaderboard/catalog/cars"),
    ("GET", "/leaderboard/catalog/class-options"),
    ("GET", "/leaderboard/catalog/classes"),
    ("GET", "/leaderboard/catalog/tracks"),
    ("GET", "/leaderboard/catalog/venues"),
    ("GET", "/telemetry/analysis/channels"),                      # the channel catalog (no lap data)
    ("GET", "/telemetry/analysis/circuits"),                      # the circuit library (layouts, no laps)
    ("GET", "/drivers/{driver_id}/public"),                       # a driver's public profile
    ("POST", "/accounts/logout"),                                 # harmless without a session
    # signing up / in and the e-mail flows: public by nature (rate limited)
    ("POST", "/accounts/register"),
    ("POST", "/accounts/signup"),                                 # V1.0: the website's sign-up form
    ("POST", "/accounts/desktop-login"),                          # V1.0: the desktop client's connect
    ("GET", "/legal"),                                            # V1.0: operator data for the legal pages
    ("POST", "/accounts/login"),
    ("POST", "/accounts/app-session/activate"),
    ("POST", "/accounts/password-reset/request"),
    ("POST", "/accounts/password-reset/confirm"),
    ("POST", "/accounts/verify-email/confirm"),
}


def _operations():
    from server.main import app

    for path, ops in sorted(app.openapi()["paths"].items()):
        for method in ops:
            yield method.upper(), path


def _concrete(path: str, **values) -> str:
    return re.sub(r"\{([^}]+)\}", lambda m: str(values.get(m.group(1), 1)), path)


def test_the_public_list_matches_the_app():
    ops = set(_operations())
    assert PUBLIC <= ops, f"documented public endpoints that no longer exist: {sorted(PUBLIC - ops)}"


def test_without_credentials_everything_but_the_public_endpoints_is_refused(client):
    leaks = []
    for method, path in _operations():
        if (method, path) in PUBLIC:
            continue
        resp = client.request(method, _concrete(path))
        if resp.status_code not in (401, 403):
            leaks.append((method, path, resp.status_code))
    assert not leaks, f"reachable without signing in: {leaks}"


def test_admin_endpoints_refuse_drivers_and_wrong_tokens(client):
    driver = register(client, "Perm Driver")
    for method, path in _operations():
        if not path.startswith("/admin"):
            continue
        url = _concrete(path)
        assert client.request(method, url, headers=headers(driver)).status_code == 403, (method, path)
        assert client.request(method, url, headers={"X-Admin-Token": "wrong"}).status_code == 403, (method, path)


BODIES = {
    ("PATCH", "/teams/{team_id}"): {"name": "Taken Over"},
    ("POST", "/teams/{team_id}/invitations"): {"invited_driver_id": 0},
    ("POST", "/teams/{team_id}/members/{target_driver_id}/role"): {"role": "admin"},
    ("POST", "/teams/{team_id}/transfer-ownership"): {"new_owner_driver_id": 0},
}
# what a plain member may do in their own team (read it, leave it)
MEMBER_ALLOWED = {
    ("GET", "/teams/{team_id}"), ("GET", "/teams/{team_id}/members"), ("GET", "/teams/{team_id}/records"),
    ("GET", "/teams/{team_id}/stats"), ("GET", "/teams/{team_id}/activity"),
    ("GET", "/teams/{team_id}/leaderboard/car/{track_name}/{car_model}"),
    ("GET", "/teams/{team_id}/leaderboard/class/{track_name}/{car_class}"),
    ("GET", "/teams/{team_id}/invitations"),
}


@pytest.fixture()
def team(client):
    owner, member, outsider = register(client, "Perm Owner"), register(client, "Perm Member"), register(client, "Perm Outsider")
    created = client.post("/teams", json={"name": "Perm Team"}, headers=headers(owner))
    assert created.status_code == 200, created.text
    body = created.json()
    other = register(client, "Perm Member Two")
    for who in (member, other):
        assert client.post("/teams/join", json={"invite_code": body["invite_code"]}, headers=headers(who)).status_code == 200
    return {"id": body["id"], "owner": owner, "member": member, "other": other, "outsider": outsider}


def _team_ops():
    return [(m, p) for m, p in _operations() if p.startswith("/teams/{team_id}")]


def test_team_endpoints_refuse_drivers_outside_the_team(client, team):
    for method, path in _team_ops():
        body = BODIES.get((method, path))
        url = _concrete(path, team_id=team["id"], target_driver_id=team["owner"]["driver_id"],
                        track_name="Le Mans", car_model="x", car_class="Hyper", invitation_id=1)
        resp = client.request(method, url, headers=headers(team["outsider"]), json=body)
        assert resp.status_code in (403, 404), (method, path, resp.status_code, resp.text[:200])


def test_team_management_refuses_a_plain_member(client, team):
    for method, path in _team_ops():
        if (method, path) in MEMBER_ALLOWED:
            continue
        body = BODIES.get((method, path))
        # another member as the target (removing / promoting someone else; leaving is allowed)
        target = team["other"]["driver_id"]
        if body and "invited_driver_id" in body:
            body = {"invited_driver_id": team["outsider"]["driver_id"]}
        if body and "new_owner_driver_id" in body:
            body = {"new_owner_driver_id": team["member"]["driver_id"]}
        url = _concrete(path, team_id=team["id"], target_driver_id=target, invitation_id=1)
        resp = client.request(method, url, headers=headers(team["member"]), json=body)
        assert resp.status_code in (403, 404), (method, path, resp.status_code, resp.text[:200])
    # and the team is unchanged
    detail = client.get(f"/teams/{team['id']}", headers=headers(team["owner"])).json()
    assert detail["name"] == "Perm Team"


# ---------------------------------------------------------------- upload limits

def test_an_oversized_upload_is_refused_while_streaming(client, monkeypatch):
    from server.config import settings

    me = register(client, "Perm Big")
    monkeypatch.setattr(settings, "max_upload_bytes", 1024)
    from tests.helpers.synthetic_lap import make_lap
    from tests.helpers.v093 import upload_df

    upload_df(client, me, make_lap(seed=5), expect=413)


@pytest.mark.parametrize("envelope", ["not json", "[]", "42", json.dumps({"client_version": "0.9.4"})])
def test_malformed_envelopes_are_a_clean_400(client, envelope):
    me = register(client, f"Perm Env {abs(hash(envelope)) % 1000}")
    resp = client.post("/telemetry/upload", headers=headers(me), data={"envelope": envelope, "signature": "ab"},
                       files={"telemetry": ("lap.parquet", b"x")})
    assert resp.status_code == 400, resp.text
