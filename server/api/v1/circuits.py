"""The circuit library and a driver's history at a track: circuit cards and outlines, a driver's laps, theoretical best, sessions, corner history and best sectors."""

from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Dict, List, Optional, Sequence

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from server.accounts.auth import get_current_driver
from server.analysis.lap_access import _lap_for_viewer, _telemetry_file
from server.analysis.lap_insights import (
    condition_class,
    conditions_summary,
    group_sessions,
    theoretical_best,
)
from server.analysis.racing_line import resolve_mirror, track_xy
from server.analysis.telemetry_analysis import (
    AnalysisError,
    json_series,
    load_lap,
    make_grid,
    sector_distances,
)
from server.catalog.catalog import (
    canonical_class,
    known_venue_names,
    venue_for,
    venue_matches_query,
)
from server.core.database import get_db
from server.core.models import Driver, Lap
from server.core.rate_limit import rate_limit
from server.core.versioning import data_level_for_client_version
from server.laps.official import official_filter, visible_filter

router = APIRouter(tags=["analysis"])



OUTLINE_POINTS = 600


OUTLINE_CANDIDATES = 5


@router.get("/drivers/{driver_id}/laps")
def driver_laps(
    driver_id: int,
    track_name: Optional[str] = Query(default=None, max_length=200),
    car_model: Optional[str] = Query(default=None, max_length=100),
    limit: int = Query(default=50, ge=1, le=200),
    viewer: Driver = Depends(get_current_driver),
    db: Session = Depends(get_db),
    _rl: None = Depends(rate_limit("analysis", per_driver=True)),
) -> dict:
    """The driver's laps, fastest first — the valid ones, plus invalid ones when
    the driver asks about themselves. Optionally for one track and car model."""
    target = db.get(Driver, driver_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Driver not found.")
    stmt = select(Lap).where(Lap.driver_id == driver_id)
    if driver_id != viewer.id:
        stmt = stmt.where(visible_filter())
    if track_name:
        stmt = stmt.where(Lap.track_name == track_name)
    if car_model:
        stmt = stmt.where(Lap.car_model == car_model)
    laps = db.execute(stmt.order_by(Lap.lap_time.asc(), Lap.id.asc()).limit(limit)).scalars().all()
    return {
        "driver_id": driver_id,
        "driver_name": target.display_name,
        "laps": [
            {
                "id": lap.id, "lap_number": lap.lap_number, "lap_time": lap.lap_time,
                "sector_times": lap.sector_times, "is_valid": lap.is_valid,
                "track_name": lap.track_name, "car_name": lap.car_name, "car_model": lap.car_model,
                "car_class": lap.car_class, "uploaded_at": lap.uploaded_at.isoformat() if lap.uploaded_at else None,
                "data_level": data_level_for_client_version(lap.client_version),
            }
            for lap in laps
        ],
    }


@router.get("/public/circuits")
def circuits(q: Optional[str] = Query(default=None, max_length=100), db: Session = Depends(get_db)) -> dict:
    """Track cards: every venue with all its layouts, lap/driver counts, the
    fastest valid lap and the classes driven there. Built entirely from uploads
    (plus the seeded venue list) — nobody enters a track by hand. Public: it only
    contains what the leaderboards show anyway. The outline of a layout is a
    separate call (it has to read a telemetry file)."""
    ranked = (
        select(
            Lap.id.label("lap_id"), Lap.track_name, Lap.lap_time, Lap.car_model, Lap.car_class,
            Lap.driver_id,
            func.row_number().over(partition_by=Lap.track_name, order_by=(Lap.lap_time.asc(), Lap.id.asc())).label("rn"),
        )
        .where(official_filter())
        .subquery()
    )
    fastest_rows = db.execute(
        select(ranked.c.lap_id, ranked.c.track_name, ranked.c.lap_time, ranked.c.car_model, ranked.c.car_class,
               ranked.c.driver_id, Driver.display_name)
        .join(Driver, Driver.id == ranked.c.driver_id)
        .where(ranked.c.rn == 1)
    ).all()
    fastest = {row.track_name: row for row in fastest_rows}

    totals = {
        track: (laps, drivers)
        for track, laps, drivers in db.execute(
            select(Lap.track_name, func.count(Lap.id), func.count(func.distinct(Lap.driver_id)))
            .where(official_filter()).group_by(Lap.track_name)
        ).all()
    }
    classes: Dict[str, set] = {}
    for track, car_class in db.execute(
        select(Lap.track_name, Lap.car_class).where(official_filter(), Lap.car_class.is_not(None)).distinct()
    ).all():
        classes.setdefault(track, set()).add(canonical_class(car_class))

    by_venue: Dict[str, List[dict]] = {}
    for track, (lap_count, driver_count) in totals.items():
        best = fastest.get(track)
        by_venue.setdefault(venue_for(track), []).append({
            "track_name": track,
            "lap_count": lap_count,
            "driver_count": driver_count,
            "classes": sorted(c for c in classes.get(track, set()) if c),
            "fastest": None if best is None else {
                "lap_id": best.lap_id, "lap_time": best.lap_time, "driver_id": best.driver_id,
                "driver_name": best.display_name, "car_model": best.car_model,
                "car_class": canonical_class(best.car_class),
            },
        })
    for known in known_venue_names():
        by_venue.setdefault(known, [])

    cards = []
    for venue in sorted(by_venue, key=str.lower):
        layouts = sorted(by_venue[venue], key=lambda item: item["track_name"].lower())
        if q and not venue_matches_query(venue, [layout["track_name"] for layout in layouts], q):
            continue
        cards.append({
            "venue": venue,
            "lap_count": sum(item["lap_count"] for item in layouts),
            "layouts": layouts,
        })
    return {"venues": cards}


# Outlines are derived from one lap file; reading it is the expensive part, so the
# result is cached in this process, keyed by the lap it came from (a new fastest
# lap automatically produces a new key). Small LRU: a handful of layouts at most.
_OUTLINE_CACHE: "OrderedDict[tuple, dict]" = OrderedDict()


_OUTLINE_CACHE_LOCK = threading.Lock()


_OUTLINE_CACHE_SIZE = 64


def _outline_from_lap(lap: Lap) -> dict:
    path = _telemetry_file(lap)
    series = load_lap(path, ["speed"])
    if "pos_x" not in series.raw or "pos_z" not in series.raw:
        raise AnalysisError("This lap has no position data.")
    grid, step = make_grid(float(series.dist[0]), float(series.dist[-1]), series.length / (OUTLINE_POINTS - 1))
    mirror, info = resolve_mirror(series)
    xy = track_xy(series, grid, mirror)
    sectors = sector_distances(series, lap.sector_times or [])
    return {
        "track_name": lap.track_name,
        "source_lap_id": lap.id,
        "length_m": round(series.length, 1),
        "x": json_series(xy[0], 1),
        "y": json_series(xy[1], 1),
        "grid": {"start": round(float(grid[0]), 3), "step": round(float(step), 4), "count": int(len(grid))},
        "sectors": sectors,
        "mirrored": mirror,
        **info,
    }


@router.get("/circuits/outline")
def circuit_outline(
    track_name: str = Query(min_length=1, max_length=200),
    viewer: Driver = Depends(get_current_driver),
    db: Session = Depends(get_db),
    _rl: None = Depends(rate_limit("analysis", per_driver=True)),
) -> dict:
    """The shape of a layout, taken from its fastest valid lap that has position
    data (no stored track files to maintain; a layout nobody has driven has no
    outline). Includes the lap length and, when known, the sector boundaries."""
    candidates = db.execute(
        select(Lap).where(visible_filter(), Lap.track_name == track_name)
        .order_by(Lap.lap_time.asc(), Lap.id.asc()).limit(OUTLINE_CANDIDATES)
    ).scalars().all()
    if not candidates:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No valid lap on this track yet.")
    last_error = "No lap with usable position data."
    for lap in candidates:
        key = (lap.track_name, lap.id)
        with _OUTLINE_CACHE_LOCK:
            cached = _OUTLINE_CACHE.get(key)
            if cached is not None:
                _OUTLINE_CACHE.move_to_end(key)
                return cached
        try:
            outline = _outline_from_lap(lap)
        except HTTPException as exc:  # file gone / outside storage: try the next lap
            last_error = str(exc.detail)
            continue
        except AnalysisError as exc:
            last_error = str(exc)
            continue
        with _OUTLINE_CACHE_LOCK:
            _OUTLINE_CACHE[key] = outline
            while len(_OUTLINE_CACHE) > _OUTLINE_CACHE_SIZE:
                _OUTLINE_CACHE.popitem(last=False)
        return outline
    raise HTTPException(status.HTTP_404_NOT_FOUND, last_error)


def _driver_laps_for(db: Session, driver_id: int, track_name: str, car_model: Optional[str], valid_only: bool):
    stmt = select(Lap).where(Lap.driver_id == driver_id, Lap.track_name == track_name)
    if car_model:
        stmt = stmt.where(Lap.car_model == car_model)
    if valid_only:
        stmt = stmt.where(visible_filter())
    return db.execute(stmt.order_by(Lap.id.asc()).limit(2000)).scalars().all()


@router.get("/analysis/theoretical-best")
def theoretical_best_lap(
    track_name: str = Query(min_length=1, max_length=200),
    # Required — a theoretical best only makes sense for ONE car model
    car_model: str = Query(min_length=1, max_length=100),
    driver_id: Optional[int] = Query(default=None, ge=1),
    conditions: str = Query(default="auto", pattern="^(auto|dry|mixed|wet|unknown|all)$"),
    viewer: Driver = Depends(get_current_driver),
    db: Session = Depends(get_db),
    _rl: None = Depends(rate_limit("analysis", per_driver=True)),
) -> dict:
    """A driver's theoretical best on a layout with one car model: the fastest time over
    every PROFILE_STEP_M stretch, taken from their VALID laps, summed — plus the simpler
    sector-based ideal (best S1 + S2 + S3). A CALCULATED figure: stretches of different
    laps, never driven as one lap.

    Track conditions are not mixed silently: laps are grouped as dry / mixed /
    wet (share of the lap on a wet surface, server/analysis/lap_insights.py) or unknown (older laps
    without that signal). `conditions=auto` uses the group of the driver's fastest lap;
    every other group is reported with its lap count, never folded in. `all` mixes them on
    request (and says so). Grip level and track temperature are reported as a spread.

    From the database only (the per-lap profiles are stored at upload), so it costs no
    file reads. Another driver's figure uses their valid laps only, like everything else."""
    target_id = driver_id or viewer.id
    target = db.get(Driver, target_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Driver not found.")
    laps = _driver_laps_for(db, target_id, track_name, car_model, valid_only=True)
    described = []
    for lap in laps:
        stats = lap.lap_stats if isinstance(lap.lap_stats, dict) else {}
        session = stats.get("session") if isinstance(stats.get("session"), dict) else {}
        described.append({"lap": lap, "lap_time": lap.lap_time, "conditions": condition_class(stats),
                          "grip": session.get("track_grip_level"), "track_temp": lap.track_temp})
    summary = conditions_summary(described)
    if conditions == "auto":
        fastest = min(described, key=lambda d: (d["lap_time"], d["lap"].id)) if described else None
        selected = fastest["conditions"] if fastest else None
    else:
        selected = conditions
    chosen = described if selected == "all" else [d for d in described if d["conditions"] == selected]
    used_summary = conditions_summary(chosen) if chosen else None
    result = theoretical_best([
        {"id": d["lap"].id, "lap_time": d["lap"].lap_time, "sector_times": d["lap"].sector_times,
         "profile": (d["lap"].lap_stats or {}).get("profile") if isinstance(d["lap"].lap_stats, dict) else None}
        for d in chosen
    ])
    return {
        "driver_id": target_id, "driver_name": target.display_name, "track_name": track_name,
        "car_model": car_model, "found": result is not None, "result": result, "calculated": True,
        "conditions": {
            "selected": selected,
            "groups": summary["groups"],
            "excluded_laps": len(described) - len(chosen),
            "laps_without_conditions": summary["groups"].get("unknown", {}).get("laps", 0),
            "mixed_on_request": selected == "all" and len(summary["groups"]) > 1,
            "grip_levels": used_summary["grip_levels"] if used_summary else [],
            "track_temp_c": used_summary["track_temp_c"] if used_summary else None,
            "varying": bool(used_summary and used_summary["varying"]),
        },
        "laps_without_profile": sum(
            1 for d in chosen if not (isinstance(d["lap"].lap_stats, dict) and d["lap"].lap_stats.get("profile"))),
    }


@router.get("/analysis/sessions")
def driving_sessions(
    track_name: Optional[str] = Query(default=None, max_length=200),
    car_model: Optional[str] = Query(default=None, max_length=100),
    limit: int = Query(default=10, ge=1, le=50),
    viewer: Driver = Depends(get_current_driver),
    db: Session = Depends(get_db),
    _rl: None = Depends(rate_limit("analysis", per_driver=True)),
) -> dict:
    """Your sessions (stints): consecutive laps on one track, car and session type
    without a pause longer than 15 minutes — with robust consistency figures (median,
    spread of the clean laps, how many within 0.5 s of the best), the lap-time trend over
    the stint, and fuel / virtual energy / tyre wear per lap. Your own laps only."""
    stmt = select(Lap).where(Lap.driver_id == viewer.id)
    if track_name:
        stmt = stmt.where(Lap.track_name == track_name)
    if car_model:
        stmt = stmt.where(Lap.car_model == car_model)
    laps = db.execute(stmt.order_by(Lap.id.desc()).limit(1500)).scalars().all()
    sessions = group_sessions([
        {"id": lap.id, "lap_number": lap.lap_number, "lap_time": lap.lap_time, "is_valid": lap.is_valid,
         "recorded_at": lap.recorded_at, "session_type": lap.session_type, "track_name": lap.track_name,
         "car_model": lap.car_model or lap.car_name, "stats": lap.lap_stats}
        for lap in laps
    ])
    return {"sessions": sessions[:limit], "total": len(sessions)}


CORNER_HISTORY_MAX = 20


@router.get("/analysis/corner-history")
def corner_history(
    lap_id: int = Query(ge=1),
    limit: int = Query(default=10, ge=1, le=CORNER_HISTORY_MAX),
    viewer: Driver = Depends(get_current_driver),
    db: Session = Depends(get_db),
    _rl: None = Depends(rate_limit("analysis", per_driver=True)),
) -> dict:
    """The corners of the same driver's EARLIER valid laps on the same layout with the same car
    model, as stored at upload (server/analysis/lap_insights.py "corners": [apex, braking point, min
    speed, full-throttle point, exit speed] per corner). From the database only — the coach
    compares a corner of Lap 1 with how the driver usually drives it ("in 4 of your last 5
    laps ..."). Another driver's laps: valid laps only, like everywhere."""
    lap = _lap_for_viewer(db, lap_id, viewer)
    stmt = (
        select(Lap).where(Lap.driver_id == lap.driver_id, Lap.track_name == lap.track_name,
                          Lap.car_model == lap.car_model, visible_filter(), Lap.id != lap.id,
                          Lap.lap_stats.is_not(None))
        .where((Lap.recorded_at < lap.recorded_at) | ((Lap.recorded_at == lap.recorded_at) & (Lap.id < lap.id)))
        .order_by(Lap.recorded_at.desc(), Lap.id.desc()).limit(limit * 3)
    )
    rows = []
    for other in db.execute(stmt).scalars():
        corners = (other.lap_stats or {}).get("corners") if isinstance(other.lap_stats, dict) else None
        if corners:
            rows.append(other)
        if len(rows) >= limit:
            break
    own = (lap.lap_stats or {}).get("corners") if isinstance(lap.lap_stats, dict) else None
    return {
        "lap_id": lap.id,
        "corners": own or [],
        "laps": [{"id": r.id, "lap_number": r.lap_number, "lap_time": r.lap_time, "recorded_at": r.recorded_at,
                  "corners": r.lap_stats["corners"]} for r in rows],
    }


def _best_sectors(laps: Sequence[Lap]) -> Optional[List[dict]]:
    usable = [lap for lap in laps if lap.is_valid and isinstance(lap.sector_times, list) and len(lap.sector_times) == 3
              and all(isinstance(s, (int, float)) and s > 0 for s in lap.sector_times)]
    if not usable:
        return None
    out = []
    for i in range(3):
        pick = min(usable, key=lambda lap: (lap.sector_times[i], lap.id))
        out.append({"sector": i + 1, "time": round(float(pick.sector_times[i]), 3), "lap_id": pick.id,
                    "lap_number": pick.lap_number})
    return out


@router.get("/analysis/lap-sectors")
def lap_sectors(
    lap_id: int = Query(ge=1),
    viewer: Driver = Depends(get_current_driver),
    db: Session = Depends(get_db),
    _rl: None = Depends(rate_limit("analysis", per_driver=True)),
) -> dict:
    """Sector times of every lap of the SESSION this lap belongs to (same grouping as the Stint
    Analyzer), the fastest DRIVEN sector of the session and of all the driver's valid laps on this
    layout and car model — each with the lap it comes from — and this lap's potential per sector
    (its sector minus the best). The sum of best sectors is a calculated figure, labelled so."""
    lap = _lap_for_viewer(db, lap_id, viewer)
    stmt = select(Lap).where(Lap.driver_id == lap.driver_id, Lap.track_name == lap.track_name,
                             Lap.car_model == lap.car_model)
    if lap.driver_id != viewer.id:
        stmt = stmt.where(visible_filter())
    laps = db.execute(stmt.order_by(Lap.id.desc()).limit(1500)).scalars().all()
    by_id = {row.id: row for row in laps}
    sessions = group_sessions([
        {"id": row.id, "lap_number": row.lap_number, "lap_time": row.lap_time, "is_valid": row.is_valid,
         "recorded_at": row.recorded_at, "session_type": row.session_type, "track_name": row.track_name,
         "car_model": row.car_model or row.car_name, "stats": None}
        for row in laps
    ])
    session = next((s for s in sessions if any(item["id"] == lap.id for item in s["laps"])), None)
    session_rows = [by_id[item["id"]] for item in session["laps"]] if session else [lap]
    session_best = _best_sectors(session_rows)
    personal_best = _best_sectors(laps)

    def potential(best):
        if not best or not (isinstance(lap.sector_times, list) and len(lap.sector_times) == 3
                            and all(isinstance(s, (int, float)) and s > 0 for s in lap.sector_times)):
            return None
        return [round(float(lap.sector_times[i]) - best[i]["time"], 3) for i in range(3)]

    ideal = lambda best: round(sum(b["time"] for b in best), 3) if best else None  # noqa: E731
    return {
        "lap_id": lap.id,
        "laps": [{"id": row.id, "lap_number": row.lap_number, "lap_time": row.lap_time, "is_valid": row.is_valid,
                  "sector_times": row.sector_times} for row in session_rows],
        "session_best": session_best, "session_ideal": ideal(session_best),
        "personal_best": personal_best, "personal_ideal": ideal(personal_best),
        "potential_session": potential(session_best), "potential_personal": potential(personal_best),
        "calculated": True,
    }
