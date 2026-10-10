"""
routers/analysis.py — lap analysis (V0.9.0): channel catalog, lap-vs-lap
comparison, a driver's laps at a track, and the circuit library.

All paths live under /telemetry/analysis/... on purpose: /telemetry/* is already
routed to the API by docker/Caddyfile, so shipping this needs no proxy change.

Who can see what
----------------
  * The channel catalog and the circuit CARDS (names, lap counts, fastest lap)
    are public — they contain nothing the public leaderboards do not already show.
  * Telemetry itself (compare, a track outline, a driver's lap list) needs a
    signed-in driver. Any signed-in driver may open ANY driver's VALID lap —
    that is the point: compare against the leaderboard's number one. A driver can
    always open their own laps, valid or not. Invalid laps of other drivers are
    reported as "not found" (same answer as a lap that does not exist), so a
    moderator-invalidated lap is not reachable by guessing ids.
  * A lap whose file is gone (retention cleanup, see server/telemetry_lifecycle.py)
    answers 404 with a clear message instead of failing.
"""

from __future__ import annotations

import json
import logging
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..analysis_cache import ANALYSIS_CACHE_VERSION, AnalysisCache, analysis_cache
from ..analysis_reference import LapContext, quick_pair, resolve_reference
from ..auth import get_current_driver
from ..catalog import (
    canonical_class,
    known_venue_names,
    venue_for,
    venue_matches_query,
)
from ..config import settings
from ..database import get_db
from ..models import Driver, Lap
from ..rate_limit import rate_limit
from ..telemetry_analysis import (
    DEFAULT_STEP_M,
    AnalysisError,
    LapSeries,
    channel_values,
    common_range,
    delta_series,
    channel_decimals,
    channel_span,
    corner_analysis,
    delta_facts,
    json_series,
    load_lap,
    spikes,
    logical_columns,
    make_grid,
    orientation_report,
    recording_level,
    resample,
    resolve_mirror,
    sector_distances,
    time_at,
    track_xy,
)
from ..versioning import data_level_for_client_version
from ..telemetry_channels import (
    CATALOG,
    DEFAULT_CHANNELS,
    catalog_listing,
)

logger = logging.getLogger("lmu_garage.analysis")

router = APIRouter(prefix="/telemetry/analysis", tags=["analysis"])

MAX_CHANNELS_PER_REQUEST = 24
MAX_POINTS_DEFAULT = 8000  # = telemetry_analysis.MAX_GRID_POINTS, the V0.9.0 ceiling
OUTLINE_POINTS = 600
OUTLINE_CANDIDATES = 5


# ---------------------------------------------------------------------------
# Access + files
# ---------------------------------------------------------------------------

def _lap_for_viewer(db: Session, lap_id: int, viewer: Driver) -> Lap:
    lap = db.get(Lap, lap_id)
    if lap is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Lap {lap_id} not found.")
    if lap.driver_id == viewer.id:
        return lap
    if not lap.is_valid:
        # Same answer as a missing lap: no hint that an invalidated lap exists.
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Lap {lap_id} not found.")
    return lap


def _telemetry_file(lap: Lap) -> Path:
    """The lap's parquet file, only if it really lives under the telemetry
    storage directory (the DB value is never trusted as a free-form path)."""
    root = settings.telemetry_storage_dir.resolve()
    try:
        path = Path(lap.telemetry_path).resolve()
    except (OSError, RuntimeError, ValueError):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Telemetry file is not available.")
    if root != path and root not in path.parents:
        logger.error("Lap %s has a telemetry_path outside the storage directory", lap.id)
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Telemetry file is not available.")
    if not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "The telemetry of this lap is no longer stored.")
    return path


def _parse_channels(channels: Optional[str]) -> List[str]:
    if channels is None or not channels.strip():
        return list(DEFAULT_CHANNELS)
    keys = [part.strip() for part in channels.split(",") if part.strip()]
    keys = list(dict.fromkeys(keys))  # drop duplicates, keep order
    if len(keys) > MAX_CHANNELS_PER_REQUEST:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"At most {MAX_CHANNELS_PER_REQUEST} channels per request.")
    unknown = [key for key in keys if key not in CATALOG]
    if unknown:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown channel(s): {', '.join(unknown[:5])}")
    return keys


def _recording(offered: set, lap: Lap) -> dict:
    """What the lap's FILE actually contains (authoritative — it is what the
    charts are drawn from): "full" for a V0.9.0+ recording, "basic" for a lap from
    an older client, which has the basic channels only."""
    available = sum(1 for channel in CATALOG.values() if all(column in offered for column in channel.needs))
    return {
        "level": recording_level(offered),
        "client_version": lap.client_version,
        "channels_available": available,
        "channels_total": len(CATALOG),
    }


def _lap_header(lap: Lap, driver_name: str) -> dict:
    return {
        "id": lap.id,
        "driver_id": lap.driver_id,
        "driver_name": driver_name,
        "track_name": lap.track_name,
        "car_name": lap.car_name,
        "car_class": lap.car_class,
        "car_model": lap.car_model,
        "lap_number": lap.lap_number,
        "lap_time": lap.lap_time,
        "sector_times": lap.sector_times,
        "is_valid": lap.is_valid,
        "ambient_temp": lap.ambient_temp,
        "track_temp": lap.track_temp,
        "uploaded_at": lap.uploaded_at.isoformat() if lap.uploaded_at else None,
    }


def _available_keys(path: Path) -> List[str]:
    offered = logical_columns(path)
    return [key for key, channel in CATALOG.items() if all(column in offered for column in channel.needs)]


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------

def _catalog_payload() -> tuple:
    body = _compact_json({"channels": catalog_listing(), "default": list(DEFAULT_CHANNELS)})
    return body, AnalysisCache.etag_for(AnalysisCache.make_key("catalog"), body.decode("utf-8"))


@router.get("/channels")
def list_channels(request: Request) -> Response:
    """Every channel the analysis can show, grouped, with unit and label.

    Static and the same for everyone, so — unlike everything else here — it may be
    cached publicly. The ETag is derived from the content, so it changes exactly
    when the catalog does."""
    body, etag = _catalog_payload()
    headers = {"ETag": etag, "Cache-Control": "public, max-age=300"}
    if _etag_matches(request, etag):
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)
    return Response(content=body, media_type="application/json", headers=headers)


# ---------------------------------------------------------------------------
# A driver's laps (for picking a lap to compare)
# ---------------------------------------------------------------------------

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
        stmt = stmt.where(Lap.is_valid.is_(True))
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


@router.get("/laps/{lap_id}")
def lap_info(
    lap_id: int,
    viewer: Driver = Depends(get_current_driver),
    db: Session = Depends(get_db),
    _rl: None = Depends(rate_limit("analysis", per_driver=True)),
) -> dict:
    """A lap's header plus which catalog channels its file actually contains
    (laps recorded before V0.9.0 only have the basic ones)."""
    lap = _lap_for_viewer(db, lap_id, viewer)
    path = _telemetry_file(lap)
    owner = db.get(Driver, lap.driver_id)
    try:
        available = _available_keys(path)
    except AnalysisError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    return {
        "lap": _lap_header(lap, owner.display_name if owner else "?"),
        "available_channels": available,
        "recording": _recording(logical_columns(path), lap),
    }


# ---------------------------------------------------------------------------
# Reference laps: "compare with Garage16 Best / Team Best / My Best" (V0.9.1)
# ---------------------------------------------------------------------------

REFERENCE_LABELS = {"wr": "Garage16 Best", "team_best": "Team Best", "my_best": "My Best"}
REASON_MESSAGES = {
    "no_lap": "No comparable lap available.",
    "no_team": "You are not in a team.",
    "no_team_lap": "No team best available.",
    "no_own_lap": "You have no analysable lap on this layout yet.",
    "already_best": "This lap already is the best one.",
}


def _analysable(lap: Lap) -> bool:
    """A lap can be offered as a reference only if its file can actually be opened."""
    try:
        _telemetry_file(lap)
    except HTTPException:
        return False
    return True


def _header_for(db: Session, lap: Optional[Lap]) -> Optional[dict]:
    if lap is None:
        return None
    owner = db.get(Driver, lap.driver_id)
    header = _lap_header(lap, owner.display_name if owner else "?")
    header["data_level"] = data_level_for_client_version(lap.client_version)
    return header


@router.get("/reference")
def reference_lap(
    kind: str = Query(pattern="^(wr|team_best|my_best)$"),
    lap_id: int = Query(ge=1, description="The lap being viewed; defines track and car."),
    scope: str = Query(default="model", pattern="^(model|class)$"),
    viewer: Driver = Depends(get_current_driver),
    db: Session = Depends(get_db),
    _rl: None = Depends(rate_limit("analysis", per_driver=True)),
) -> dict:
    """The lap to compare `lap_id` with.

      kind=wr         fastest VALID lap by anyone on the same track layout and car
                      (records.py's WR rule). Garage16 has no official world record:
                      this is the fastest lap on Garage16, shown as "Garage16 Best".
      kind=team_best  fastest valid lap among everyone who shares a team with you
      kind=my_best    your own fastest valid lap
      scope           "model" (same car, default) or "class" (same car class)

    `found` is false — with a `reason` code and no lap — when nothing suitable exists;
    a different lap is never substituted. If the best lap IS `lap_id`, it is not
    offered again: `already_best` is true and `lap` is the next-fastest, or null.
    The lap being viewed must be one the viewer may open.
    """
    current = _lap_for_viewer(db, lap_id, viewer)
    result = resolve_reference(
        db, viewer, kind, LapContext.of(current), scope=scope, exclude_lap_id=current.id, analysable=_analysable,
    )
    return {
        "kind": kind,
        "label": REFERENCE_LABELS[kind],
        "scope": scope,
        "found": result.lap is not None,
        "lap": _header_for(db, result.lap),
        "reason": result.reason,
        "message": REASON_MESSAGES.get(result.reason) if result.reason else None,
        "already_best": result.already_best,
    }


@router.get("/quick")
def quick_compare(
    mode: str = Query(pattern="^(wr|team_best|my_best)$"),
    track_name: Optional[str] = Query(default=None, min_length=1, max_length=200),
    car_model: Optional[str] = Query(default=None, max_length=100),
    scope: str = Query(default="model", pattern="^(model|class)$"),
    viewer: Driver = Depends(get_current_driver),
    db: Session = Depends(get_db),
    _rl: None = Depends(rate_limit("analysis", per_driver=True)),
) -> dict:
    """"You vs ..." in one call — nobody has to look up two lap ids.

    Lap A: your best valid lap on `track_name` (optionally for one car model) — for
    mode=my_best your most RECENT valid lap instead, compared with your best.
    Lap B: the reference of `mode` for lap A's track and car (see /reference).
    Used by "You vs Garage16 Best", "You vs Team Best", "You vs My Best" and the
    circuit library's "Compare my lap with it". Nothing found => found=false and a
    reason, never a made-up pairing. Without `track_name` the viewer's most recent
    valid lap decides track and car.
    """
    pair = quick_pair(
        db, viewer, mode, track_name, car_model=car_model or None, scope=scope, analysable=_analysable,
    )
    reference = pair.reference
    found = pair.lap_a is not None and pair.lap_b is not None
    return {
        "mode": mode,
        "label": REFERENCE_LABELS[mode],
        "found": found,
        "lap_a": _header_for(db, pair.lap_a),
        "lap_b": _header_for(db, pair.lap_b),
        "reason": None if found else reference.reason,
        "message": None if found else REASON_MESSAGES.get(reference.reason),
        "already_best": reference.already_best,
    }


# ---------------------------------------------------------------------------
# Compare
# ---------------------------------------------------------------------------

def _open_lap(db: Session, lap_id: int, viewer: Driver):
    """Authorisation and file checks for one lap — always BEFORE the cache is
    consulted, so a cache entry can never be reached for a lap the viewer may not
    see. Returns (lap row, parquet path, owner's display name)."""
    lap = _lap_for_viewer(db, lap_id, viewer)
    path = _telemetry_file(lap)
    owner = db.get(Driver, lap.driver_id)
    return lap, path, owner.display_name if owner else "?"


def _load(lap_id: int, path: Path, channels: List[str]) -> LapSeries:
    try:
        return load_lap(path, channels)
    except AnalysisError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Lap {lap_id}: {exc}")


def _compact_json(value) -> bytes:
    return json.dumps(value, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _etag_matches(request: Request, etag: str) -> bool:
    """If-None-Match may list several validators and may mark them weak (W/...)."""
    header = request.headers.get("if-none-match")
    if not header:
        return False
    wanted = etag.strip('"')
    for token in header.split(","):
        token = token.strip()
        if token == "*" or token.removeprefix("W/").strip('"') == wanted:
            return True
    return False


# Authenticated responses: the browser may keep them but must revalidate (a 304 is
# cheap, and the permission check has already happened by then). Never "public".
PRIVATE_REVALIDATE = "private, no-cache"
VARY_AUTH = "Authorization, Cookie"


def _compute_comparison(
    series_a: LapSeries, series_b: Optional[LapSeries], lap_a_row: Lap, lap_b_row: Optional[Lap],
    keys: List[str], lo: float, hi: float, step: float, max_points: int, whole_lap: bool = True,
) -> dict:
    """Everything numeric in the response (no lap headers — those are added fresh
    for every request, see compare_laps)."""
    try:
        grid, used_step = make_grid(lo, hi, step, max_points)
    except AnalysisError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))

    laps: Dict[str, Optional[LapSeries]] = {"a": series_a, "b": series_b}
    out_channels: Dict[str, dict] = {}
    unavailable: List[str] = []
    for key in keys:
        channel = CATALOG[key]
        decimals = channel_decimals(channel.unit, channel.discrete)
        entry = {
            "label": channel.label, "unit": channel.unit, "group": channel.group,
            "discrete": channel.discrete,
            "fixed_range": list(channel.fixed_range) if channel.fixed_range else None,
        }
        native = {side: channel_values(series, key) if series is not None else None for side, series in laps.items()}
        span = channel_span(list(native.values()), channel.fixed_range)
        missing = False
        for side, series in laps.items():
            if series is None:
                entry[side] = None
                continue
            values = native[side]
            if values is None:
                entry[side] = None
                missing = True
                continue
            line = resample(series, values, grid, channel.discrete)
            entry[side] = json_series(line, decimals)
            # V0.9.1: what the grid would hide (brake taps, kerbs, spikes between grid points)
            if not channel.discrete:
                hidden = spikes(series, values, grid, used_step, line, span, decimals)
                if hidden:
                    entry[f"{side}_spikes"] = hidden
        if missing:
            unavailable.append(key)
        out_channels[key] = entry

    mirror, mirror_info = resolve_mirror(series_a, series_b)
    map_out: dict = {"mirrored": mirror, **mirror_info}
    for side, series in laps.items():
        xy = track_xy(series, grid, mirror) if series is not None else None
        map_out[f"x_{side}"] = json_series(xy[0], 1) if xy else None
        map_out[f"y_{side}"] = json_series(xy[1], 1) if xy else None

    return {
        "grid": {"start": round(float(grid[0]), 3), "step": round(float(used_step), 4), "count": int(len(grid))},
        "delta": json_series(delta_series(series_a, series_b, grid)) if series_b is not None else None,
        "time": {
            "a": json_series(time_at(series_a, grid)),
            "b": json_series(time_at(series_b, grid)) if series_b is not None else None,
        },
        "recording": {
            "a": _recording(series_a.available, lap_a_row),
            "b": _recording(series_b.available, lap_b_row) if series_b is not None else None,
        },
        "channels": out_channels,
        "unavailable": unavailable,
        "map": map_out,
        "sectors": {
            "a": sector_distances(series_a, lap_a_row.sector_times or []),
            "b": sector_distances(series_b, lap_b_row.sector_times or []) if series_b is not None else [],
        },
        "orientation": {
            "a": orientation_report(series_a),
            "b": orientation_report(series_b) if series_b is not None else None,
        },
        # V0.9.1: every corner of the common stretch, measured on the raw samples (whole lap only;
        # a zoom stretch has no corners of its own)
        "corners": corner_analysis(series_a, series_b) if series_b is not None and whole_lap else [],
        "delta_facts": delta_facts(series_a, series_b) if series_b is not None and whole_lap else None,
        "analysis_version": ANALYSIS_CACHE_VERSION,
    }


@router.get("/compare")
def compare_laps(
    request: Request,
    lap_a: int = Query(ge=1),
    lap_b: Optional[int] = Query(default=None, ge=1),
    channels: Optional[str] = Query(default=None, max_length=1200),
    step: float = Query(default=DEFAULT_STEP_M, ge=0.1, le=100.0),
    start: Optional[float] = Query(default=None, ge=0, description="Only this stretch of track: first metre."),
    end: Optional[float] = Query(default=None, ge=0, description="Only this stretch of track: last metre."),
    max_points: Optional[int] = Query(default=None, ge=50, le=8000, description="Upper bound for points per series."),
    viewer: Driver = Depends(get_current_driver),
    db: Session = Depends(get_db),
    _rl: None = Depends(rate_limit("analysis", per_driver=True)),
) -> Response:
    """Two laps (or one, when lap_b is omitted) on a common distance grid.

    Response, in short:
      grid        {start, step, count} — distance in metres; point i is at start + i*step
      laps        {a, b} headers (driver, car, lap time, sectors ...)
      delta       seconds, A minus B, null without lap_b; positive = A is behind
      time        {a, b} seconds since lap start at each grid point
      channels    {key: {label, unit, group, discrete, fixed_range, a, b}} — a/b are
                  value lists, or null when that lap's file lacks the channel
      recording   {a, b}: {level: "full"|"basic", client_version, channels_available,
                  channels_total} — "basic" = recorded by a client older than 0.9.0,
                  so only the basic channels exist for that lap
      unavailable channel keys missing in at least one of the laps
      map         {x_a, y_a, x_b, y_b, mirrored, ...} plan view, Y up, same frame for both
      sectors     {a: [d1, d2], b: [...]} distances of the sector boundaries
      orientation how each lap's sign conventions were decided (for transparency)
      analysis_version  version of the computation (V0.9.1, additive)

    How much data is sent (V0.9.1): only the requested channels are read and
    returned; `max_points` bounds the points per series (the grid is widened to
    fit — the raw data in the file is untouched); `start`/`end` return just a
    stretch of track, at the same or a finer `step`, for zoom-in refinement. Without
    these parameters the response is what V0.9.0 returned.

    Caching: the numeric part is cached in memory (server/analysis_cache.py), keyed
    by the laps' content hashes, the channels and the grid parameters. Permissions
    are checked first, every time. The response carries an ETag; a repeat request
    with If-None-Match gets 304 without recomputing anything.
    """
    keys = _parse_channels(channels)

    # 1. Permission + file checks (never skipped, never answered from the cache)
    lap_a_row, path_a, name_a = _open_lap(db, lap_a, viewer)
    lap_b_row = path_b = name_b = None
    if lap_b is not None:
        lap_b_row, path_b, name_b = _open_lap(db, lap_b, viewer)
        if lap_b_row.track_name != lap_a_row.track_name:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"The laps are from different tracks ({lap_a_row.track_name} / {lap_b_row.track_name}).",
            )

    # 2. Key + validator. Lap headers (driver names, validity) can change after the
    #    fact, so they are NOT part of the cached bytes but ARE part of the ETag.
    headers_json = _compact_json({
        "a": _lap_header(lap_a_row, name_a),
        "b": _lap_header(lap_b_row, name_b) if lap_b_row is not None else None,
    })
    cache_key = AnalysisCache.make_key(
        "compare",
        lap_a_row.telemetry_hash, lap_b_row.telemetry_hash if lap_b_row is not None else None,  # order matters
        tuple(lap_a_row.sector_times or []), tuple(lap_b_row.sector_times or []) if lap_b_row is not None else (),
        tuple(keys), round(step, 4), start, end, max_points,
    )
    etag = AnalysisCache.etag_for(cache_key, headers_json.decode("utf-8"))
    response_headers = {"ETag": etag, "Cache-Control": PRIVATE_REVALIDATE, "Vary": VARY_AUTH}
    if _etag_matches(request, etag):
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=response_headers)

    # 3. Cache lookup, else compute from only the columns needed
    numeric = analysis_cache.get(cache_key)
    if numeric is None:
        series_a = _load(lap_a, path_a, keys)
        series_b = _load(lap_b, path_b, keys) if lap_b is not None else None
        if series_b is not None:
            lo, hi = common_range(series_a, series_b)
        else:
            lo, hi = float(series_a.dist[0]), float(series_a.dist[-1])
        if start is not None:
            lo = max(lo, float(start))
        if end is not None:
            hi = min(hi, float(end))
        if (start is not None or end is not None) and hi - lo < 10.0:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "The requested section does not overlap the lap.")
        result = _compute_comparison(
            series_a, series_b, lap_a_row, lap_b_row, keys, lo, hi, step, max_points or MAX_POINTS_DEFAULT,
            whole_lap=start is None and end is None,
        )
        numeric = _compact_json(result)
        analysis_cache.put(cache_key, numeric)

    # 4. {"laps": <fresh headers>, <cached numeric members>}
    body = b'{"laps":' + headers_json + b"," + numeric[1:]
    return Response(content=body, media_type="application/json", headers=response_headers)


# ---------------------------------------------------------------------------
# Circuit library
# ---------------------------------------------------------------------------

@router.get("/circuits")
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
        .where(Lap.is_valid.is_(True))
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
            .where(Lap.is_valid.is_(True)).group_by(Lap.track_name)
        ).all()
    }
    classes: Dict[str, set] = {}
    for track, car_class in db.execute(
        select(Lap.track_name, Lap.car_class).where(Lap.is_valid.is_(True), Lap.car_class.is_not(None)).distinct()
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
        select(Lap).where(Lap.is_valid.is_(True), Lap.track_name == track_name)
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
