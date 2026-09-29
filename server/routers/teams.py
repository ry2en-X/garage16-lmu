from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import List, Sequence

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import ColumnElement, func, select
from sqlalchemy.orm import Session, joinedload

from ..auth import get_current_driver
from ..catalog import canonical_class
from ..database import get_db
from ..leaderboard_ranking import ranked_leaderboard
from ..models import Driver, Lap, RecordEvent, Team, TeamInvitation, TeamMembership
from ..rate_limit import rate_limit
from ..team_lifecycle import delete_team_cascade
from ..schemas import (
    DriverLookupOut,
    LinkCodeResponse,
    TeamActivityItem,
    TeamCreateRequest,
    TeamDetail,
    TeamInvitationCreateRequest,
    TeamInvitationOut,
    TeamJoinRequest,
    TeamLeaderboardEntry,
    TeamMemberOut,
    TeamOut,
    TeamStatsOut,
    TeamRecordOut,
    TeamRoleChangeRequest,
    TeamTransferOwnershipRequest,
    TeamUpdateRequest,
)

router = APIRouter(prefix="/teams", tags=["teams"])
invitations_router = APIRouter(prefix="/invitations", tags=["teams"])

TEAM_INVITATION_LIFETIME = timedelta(days=7)

# A driver hasn't uploaded in this many days is considered inactive for
# dashboard purposes (V0.6.8) — purely a display heuristic, not stored or
# enforced anywhere else (e.g. it doesn't affect leaderboard eligibility).
_ACTIVE_WINDOW_DAYS = 14
_PBS_THIS_WEEK_DAYS = 7


def _generate_code(length: int = 8) -> str:
    return secrets.token_urlsafe(length)[:length].upper()


def _get_membership(db: Session, team_id: int, driver_id: int) -> TeamMembership:
    membership = db.query(TeamMembership).filter(
        TeamMembership.team_id == team_id, TeamMembership.driver_id == driver_id
    ).one_or_none()
    if membership is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You're not a member of this team.")
    return membership


def _get_team_or_404(db: Session, team_id: int) -> Team:
    team = db.get(Team, team_id)
    if team is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Team not found.")
    return team


def _require_role(membership: TeamMembership, *allowed: str) -> None:
    if membership.role not in allowed:
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"Requires one of: {', '.join(allowed)}.")


def _team_out(db: Session, team: Team, role: str | None = None) -> TeamOut:
    count = db.query(TeamMembership).filter(TeamMembership.team_id == team.id).count()
    return TeamOut(id=team.id, name=team.name, invite_code=team.invite_code, member_count=count, role=role)


def _name_taken(db: Session, name: str, *, exclude_team_id: int | None = None) -> bool:
    """Case-insensitive: must match the DB-level constraint (migration
    0008's functional index on lower(name)) exactly, or the app-level
    check and the DB constraint could disagree (e.g. reject a name the DB
    would actually have allowed, or vice versa)."""
    query = db.query(Team).filter(func.lower(Team.name) == name.lower())
    if exclude_team_id is not None:
        query = query.filter(Team.id != exclude_team_id)
    return query.one_or_none() is not None


@router.post("", response_model=TeamOut)
def create_team(body: TeamCreateRequest, driver: Driver = Depends(get_current_driver), db: Session = Depends(get_db)) -> TeamOut:
    name = body.name.strip()
    if not name:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Team name cannot be empty.")
    if _name_taken(db, name):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Team name already taken.")
    team = Team(name=name, invite_code=_generate_code())
    db.add(team)
    db.flush()  # assigns team.id within the same transaction
    db.add(TeamMembership(team_id=team.id, driver_id=driver.id, role="owner"))
    db.commit()
    return _team_out(db, team, role="owner")


@router.post("/join", response_model=TeamOut)
def join_team(
    body: TeamJoinRequest,
    driver: Driver = Depends(get_current_driver),
    db: Session = Depends(get_db),
    _rl: None = Depends(rate_limit("team_join", per_driver=True)),
) -> TeamOut:
    team = db.query(Team).filter(Team.invite_code == body.invite_code.strip().upper()).one_or_none()
    if team is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No team with that invite code.")
    existing = db.query(TeamMembership).filter(
        TeamMembership.team_id == team.id, TeamMembership.driver_id == driver.id
    ).one_or_none()
    if existing is None:
        existing = TeamMembership(team_id=team.id, driver_id=driver.id, role="member")
        db.add(existing)
        db.commit()
    return _team_out(db, team, role=existing.role)


@router.get("/mine", response_model=List[TeamOut])
def my_teams(driver: Driver = Depends(get_current_driver), db: Session = Depends(get_db)) -> List[TeamOut]:
    memberships = db.query(TeamMembership).filter(TeamMembership.driver_id == driver.id).all()
    return [_team_out(db, m.team, role=m.role) for m in memberships]


@router.post("/discord-link-code", response_model=LinkCodeResponse)
def create_discord_account_link_code(
    driver: Driver = Depends(get_current_driver),
    db: Session = Depends(get_db),
    _rl: None = Depends(rate_limit("link_code", per_driver=True)),
) -> LinkCodeResponse:
    """DEPRECATED ALIAS (V0.8.7) of POST /accounts/discord/link-code, kept
    so nothing that still calls the old path breaks. It does not belong
    under /teams — it links a driver's own Discord account — and shares the
    exact same implementation, including the "already linked" 409."""
    from .accounts import issue_discord_link_code

    return issue_discord_link_code(driver, db)


# ============================================================= V0.6.8 =====
# Team dashboard: detail/stats, members, settings, roles, leaderboard,
# records, and an activity console — all reusing Lap/RecordEvent/
# TeamMembership rather than new storage. See CHANGELOG.md.


def _team_driver_ids(db: Session, team_id: int) -> List[int]:
    rows = db.execute(
        select(TeamMembership.driver_id).where(TeamMembership.team_id == team_id)
    ).all()
    return [r[0] for r in rows]


@router.get("/{team_id}", response_model=TeamDetail)
def team_detail(team_id: int, driver: Driver = Depends(get_current_driver), db: Session = Depends(get_db)) -> TeamDetail:
    team = _get_team_or_404(db, team_id)
    membership = _get_membership(db, team_id, driver.id)
    driver_ids = _team_driver_ids(db, team_id)

    # DB DateTime columns have no timezone=True, so values round-trip as
    # naive under both SQLite and PostgreSQL — comparing against an aware
    # "now" raises TypeError. .replace(tzinfo=None) keeps the UTC value
    # but drops the tz marker so it compares cleanly against DB reads.
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    active_cutoff = now - timedelta(days=_ACTIVE_WINDOW_DAYS)
    pbs_cutoff = now - timedelta(days=_PBS_THIS_WEEK_DAYS)

    member_count = len(driver_ids)
    active_driver_count = db.execute(
        select(func.count(func.distinct(Lap.driver_id))).where(
            Lap.driver_id.in_(driver_ids), Lap.uploaded_at >= active_cutoff
        )
    ).scalar_one()
    total_laps = db.execute(
        select(func.count()).select_from(Lap).where(Lap.driver_id.in_(driver_ids))
    ).scalar_one()
    pbs_this_week = db.execute(
        select(func.count()).select_from(RecordEvent).where(
            RecordEvent.driver_id.in_(driver_ids),
            RecordEvent.record_type == "PR",
            RecordEvent.created_at >= pbs_cutoff,
        )
    ).scalar_one()
    distinct_track_car = (
        select(Lap.track_name, Lap.car_model)
        .where(Lap.driver_id.in_(driver_ids), Lap.is_valid.is_(True), Lap.car_model.is_not(None))
        .distinct()
        .subquery()
    )
    team_best_count = db.execute(select(func.count()).select_from(distinct_track_car)).scalar_one()

    return TeamDetail(
        id=team.id, name=team.name, description=team.description, invite_code=team.invite_code,
        discord_announcements_enabled=team.discord_announcements_enabled, my_role=membership.role,
        member_count=member_count, active_driver_count=active_driver_count or 0,
        total_laps=total_laps or 0, pbs_this_week=pbs_this_week or 0, team_best_count=team_best_count or 0,
    )


_WINDOW_DAYS = {"today": 1, "7d": 7, "30d": 30}


@router.get("/{team_id}/stats", response_model=TeamStatsOut)
def team_stats(
    team_id: int,
    window: str = Query("all", pattern="^(today|7d|30d|all)$"),
    driver: Driver = Depends(get_current_driver),
    db: Session = Depends(get_db),
) -> TeamStatsOut:
    """V0.7.2 §9.4 — the same four numbers as TeamDetail's fixed-window
    fields (laps/PBs/active drivers), but for a caller-chosen window
    (today/7d/30d/all) instead of TeamDetail's hardcoded 14- and 7-day
    ones. Deliberately a separate endpoint rather than replacing
    TeamDetail's fields — those stay exactly as they were for anyone
    already depending on that response shape; this is additive.

    "today" is the trailing 24 hours, not "since local midnight" — the
    server has no notion of the caller's timezone, and a rolling window
    is simpler to reason about (and to test) than a calendar-day one that
    would silently depend on whichever timezone happened to be used.
    """
    _get_team_or_404(db, team_id)
    _get_membership(db, team_id, driver.id)
    driver_ids = _team_driver_ids(db, team_id)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    window_start = now - timedelta(days=_WINDOW_DAYS[window]) if window in _WINDOW_DAYS else None

    lap_filter = [Lap.driver_id.in_(driver_ids), Lap.is_valid.is_(True)]
    pr_filter = [RecordEvent.driver_id.in_(driver_ids), RecordEvent.record_type == "PR"]
    team_best_filter = [RecordEvent.team_id == team_id, RecordEvent.record_type == "TEAM_BEST"]
    if window_start is not None:
        lap_filter.append(Lap.uploaded_at >= window_start)
        pr_filter.append(RecordEvent.created_at >= window_start)
        team_best_filter.append(RecordEvent.created_at >= window_start)

    laps = db.execute(select(func.count()).select_from(Lap).where(*lap_filter)).scalar_one()
    active_drivers = db.execute(
        select(func.count(func.distinct(Lap.driver_id))).where(*lap_filter)
    ).scalar_one()
    pbs = db.execute(select(func.count()).select_from(RecordEvent).where(*pr_filter)).scalar_one()
    records = db.execute(select(func.count()).select_from(RecordEvent).where(*team_best_filter)).scalar_one()

    return TeamStatsOut(
        window=window, laps=laps or 0, pbs=pbs or 0, active_drivers=active_drivers or 0, records=records or 0,
    )


@router.patch("/{team_id}", response_model=TeamDetail)
def update_team(
    team_id: int, body: TeamUpdateRequest, driver: Driver = Depends(get_current_driver), db: Session = Depends(get_db)
) -> TeamDetail:
    team = _get_team_or_404(db, team_id)
    membership = _get_membership(db, team_id, driver.id)
    _require_role(membership, "owner", "admin")

    if body.name is not None:
        new_name = body.name.strip()
        if not new_name:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Team name cannot be empty.")
        clash = _name_taken(db, new_name, exclude_team_id=team.id)
        if clash:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Team name already taken.")
        team.name = new_name
    if body.description is not None:
        team.description = body.description.strip() or None
    if body.discord_announcements_enabled is not None:
        team.discord_announcements_enabled = body.discord_announcements_enabled
    db.commit()
    return team_detail(team_id, driver, db)


@router.delete("/{team_id}")
def delete_team(team_id: int, driver: Driver = Depends(get_current_driver), db: Session = Depends(get_db)) -> dict:
    team = _get_team_or_404(db, team_id)
    membership = _get_membership(db, team_id, driver.id)
    _require_role(membership, "owner")

    delete_team_cascade(db, team)
    db.commit()
    return {"status": "deleted"}


@router.get("/{team_id}/members", response_model=List[TeamMemberOut])
def team_members(team_id: int, driver: Driver = Depends(get_current_driver), db: Session = Depends(get_db)) -> List[TeamMemberOut]:
    """V0.7.2: rewritten from N queries-per-member (3 queries × member
    count) to a fixed small number of queries regardless of team size —
    one membership+driver fetch (eager-loaded, so display_name doesn't
    trigger a lazy-load per row either) plus one GROUP BY aggregate query
    each for lap count, PB count, and last activity, merged in Python.
    Same response shape and values as before — see
    tests/test_v0_7_2_phase1.py for both the query-count bound and a
    3-member correctness check (each member's own driver, not the
    caller's)."""
    _get_team_or_404(db, team_id)
    _get_membership(db, team_id, driver.id)

    active_cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=_ACTIVE_WINDOW_DAYS)
    memberships = (
        db.query(TeamMembership)
        .options(joinedload(TeamMembership.driver))
        .filter(TeamMembership.team_id == team_id)
        .all()
    )
    if not memberships:
        return []
    driver_ids = [m.driver_id for m in memberships]

    lap_counts = dict(db.execute(
        select(Lap.driver_id, func.count()).where(Lap.driver_id.in_(driver_ids)).group_by(Lap.driver_id)
    ).all())
    pb_counts = dict(db.execute(
        select(RecordEvent.driver_id, func.count()).where(
            RecordEvent.driver_id.in_(driver_ids), RecordEvent.record_type == "PR"
        ).group_by(RecordEvent.driver_id)
    ).all())
    last_activity = dict(db.execute(
        select(Lap.driver_id, func.max(Lap.uploaded_at)).where(Lap.driver_id.in_(driver_ids)).group_by(Lap.driver_id)
    ).all())

    out = []
    for m in memberships:
        last_lap_at = last_activity.get(m.driver_id)
        out.append(TeamMemberOut(
            driver_id=m.driver_id, display_name=m.driver.display_name, role=m.role,
            is_active=bool(last_lap_at and last_lap_at >= active_cutoff),
            lap_count=lap_counts.get(m.driver_id, 0), pb_count=pb_counts.get(m.driver_id, 0),
            last_activity=last_lap_at.isoformat() if last_lap_at else None,
        ))
    return out


@router.delete("/{team_id}/members/{target_driver_id}")
def remove_member(
    team_id: int, target_driver_id: int, driver: Driver = Depends(get_current_driver), db: Session = Depends(get_db)
) -> dict:
    """A member may always remove themselves (leave). Otherwise: owner can
    remove admins/members; admin can remove members only. The owner can
    never be removed this way — transfer ownership or delete the team
    instead, so a team is never left without an owner."""
    _get_team_or_404(db, team_id)
    acting = _get_membership(db, team_id, driver.id)
    target = db.query(TeamMembership).filter(
        TeamMembership.team_id == team_id, TeamMembership.driver_id == target_driver_id
    ).one_or_none()
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "That driver isn't on this team.")

    if target.role == "owner":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The owner can't be removed — transfer ownership or delete the team.")
    if driver.id != target_driver_id:
        if acting.role == "member":
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Only an owner or admin can remove another member.")
        if acting.role == "admin" and target.role == "admin":
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Admins can't remove other admins.")

    db.delete(target)
    db.commit()
    return {"status": "removed"}


@router.post("/{team_id}/members/{target_driver_id}/role", response_model=TeamMemberOut)
def change_member_role(
    team_id: int, target_driver_id: int, body: TeamRoleChangeRequest,
    driver: Driver = Depends(get_current_driver), db: Session = Depends(get_db),
) -> TeamMemberOut:
    """Owner-only: promote a member to admin, or demote an admin back to
    member. Setting "owner" here is deliberately rejected by the request
    schema's pattern — use /transfer-ownership for that, which also
    demotes the previous owner in the same operation."""
    _get_team_or_404(db, team_id)
    acting = _get_membership(db, team_id, driver.id)
    _require_role(acting, "owner")
    target = db.query(TeamMembership).filter(
        TeamMembership.team_id == team_id, TeamMembership.driver_id == target_driver_id
    ).one_or_none()
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "That driver isn't on this team.")
    if target.role == "owner":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Use /transfer-ownership to change the owner.")
    target.role = body.role
    db.commit()
    return TeamMemberOut(
        driver_id=target.driver_id, display_name=target.driver.display_name, role=target.role,
        is_active=False, lap_count=0, pb_count=0, last_activity=None,
    )


@router.post("/{team_id}/transfer-ownership", response_model=TeamOut)
def transfer_ownership(
    team_id: int, body: TeamTransferOwnershipRequest,
    driver: Driver = Depends(get_current_driver), db: Session = Depends(get_db),
) -> TeamOut:
    team = _get_team_or_404(db, team_id)
    acting = _get_membership(db, team_id, driver.id)
    _require_role(acting, "owner")
    new_owner = db.query(TeamMembership).filter(
        TeamMembership.team_id == team_id, TeamMembership.driver_id == body.new_owner_driver_id
    ).one_or_none()
    if new_owner is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "That driver isn't on this team.")
    acting.role = "admin"
    new_owner.role = "owner"
    db.commit()
    return _team_out(db, team, role="admin")


# --- Team invitations (V0.7.2 §9.2) ---------------------------------------

def _expire_if_due(db: Session, invitation: TeamInvitation, now: datetime) -> None:
    """Lazy expiry — no background job. Called wherever a pending
    invitation is read; persists the transition so subsequent reads (and
    anyone else's) see the correct status too, not just this response."""
    if invitation.status == "pending" and invitation.expires_at < now:
        invitation.status = "expired"
        db.add(invitation)


def _invitation_out(invitation: TeamInvitation) -> TeamInvitationOut:
    return TeamInvitationOut(
        id=invitation.id, team_id=invitation.team_id, team_name=invitation.team.name,
        invited_driver_id=invitation.invited_driver_id,
        invited_display_name=invitation.invited_driver.display_name,
        created_by_driver_id=invitation.created_by_driver_id,
        created_by_display_name=invitation.created_by.display_name,
        status=invitation.status,
        created_at=invitation.created_at.isoformat() if invitation.created_at else None,
        expires_at=invitation.expires_at.isoformat(),
        accepted_at=invitation.accepted_at.isoformat() if invitation.accepted_at else None,
        declined_at=invitation.declined_at.isoformat() if invitation.declined_at else None,
        revoked_at=invitation.revoked_at.isoformat() if invitation.revoked_at else None,
    )


@router.post("/{team_id}/invitations", response_model=TeamInvitationOut)
def create_invitation(
    team_id: int, body: TeamInvitationCreateRequest,
    driver: Driver = Depends(get_current_driver), db: Session = Depends(get_db),
    _rl: None = Depends(rate_limit("team_invite", per_driver=True)),
) -> TeamInvitationOut:
    team = _get_team_or_404(db, team_id)
    membership = _get_membership(db, team_id, driver.id)
    _require_role(membership, "owner", "admin")

    invited = db.query(Driver).filter(Driver.id == body.invited_driver_id).one_or_none()
    if invited is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No driver with that id.")

    already_member = db.query(TeamMembership).filter(
        TeamMembership.team_id == team_id, TeamMembership.driver_id == invited.id
    ).one_or_none()
    if already_member is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, f"{invited.display_name} is already on this team.")

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    existing_pending = db.query(TeamInvitation).filter(
        TeamInvitation.team_id == team_id,
        TeamInvitation.invited_driver_id == invited.id,
        TeamInvitation.status == "pending",
    ).one_or_none()
    if existing_pending is not None:
        _expire_if_due(db, existing_pending, now)
        if existing_pending.status == "pending":
            raise HTTPException(
                status.HTTP_409_CONFLICT, f"{invited.display_name} already has a pending invitation to this team."
            )

    invitation = TeamInvitation(
        team_id=team_id, invited_driver_id=invited.id, created_by_driver_id=driver.id,
        status="pending", expires_at=now + TEAM_INVITATION_LIFETIME,
    )
    db.add(invitation)
    db.commit()
    db.refresh(invitation)
    return _invitation_out(invitation)


@router.get("/{team_id}/invitations", response_model=List[TeamInvitationOut])
def list_invitations(
    team_id: int, driver: Driver = Depends(get_current_driver), db: Session = Depends(get_db),
) -> List[TeamInvitationOut]:
    """Every invitation this team has ever sent, most recent first —
    owner/admin only (the invited driver's own name is visible to
    everyone on the team already via the member list once accepted; this
    view additionally shows who's pending/declined/revoked, which is
    exactly the "who have we already asked" question a manager needs
    answered before inviting more people)."""
    _get_team_or_404(db, team_id)
    membership = _get_membership(db, team_id, driver.id)
    _require_role(membership, "owner", "admin")

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    rows = db.query(TeamInvitation).filter(TeamInvitation.team_id == team_id).order_by(
        TeamInvitation.created_at.desc()
    ).all()
    for row in rows:
        _expire_if_due(db, row, now)
    db.commit()
    return [_invitation_out(r) for r in rows]


@router.post("/{team_id}/invitations/{invitation_id}/revoke", response_model=TeamInvitationOut)
def revoke_invitation(
    team_id: int, invitation_id: int,
    driver: Driver = Depends(get_current_driver), db: Session = Depends(get_db),
) -> TeamInvitationOut:
    _get_team_or_404(db, team_id)
    membership = _get_membership(db, team_id, driver.id)
    _require_role(membership, "owner", "admin")

    invitation = db.query(TeamInvitation).filter(
        TeamInvitation.id == invitation_id, TeamInvitation.team_id == team_id
    ).one_or_none()
    if invitation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invitation not found.")

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    _expire_if_due(db, invitation, now)
    if invitation.status != "pending":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Invitation is {invitation.status}, not pending.")

    invitation.status = "revoked"
    invitation.revoked_at = now
    db.add(invitation)
    db.commit()
    db.refresh(invitation)
    return _invitation_out(invitation)


@router.post("/{team_id}/invitations/{invitation_id}/resend", response_model=TeamInvitationOut)
def resend_invitation(
    team_id: int, invitation_id: int,
    driver: Driver = Depends(get_current_driver), db: Session = Depends(get_db),
    _rl: None = Depends(rate_limit("team_invite", per_driver=True)),
) -> TeamInvitationOut:
    """Only valid for a pending or already-expired invitation — a nudge,
    extending the deadline (and reviving an expired one back to pending).
    Deliberately NOT usable on a declined or revoked invitation: reviving
    a decision the driver (or the team) already explicitly made would be
    surprising. Send a brand-new invitation instead for that case — a
    fresh, deliberate ask rather than a silently resurrected old one."""
    _get_team_or_404(db, team_id)
    membership = _get_membership(db, team_id, driver.id)
    _require_role(membership, "owner", "admin")

    invitation = db.query(TeamInvitation).filter(
        TeamInvitation.id == invitation_id, TeamInvitation.team_id == team_id
    ).one_or_none()
    if invitation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invitation not found.")

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    _expire_if_due(db, invitation, now)
    if invitation.status not in ("pending", "expired"):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Invitation is {invitation.status} — send a new invitation instead of resending this one.",
        )

    invitation.status = "pending"
    invitation.expires_at = now + TEAM_INVITATION_LIFETIME
    db.add(invitation)
    db.commit()
    db.refresh(invitation)
    return _invitation_out(invitation)


@invitations_router.get("/mine", response_model=List[TeamInvitationOut])
def my_invitations(
    driver: Driver = Depends(get_current_driver), db: Session = Depends(get_db),
) -> List[TeamInvitationOut]:
    """Every invitation addressed to the caller, across all teams, most
    recent first — including past accepted/declined/expired/revoked ones
    (a short history is more useful than "pending only" and costs nothing
    extra to include)."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    rows = db.query(TeamInvitation).filter(TeamInvitation.invited_driver_id == driver.id).order_by(
        TeamInvitation.created_at.desc()
    ).all()
    for row in rows:
        _expire_if_due(db, row, now)
    db.commit()
    return [_invitation_out(r) for r in rows]


@invitations_router.post("/{invitation_id}/accept", response_model=TeamInvitationOut)
def accept_invitation(
    invitation_id: int, driver: Driver = Depends(get_current_driver), db: Session = Depends(get_db),
) -> TeamInvitationOut:
    invitation = db.query(TeamInvitation).filter(TeamInvitation.id == invitation_id).one_or_none()
    if invitation is None or invitation.invited_driver_id != driver.id:
        # Same 404 whether it doesn't exist or belongs to someone else —
        # distinguishing would leak which invitation ids exist for other
        # drivers.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invitation not found.")

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    _expire_if_due(db, invitation, now)
    if invitation.status != "pending":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Invitation is {invitation.status}, not pending.")

    existing_membership = db.query(TeamMembership).filter(
        TeamMembership.team_id == invitation.team_id, TeamMembership.driver_id == driver.id
    ).one_or_none()
    if existing_membership is None:
        db.add(TeamMembership(team_id=invitation.team_id, driver_id=driver.id, role="member"))

    invitation.status = "accepted"
    invitation.accepted_at = now
    db.add(invitation)
    db.commit()
    db.refresh(invitation)
    return _invitation_out(invitation)


@invitations_router.post("/{invitation_id}/decline", response_model=TeamInvitationOut)
def decline_invitation(
    invitation_id: int, driver: Driver = Depends(get_current_driver), db: Session = Depends(get_db),
) -> TeamInvitationOut:
    invitation = db.query(TeamInvitation).filter(TeamInvitation.id == invitation_id).one_or_none()
    if invitation is None or invitation.invited_driver_id != driver.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invitation not found.")

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    _expire_if_due(db, invitation, now)
    if invitation.status != "pending":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Invitation is {invitation.status}, not pending.")

    invitation.status = "declined"
    invitation.declined_at = now
    db.add(invitation)
    db.commit()
    db.refresh(invitation)
    return _invitation_out(invitation)


@router.get("/{team_id}/leaderboard/class/{track_name}/{car_class}", response_model=List[TeamLeaderboardEntry])
def team_class_leaderboard(
    team_id: int, track_name: str, car_class: str, limit: int = 20,
    driver: Driver = Depends(get_current_driver), db: Session = Depends(get_db),
) -> List[TeamLeaderboardEntry]:
    """Same as /leaderboard/class/..., scoped to this team's own drivers
    only — reuses the exact ranking logic (see leaderboard_ranking.py)."""
    _get_team_or_404(db, team_id)
    _get_membership(db, team_id, driver.id)
    driver_ids = _team_driver_ids(db, team_id)
    entries = ranked_leaderboard(
        db, [Lap.track_name == track_name, Lap.car_class == canonical_class(car_class), Lap.driver_id.in_(driver_ids)], limit
    )
    return _with_delta(entries)


@router.get("/{team_id}/leaderboard/car/{track_name}/{car_model}", response_model=List[TeamLeaderboardEntry])
def team_car_leaderboard(
    team_id: int, track_name: str, car_model: str, limit: int = 20,
    driver: Driver = Depends(get_current_driver), db: Session = Depends(get_db),
) -> List[TeamLeaderboardEntry]:
    _get_team_or_404(db, team_id)
    _get_membership(db, team_id, driver.id)
    driver_ids = _team_driver_ids(db, team_id)
    entries = ranked_leaderboard(
        db, [Lap.track_name == track_name, Lap.car_model == car_model, Lap.driver_id.in_(driver_ids)], limit
    )
    return _with_delta(entries)


def _with_delta(entries) -> List[TeamLeaderboardEntry]:
    leader_time = entries[0].lap_time if entries else 0.0
    return [
        TeamLeaderboardEntry(
            driver_id=e.driver_id, driver_name=e.driver_name, lap_time=e.lap_time, sector_times=e.sector_times,
            is_valid=e.is_valid, uploaded_at=e.uploaded_at, delta_to_leader=round(e.lap_time - leader_time, 3),
        )
        for e in entries
    ]


@router.get("/{team_id}/records", response_model=List[TeamRecordOut])
def team_records(team_id: int, driver: Driver = Depends(get_current_driver), db: Session = Depends(get_db)) -> List[TeamRecordOut]:
    """The team's current best on every (track, car_model) combo it has a
    valid lap on — derived live from Lap, same principle as the rest of
    the leaderboard system (no stored "current record" table). Also
    computes the caller's own gap to each (V0.7.2 §21 "You vs Team") —
    see TeamRecordOut's docstring for the None-vs-0.0 distinction."""
    _get_team_or_404(db, team_id)
    _get_membership(db, team_id, driver.id)
    driver_ids = _team_driver_ids(db, team_id)

    ranked = (
        select(
            Lap.track_name, Lap.car_class, Lap.car_model, Lap.lap_time, Lap.driver_id,
            func.row_number()
            .over(partition_by=[Lap.track_name, Lap.car_model], order_by=Lap.lap_time.asc())
            .label("rank"),
        )
        .where(Lap.driver_id.in_(driver_ids), Lap.is_valid.is_(True), Lap.car_model.is_not(None))
        .subquery()
    )
    stmt = (
        select(
            ranked.c.track_name, ranked.c.car_class, ranked.c.car_model, ranked.c.lap_time,
            ranked.c.driver_id, Driver.display_name,
        )
        .join(Driver, Driver.id == ranked.c.driver_id)
        .where(ranked.c.rank == 1)
        .order_by(ranked.c.track_name, ranked.c.car_model)
    )
    rows = db.execute(stmt).all()

    # Caller's own best per (track, car_model) — needed to compute a gap
    # even on combos where someone else holds the team record. A single
    # grouped query rather than one lookup per row.
    my_bests = {
        (t, m): lt
        for t, m, lt in db.execute(
            select(Lap.track_name, Lap.car_model, func.min(Lap.lap_time)).where(
                Lap.driver_id == driver.id, Lap.is_valid.is_(True), Lap.car_model.is_not(None)
            ).group_by(Lap.track_name, Lap.car_model)
        ).all()
    }

    out = []
    for t, c, m, lt, holder_id, name in rows:
        you_hold_it = holder_id == driver.id
        if you_hold_it:
            your_lap_time, gap = lt, 0.0
        else:
            your_lap_time = my_bests.get((t, m))
            gap = round(your_lap_time - lt, 3) if your_lap_time is not None else None
        out.append(TeamRecordOut(
            track_name=t, car_class=c, car_model=m, lap_time=lt, driver_name=name,
            your_lap_time=your_lap_time, gap=gap, you_hold_it=you_hold_it,
        ))
    return out


@router.get("/{team_id}/activity", response_model=List[TeamActivityItem])
def team_activity(team_id: int, limit: int = 30, driver: Driver = Depends(get_current_driver), db: Session = Depends(get_db)) -> List[TeamActivityItem]:
    """Recent PR/WR/TEAM_BEST events among this team's drivers — the
    "Konsole" — reuses RecordEvent, no separate activity-log table."""
    _get_team_or_404(db, team_id)
    _get_membership(db, team_id, driver.id)
    driver_ids = _team_driver_ids(db, team_id)

    stmt = (
        select(RecordEvent, Driver.display_name)
        .join(Driver, Driver.id == RecordEvent.driver_id)
        .where(RecordEvent.driver_id.in_(driver_ids))
        .order_by(RecordEvent.created_at.desc())
        .limit(min(limit, 100))
    )
    rows = db.execute(stmt).all()
    return [
        TeamActivityItem(
            record_type=ev.record_type, driver_name=name, track_name=ev.track_name, car_name=ev.car_name,
            lap_time=ev.lap_time, previous_best=ev.previous_best, created_at=ev.created_at.isoformat(),
        )
        for ev, name in rows
    ]
