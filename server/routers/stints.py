"""
routers/stints.py — the Stint Analyzer (V0.9.4): sessions, one session's stints / pit stops /
tyre sets, the comparison with team mates, and the race rule sets.

Lives under /telemetry/analysis/stints so docker/Caddyfile needs no change. Everything comes
from the database (laps.lap_stats, laps.lap_context — see server/stints.py); no telemetry file
is read.

Who sees what (same rule as the LapTime Analyzer)
  * your own sessions: every lap, valid or not
  * another driver's sessions: their VALID laps only — a session of a team mate therefore has
    gaps where a lap was invalid, and says so ("partial": true)
  * the team comparison: you and the drivers who share a team with you, valid laps only
  * rule sets: readable for every signed-in driver, changed by an admin (routers/admin.py)
"""

from __future__ import annotations

import statistics
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..auth import get_current_driver
from ..database import get_db
from ..lap_insights import consistency, group_sessions
from ..models import Driver, Lap, RaceRuleSet, TeamMembership
from ..rate_limit import rate_limit
from ..stints import analyse_session

router = APIRouter(prefix="/telemetry/analysis/stints", tags=["stints"])

MAX_LAPS_READ = 1500
TEAM_COMPARISON_MAX_DRIVERS = 30


def _teammate_ids(db: Session, viewer: Driver) -> List[int]:
    teams = select(TeamMembership.team_id).where(TeamMembership.driver_id == viewer.id)
    rows = db.execute(select(TeamMembership.driver_id).where(TeamMembership.team_id.in_(teams)).distinct()).scalars()
    return sorted({int(r) for r in rows} - {viewer.id})


def _lap_dict(lap: Lap, with_stats: bool = True) -> dict:
    return {"id": lap.id, "lap_number": lap.lap_number, "lap_time": lap.lap_time, "is_valid": lap.is_valid,
            "recorded_at": lap.recorded_at, "session_type": lap.session_type, "track_name": lap.track_name,
            "car_model": lap.car_model or lap.car_name, "sector_times": lap.sector_times,
            "track_temp": lap.track_temp, "stats": lap.lap_stats if with_stats else None,
            "context": lap.lap_context,
            # why the lap does not count (another driver's laps are only shown when valid)
            "invalid_reason": lap.invalid_reason if not lap.is_valid else None}


def _driver_laps(db: Session, driver_id: int, viewer: Driver, track_name=None, car_model=None) -> List[Lap]:
    stmt = select(Lap).where(Lap.driver_id == driver_id)
    if driver_id != viewer.id:
        stmt = stmt.where(Lap.is_valid.is_(True))
    if track_name:
        stmt = stmt.where(Lap.track_name == track_name)
    if car_model:
        stmt = stmt.where(or_(Lap.car_model == car_model, Lap.car_name == car_model))
    return db.execute(stmt.order_by(Lap.id.desc()).limit(MAX_LAPS_READ)).scalars().all()


def _check_driver(db: Session, viewer: Driver, driver_id: Optional[int]) -> int:
    if driver_id is None or driver_id == viewer.id:
        return viewer.id
    if driver_id not in _teammate_ids(db, viewer):
        # same answer whether the driver exists or not
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Driver not found among your team mates.")
    return driver_id


@router.get("/sessions")
def stint_sessions(
    driver_id: Optional[int] = Query(default=None, ge=1),
    track_name: Optional[str] = Query(default=None, max_length=200),
    car_model: Optional[str] = Query(default=None, max_length=100),
    limit: int = Query(default=20, ge=1, le=50),
    viewer: Driver = Depends(get_current_driver),
    db: Session = Depends(get_db),
    _rl: None = Depends(rate_limit("analysis", per_driver=True)),
) -> dict:
    """The sessions of a driver (you, or a team mate: valid laps only), newest first, each with
    the id of its last lap (the key to open it), and the drivers you may look at."""
    target = _check_driver(db, viewer, driver_id)
    laps = _driver_laps(db, target, viewer, track_name, car_model)
    sessions = group_sessions([_lap_dict(lap) for lap in laps])
    mates = _teammate_ids(db, viewer)
    names = dict(db.execute(select(Driver.id, Driver.display_name).where(Driver.id.in_([viewer.id, *mates]))).all())
    for session in sessions:
        session["key_lap_id"] = session["laps"][-1]["id"]
    return {
        "driver_id": target,
        "own": target == viewer.id,
        "sessions": sessions[:limit],
        "total": len(sessions),
        "drivers": [{"id": i, "name": names.get(i), "own": i == viewer.id} for i in [viewer.id, *mates] if i in names],
    }


@router.get("/session")
def stint_session(
    lap_id: int = Query(ge=1),
    viewer: Driver = Depends(get_current_driver),
    db: Session = Depends(get_db),
    _rl: None = Depends(rate_limit("analysis", per_driver=True)),
) -> dict:
    """One session (the one this lap belongs to): every lap with its flags, the stints, the pit
    stops, the tyre sets and the session's figures — see server/stints.py."""
    lap = db.get(Lap, lap_id)
    if lap is None or (lap.driver_id != viewer.id and not lap.is_valid):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Lap {lap_id} not found.")
    laps = _driver_laps(db, lap.driver_id, viewer, lap.track_name)
    sessions = group_sessions([_lap_dict(row, with_stats=False) for row in laps])
    session = next((s for s in sessions if any(item["id"] == lap.id for item in s["laps"])), None)
    if session is None:   # the lap is older than what was read
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Lap {lap_id} not found.")
    ids = [item["id"] for item in session["laps"]]
    by_id = {row.id: row for row in laps if row.id in set(ids)}
    result = analyse_session([_lap_dict(by_id[i]) for i in ids], session_type=session["session_type"])
    driver = db.get(Driver, lap.driver_id)
    result.update({
        "driver": {"id": lap.driver_id, "name": driver.display_name if driver else None, "own": lap.driver_id == viewer.id},
        "partial": lap.driver_id != viewer.id,
        "track_name": session["track_name"],
        "car_model": session["car_model"],
        "session_type": session["session_type"],
        "started": session["started"],
        "ended": session["ended"],
    })
    return result


@router.get("/drivers")
def team_comparison(
    track_name: str = Query(min_length=1, max_length=200),
    car_model: Optional[str] = Query(default=None, max_length=100),
    viewer: Driver = Depends(get_current_driver),
    db: Session = Depends(get_db),
    _rl: None = Depends(rate_limit("analysis", per_driver=True)),
) -> dict:
    """You and your team mates on one track (and car model): valid laps only, robust figures
    (median of the clean laps, spread, best), consumption and wear per lap (medians)."""
    ids = [viewer.id, *_teammate_ids(db, viewer)][:TEAM_COMPARISON_MAX_DRIVERS]
    names = dict(db.execute(select(Driver.id, Driver.display_name).where(Driver.id.in_(ids))).all())
    stmt = select(Lap).where(Lap.driver_id.in_(ids), Lap.track_name == track_name, Lap.is_valid.is_(True))
    if car_model:
        stmt = stmt.where(or_(Lap.car_model == car_model, Lap.car_name == car_model))
    laps = db.execute(stmt.order_by(Lap.id.desc()).limit(MAX_LAPS_READ * 2)).scalars().all()
    out = []
    for driver_id in ids:
        own = [lap for lap in laps if lap.driver_id == driver_id]
        if not own:
            continue
        own.sort(key=lambda lap: (lap.recorded_at or 0, lap.id))
        stats = [lap.lap_stats if isinstance(lap.lap_stats, dict) else {} for lap in own]

        def median(key):
            values = [s.get(key) for s in stats if isinstance(s.get(key), (int, float)) and s.get(key) >= 0]
            return round(statistics.median(values), 2) if values else None

        wear = []
        for s in stats:
            used = [w.get("wear_used_pct") for w in (s.get("wheels") or {}).values()
                    if isinstance(w, dict) and isinstance(w.get("wear_used_pct"), (int, float)) and w["wear_used_pct"] >= 0]
            if used:
                wear.append(sum(used) / len(used))
        out.append({
            "driver_id": driver_id,
            "name": names.get(driver_id),
            "own": driver_id == viewer.id,
            "car_models": sorted({lap.car_model or lap.car_name for lap in own}),
            "consistency": consistency([lap.lap_time for lap in own]),
            "fuel_per_lap_l": median("fuel_used_l"),
            "energy_per_lap_pct": median("energy_used_pct"),
            "wear_per_lap_pct": round(statistics.median(wear), 3) if wear else None,
            "last_recorded_at": own[-1].recorded_at,
        })
    return {"track_name": track_name, "car_model": car_model, "drivers": out, "valid_laps_only": True}


@router.get("/rule-sets")
def rule_sets(
    track_name: Optional[str] = Query(default=None, max_length=200),
    viewer: Driver = Depends(get_current_driver),
    db: Session = Depends(get_db),
) -> dict:
    """The race rule sets an admin defined; with track_name, the ones for that track and the
    general ones (no track) — for the strategy calculator."""
    stmt = select(RaceRuleSet)
    if track_name:
        stmt = stmt.where(or_(RaceRuleSet.track_name == track_name, RaceRuleSet.track_name.is_(None)))
    rows = db.execute(stmt.order_by(RaceRuleSet.name)).scalars().all()
    return {"rule_sets": [{"id": r.id, "name": r.name, "description": r.description, "track_name": r.track_name,
                           "car_class": r.car_class, "rules": r.rules} for r in rows]}
