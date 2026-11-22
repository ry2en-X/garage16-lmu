"""
server/api/v1/drivers.py — Driver identity lookups and public profiles.

GET /drivers/lookup: name -> driver_id resolution team invitations need
. GET /drivers/{id}/public: the public driver profile (§4) —
statistics, personal records, recent activity, team memberships. Neither
endpoint ever touches email, password_hash, auth_token, client_secret,
session data, or is_locked — see DriverProfileOut's docstring.
"""

from __future__ import annotations

import statistics
from datetime import datetime, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from server.accounts.auth import get_current_driver
from server.accounts.schemas import (
    DriverActivityItem,
    DriverLookupOut,
    DriverPersonalRecordOut,
    DriverProfileOut,
    DriverTeamOut,
)
from server.core.database import get_db
from server.core.models import Driver, Lap, RecordEvent, TeamMembership
from server.laps.official import official_filter

router = APIRouter(tags=["drivers"])


@router.get("/drivers/lookup", response_model=List[DriverLookupOut])
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


@router.get("/public/drivers/{driver_id}", response_model=DriverProfileOut)
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
        select(func.count()).select_from(Lap).where(Lap.driver_id == driver_id, official_filter())
    ).scalar_one()
    tracks_driven = db.execute(
        select(func.count(func.distinct(Lap.track_name))).where(
            Lap.driver_id == driver_id, official_filter()
        )
    ).scalar_one()
    cars_driven = db.execute(
        select(func.count(func.distinct(Lap.car_model))).where(
            Lap.driver_id == driver_id, official_filter(), Lap.car_model.is_not(None)
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
        .where(Lap.driver_id == driver_id, official_filter(), Lap.car_model.is_not(None))
        .subquery()
    )
    pr_rows = db.execute(
        select(ranked.c.track_name, ranked.c.car_name, ranked.c.car_class, ranked.c.car_model, ranked.c.lap_time, ranked.c.uploaded_at)
        .where(ranked.c.rank == 1)
        .order_by(ranked.c.lap_time.asc())
    ).all()
    # The Garage16 Best of each (track, class) these records are in — "Your Best" shows one
    # row per class, so a GT3 lap is measured against the GT3 record, never against a Hypercar.
    # One query for every pair (no request per row); same lap set as the class leaderboard.
    class_best = _class_bests(db, {(t, cc) for t, _c, cc, _cm, _lt, _up in pr_rows if cc})
    personal_records = []
    for t, c, cc, cm, lt, up in pr_rows:
        best = class_best.get((t, cc))
        personal_records.append(DriverPersonalRecordOut(
            track_name=t, car_name=c, car_class=cc, car_model=cm, lap_time=lt, set_at=up.isoformat(),
            class_best_time=best[0] if best else None,
            class_best_driver_id=best[1] if best else None,
            class_best_driver_name=best[2] if best else None,
        ))

    activity_rows = db.execute(
        select(RecordEvent, Lap.car_model)
        .outerjoin(Lap, Lap.id == RecordEvent.lap_id)
        .where(RecordEvent.driver_id == driver_id)
        .order_by(RecordEvent.created_at.desc())
        .limit(30)
    ).all()
    recent_activity = [
        DriverActivityItem(
            record_type=ev.record_type, track_name=ev.track_name, car_name=ev.car_name,
            lap_time=ev.lap_time, previous_best=ev.previous_best, created_at=ev.created_at.isoformat(),
            lap_id=ev.lap_id, car_model=car_model,
        )
        for ev, car_model in activity_rows
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


@router.get("/public/drivers/{driver_id}/progress")
def driver_progress(
    driver_id: int,
    track: Optional[str] = Query(default=None, min_length=1, max_length=120),
    car_model: Optional[str] = Query(default=None, min_length=1, max_length=120),
    db: Session = Depends(get_db),
) -> dict:
    """Public, unauthenticated — how a driver's best lap developed per track and car model (V1.0.3).

    Same rules as the public profile: the driver must exist (404), only official laps count
    (official_filter: valid, not held/quarantined) and laps without a car_model are left out.
    {"combos": [{track_name, car_model, car_name, car_class, laps, last_day}] — combinations with at
     least 2 laps, most recently driven first; "selected": {track_name, car_model} | null;
     "days": [{day: "YYYY-MM-DD" (UTC), best, median, laps}] oldest first, for the selected
    combination}. Without `track` and `car_model` the most recent combination is selected; with
    them an unknown combination gives an empty list. Giving only one of the two is a 422.
    """
    if (track is None) != (car_model is None):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "track and car_model go together.")
    if db.query(Driver.id).filter(Driver.id == driver_id).one_or_none() is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Driver not found.")

    rows = db.execute(
        select(
            Lap.track_name, Lap.car_model, func.max(Lap.car_name), func.max(Lap.car_class),
            func.count(Lap.id), func.max(Lap.recorded_at),
        )
        .where(Lap.driver_id == driver_id, official_filter(), Lap.car_model.is_not(None))
        .group_by(Lap.track_name, Lap.car_model)
        .having(func.count(Lap.id) >= 2)
    ).all()
    rows = sorted(rows, key=lambda r: (-(r[5] or 0), r[0], r[1]))
    combos = [
        {"track_name": t, "car_model": cm, "car_name": cn, "car_class": cc, "laps": n, "last_day": _day(last)}
        for t, cm, cn, cc, n, last in rows
    ]
    if track is None:
        selected = {"track_name": combos[0]["track_name"], "car_model": combos[0]["car_model"]} if combos else None
    else:
        selected = {"track_name": track, "car_model": car_model}

    days: List[dict] = []
    if selected is not None:
        laps = db.execute(
            select(Lap.recorded_at, Lap.lap_time).where(
                Lap.driver_id == driver_id, official_filter(),
                Lap.track_name == selected["track_name"], Lap.car_model == selected["car_model"],
            )
        ).all()
        per_day: Dict[str, List[float]] = {}
        for recorded_at, lap_time in laps:
            per_day.setdefault(_day(recorded_at), []).append(lap_time)
        days = [
            {"day": d, "best": min(v), "median": round(statistics.median(v), 3), "laps": len(v)}
            for d, v in sorted(per_day.items())
        ]
    return {"combos": combos, "selected": selected, "days": days}


def _day(recorded_at: Optional[int]) -> str:
    """The session day (UTC date) of a lap's recording time."""
    return datetime.fromtimestamp(recorded_at or 0, tz=timezone.utc).strftime("%Y-%m-%d")


def _class_bests(db: Session, pairs: set) -> dict:
    """{(track_name, car_class): (lap_time, driver_id, display_name)} — the fastest official lap
    of anybody per pair, i.e. the class leaderboard's P1 (ties: the older lap id, as there)."""
    if not pairs:
        return {}
    tracks = sorted({track for track, _cls in pairs})
    ranked = (
        select(
            Lap.track_name, Lap.car_class, Lap.lap_time, Lap.driver_id,
            func.row_number()
            .over(partition_by=(Lap.track_name, Lap.car_class), order_by=(Lap.lap_time.asc(), Lap.id.asc()))
            .label("rank"),
        )
        .where(official_filter(), Lap.track_name.in_(tracks), Lap.car_class.is_not(None))
        .subquery()
    )
    rows = db.execute(
        select(ranked.c.track_name, ranked.c.car_class, ranked.c.lap_time, ranked.c.driver_id, Driver.display_name)
        .join(Driver, Driver.id == ranked.c.driver_id)
        .where(ranked.c.rank == 1)
    ).all()
    return {(t, cc): (lt, did, name) for t, cc, lt, did, name in rows if (t, cc) in pairs}
