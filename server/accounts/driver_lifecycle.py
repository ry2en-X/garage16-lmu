"""server/accounts/driver_lifecycle.py — deleting a driver and everything that
points at them.

Both DELETE /accounts/me (self-service) and DELETE /admin/drivers/{id}
used to carry their own copy of this cascade, and both were incomplete:
they never removed link_codes, password_reset_tokens, sessions,
email_verification_tokens or team_invitations. SQLite (this project's
default for tests) doesn't enforce foreign keys, so nothing ever failed —
but PostgreSQL, the real production database, does, so on PostgreSQL an
account deletion returned HTTP 500 for any driver who had ever logged in
on the web, set an email (a verification token row), received or sent a
team invitation, requested a password reset, or generated a Discord link
code. (Found while testing the Discord link rework against real
PostgreSQL; the admin copy also skipped Discord channel cleanup.)

One implementation now, with HANDLED_DRIVER_REFERENCES as the single list
of every (table, column) that references drivers.id.
tests/accounts/test_driver_deletion.py introspects the live SQLAlchemy metadata and
fails if a table gains a foreign key to drivers that isn't listed here — so
the next migration that adds one can't silently re-break account deletion.

Deliberately does NOT resolve team ownership: callers do that first
(resolve_owned_teams_before_driver_deletion) because the policy differs —
self-service refuses, admin may force a transfer.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import List

from sqlalchemy import or_
from sqlalchemy.orm import Session

from server.core.models import (
    DeviceToken,
    DiscordChannel,
    Driver,
    EmailVerificationToken,
    Lap,
    LapReport,
    LinkCode,
    PasswordResetToken,
    RecordEvent,
    TeamInvitation,
    TeamMembership,
    UploadEvent,
)
from server.core.models import (
    Session as WebSession,
)

# Every (table, column) with a foreign key to drivers.id. Keep in sync with
# the models — the guard test enforces it.
HANDLED_DRIVER_REFERENCES = frozenset(
    {
        ("laps", "driver_id"),
        ("record_events", "driver_id"),
        ("lap_reports", "reported_by_driver_id"),
        ("team_memberships", "driver_id"),
        ("team_invitations", "invited_driver_id"),
        ("team_invitations", "created_by_driver_id"),
        ("sessions", "driver_id"),
        ("password_reset_tokens", "driver_id"),
        ("email_verification_tokens", "driver_id"),
        ("link_codes", "driver_id"),
        ("device_tokens", "driver_id"),
    }
)


def delete_driver_rows(db: Session, driver: Driver) -> List[str]:
    """Deletes the driver and every row referencing them, children before
    parents (PostgreSQL enforces the foreign keys), and commits. Returns
    the telemetry file paths of their laps for the caller's best-effort
    file cleanup AFTER this commit — the DB is the source of truth; a
    leftover file is an orphan, not undeleted personal data."""
    driver_id = driver.id
    lap_ids = [lap_id for (lap_id,) in db.query(Lap.id).filter(Lap.driver_id == driver_id).all()]
    telemetry_paths = [p for (p,) in db.query(Lap.telemetry_path).filter(Lap.driver_id == driver_id).all()]

    if lap_ids:
        db.query(RecordEvent).filter(RecordEvent.lap_id.in_(lap_ids)).delete(synchronize_session=False)
        db.query(LapReport).filter(LapReport.lap_id.in_(lap_ids)).delete(synchronize_session=False)
    db.query(RecordEvent).filter(RecordEvent.driver_id == driver_id).delete(synchronize_session=False)
    db.query(LapReport).filter(LapReport.reported_by_driver_id == driver_id).delete(synchronize_session=False)
    db.query(Lap).filter(Lap.driver_id == driver_id).delete(synchronize_session=False)
    db.query(TeamMembership).filter(TeamMembership.driver_id == driver_id).delete(synchronize_session=False)
    db.query(TeamInvitation).filter(
        or_(TeamInvitation.invited_driver_id == driver_id, TeamInvitation.created_by_driver_id == driver_id)
    ).delete(synchronize_session=False)
    db.query(WebSession).filter(WebSession.driver_id == driver_id).delete(synchronize_session=False)
    db.query(PasswordResetToken).filter(PasswordResetToken.driver_id == driver_id).delete(synchronize_session=False)
    db.query(EmailVerificationToken).filter(EmailVerificationToken.driver_id == driver_id).delete(synchronize_session=False)
    db.query(LinkCode).filter(LinkCode.driver_id == driver_id).delete(synchronize_session=False)
    db.query(DeviceToken).filter(DeviceToken.driver_id == driver_id).delete(synchronize_session=False)
    # The upload log of this driver (no FK — it is a log — but it is their data)
    db.query(UploadEvent).filter(UploadEvent.driver_id == driver_id).delete(synchronize_session=False)
    # No FK here (registered_by is a plain string) but it's the driver's data.
    db.query(DiscordChannel).filter(DiscordChannel.registered_by == str(driver_id)).delete(synchronize_session=False)
    # The audit log keeps its events (they are the history of the laps), but no longer the
    # driver's id — and records the deletion itself, with counts only (server/ops/audit.py)
    from server.ops import audit

    audit.clear_driver(db, driver_id)
    db.delete(driver)
    audit.append(db, "driver.deleted", actor="system", detail={"laps": len(lap_ids)})
    db.commit()
    return telemetry_paths


logger = logging.getLogger("lmu_garage.accounts")


def prune_unverified_signups(db: Session, days: int, now: datetime | None = None) -> int:
    """Deletes accounts made on the sign-up form (terms recorded) whose email was never
    confirmed within `days`, as long as they have no laps and are in no team — nothing of theirs
    is lost, and the address can sign up again. Called at server start and on every sign-up.
    Returns how many were deleted. `days` <= 0 does nothing."""
    if days <= 0:
        return 0
    now = (now or datetime.now(timezone.utc)).replace(tzinfo=None)
    cutoff = now - timedelta(days=days)
    has_laps = db.query(Lap.id).filter(Lap.driver_id == Driver.id).exists()
    in_team = db.query(TeamMembership.id).filter(TeamMembership.driver_id == Driver.id).exists()
    candidates = (
        db.query(Driver)
        .filter(Driver.terms_version.isnot(None), Driver.email_verified.is_(False),
                Driver.created_at < cutoff, ~has_laps, ~in_team)
        .limit(200)
        .all()
    )
    for driver in candidates:
        delete_driver_rows(db, driver)
    if candidates:
        logger.info("Removed %d account(s) whose email was not confirmed within %d days", len(candidates), days)
    return len(candidates)
