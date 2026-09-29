"""Tests for the V0.7.2 §9.2 team invitation model (server/models.py's
TeamInvitation, server/routers/teams.py's invitation endpoints, and
server/routers/drivers.py's name lookup)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


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


def _lookup_id(client, account, display_name):
    resp = client.get("/drivers/lookup", params={"display_name": display_name}, headers=_auth(account))
    assert resp.status_code == 200
    matches = resp.json()
    assert len(matches) == 1
    return matches[0]["driver_id"]


# --- driver lookup --------------------------------------------------------

def test_lookup_finds_exact_case_insensitive_match(client):
    owner = _register(client, "Owner")
    target = _register(client, "Nito")

    resp = client.get("/drivers/lookup", params={"display_name": "NITO"}, headers=_auth(owner))
    assert resp.status_code == 200
    assert resp.json() == [{"driver_id": target["driver_id"], "display_name": "Nito"}]


def test_lookup_requires_auth(client):
    resp = client.get("/drivers/lookup", params={"display_name": "Anyone"})
    assert resp.status_code == 401


def test_lookup_returns_empty_list_for_no_match(client):
    owner = _register(client, "Owner")
    resp = client.get("/drivers/lookup", params={"display_name": "Nobody Here"}, headers=_auth(owner))
    assert resp.status_code == 200
    assert resp.json() == []


def test_lookup_returns_all_matches_when_names_collide(client):
    owner = _register(client, "Owner")
    _register(client, "Duplicate")
    _register(client, "Duplicate")

    resp = client.get("/drivers/lookup", params={"display_name": "Duplicate"}, headers=_auth(owner))
    assert resp.status_code == 200
    assert len(resp.json()) == 2


# --- creating invitations ---------------------------------------------

def test_owner_can_invite_an_existing_driver(client):
    owner = _register(client, "Owner")
    team = _create_team(client, owner, "Invite Team")
    target = _register(client, "Invitee")

    resp = client.post(
        f"/teams/{team['id']}/invitations", json={"invited_driver_id": target["driver_id"]}, headers=_auth(owner)
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "pending"
    assert body["invited_display_name"] == "Invitee"
    assert body["created_by_display_name"] == "Owner"


def test_plain_member_cannot_invite(client):
    owner = _register(client, "Owner")
    team = _create_team(client, owner, "Members Cant Invite")
    member = _register(client, "Member")
    client.post("/teams/join", json={"invite_code": team["invite_code"]}, headers=_auth(member))
    target = _register(client, "Target")

    resp = client.post(
        f"/teams/{team['id']}/invitations", json={"invited_driver_id": target["driver_id"]}, headers=_auth(member)
    )
    assert resp.status_code == 403


def test_cannot_invite_someone_already_on_the_team(client):
    owner = _register(client, "Owner")
    team = _create_team(client, owner, "Already Member Team")
    member = _register(client, "AlreadyIn")
    client.post("/teams/join", json={"invite_code": team["invite_code"]}, headers=_auth(member))

    resp = client.post(
        f"/teams/{team['id']}/invitations", json={"invited_driver_id": member["driver_id"]}, headers=_auth(owner)
    )
    assert resp.status_code == 409


def test_cannot_send_a_second_pending_invitation_to_the_same_driver(client):
    owner = _register(client, "Owner")
    team = _create_team(client, owner, "No Duplicate Invites")
    target = _register(client, "Target")

    first = client.post(
        f"/teams/{team['id']}/invitations", json={"invited_driver_id": target["driver_id"]}, headers=_auth(owner)
    )
    assert first.status_code == 200

    second = client.post(
        f"/teams/{team['id']}/invitations", json={"invited_driver_id": target["driver_id"]}, headers=_auth(owner)
    )
    assert second.status_code == 409


def test_invite_nonexistent_driver_404s(client):
    owner = _register(client, "Owner")
    team = _create_team(client, owner, "Nonexistent Target Team")
    resp = client.post(f"/teams/{team['id']}/invitations", json={"invited_driver_id": 999999}, headers=_auth(owner))
    assert resp.status_code == 404


# --- accept / decline ---------------------------------------------------

def test_accept_creates_membership_and_marks_accepted(client):
    owner = _register(client, "Owner")
    team = _create_team(client, owner, "Accept Team")
    target = _register(client, "Accepter")

    invite = client.post(
        f"/teams/{team['id']}/invitations", json={"invited_driver_id": target["driver_id"]}, headers=_auth(owner)
    ).json()

    accept = client.post(f"/invitations/{invite['id']}/accept", headers=_auth(target))
    assert accept.status_code == 200
    assert accept.json()["status"] == "accepted"

    members = client.get(f"/teams/{team['id']}/members", headers=_auth(owner)).json()
    assert any(m["driver_id"] == target["driver_id"] for m in members)


def test_decline_does_not_create_membership(client):
    owner = _register(client, "Owner")
    team = _create_team(client, owner, "Decline Team")
    target = _register(client, "Decliner")

    invite = client.post(
        f"/teams/{team['id']}/invitations", json={"invited_driver_id": target["driver_id"]}, headers=_auth(owner)
    ).json()

    decline = client.post(f"/invitations/{invite['id']}/decline", headers=_auth(target))
    assert decline.status_code == 200
    assert decline.json()["status"] == "declined"

    members = client.get(f"/teams/{team['id']}/members", headers=_auth(owner)).json()
    assert not any(m["driver_id"] == target["driver_id"] for m in members)


def test_only_the_invited_driver_can_accept(client):
    owner = _register(client, "Owner")
    team = _create_team(client, owner, "Wrong Acceptor Team")
    target = _register(client, "RealTarget")
    someone_else = _register(client, "SomeoneElse")

    invite = client.post(
        f"/teams/{team['id']}/invitations", json={"invited_driver_id": target["driver_id"]}, headers=_auth(owner)
    ).json()

    resp = client.post(f"/invitations/{invite['id']}/accept", headers=_auth(someone_else))
    assert resp.status_code == 404  # not "belongs to someone else" — no enumeration


def test_cannot_accept_an_already_accepted_invitation_twice(client):
    owner = _register(client, "Owner")
    team = _create_team(client, owner, "Double Accept Team")
    target = _register(client, "DoubleAccepter")

    invite = client.post(
        f"/teams/{team['id']}/invitations", json={"invited_driver_id": target["driver_id"]}, headers=_auth(owner)
    ).json()
    client.post(f"/invitations/{invite['id']}/accept", headers=_auth(target))

    second = client.post(f"/invitations/{invite['id']}/accept", headers=_auth(target))
    assert second.status_code == 400


def test_accepting_while_already_a_member_is_idempotent_not_an_error(client):
    """Edge case: driver joined via the legacy invite_code in the
    meantime, then also accepts the pending invitation — must not crash
    or create a duplicate membership."""
    owner = _register(client, "Owner")
    team = _create_team(client, owner, "Idempotent Accept Team")
    target = _register(client, "AlreadyJoinedViaCode")

    invite = client.post(
        f"/teams/{team['id']}/invitations", json={"invited_driver_id": target["driver_id"]}, headers=_auth(owner)
    ).json()
    client.post("/teams/join", json={"invite_code": team["invite_code"]}, headers=_auth(target))

    accept = client.post(f"/invitations/{invite['id']}/accept", headers=_auth(target))
    assert accept.status_code == 200

    members = client.get(f"/teams/{team['id']}/members", headers=_auth(owner)).json()
    assert sum(1 for m in members if m["driver_id"] == target["driver_id"]) == 1


# --- revoke / resend / expiry -------------------------------------------

def test_owner_can_revoke_a_pending_invitation(client):
    owner = _register(client, "Owner")
    team = _create_team(client, owner, "Revoke Team")
    target = _register(client, "ToBeRevoked")

    invite = client.post(
        f"/teams/{team['id']}/invitations", json={"invited_driver_id": target["driver_id"]}, headers=_auth(owner)
    ).json()

    revoke = client.post(f"/teams/{team['id']}/invitations/{invite['id']}/revoke", headers=_auth(owner))
    assert revoke.status_code == 200
    assert revoke.json()["status"] == "revoked"

    accept = client.post(f"/invitations/{invite['id']}/accept", headers=_auth(target))
    assert accept.status_code == 400


def test_revoking_after_acceptance_is_rejected(client):
    owner = _register(client, "Owner")
    team = _create_team(client, owner, "Late Revoke Team")
    target = _register(client, "AlreadyAccepted")

    invite = client.post(
        f"/teams/{team['id']}/invitations", json={"invited_driver_id": target["driver_id"]}, headers=_auth(owner)
    ).json()
    client.post(f"/invitations/{invite['id']}/accept", headers=_auth(target))

    revoke = client.post(f"/teams/{team['id']}/invitations/{invite['id']}/revoke", headers=_auth(owner))
    assert revoke.status_code == 400


def test_expired_invitation_cannot_be_accepted_and_shows_as_expired(client):
    owner = _register(client, "Owner")
    team = _create_team(client, owner, "Expiry Team")
    target = _register(client, "TooLate")

    invite = client.post(
        f"/teams/{team['id']}/invitations", json={"invited_driver_id": target["driver_id"]}, headers=_auth(owner)
    ).json()

    from server.database import SessionLocal
    from server.models import TeamInvitation
    db = SessionLocal()
    try:
        row = db.query(TeamInvitation).filter(TeamInvitation.id == invite["id"]).one()
        row.expires_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1)
        db.add(row)
        db.commit()
    finally:
        db.close()

    accept = client.post(f"/invitations/{invite['id']}/accept", headers=_auth(target))
    assert accept.status_code == 400

    listing = client.get(f"/teams/{team['id']}/invitations", headers=_auth(owner)).json()
    assert next(i for i in listing if i["id"] == invite["id"])["status"] == "expired"


def test_resend_revives_an_expired_invitation(client):
    owner = _register(client, "Owner")
    team = _create_team(client, owner, "Resend Team")
    target = _register(client, "ResendTarget")

    invite = client.post(
        f"/teams/{team['id']}/invitations", json={"invited_driver_id": target["driver_id"]}, headers=_auth(owner)
    ).json()

    from server.database import SessionLocal
    from server.models import TeamInvitation
    db = SessionLocal()
    try:
        row = db.query(TeamInvitation).filter(TeamInvitation.id == invite["id"]).one()
        row.expires_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1)
        db.add(row)
        db.commit()
    finally:
        db.close()

    resend = client.post(f"/teams/{team['id']}/invitations/{invite['id']}/resend", headers=_auth(owner))
    assert resend.status_code == 200
    assert resend.json()["status"] == "pending"

    accept = client.post(f"/invitations/{invite['id']}/accept", headers=_auth(target))
    assert accept.status_code == 200


def test_resend_does_not_revive_a_declined_invitation(client):
    owner = _register(client, "Owner")
    team = _create_team(client, owner, "No Resend After Decline")
    target = _register(client, "DeclinedTarget")

    invite = client.post(
        f"/teams/{team['id']}/invitations", json={"invited_driver_id": target["driver_id"]}, headers=_auth(owner)
    ).json()
    client.post(f"/invitations/{invite['id']}/decline", headers=_auth(target))

    resend = client.post(f"/teams/{team['id']}/invitations/{invite['id']}/resend", headers=_auth(owner))
    assert resend.status_code == 400


# --- listings -------------------------------------------------------------

def test_my_invitations_shows_pending_invite_across_teams(client):
    owner1 = _register(client, "Owner1")
    team1 = _create_team(client, owner1, "Team One")
    owner2 = _register(client, "Owner2")
    team2 = _create_team(client, owner2, "Team Two")
    target = _register(client, "MultiInvited")

    client.post(f"/teams/{team1['id']}/invitations", json={"invited_driver_id": target["driver_id"]}, headers=_auth(owner1))
    client.post(f"/teams/{team2['id']}/invitations", json={"invited_driver_id": target["driver_id"]}, headers=_auth(owner2))

    mine = client.get("/invitations/mine", headers=_auth(target))
    assert mine.status_code == 200
    assert len(mine.json()) == 2


def test_non_admin_cannot_list_team_invitations(client):
    owner = _register(client, "Owner")
    team = _create_team(client, owner, "Private Invites Team")
    member = _register(client, "PlainMember")
    client.post("/teams/join", json={"invite_code": team["invite_code"]}, headers=_auth(member))

    resp = client.get(f"/teams/{team['id']}/invitations", headers=_auth(member))
    assert resp.status_code == 403
