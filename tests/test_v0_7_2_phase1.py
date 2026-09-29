"""Tests for V0.7.2 Phase 1 (completion-sprint audit fixes):

- teams.name is case-insensitively unique (migration 0008)
- GET /teams/{id}/members returns each membership's OWN driver, not the
  caller's — explicit 3-member regression per the spec's own repro steps
  (the underlying code already did this correctly; this test just locks
  it in) — and does so in a bounded number of queries, not one set of
  queries per member (the N+1 that WAS real).
- DELETE /accounts/me refuses to silently orphan a team the caller owns.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import event


def _register(client, name):
    from server.rate_limit import reset_all

    # This test file registers more drivers in a single test than the
    # default "register" rate-limit bucket allows (5/hour) — resetting
    # here is a test-infra concern only (proving the member-list query
    # count doesn't scale with team size has nothing to do with
    # rate-limiting), same as conftest.py's own per-test reset.
    reset_all()
    resp = client.post("/accounts/register", params={"display_name": name})
    assert resp.status_code == 200
    return resp.json()


def _auth(account):
    return {"Authorization": f"Bearer {account['auth_token']}"}


def _create_team(client, owner, name):
    resp = client.post("/teams", json={"name": name}, headers=_auth(owner))
    assert resp.status_code == 200
    return resp.json()


def _join_team(client, member, invite_code):
    resp = client.post("/teams/join", json={"invite_code": invite_code}, headers=_auth(member))
    assert resp.status_code == 200
    return resp.json()


def _upload(client, account, track="Monza", car_name="GT3 #1", car_class="GT3", car_model="911 GT3 R", lap_time=90.0):
    from client.telemetry.recorder import LapRecorder
    from client.uploader.uploader import Uploader

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        n = 50
        speed = 4500.0 / lap_time
        df = pd.DataFrame({
            "t": np.linspace(0, lap_time, n), "lap_dist": np.linspace(0, 4500, n),
            "speed": np.full(n, speed), "throttle": np.full(n, 0.8), "brake": np.zeros(n),
            "clutch": np.zeros(n), "steering": np.zeros(n), "gear": np.full(n, 4),
            "rpm": np.full(n, 6000.0), "fuel": np.linspace(50, 48, n),
        })
        pp = tmp_dir / "lap.parquet"
        df.to_parquet(pp, index=False)
        meta = {
            "track_name": track, "car_name": car_name, "car_class": car_class, "car_model": car_model,
            "session_type": 10, "lap_number": 1, "lap_time": lap_time,
            "sector_times": [lap_time / 3] * 3, "is_valid": True, "started_at": 0.0,
            "recorded_at": 1700000000, "sample_count": n, "telemetry_file": "lap.parquet", "uploaded": False,
        }
        mp = tmp_dir / "lap.json"
        mp.write_text(json.dumps(meta), encoding="utf-8")
        uploader = Uploader(recorder=LapRecorder(data_dir=str(tmp_dir)), auth_token=account["auth_token"], client_secret=account["client_secret"])
        payload = uploader._build_payload(mp)
        resp = client.post(
            "/telemetry/upload",
            data={"envelope": json.dumps(payload["envelope"]), "signature": payload["signature"]},
            files={"telemetry": ("lap.parquet", payload["telemetry_path"].read_bytes())},
            headers={"Authorization": f"Bearer {account['auth_token']}"},
        )
    assert resp.status_code == 200
    return resp.json()


# --- §10: team name case-insensitivity ------------------------------------

def test_team_name_collides_case_insensitively_on_create(client):
    owner = _register(client, "Owner1")
    first = _create_team(client, owner, "Garage16")
    assert first["name"] == "Garage16"

    other = _register(client, "Owner2")
    resp = client.post("/teams", json={"name": "garage16"}, headers=_auth(other))
    assert resp.status_code == 400

    resp2 = client.post("/teams", json={"name": "GARAGE16"}, headers=_auth(other))
    assert resp2.status_code == 400


def test_team_rename_also_respects_case_insensitive_uniqueness(client):
    owner1 = _register(client, "Owner1")
    _create_team(client, owner1, "Alpha Squad")
    owner2 = _register(client, "Owner2")
    team2 = _create_team(client, owner2, "Bravo Squad")

    resp = client.patch(f"/teams/{team2['id']}", json={"name": "alpha squad"}, headers=_auth(owner2))
    assert resp.status_code == 400

    # Renaming to its own current name (any case) must still be allowed —
    # the exclude_team_id in _name_taken must actually exclude self.
    resp2 = client.patch(f"/teams/{team2['id']}", json={"name": "BRAVO SQUAD"}, headers=_auth(owner2))
    assert resp2.status_code == 200


# --- §9.1 / §9.6: member list correctness + query count --------------------

def test_member_list_returns_each_members_own_driver_not_the_callers(client):
    owner = _register(client, "Alice")
    team = _create_team(client, owner, "Three Person Team")
    bob = _register(client, "Bob")
    _join_team(client, bob, team["invite_code"])
    carol = _register(client, "Carol")
    _join_team(client, carol, team["invite_code"])

    # Query as each of the three members in turn — the response must
    # always contain all three distinct real names, never the caller's
    # own name duplicated across rows.
    for caller in (owner, bob, carol):
        resp = client.get(f"/teams/{team['id']}/members", headers=_auth(caller))
        assert resp.status_code == 200
        names = sorted(m["display_name"] for m in resp.json())
        assert names == ["Alice", "Bob", "Carol"]


def test_member_list_query_count_does_not_scale_with_member_count(client):
    owner = _register(client, "Owner")
    team = _create_team(client, owner, "Scaling Team")
    members = [owner]
    for i in range(8):
        m = _register(client, f"Member{i}")
        _join_team(client, m, team["invite_code"])
        members.append(m)
    for m in members[:5]:
        _upload(client, m, lap_time=90.0 + members.index(m))

    from server.database import engine

    counts = {}

    def _count(conn, cursor, statement, *args):
        counts["n"] = counts.get("n", 0) + 1

    event.listen(engine, "before_cursor_execute", _count)
    try:
        resp = client.get(f"/teams/{team['id']}/members", headers=_auth(owner))
    finally:
        event.remove(engine, "before_cursor_execute", _count)

    assert resp.status_code == 200
    assert len(resp.json()) == 9
    # Fixed, small number of queries (team lookup, membership check,
    # one membership+driver query, three aggregate queries) — NOT
    # O(member count). 9 members at ~3 queries/member (the old N+1
    # shape) would be 27+; this must stay well under that regardless of
    # exactly how many fixed queries the endpoint ends up using.
    assert counts["n"] < 12, f"expected a bounded query count, got {counts['n']} for 9 members"


# --- §3.10: account deletion vs. team ownership ----------------------------

def test_solo_owner_can_delete_account_and_their_team_goes_with_it(client):
    owner = _register(client, "SoloOwner")
    team = _create_team(client, owner, "Solo Team")

    resp = client.delete("/accounts/me", headers=_auth(owner))
    assert resp.status_code == 204

    # The team had exactly one member (the owner) — it must be gone too,
    # not left behind as an orphan with zero members.
    other = _register(client, "Someone Else")
    join = client.post("/teams/join", json={"invite_code": team["invite_code"]}, headers=_auth(other))
    assert join.status_code == 404


def test_owner_of_multi_member_team_cannot_delete_account_without_resolving_ownership(client):
    owner = _register(client, "TeamOwner")
    team = _create_team(client, owner, "Guarded Team")
    mate = _register(client, "Teammate")
    _join_team(client, mate, team["invite_code"])

    resp = client.delete("/accounts/me", headers=_auth(owner))
    assert resp.status_code == 409
    assert "Guarded Team" in resp.json()["detail"]

    # Nothing was deleted — the owner's account and the team must both
    # still be fully intact after the blocked attempt.
    still_there = client.get(f"/teams/{team['id']}", headers=_auth(owner))
    assert still_there.status_code == 200
    assert still_there.json()["my_role"] == "owner"


def test_owner_can_delete_account_after_transferring_ownership(client):
    owner = _register(client, "TeamOwner2")
    team = _create_team(client, owner, "Transferable Team")
    mate = _register(client, "Teammate2")
    _join_team(client, mate, team["invite_code"])

    transfer = client.post(
        f"/teams/{team['id']}/transfer-ownership",
        json={"new_owner_driver_id": mate["driver_id"]},
        headers=_auth(owner),
    )
    assert transfer.status_code == 200

    resp = client.delete("/accounts/me", headers=_auth(owner))
    assert resp.status_code == 204

    detail = client.get(f"/teams/{team['id']}", headers=_auth(mate))
    assert detail.status_code == 200
    assert detail.json()["my_role"] == "owner"


def test_admin_delete_driver_also_blocked_by_default_but_force_transfers_ownership(client):
    owner = _register(client, "AdminTarget")
    team = _create_team(client, owner, "Admin Guarded Team")
    mate = _register(client, "AdminTargetMate")
    _join_team(client, mate, team["invite_code"])

    admin_headers = {"X-Admin-Token": "test-admin-token"}
    blocked = client.delete(f"/admin/drivers/{owner['driver_id']}", headers=admin_headers)
    assert blocked.status_code == 409

    forced = client.delete(f"/admin/drivers/{owner['driver_id']}", params={"force": True}, headers=admin_headers)
    assert forced.status_code == 200

    detail = client.get(f"/teams/{team['id']}", headers=_auth(mate))
    assert detail.status_code == 200
    assert detail.json()["my_role"] == "owner"


def test_deleting_account_that_owns_no_team_is_unaffected_by_the_guard(client):
    """Regression guard: the new ownership check must not affect the
    common case (an account that isn't a team owner) at all."""
    plain = _register(client, "PlainDriver")
    resp = client.delete("/accounts/me", headers=_auth(plain))
    assert resp.status_code == 204
