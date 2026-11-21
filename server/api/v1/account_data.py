"""The signed-in driver's own account: who am I, display name, Discord link, data export (GDPR Art. 15/20) and deleting the account."""

from __future__ import annotations


from pathlib import Path

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from server.accounts import discord_link
from server.accounts.auth import get_current_driver
from server.accounts.driver_lifecycle import delete_driver_rows
from server.accounts.names import (
    NAME_TAKEN_MESSAGE,
    name_key,
)
from server.accounts.schemas import (
    AccountExport,
    DiscordLinkStatusOut,
    DisplayNameUpdateRequest,
    LinkCodeResponse,
    StatusResponse,
    WhoAmIOut,
)
from server.accounts.team_lifecycle import (
    TeamOwnershipConflict,
    resolve_owned_teams_before_driver_deletion,
)
from server.core.database import get_db
from server.core.models import (
    DeviceToken,
    Driver,
    Lap,
    RecordEvent,
    Team,
    TeamMembership,
    UploadEvent,
)
from server.core.models import Session as SessionModel
from server.core.rate_limit import rate_limit
from server.laps.schemas import LapSummary
from server.ops.audit import for_driver as audit_for_driver
from server.ops.write_lock import RETRY_AFTER_S, MaintenanceActive, writing
from server.api.v1.accounts import _clean_name, _whoami
from server.api.v1.accounts import _iso

router = APIRouter(prefix="/accounts", tags=["accounts"])


@router.get("/me", response_model=WhoAmIOut)
def whoami(driver: Driver = Depends(get_current_driver)) -> WhoAmIOut:
    """Resolves the caller's own identity from whichever credential they
    presented (desktop bearer token or web session cookie — see
    server/accounts/auth.py). See WhoAmIOut's docstring for why this needed to
    exist."""
    return _whoami(driver)


def issue_discord_link_code(driver: Driver, db: Session) -> LinkCodeResponse:
    try:
        code, expires_at = discord_link.create_account_link_code(db, driver)
    except discord_link.DiscordAlreadyLinked:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Your account is already linked to a Discord account. Unlink it first to link a different one.",
        )
    shown = discord_link.format_code(code)
    return LinkCodeResponse(
        code=shown,
        expires_in_seconds=int(discord_link.CODE_LIFETIME.total_seconds()),
        command=f"/link {shown}",
    )


@router.get("/discord", response_model=DiscordLinkStatusOut)
def discord_link_status(driver: Driver = Depends(get_current_driver)) -> DiscordLinkStatusOut:
    return DiscordLinkStatusOut(linked=driver.discord_user_id is not None)


@router.post("/discord/link-code", response_model=LinkCodeResponse)
def create_discord_link_code(
    driver: Driver = Depends(get_current_driver),
    db: Session = Depends(get_db),
    _rl: None = Depends(rate_limit("link_code", per_driver=True)),
) -> LinkCodeResponse:
    """The "profile link token": a one-time code, valid 10 minutes, that
    the driver types into Discord as `/link <code>`. 409 if already linked;
    a fresh code invalidates any earlier unused one."""
    return issue_discord_link_code(driver, db)


@router.delete("/discord", response_model=StatusResponse)
def unlink_discord(driver: Driver = Depends(get_current_driver), db: Session = Depends(get_db)) -> StatusResponse:
    """Removes the Discord link (idempotent — unlinking when nothing is
    linked is a harmless 200) and cancels any pending link code."""
    removed = discord_link.unlink_driver(db, driver)
    return StatusResponse(status="unlinked" if removed else "not_linked")


@router.patch("/me", response_model=WhoAmIOut)
def update_my_display_name(
    body: DisplayNameUpdateRequest,
    driver: Driver = Depends(get_current_driver),
    db: Session = Depends(get_db),
) -> WhoAmIOut:
    """Rename yourself. Everything that shows a driver's name reads it from the
    driver row at display time (leaderboards, profiles, teams, invitations,
    Discord announcements), so the new name appears everywhere at once — old
    laps and records are not rewritten because they never stored the name.
    Names are unique in their compared form (server/accounts/names.py) — 409 if another driver
    already uses it; changing only the capitalisation of your own name is allowed."""
    name = _clean_name(body.display_name, db, except_driver_id=driver.id)
    driver.display_name = name
    driver.display_name_key = name_key(name)
    db.add(driver)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, NAME_TAKEN_MESSAGE)
    return _whoami(driver)


@router.get("/me/export", response_model=AccountExport)
def export_my_data(
    driver: Driver = Depends(get_current_driver),
    db: Session = Depends(get_db),
) -> AccountExport:
    """Self-service data export (DSGVO Art. 15/20-style 'what do you have
    about me'). Returns the structured DB data — driver profile and lap
    records. Deliberately does not inline raw telemetry file bytes (those
    would make this response arbitrarily large); the telemetry_path on
    each lap is server-internal only and not exposed here."""
    laps = db.query(Lap).filter(Lap.driver_id == driver.id).order_by(Lap.uploaded_at.asc()).all()
    return AccountExport(
        driver_id=driver.id,
        display_name=driver.display_name,
        created_at=driver.created_at.isoformat(),
        discord_linked=driver.discord_user_id is not None,
        laps=[
            LapSummary(
                id=lap.id,
                track_name=lap.track_name,
                car_name=lap.car_name,
                session_type=lap.session_type,
                lap_number=lap.lap_number,
                lap_time=lap.lap_time,
                sector_times=lap.sector_times,
                is_valid=lap.is_valid,
                invalid_reason=lap.invalid_reason,
                ambient_temp=lap.ambient_temp,
                track_temp=lap.track_temp,
                uploaded_at=lap.uploaded_at.isoformat(),
            )
            for lap in laps
        ],
        email=driver.email,
        email_verified=bool(driver.email_verified),
        teams=_export_teams(db, driver.id),
        sessions=[{"created_at": _iso(s.created_at), "last_used_at": _iso(s.last_used_at), "expires_at": _iso(s.expires_at),
                   "revoked_at": _iso(s.revoked_at), "user_agent": s.user_agent}
                  for s in db.query(SessionModel).filter(SessionModel.driver_id == driver.id).order_by(SessionModel.id)],
        records=[{"type": e.record_type, "lap_id": e.lap_id, "track_name": e.track_name, "car_name": e.car_name,
                  "lap_time": e.lap_time, "created_at": _iso(e.created_at)}
                 for e in db.query(RecordEvent).filter(RecordEvent.driver_id == driver.id).order_by(RecordEvent.id)],
        upload_log=[{"at": _iso(u.created_at), "outcome": u.outcome, "status_code": u.status_code, "reason": u.reason,
                     "lap_id": u.lap_id, "client_version": u.client_version, "request_id": u.request_id}
                    for u in db.query(UploadEvent).filter(UploadEvent.driver_id == driver.id).order_by(UploadEvent.id)],
        # What the server stored of each received original, and the audit events about them
        lap_files=[{"lap_id": lap.id, "sha256": lap.telemetry_hash, "bytes": lap.telemetry_bytes,
                    "uploaded_at": _iso(lap.uploaded_at), "request_id": lap.upload_request_id,
                    "review_status": lap.review_status} for lap in laps],
        audit_events=audit_for_driver(db, driver.id),
        terms_accepted_at=_iso(driver.terms_accepted_at),
        terms_version=driver.terms_version,
        devices=[{"name": d.name, "created_at": _iso(d.created_at), "last_used_at": _iso(d.last_used_at),
                  "revoked_at": _iso(d.revoked_at)}
                 for d in db.query(DeviceToken).filter(DeviceToken.driver_id == driver.id).order_by(DeviceToken.id)],
    )


def _export_teams(db: Session, driver_id: int) -> list:
    rows = (db.query(TeamMembership, Team).join(Team, Team.id == TeamMembership.team_id)
            .filter(TeamMembership.driver_id == driver_id).all())
    return [{"team": t.name, "role": m.role, "joined_at": _iso(m.joined_at)} for m, t in rows]


@router.delete("/me", status_code=status.HTTP_204_NO_CONTENT)
def delete_my_account(
    driver: Driver = Depends(get_current_driver),
    db: Session = Depends(get_db),
) -> None:

    """Self-service full account deletion — driver, laps, telemetry files,
    record events, team memberships, discord link, and any lap reports
    they filed. Irreversible; no confirmation step here because the web
    UI is expected to confirm before calling this (see web/js/pages/account.js).

    The cascade itself lives in server/accounts/driver_lifecycle.py (shared with the
    admin endpoint; V0.8.7 completed it — it used to miss several tables,
    which made this endpoint fail with a 500 on PostgreSQL).

    Refuses (409) to proceed if the driver owns a team that still
    has other members — deleting the account would otherwise silently
    leave that team without an owner. A solo-owned team (this driver is
    the only member) is deleted along with the account; a team with other
    members must be explicitly transferred (POST
    .../transfer-ownership) or deleted by its owner first — see
    server/accounts/team_lifecycle.py's resolve_owned_teams_before_driver_deletion
    for why this isn't done automatically here.
    """
    try:
        resolve_owned_teams_before_driver_deletion(db, driver.id, force=False)
    except TeamOwnershipConflict as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "You're the owner of a team with other members — transfer ownership or "
            f"delete the team before deleting your account: {', '.join(t.name for t in exc.teams)}",
        ) from exc

    # Rows and files go together under the shared write lock (server/ops/write_lock.py) —
    # a backup running right now must not catch the rows deleted but the files still there
    try:
        with writing():
            telemetry_paths = delete_driver_rows(db, driver)

            # Best-effort file cleanup — DB deletion above is the source of truth;
            # a leftover file after a crash here is an orphan (P2 cleanup concern,
            # not a correctness one), not a driver whose data wasn't deleted.
            for path_str in telemetry_paths:
                try:
                    Path(path_str).unlink(missing_ok=True)
                except OSError:
                    pass
    except MaintenanceActive:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, {"code": "MAINTENANCE", "message": "A backup is running — try again in a few minutes."},
                            headers={"Retry-After": str(RETRY_AFTER_S)})
