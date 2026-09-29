"""Deleting a driver leaves nothing behind (V0.8.7) — self-service and
admin — verified on every table that references drivers.

Found via the PostgreSQL run: the cascade used to miss link_codes,
password_reset_tokens, sessions, email_verification_tokens and
team_invitations. SQLite doesn't enforce foreign keys so nothing failed
there; on PostgreSQL DELETE /accounts/me returned 500 for any driver with
such a row. These tests assert the OUTCOME (no row anywhere still points
at the driver) rather than just a status code, so they catch the problem
on SQLite too."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from server.database import SessionLocal
from server.driver_lifecycle import HANDLED_DRIVER_REFERENCES
from server.models import Base, Driver, PasswordResetToken
from tests.test_moderation import _upload_lap

ADMIN = {"X-Admin-Token": "test-admin-token"}


def _register(client, name):
    resp = client.post("/accounts/register", params={"display_name": name})
    assert resp.status_code == 200
    return resp.json()


def _auth(account):
    return {"Authorization": f"Bearer {account['auth_token']}"}


def _rows_pointing_at(driver_id: int) -> dict:
    """{ 'table.column': count } for every FK to drivers.id, from the LIVE
    metadata (not the hand-kept list), so a table added tomorrow is
    checked too."""
    counts = {}
    db = SessionLocal()
    try:
        for table in Base.metadata.sorted_tables:
            for fk in table.foreign_keys:
                if fk.column.table.name == "drivers":
                    n = db.execute(select(func.count()).select_from(table).where(fk.parent == driver_id)).scalar()
                    counts[f"{table.name}.{fk.parent.name}"] = n
    finally:
        db.close()
    return counts


def test_every_foreign_key_to_drivers_is_handled_by_the_deletion_cascade():
    """The guard: add a table with a foreign key to drivers and this fails
    until server/driver_lifecycle.py deletes it too."""
    actual = {
        (table.name, fk.parent.name)
        for table in Base.metadata.sorted_tables
        for fk in table.foreign_keys
        if fk.column.table.name == "drivers"
    }
    assert actual == set(HANDLED_DRIVER_REFERENCES)


def _populate_everything(client) -> dict:
    """A driver with a row in EVERY table that references drivers."""
    subject = _register(client, "Subject")
    other = _register(client, "Other")
    third = _register(client, "Third")

    # email_verification_tokens (set-password issues one) + sessions (web login)
    client.post("/accounts/set-password", json={"email": "subject@example.com", "new_password": "correct horse battery"}, headers=_auth(subject))
    assert client.post("/accounts/login", json={"email": "subject@example.com", "password": "correct horse battery"}).status_code == 200

    # password_reset_tokens
    db = SessionLocal()
    try:
        db.add(PasswordResetToken(token_hash="deletion-test-hash", driver_id=subject["driver_id"],
                                  expires_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1)))
        db.commit()
    finally:
        db.close()

    # link_codes
    assert client.post("/accounts/discord/link-code", headers=_auth(subject)).status_code == 200

    # laps + record_events (uploading a first lap sets a record)
    lap = _upload_lap(client, subject)
    # lap_reports: someone reports Subject's lap, and Subject reports someone else's
    assert client.post(f"/telemetry/laps/{lap}/report", json={"reason": "suspicious lap"}, headers=_auth(other)).status_code == 201
    others_lap = _upload_lap(client, other, lap_time=95.0)
    assert client.post(f"/telemetry/laps/{others_lap}/report", json={"reason": "also suspicious"}, headers=_auth(subject)).status_code == 201

    # team_memberships + team_invitations in both directions, on a team that
    # SURVIVES (owned by someone else) so the invitation rows aren't just
    # deleted along with a solo-owned team
    team = client.post("/teams", json={"name": "Survivors"}, headers=_auth(other)).json()
    client.post("/teams/join", json={"invite_code": team["invite_code"]}, headers=_auth(subject))
    assert client.post(f"/teams/{team['id']}/members/{subject['driver_id']}/role", json={"role": "admin"}, headers=_auth(other)).status_code == 200
    sent = client.post(f"/teams/{team['id']}/invitations", json={"invited_driver_id": third["driver_id"]}, headers=_auth(subject))
    assert sent.status_code == 200, sent.text  # created_by = Subject
    other_team = client.post("/teams", json={"name": "Elsewhere"}, headers=_auth(third)).json()
    received = client.post(f"/teams/{other_team['id']}/invitations", json={"invited_driver_id": subject["driver_id"]}, headers=_auth(third))
    assert received.status_code == 200, received.text  # invited = Subject

    # every FK table really has a row for Subject before we delete (so the
    # test can't pass vacuously)
    before = _rows_pointing_at(subject["driver_id"])
    assert all(n > 0 for n in before.values()), before
    return {"subject": subject, "other": other, "third": third, "team": team}


def test_self_service_deletion_removes_every_trace(client):
    ctx = _populate_everything(client)
    subject = ctx["subject"]

    resp = client.delete("/accounts/me", headers=_auth(subject))
    assert resp.status_code == 204, resp.text

    assert all(n == 0 for n in _rows_pointing_at(subject["driver_id"]).values()), _rows_pointing_at(subject["driver_id"])
    db = SessionLocal()
    try:
        assert db.get(Driver, subject["driver_id"]) is None
        assert db.get(Driver, ctx["other"]["driver_id"]) is not None  # others untouched
    finally:
        db.close()
    # the surviving team still works for its owner
    assert client.get(f"/teams/{ctx['team']['id']}", headers=_auth(ctx["other"])).status_code == 200


def test_admin_deletion_removes_every_trace_too(client):
    ctx = _populate_everything(client)
    subject = ctx["subject"]

    resp = client.delete(f"/admin/drivers/{subject['driver_id']}", headers=ADMIN)
    assert resp.status_code == 200, resp.text

    assert all(n == 0 for n in _rows_pointing_at(subject["driver_id"]).values()), _rows_pointing_at(subject["driver_id"])
    db = SessionLocal()
    try:
        assert db.get(Driver, subject["driver_id"]) is None
    finally:
        db.close()


def test_deleting_a_brand_new_driver_with_nothing_still_works(client):
    account = _register(client, "Fresh")
    assert client.delete("/accounts/me", headers=_auth(account)).status_code == 204
