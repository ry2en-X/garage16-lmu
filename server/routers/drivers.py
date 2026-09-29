"""
routers/drivers.py — Driver identity lookups and public profiles.

GET /drivers/lookup: name -> driver_id resolution team invitations need
(V0.7.2 §9.2). GET /drivers/{id}/public: the public driver profile (§4) —
statistics, personal records, recent activity, team memberships. Neither
endpoint ever touches email, password_hash, auth_token, client_secret,
session data, or is_locked — see DriverProfileOut's docstring.
"""

from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..auth import get_current_driver
from ..database import get_db
from ..models import Driver, Lap, RecordEvent, TeamMembership
from ..schemas import (
    DriverActivityItem,
    DriverLookupOut,
    DriverPersonalRecordOut,
    DriverProfileOut,
    DriverTeamOut,
)

router = APIRouter(prefix="/drivers", tags=["drivers"])


@router.get("/lookup", response_model=List[DriverLookupOut])
def lookup_driver_by_display_name(
    display_name: str = Query(min_length=1, max_length=100),
    driver: Driver = Depends(get_current_driver),  # signed-in only — not a public, unauthenticated directory
    db: Session = Depends(get_db),
) -> List[DriverLookupOut]:
    """Exact, case-insensitive match only — deliberately never a fuzzy or
    partial-substring search, which would make this a de facto searchable
    driver directory (a bigger privacy/product surface than "resolve a
    name I already know exactly"). display_name has no uniqueness
    constraint (see models.py), so this can return more than one match —
    that's the caller's (or the inviting UI's) problem to disambiguate,
    not something to silently pick the "first" of.
    """
    rows = (
        db.query(Driver)
        .filter(func.lower(Driver.display_name) == display_name.strip().lower())
        .order_by(Driver.id)
        .all()
    )
    return [DriverLookupOut(driver_id=d.id, display_name=d.display_name) for d in rows]


@router.get("/{driver_id}/public", response_model=DriverProfileOut)
def driver_public_profile(driver_id: int, db: Session = Depends(get_db)) -> DriverProfileOut:
    """Public, unauthenticated — same trust level as the leaderboard
    itself (which already names this driver on every lap they've set).
    Every field here is something already visible elsewhere in the
    public API (leaderboards, team member lists); this just collects it
    into one place per driver, per §4.
    """
    driver = db.query(Driver).filter(Driver.id == driver_id).one_or_none()
    if driver is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Driver not found.")

    total_laps = db.execute(
        select(func.count()).select_from(Lap).where(Lap.driver_id == driver_id, Lap.is_valid.is_(True))
    ).scalar_one()
    tracks_driven = db.execute(
        select(func.count(func.distinct(Lap.track_name))).where(
            Lap.driver_id == driver_id, Lap.is_valid.is_(True)
        )
    ).scalar_one()
    cars_driven = db.execute(
        select(func.count(func.distinct(Lap.car_model))).where(
            Lap.driver_id == driver_id, Lap.is_valid.is_(True), Lap.car_model.is_not(None)
        )
    ).scalar_one()

    # Personal records: this driver's own best valid lap per (track,
    # car_model) — same car-identity grouping as records.py's PR/WR logic
    # (car_model, not car_name — ignores livery/team/number). Laps with no
    # car_model (pre-V0.6.3 clients) are excluded here, same as the
    # leaderboard itself already excludes them.
    ranked = (
        select(
            Lap.track_name, Lap.car_name, Lap.car_class, Lap.car_model, Lap.lap_time, Lap.uploaded_at,
            func.row_number()
            .over(partition_by=(Lap.track_name, Lap.car_model), order_by=(Lap.lap_time.asc(), Lap.id.asc()))
            .label("rank"),
        )
        .where(Lap.driver_id == driver_id, Lap.is_valid.is_(True), Lap.car_model.is_not(None))
        .subquery()
    )
    pr_rows = db.execute(
        select(ranked.c.track_name, ranked.c.car_name, ranked.c.car_class, ranked.c.car_model, ranked.c.lap_time, ranked.c.uploaded_at)
        .where(ranked.c.rank == 1)
        .order_by(ranked.c.lap_time.asc())
    ).all()
    personal_records = [
        DriverPersonalRecordOut(
            track_name=t, car_name=c, car_class=cc, car_model=cm, lap_time=lt, set_at=up.isoformat()
        )
        for t, c, cc, cm, lt, up in pr_rows
    ]

    activity_rows = db.execute(
        select(RecordEvent)
        .where(RecordEvent.driver_id == driver_id)
        .order_by(RecordEvent.created_at.desc())
        .limit(30)
    ).scalars().all()
    recent_activity = [
        DriverActivityItem(
            record_type=ev.record_type, track_name=ev.track_name, car_name=ev.car_name,
            lap_time=ev.lap_time, previous_best=ev.previous_best, created_at=ev.created_at.isoformat(),
        )
        for ev in activity_rows
    ]

    memberships = (
        db.query(TeamMembership)
        .filter(TeamMembership.driver_id == driver_id)
        .all()
    )
    teams = [
        DriverTeamOut(team_id=m.team_id, team_name=m.team.name, role=m.role)
        for m in memberships
    ]

    return DriverProfileOut(
        driver_id=driver.id, display_name=driver.display_name,
        total_laps=total_laps or 0, tracks_driven=tracks_driven or 0, cars_driven=cars_driven or 0,
        personal_records=personal_records, recent_activity=recent_activity, teams=teams,
    )
