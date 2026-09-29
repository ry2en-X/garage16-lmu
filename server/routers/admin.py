"""
routers/admin.py — Moderation endpoints.

Auth model: a single shared bearer token (LMU_GARAGE_ADMIN_TOKEN), checked
via a dependency separate from get_current_driver (this is operator
access, not a driver identity). Deliberately simple for this project's
scale (one or a few trusted operators) — if that stops being true, this
is the place to replace the shared-token check with per-admin accounts
and an audit log, not the endpoints themselves.

If LMU_GARAGE_ADMIN_TOKEN is unset, every endpoint here 503s rather than
either being silently open or silently protected by a guessable default.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..driver_lifecycle import delete_driver_rows
from ..models import Driver, Lap, LapReport
from ..schemas import AdminDriverOut, AdminInvalidateRequest, AdminLapReportOut, AdminLapSummary, AdminLockRequest
from ..team_lifecycle import TeamOwnershipConflict, resolve_owned_teams_before_driver_deletion

router = APIRouter(prefix="/admin", tags=["admin"])


def require_admin(x_admin_token: Optional[str] = Header(None)) -> None:
    if settings.admin_token is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Admin endpoints are disabled — LMU_GARAGE_ADMIN_TOKEN is not set.",
        )
    if not x_admin_token or x_admin_token != settings.admin_token:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Invalid or missing admin token.")


def _lap_summary(lap: Lap, driver_name: str) -> AdminLapSummary:
    return AdminLapSummary(
        id=lap.id, driver_id=lap.driver_id, driver_name=driver_name,
        track_name=lap.track_name, car_name=lap.car_name, car_class=lap.car_class, car_model=lap.car_model,
        lap_time=lap.lap_time, is_valid=lap.is_valid, invalid_reason=lap.invalid_reason,
        client_claimed_valid=lap.client_claimed_valid, uploaded_at=lap.uploaded_at.isoformat(),
    )


@router.get("/reports", response_model=List[AdminLapReportOut], dependencies=[Depends(require_admin)])
def list_reports(
    unresolved_only: bool = True,
    db: Session = Depends(get_db),
) -> List[AdminLapReportOut]:
    query = db.query(LapReport)
    if unresolved_only:
        query = query.filter(LapReport.resolved_at.is_(None))
    reports = query.order_by(LapReport.created_at.desc()).limit(200).all()

    out = []
    for r in reports:
        lap = db.query(Lap).filter(Lap.id == r.lap_id).one_or_none()
        reporter = db.query(Driver).filter(Driver.id == r.reported_by_driver_id).one_or_none()
        if lap is None or reporter is None:
            continue  # lap or reporter deleted since the report was filed — nothing sensible to show
        lap_driver = db.query(Driver).filter(Driver.id == lap.driver_id).one_or_none()
        out.append(AdminLapReportOut(
            id=r.id,
            lap=_lap_summary(lap, lap_driver.display_name if lap_driver else "(deleted driver)"),
            reported_by_driver_id=r.reported_by_driver_id,
            reported_by_display_name=reporter.display_name,
            reason=r.reason,
            created_at=r.created_at.isoformat(),
            resolved_at=r.resolved_at.isoformat() if r.resolved_at else None,
            resolution=r.resolution,
        ))
    return out


@router.post("/reports/{report_id}/resolve", dependencies=[Depends(require_admin)])
def resolve_report(
    report_id: int,
    resolution: str,
    db: Session = Depends(get_db),
) -> dict:
    report = db.query(LapReport).filter(LapReport.id == report_id).one_or_none()
    if report is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Report not found.")
    report.resolved_at = datetime.now(timezone.utc)
    report.resolution = resolution
    db.add(report)
    db.commit()
    return {"status": "ok"}


@router.get("/laps/{lap_id}", response_model=AdminLapSummary, dependencies=[Depends(require_admin)])
def get_lap(lap_id: int, db: Session = Depends(get_db)) -> AdminLapSummary:
    """The "View" action on a report, or standalone lap lookup for direct
    moderation outside of any report (§15's "Lap moderation: View,
    Invalidate")."""
    lap = db.query(Lap).filter(Lap.id == lap_id).one_or_none()
    if lap is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Lap not found.")
    driver = db.query(Driver).filter(Driver.id == lap.driver_id).one_or_none()
    return _lap_summary(lap, driver.display_name if driver else "(deleted driver)")


@router.patch("/laps/{lap_id}/invalidate", dependencies=[Depends(require_admin)])
def invalidate_lap(
    lap_id: int,
    body: AdminInvalidateRequest,
    db: Session = Depends(get_db),
) -> dict:
    """Soft-invalidate a lap: flips is_valid to False with a recorded
    reason. Deliberately does NOT delete the row or its RecordEvents —
    per the architecture decision (see records.py), leaderboards and
    records are derived live from Lap.is_valid, so this alone removes the
    lap from leaderboards/records on the next query. No CurrentRecord
    table to reconcile, no re-computation step needed."""
    lap = db.query(Lap).filter(Lap.id == lap_id).one_or_none()
    if lap is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Lap not found.")
    lap.is_valid = False
    lap.invalid_reason = [f"admin: {body.reason}"]
    db.add(lap)
    db.commit()
    return {"status": "ok", "lap_id": lap_id}


@router.get("/drivers", response_model=List[AdminDriverOut], dependencies=[Depends(require_admin)])
def list_drivers(
    search: Optional[str] = Query(default=None, max_length=100),
    limit: int = 50,
    db: Session = Depends(get_db),
) -> List[AdminDriverOut]:
    """§15 driver moderation needs a way to find a driver at all — there
    was no listing/search endpoint anywhere in the API before this one.
    Substring, case-insensitive (unlike GET /drivers/lookup's exact-match
    requirement — that endpoint is reachable by any signed-in driver and
    deliberately can't become a searchable directory; this one is gated
    by the operator admin token, a categorically different trust level).
    Includes email — see AdminDriverOut's docstring for why that's fine
    here specifically."""
    query = db.query(Driver)
    if search:
        query = query.filter(Driver.display_name.ilike(f"%{search.strip()}%"))
    drivers = query.order_by(Driver.id).limit(min(limit, 200)).all()

    lap_counts = dict(
        db.execute(
            select(Lap.driver_id, func.count()).where(
                Lap.driver_id.in_([d.id for d in drivers])
            ).group_by(Lap.driver_id)
        ).all()
    ) if drivers else {}

    return [
        AdminDriverOut(
            driver_id=d.id, display_name=d.display_name, email=d.email,
            is_locked=d.is_locked, locked_reason=d.locked_reason,
            total_laps=lap_counts.get(d.id, 0),
        )
        for d in drivers
    ]


@router.post("/drivers/{driver_id}/lock", dependencies=[Depends(require_admin)])
def lock_driver(
    driver_id: int,
    body: AdminLockRequest,
    db: Session = Depends(get_db),
) -> dict:
    """Blocks all future authenticated requests from this driver
    (get_current_driver checks is_locked) without touching their existing
    data or revoking+losing the token the way self-service revoke does —
    reversible via /unlock."""
    driver = db.query(Driver).filter(Driver.id == driver_id).one_or_none()
    if driver is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Driver not found.")
    driver.is_locked = True
    driver.locked_reason = body.reason
    db.add(driver)
    db.commit()
    return {"status": "ok", "driver_id": driver_id}


@router.post("/drivers/{driver_id}/unlock", dependencies=[Depends(require_admin)])
def unlock_driver(driver_id: int, db: Session = Depends(get_db)) -> dict:
    driver = db.query(Driver).filter(Driver.id == driver_id).one_or_none()
    if driver is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Driver not found.")
    driver.is_locked = False
    driver.locked_reason = None
    db.add(driver)
    db.commit()
    return {"status": "ok", "driver_id": driver_id}


@router.delete("/drivers/{driver_id}", dependencies=[Depends(require_admin)])
def delete_driver(
    driver_id: int,
    force: bool = False,
    db: Session = Depends(get_db),
) -> dict:
    """Same cascade as the self-service DELETE /accounts/me — see that
    endpoint's docstring for the FK-ordering reasoning. This is the admin
    equivalent for e.g. GDPR/DSGVO erasure requests submitted outside the
    web UI (support email, etc.), or removing a banned cheater's data.

    V0.7.2: same team-ownership guard as the self-service endpoint —
    blocked (409) by default if this driver owns a team with other
    members. Unlike self-service, an admin can pass ?force=true to
    auto-transfer ownership to the next-earliest-joined member instead: a
    banned cheater won't come back to transfer it themselves, and an
    admin acting on a support/GDPR request needs a way to actually finish
    the deletion."""
    driver = db.query(Driver).filter(Driver.id == driver_id).one_or_none()
    if driver is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Driver not found.")

    try:
        resolve_owned_teams_before_driver_deletion(db, driver_id, force=force)
    except TeamOwnershipConflict as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This driver owns a team with other members — retry with ?force=true to "
            f"auto-transfer ownership, or resolve manually first: {', '.join(t.name for t in exc.teams)}",
        ) from exc

    telemetry_paths = delete_driver_rows(db, driver)

    for path_str in telemetry_paths:
        try:
            Path(path_str).unlink(missing_ok=True)
        except OSError:
            pass

    return {"status": "ok", "driver_id": driver_id}
