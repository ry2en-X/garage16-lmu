"""team_lifecycle.py — team deletion and ownership-transfer logic shared
between routers/teams.py's own DELETE /teams/{id} and account deletion
(routers/accounts.py, routers/admin.py — see V0.7.2's account-deletion
ownership guard). One implementation, not two copies that could drift.
"""

from __future__ import annotations

from typing import List, Optional

from sqlalchemy.orm import Session

from .models import DiscordChannel, RecordEvent, Team, TeamMembership


def delete_team_cascade(db: Session, team: Team) -> None:
    """FK-safe team deletion: null out RecordEvent.team_id (records stay,
    same principle as admin lap invalidation — history isn't destroyed
    just because the team is gone), delete DiscordChannel rows pointing
    at this team (previously missed here — harmless on SQLite, which
    doesn't enforce the FK, but a real IntegrityError on PostgreSQL, the
    actual production database), delete TeamMembership rows, then the
    Team row itself. Does not commit — caller owns the transaction.
    """
    db.query(RecordEvent).filter(RecordEvent.team_id == team.id).update({RecordEvent.team_id: None})
    db.query(DiscordChannel).filter(DiscordChannel.team_id == team.id).delete(synchronize_session=False)
    db.query(TeamMembership).filter(TeamMembership.team_id == team.id).delete(synchronize_session=False)
    db.delete(team)


def transfer_ownership_to_next_member(db: Session, team: Team, *, excluding_driver_id: int) -> TeamMembership:
    """Promotes the earliest-joined remaining member to owner. Same
    heuristic as migration 0006's original backfill (lowest membership id
    = joined first, since team creation didn't record anything more
    precise before that migration). Caller must already know at least one
    other member exists — this raises LookupError if not, rather than
    silently doing nothing."""
    next_membership = (
        db.query(TeamMembership)
        .filter(TeamMembership.team_id == team.id, TeamMembership.driver_id != excluding_driver_id)
        .order_by(TeamMembership.id.asc())
        .first()
    )
    if next_membership is None:
        raise LookupError(f"Team {team.id} has no other member to transfer ownership to.")
    next_membership.role = "owner"
    db.add(next_membership)
    return next_membership


class TeamOwnershipConflict(Exception):
    """Raised by delete_driver_cascade when the driver being deleted owns
    at least one team that still has other members — deleting the account
    outright would leave that team without an owner. Carries the teams
    so the caller can report exactly which ones need resolving."""

    def __init__(self, teams: List[Team]):
        self.teams = teams
        super().__init__(
            "Driver owns team(s) with other members: " + ", ".join(t.name for t in teams)
        )


def resolve_owned_teams_before_driver_deletion(
    db: Session, driver_id: int, *, force: bool
) -> None:
    """Must be called, and succeed, before the rest of a driver-deletion
    cascade touches anything. Two cases per owned team:

      - Solo-owned (driver is the only member): deleted outright as part
        of removing the driver — there's no one to hand it to and no one
        left to miss it.
      - Owned with other members: BLOCKED by default (raises
        TeamOwnershipConflict, before any row is touched) — the spec is
        explicit that the preferred resolution is the owner explicitly
        transferring ownership or deleting the team themselves first, not
        an automatic silent transfer. force=True (admin-only escalation,
        e.g. removing a banned cheater who won't do that themselves)
        instead auto-transfers to the next-earliest-joined member.

    Deliberately computes ALL conflicts before mutating anything: if
    force=False and any conflict exists, nothing is written — the caller
    can raise straight to a 409 with a clean, still-fully-intact DB.
    """
    owned_memberships = (
        db.query(TeamMembership)
        .filter(TeamMembership.driver_id == driver_id, TeamMembership.role == "owner")
        .all()
    )
    if not owned_memberships:
        return

    with_others: List[Team] = []
    solo: List[Team] = []
    for membership in owned_memberships:
        team = db.get(Team, membership.team_id)
        if team is None:
            continue  # already gone somehow — nothing to resolve
        other_count = (
            db.query(TeamMembership)
            .filter(TeamMembership.team_id == team.id, TeamMembership.driver_id != driver_id)
            .count()
        )
        (with_others if other_count > 0 else solo).append(team)

    if with_others and not force:
        raise TeamOwnershipConflict(with_others)

    for team in with_others:  # only reached when force=True
        transfer_ownership_to_next_member(db, team, excluding_driver_id=driver_id)
    for team in solo:
        delete_team_cascade(db, team)
