"""
Tests for the V0.6.8 team dashboard: roles/permissions, live-computed
stats (no artificial counter columns), team-scoped leaderboard, team
records, and the activity console — all reusing Lap/RecordEvent/
TeamMembership rather than new storage (server/routers/teams.py).
"""

import json
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd


def _register(client, name):
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


def _upload(client, account, track, car_name, car_class, car_model, lap_time=90.0, is_valid=True):
    from client.telemetry.recorder import LapRecorder
    from client.uploader.uploader import Uploader

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        n = 50
        salt = (hash((track, car_name, car_model, lap_time)) % 1000) / 10000.0
        speed_ms = 50.0 + salt
        df = pd.DataFrame({
            "t": np.linspace(0, lap_time, n), "lap_dist": np.linspace(0, speed_ms * lap_time, n),
            "speed": np.full(n, speed_ms), "throttle": np.full(n, 0.8), "brake": np.zeros(n),
            "clutch": np.zeros(n), "steering": np.zeros(n), "gear": np.full(n, 4),
            "rpm": np.full(n, 6000.0), "fuel": np.linspace(50, 48, n),
        })
        parquet_path = tmp_dir / "lap.parquet"
        df.to_parquet(parquet_path, index=False)
        meta_path = tmp_dir / "lap.json"
        meta_path.write_text(json.dumps({
            "track_name": track, "car_name": car_name, "car_class": car_class,
            "car_model": car_model, "session_type": 10, "lap_number": 1,
            "lap_time": lap_time, "sector_times": [lap_time / 3] * 3, "is_valid": is_valid,
            "started_at": 0.0, "recorded_at": 1700000000, "sample_count": n,
            "telemetry_file": "lap.parquet", "uploaded": False,
        }), encoding="utf-8")
        uploader = Uploader(
            recorder=LapRecorder(data_dir=str(tmp_dir)),
            auth_token=account["auth_token"], client_secret=account["client_secret"],
        )
        payload = uploader._build_payload(meta_path)
        resp = client.post(
            "/telemetry/upload",
            data={"envelope": json.dumps(payload["envelope"]), "signature": payload["signature"]},
            files={"telemetry": ("lap.parquet", payload["telemetry_path"].read_bytes())},
            headers=_auth(account),
        )
    assert resp.status_code == 200, resp.text
    return resp.json()


# --------------------------------------------------------------- roles --

def test_creator_becomes_owner(client):
    owner = _register(client, "Owner")
    team = _create_team(client, owner, "G16 Racing")
    assert team["role"] == "owner"


def test_joiner_becomes_plain_member(client):
    owner = _register(client, "Owner2")
    team = _create_team(client, owner, "Team B")
    member = _register(client, "Member2")
    joined = _join_team(client, member, team["invite_code"])
    assert joined["role"] == "member"


def test_non_member_cannot_view_team_detail(client):
    owner = _register(client, "Owner3")
    team = _create_team(client, owner, "Team C")
    outsider = _register(client, "Outsider")
    resp = client.get(f"/teams/{team['id']}", headers=_auth(outsider))
    assert resp.status_code == 403


# --------------------------------------------------------- team detail --

def test_team_detail_computes_stats_live_not_from_counters(client):
    owner = _register(client, "StatsOwner")
    team = _create_team(client, owner, "Stats Team")
    member = _register(client, "StatsMember")
    _join_team(client, member, team["invite_code"])

    _upload(client, owner, "Le Mans", "499P #50", "Hyper", "397_25_499P", lap_time=90.0)
    _upload(client, member, "Le Mans", "499P #51", "Hyper", "397_25_499P", lap_time=91.0)

    resp = client.get(f"/teams/{team['id']}", headers=_auth(owner))
    assert resp.status_code == 200
    detail = resp.json()
    assert detail["member_count"] == 2
    assert detail["total_laps"] == 2
    assert detail["active_driver_count"] == 2  # both uploaded just now
    assert detail["team_best_count"] == 1  # same (track, car_model) combo
    assert detail["my_role"] == "owner"


def test_only_owner_or_admin_can_update_team(client):
    owner = _register(client, "UpdOwner")
    team = _create_team(client, owner, "Upd Team")
    member = _register(client, "UpdMember")
    _join_team(client, member, team["invite_code"])

    resp = client.patch(f"/teams/{team['id']}", json={"description": "hi"}, headers=_auth(member))
    assert resp.status_code == 403

    resp = client.patch(f"/teams/{team['id']}", json={"description": "Our team"}, headers=_auth(owner))
    assert resp.status_code == 200
    assert resp.json()["description"] == "Our team"


def test_only_owner_can_delete_team(client):
    owner = _register(client, "DelOwner")
    team = _create_team(client, owner, "Del Team")
    member = _register(client, "DelMember")
    _join_team(client, member, team["invite_code"])

    resp = client.delete(f"/teams/{team['id']}", headers=_auth(member))
    assert resp.status_code == 403

    resp = client.delete(f"/teams/{team['id']}", headers=_auth(owner))
    assert resp.status_code == 200
    assert client.get(f"/teams/{team['id']}", headers=_auth(owner)).status_code == 404


# ------------------------------------------------------------- members --

def test_member_list_shows_roles_and_activity(client):
    owner = _register(client, "MemOwner")
    team = _create_team(client, owner, "Mem Team")
    member = _register(client, "MemMember")
    _join_team(client, member, team["invite_code"])
    _upload(client, owner, "Spa", "911 #1", "GT3", "911_gt3", lap_time=100.0)

    resp = client.get(f"/teams/{team['id']}/members", headers=_auth(owner))
    assert resp.status_code == 200
    members = {m["display_name"]: m for m in resp.json()}
    assert members["MemOwner"]["role"] == "owner"
    assert members["MemOwner"]["lap_count"] == 1
    assert members["MemOwner"]["is_active"] is True
    assert members["MemMember"]["role"] == "member"
    assert members["MemMember"]["lap_count"] == 0


def test_owner_cannot_be_removed(client):
    owner = _register(client, "ProtOwner")
    team = _create_team(client, owner, "Prot Team")
    resp = client.delete(f"/teams/{team['id']}/members/{owner['driver_id']}", headers=_auth(owner))
    assert resp.status_code == 400


def test_member_can_remove_themselves(client):
    owner = _register(client, "LeaveOwner")
    team = _create_team(client, owner, "Leave Team")
    member = _register(client, "LeaveMember")
    joined = _join_team(client, member, team["invite_code"])

    resp = client.delete(f"/teams/{team['id']}/members/{member['driver_id']}", headers=_auth(member))
    assert resp.status_code == 200
    resp = client.get(f"/teams/{team['id']}/members", headers=_auth(owner))
    assert len(resp.json()) == 1


def test_plain_member_cannot_remove_another_member(client):
    owner = _register(client, "RmOwner")
    team = _create_team(client, owner, "Rm Team")
    m1 = _register(client, "RmM1")
    m2 = _register(client, "RmM2")
    _join_team(client, m1, team["invite_code"])
    _join_team(client, m2, team["invite_code"])

    resp = client.delete(f"/teams/{team['id']}/members/{m2['driver_id']}", headers=_auth(m1))
    assert resp.status_code == 403


def test_owner_can_promote_and_demote_admin(client):
    owner = _register(client, "PromOwner")
    team = _create_team(client, owner, "Prom Team")
    member = _register(client, "PromMember")
    joined = _join_team(client, member, team["invite_code"])

    resp = client.post(
        f"/teams/{team['id']}/members/{member['driver_id']}/role", json={"role": "admin"}, headers=_auth(owner)
    )
    assert resp.status_code == 200
    assert resp.json()["role"] == "admin"

    # Admin can now update team settings.
    resp = client.patch(f"/teams/{team['id']}", json={"description": "set by admin"}, headers=_auth(member))
    assert resp.status_code == 200

    # But still can't delete the team.
    resp = client.delete(f"/teams/{team['id']}", headers=_auth(member))
    assert resp.status_code == 403


def test_admin_cannot_remove_another_admin(client):
    owner = _register(client, "AdmOwner")
    team = _create_team(client, owner, "Adm Team")
    a1 = _register(client, "Adm1")
    a2 = _register(client, "Adm2")
    _join_team(client, a1, team["invite_code"])
    _join_team(client, a2, team["invite_code"])
    client.post(f"/teams/{team['id']}/members/{a1['driver_id']}/role", json={"role": "admin"}, headers=_auth(owner))
    client.post(f"/teams/{team['id']}/members/{a2['driver_id']}/role", json={"role": "admin"}, headers=_auth(owner))

    resp = client.delete(f"/teams/{team['id']}/members/{a2['driver_id']}", headers=_auth(a1))
    assert resp.status_code == 403


def test_transfer_ownership(client):
    owner = _register(client, "TransOwner")
    team = _create_team(client, owner, "Trans Team")
    member = _register(client, "TransMember")
    _join_team(client, member, team["invite_code"])

    resp = client.post(
        f"/teams/{team['id']}/transfer-ownership",
        json={"new_owner_driver_id": member["driver_id"]}, headers=_auth(owner),
    )
    assert resp.status_code == 200

    members = client.get(f"/teams/{team['id']}/members", headers=_auth(member)).json()
    roles = {m["display_name"]: m["role"] for m in members}
    assert roles["TransMember"] == "owner"
    assert roles["TransOwner"] == "admin"

    # Old owner (now admin) can no longer delete the team.
    resp = client.delete(f"/teams/{team['id']}", headers=_auth(owner))
    assert resp.status_code == 403


# --------------------------------------------------------- leaderboard --

def test_team_leaderboard_excludes_non_members(client):
    owner = _register(client, "LbOwner")
    team = _create_team(client, owner, "Lb Team")
    outsider = _register(client, "LbOutsider")

    _upload(client, owner, "Le Mans", "499P #1", "Hyper", "397_25_499P", lap_time=90.0)
    _upload(client, outsider, "Le Mans", "499P #2", "Hyper", "397_25_499P", lap_time=80.0)  # faster, but not on the team

    resp = client.get(f"/teams/{team['id']}/leaderboard/class/Le%20Mans/Hyper", headers=_auth(owner))
    assert resp.status_code == 200
    board = resp.json()
    assert len(board) == 1
    assert board[0]["driver_name"] == "LbOwner"
    assert board[0]["delta_to_leader"] == 0.0


def test_team_leaderboard_delta_to_leader(client):
    owner = _register(client, "DeltaOwner")
    team = _create_team(client, owner, "Delta Team")
    member = _register(client, "DeltaMember")
    _join_team(client, member, team["invite_code"])

    _upload(client, owner, "Spa", "911 #1", "GT3", "911_gt3", lap_time=100.0)
    _upload(client, member, "Spa", "911 #2", "GT3", "911_gt3", lap_time=102.5)

    resp = client.get(f"/teams/{team['id']}/leaderboard/class/Spa/GT3", headers=_auth(owner))
    board = resp.json()
    assert board[0]["driver_name"] == "DeltaOwner"
    assert board[0]["delta_to_leader"] == 0.0
    assert board[1]["driver_name"] == "DeltaMember"
    assert board[1]["delta_to_leader"] == 2.5


# -------------------------------------------------------------- records --

def test_team_records_one_row_per_track_car_combo(client):
    owner = _register(client, "RecOwner")
    team = _create_team(client, owner, "Rec Team")
    member = _register(client, "RecMember")
    _join_team(client, member, team["invite_code"])

    _upload(client, owner, "Le Mans", "499P #1", "Hyper", "397_25_499P", lap_time=90.0)
    _upload(client, member, "Le Mans", "499P #2", "Hyper", "397_25_499P", lap_time=88.0)  # faster
    _upload(client, owner, "Spa", "911 #1", "GT3", "911_gt3", lap_time=100.0)

    resp = client.get(f"/teams/{team['id']}/records", headers=_auth(owner))
    assert resp.status_code == 200
    records = {r["track_name"]: r for r in resp.json()}
    assert len(records) == 2
    assert records["Le Mans"]["driver_name"] == "RecMember"
    assert records["Le Mans"]["lap_time"] == 88.0
    assert records["Spa"]["driver_name"] == "RecOwner"


# ------------------------------------------------------------- activity --

def test_team_activity_shows_pr_events_for_members_only(client):
    owner = _register(client, "ActOwner")
    team = _create_team(client, owner, "Act Team")
    outsider = _register(client, "ActOutsider")

    _upload(client, owner, "Le Mans", "499P #1", "Hyper", "397_25_499P", lap_time=90.0)
    _upload(client, outsider, "Le Mans", "499P #9", "Hyper", "397_25_499P", lap_time=70.0)

    resp = client.get(f"/teams/{team['id']}/activity", headers=_auth(owner))
    assert resp.status_code == 200
    activity = resp.json()
    assert all(a["driver_name"] == "ActOwner" for a in activity)
    assert len(activity) >= 1


# --------------------------------------------------------- discord flag --

def test_discord_announcements_toggle_defaults_true_and_is_editable(client):
    owner = _register(client, "FlagOwner")
    team = _create_team(client, owner, "Flag Team")
    detail = client.get(f"/teams/{team['id']}", headers=_auth(owner)).json()
    assert detail["discord_announcements_enabled"] is True

    resp = client.patch(
        f"/teams/{team['id']}", json={"discord_announcements_enabled": False}, headers=_auth(owner)
    )
    assert resp.status_code == 200
    assert resp.json()["discord_announcements_enabled"] is False
