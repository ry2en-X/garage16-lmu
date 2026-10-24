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
from typing import Dict, List, Optional, Sequence

import numpy as np

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session, object_session

from ..analysis_cache import ANALYSIS_CACHE_VERSION, AnalysisCache, analysis_cache
from ..analysis_reference import LapContext, quick_pair, resolve_reference
from ..auth import get_current_driver
from ..catalog import (
    canonical_class,
    known_venue_names,
    venue_for,
    venue_matches_query,
)
from ..database import get_db
from ..models import Driver, Lap
from ..rate_limit import rate_limit
from ..lap_insights import condition_class, conditions_summary, group_sessions, theoretical_best
from ..telemetry_analysis import (
    CORNER_KEYS,
    DAMPER_KNEE_MM_S,
    _find_corners,
    corner_turns,
    inside_sign,
    line_offset,
    DEFAULT_STEP_M,
    WHEEL_EVENT_CHANNELS,
    distribution,
    histogram_edges,
    wheel_events,
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
from .. import integrity
from ..request_context import current_request_id
from ..official import is_visible, official_filter, visible_filter
from ..telemetry_channels import (
    CATALOG,
    DEFAULT_CHANNELS,
    catalog_listing,
)

logger = logging.getLogger("lmu_garage.analysis")

router = APIRouter(prefix="/telemetry/analysis", tags=["analysis"])

MAX_CHANNELS_PER_REQUEST = 24
# V0.9.4: up to two more laps drawn over Lap 1 / Lap 2 ("c", "d"); delta to the reference lap
EXTRA_SIDES = ("c", "d")
LINE_MAX_POINTS = 4000
LINE_MIN_STEP_M = 0.5

# V0.9.4: channels that are not in a lap file but come from the PAIR of laps. They are listed
# in the catalog (marked "pair") and requested like any other channel.
PAIR_CHANNELS: Dict[str, dict] = {
    "line_offset": {
        "key": "line_offset", "label": "Line of Lap 2 vs Lap 1 (inside = +)", "unit": "m", "group": "Racing line",
        "discrete": False, "fixed_range": None, "pair": True,
    },
}
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
    if not is_visible(lap):
        # Same answer as a missing lap: no hint that an invalidated (V0.9.9: or quarantined) lap exists.
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Lap {lap_id} not found.")
    return lap


INTEGRITY_MESSAGE = ("INTEGRITY_FAILED: the telemetry file of this lap no longer matches what was uploaded. "
                     "The lap was set aside for an admin to check.")


def _telemetry_file(lap: Lap) -> Path:
    """The lap's parquet file, only if it really lives under the telemetry
    storage directory (the DB value is never trusted as a free-form path).

    V0.9.9: and only if it is still exactly the file that was uploaded (server/integrity.py:
    size, sha256 — cached per unchanged file — and the server's MAC). A file that is missing,
    changed or swapped puts the lap into quarantine (it leaves leaderboards and records) and the
    request fails without revealing paths: 404 for a missing file (as before), 422 with the code
    INTEGRITY_FAILED for a changed one (the status the analysis already uses for unusable files)."""
    result = integrity.verify(lap, use_cache=True)
    if result.ok:
        path, _ = integrity.resolve_inside_storage(lap.telemetry_path)
        return path
    request_id = current_request_id()
    lap_id = lap.id
    if result.status == integrity.OUTSIDE:
        logger.error("Lap %s has a telemetry_path outside the storage directory", lap_id)
    if result.quarantine:
        # end this read-only request's transaction first (SQLite: BEGIN IMMEDIATE holds the write
        # lock), then quarantine in a session of its own
        session = object_session(lap)
        if session is not None:
            session.rollback()
        integrity.quarantine_by_id(lap_id, result, request_id=request_id)
    if result.status in (integrity.MISSING, integrity.OUTSIDE):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "The telemetry of this lap is no longer stored."
                            if result.status == integrity.MISSING else "Telemetry file is not available.")
    raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, INTEGRITY_MESSAGE)


def _parse_channels(channels: Optional[str]) -> List[str]:
    if channels is None or not channels.strip():
        return list(DEFAULT_CHANNELS)
    keys = [part.strip() for part in channels.split(",") if part.strip()]
    keys = list(dict.fromkeys(keys))  # drop duplicates, keep order
    if len(keys) > MAX_CHANNELS_PER_REQUEST:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"At most {MAX_CHANNELS_PER_REQUEST} channels per request.")
    unknown = [key for key in keys if key not in CATALOG and key not in PAIR_CHANNELS]
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
        # V0.9.3: the track-limits verdict with its findings (positions on the lap), the
        # review state, and the compact per-lap figures without the time profile
        "validation": lap.validation,
        "review_status": lap.review_status,
        "stats": _public_stats(lap.lap_stats),
    }


def _public_stats(stats) -> Optional[dict]:
    if not isinstance(stats, dict):
        return None
    return {key: value for key, value in stats.items() if key != "profile"}


def _available_keys(path: Path) -> List[str]:
    offered = logical_columns(path)
    return [key for key, channel in CATALOG.items() if all(column in offered for column in channel.needs)]


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------

def _catalog_payload() -> tuple:
    body = _compact_json({"channels": catalog_listing() + list(PAIR_CHANNELS.values()), "default": list(DEFAULT_CHANNELS)})
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


@router.get("/laps/{lap_id}/health")
def lap_health(
    lap_id: int,
    request: Request,
    viewer: Driver = Depends(get_current_driver),
    db: Session = Depends(get_db),
    _rl: None = Depends(rate_limit("analysis", per_driver=True)),
) -> Response:
    """V0.9.7 — how trustworthy this lap's recording is (server/telemetry_health.py): channels,
    samples, gaps, time/distance only forward, distance-axis anchoring, speed vs lap distance,
    duration vs lap time, implausible jumps, the resolution of a braking point — plus the lap's
    validation verdict next to it. The file part is cached by the file's content hash."""
    from ..telemetry_health import HEALTH_VERSION, report, safe_file_health

    lap = _lap_for_viewer(db, lap_id, viewer)
    path = _telemetry_file(lap)
    key = AnalysisCache.make_key("health", HEALTH_VERSION, lap.telemetry_hash or str(lap.id), lap.lap_time)
    cached = analysis_cache.get(key)
    if cached is None:
        part = safe_file_health(path, lap.lap_time)
        analysis_cache.put(key, _compact_json(part))
    else:
        part = json.loads(cached)
    body = _compact_json({"lap_id": lap.id, **report(part, lap)})
    return Response(content=body, media_type="application/json",
                    headers={"Cache-Control": PRIVATE_REVALIDATE, "Vary": VARY_AUTH})


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


def _inside_for(series_a: LapSeries, series_b: Optional[LapSeries], mirror: bool, grid: np.ndarray) -> np.ndarray:
    """V0.9.4: for every point of `grid` the direction of the nearest corner (+1 left, -1 right,
    0 none) — the side that is "inside". Decided on the WHOLE lap (2 m grid), so a zoomed
    stretch gets the same answer as the overview."""
    lo, hi = (common_range(series_a, series_b) if series_b is not None
              else (float(series_a.dist[0]), float(series_a.dist[-1])))
    speed_a = channel_values(series_a, "speed")
    if speed_a is None or hi - lo < 50.0:
        return np.zeros(len(grid), dtype=int)
    step = 2.0
    whole = np.arange(lo, hi, step)
    v = np.interp(whole, series_a.dist, speed_a)
    if series_b is not None:
        speed_b = channel_values(series_b, "speed")
        if speed_b is not None:
            v = (v + np.interp(whole, series_b.dist, speed_b)) / 2.0
    found = _find_corners(whole, v, step)
    xy = track_xy(series_a, whole, mirror)
    if not found or xy is None:
        return np.zeros(len(grid), dtype=int)
    corners = [{"apex": float(whole[apex])} for apex, _ in found]
    turns = corner_turns(corners, xy[0], xy[1], whole, step)
    return inside_sign(np.asarray(grid, dtype=float), corners, turns)


def _line_offsets(series_a: LapSeries, series_b: Optional[LapSeries], xy_a, xy_b, mirror: bool,
                  grid: np.ndarray, step: float) -> Optional[dict]:
    """Lap 2's line relative to Lap 1's at every grid point: metres, + = further INSIDE of the
    nearest corner; "side" is that corner's direction (+1 left, -1 right). None without positions."""
    if series_b is None or xy_a is None or xy_b is None:
        return None
    left = line_offset(xy_a[0], xy_a[1], xy_b[0], xy_b[1], step)
    side = _inside_for(series_a, series_b, mirror, grid)
    inside = np.where(side != 0, left * side, np.nan)
    return {"offset": inside, "side": side}


def _compute_comparison(
    series_a: LapSeries, series_b: Optional[LapSeries], lap_a_row: Lap, lap_b_row: Optional[Lap],
    keys: List[str], lo: float, hi: float, step: float, max_points: int, whole_lap: bool = True,
    extras: Sequence[tuple] = (),
) -> dict:
    """Everything numeric in the response (no lap headers — those are added fresh
    for every request, see compare_laps). `extras`: [(side, series, lap row)] for the additional
    laps "c" / "d" (V0.9.4)."""
    try:
        grid, used_step = make_grid(lo, hi, step, max_points)
    except AnalysisError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))

    laps: Dict[str, Optional[LapSeries]] = {"a": series_a, "b": series_b}
    rows: Dict[str, Optional[Lap]] = {"a": lap_a_row, "b": lap_b_row}
    for side, series, row in extras:
        laps[side] = series
        rows[side] = row

    mirror, mirror_info = resolve_mirror(series_a, series_b)
    map_out: dict = {"mirrored": mirror, **mirror_info}
    xy: Dict[str, Optional[tuple]] = {}
    for side, series in laps.items():
        xy[side] = track_xy(series, grid, mirror) if series is not None else None
        if side in ("a", "b") or xy[side] is not None:
            map_out[f"x_{side}"] = json_series(xy[side][0], 1) if xy[side] else None
            map_out[f"y_{side}"] = json_series(xy[side][1], 1) if xy[side] else None
    # V0.9.4: Lap 2's line relative to Lap 1's (inside = +), and the inside direction
    line = _line_offsets(series_a, series_b, xy["a"], xy["b"], mirror, grid, used_step)
    if line is not None:
        map_out["offset_b"] = json_series(line["offset"], 2)
        map_out["side"] = [int(v) for v in line["side"]]

    out_channels: Dict[str, dict] = {}
    unavailable: List[str] = []
    for key in keys:
        if key in PAIR_CHANNELS:
            entry = {k: v for k, v in PAIR_CHANNELS[key].items() if k != "key"}
            entry["a"] = map_out.get("offset_b") if line is not None else None
            entry["b"] = None
            if entry["a"] is None:
                unavailable.append(key)
            out_channels[key] = entry
            continue
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
                if side in ("a", "b"):
                    entry[side] = None
                continue
            values = native[side]
            if values is None:
                entry[side] = None
                missing = True
                continue
            line_values = resample(series, values, grid, channel.discrete)
            entry[side] = json_series(line_values, decimals)
            # V0.9.1: what the grid would hide (brake taps, kerbs, spikes between grid points)
            if not channel.discrete:
                hidden = spikes(series, values, grid, used_step, line_values, span, decimals)
                if hidden:
                    entry[f"{side}_spikes"] = hidden
        if missing:
            unavailable.append(key)
        out_channels[key] = entry

    reference = series_b if series_b is not None else series_a
    times = {side: json_series(time_at(series, grid)) for side, series in laps.items() if series is not None}
    times.setdefault("b", None)
    result = {
        "grid": {"start": round(float(grid[0]), 3), "step": round(float(used_step), 4), "count": int(len(grid))},
        "delta": json_series(delta_series(series_a, series_b, grid)) if series_b is not None else None,
        "time": times,
        "recording": {side: (_recording(laps[side].available, rows[side]) if laps[side] is not None else None)
                      for side in laps},
        "channels": out_channels,
        "unavailable": unavailable,
        "map": map_out,
        "sectors": {side: (sector_distances(laps[side], rows[side].sector_times or []) if laps[side] is not None else [])
                    for side in laps},
        "orientation": {
            "a": orientation_report(series_a),
            "b": orientation_report(series_b) if series_b is not None else None,
        },
        # V0.9.1: every corner of the common stretch, measured on the raw samples (whole lap only;
        # a zoom stretch has no corners of its own). V0.9.4: + phases and braking/throttle details
        "corners": corner_analysis(series_a, series_b) if series_b is not None and whole_lap else [],
        "delta_facts": delta_facts(series_a, series_b) if series_b is not None and whole_lap else None,
        "analysis_version": ANALYSIS_CACHE_VERSION,
    }
    # V0.9.4: the additional laps' delta to the reference lap (Lap 2, or Lap 1 without one)
    if extras:
        result["extra_delta"] = {side: json_series(delta_series(series, reference, grid)) for side, series, _ in extras}
        result["extra_sides"] = [side for side, _, _ in extras]
    return result


def _parse_extra(extra: Optional[str], lap_a: int, lap_b: Optional[int]) -> List[int]:
    if extra is None or not extra.strip():
        return []
    ids: List[int] = []
    for part in extra.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            value = int(part)
        except ValueError:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "extra: comma-separated lap ids.")
        if value < 1:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "extra: comma-separated lap ids.")
        if value not in ids and value != lap_a and value != lap_b:
            ids.append(value)
    if len(ids) > len(EXTRA_SIDES):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"At most {len(EXTRA_SIDES)} additional laps.")
    return ids


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
    extra: Optional[str] = Query(default=None, max_length=40, description="V0.9.4: up to two more lap ids, comma-separated."),
    viewer: Driver = Depends(get_current_driver),
    db: Session = Depends(get_db),
    _rl: None = Depends(rate_limit("analysis", per_driver=True)),
) -> Response:
    """Two laps (or one, when lap_b is omitted) on a common distance grid.

    Response, in short:
      grid        {start, step, count} — distance in metres; point i is at start + i*step
      laps        {a, b} headers (driver, car, lap time, sectors ...); + c, d (V0.9.4)
      delta       seconds, A minus B, null without lap_b; positive = A is behind
      time        {a, b} seconds since lap start at each grid point
      channels    {key: {label, unit, group, discrete, fixed_range, a, b}} — a/b are
                  value lists, or null when that lap's file lacks the channel
      recording   {a, b}: {level: "full"|"basic", client_version, channels_available,
                  channels_total} — "basic" = recorded by a client older than 0.9.0,
                  so only the basic channels exist for that lap
      unavailable channel keys missing in at least one of the laps
      map         {x_a, y_a, x_b, y_b, mirrored, ...} plan view, Y up, same frame for both;
                  V0.9.4: offset_b (Lap 2's line vs Lap 1's, m, inside = +) and side
                  (direction of the nearest corner, +1 left / -1 right)
      sectors     {a: [d1, d2], b: [...]} distances of the sector boundaries
      orientation how each lap's sign conventions were decided (for transparency)
      corners     V0.9.1 corner table; V0.9.4 adds braking/throttle details and phases
      extra_delta V0.9.4, with `extra`: {c: [...], d: [...]} each extra lap minus the reference
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
    extra_ids = _parse_extra(extra, lap_a, lap_b)

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
    extra_rows = []
    for side, lap_id in zip(EXTRA_SIDES, extra_ids):
        row, path, name = _open_lap(db, lap_id, viewer)
        if row.track_name != lap_a_row.track_name:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Lap {lap_id} is from a different track ({row.track_name}).")
        extra_rows.append((side, lap_id, row, path, name))

    # 2. Key + validator. Lap headers (driver names, validity) can change after the
    #    fact, so they are NOT part of the cached bytes but ARE part of the ETag.
    headers = {
        "a": _lap_header(lap_a_row, name_a),
        "b": _lap_header(lap_b_row, name_b) if lap_b_row is not None else None,
    }
    for side, _lap_id, row, _path, name in extra_rows:
        headers[side] = _lap_header(row, name)
    headers_json = _compact_json(headers)
    cache_key = AnalysisCache.make_key(
        "compare",
        lap_a_row.telemetry_hash, lap_b_row.telemetry_hash if lap_b_row is not None else None,  # order matters
        tuple(lap_a_row.sector_times or []), tuple(lap_b_row.sector_times or []) if lap_b_row is not None else (),
        tuple(keys), round(step, 4), start, end, max_points,
        tuple((side, row.telemetry_hash, tuple(row.sector_times or [])) for side, _i, row, _p, _n in extra_rows),
    )
    etag = AnalysisCache.etag_for(cache_key, headers_json.decode("utf-8"))
    response_headers = {"ETag": etag, "Cache-Control": PRIVATE_REVALIDATE, "Vary": VARY_AUTH}
    if _etag_matches(request, etag):
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=response_headers)

    # 3. Cache lookup, else compute from only the columns needed
    numeric = analysis_cache.get(cache_key)
    if numeric is None:
        whole_lap = start is None and end is None
        file_keys = [k for k in keys if k in CATALOG]
        # V0.9.4: the corner table needs pedals, steering and long G even when no chart shows them
        load_keys = file_keys + [k for k in CORNER_KEYS if k in CATALOG and k not in file_keys] \
            if (whole_lap and lap_b is not None) else file_keys
        series_a = _load(lap_a, path_a, load_keys)
        series_b = _load(lap_b, path_b, load_keys) if lap_b is not None else None
        extras = [(side, _load(lap_id, path, file_keys), row) for side, lap_id, row, path, _n in extra_rows]
        if series_b is not None:
            lo, hi = common_range(series_a, series_b)
        else:
            lo, hi = float(series_a.dist[0]), float(series_a.dist[-1])
        for _side, series, _row in extras:
            lo, hi = max(lo, float(series.dist[0])), min(hi, float(series.dist[-1]))
        if start is not None:
            lo = max(lo, float(start))
        if end is not None:
            hi = min(hi, float(end))
        if (start is not None or end is not None) and hi - lo < 10.0:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "The requested section does not overlap the lap.")
        result = _compute_comparison(
            series_a, series_b, lap_a_row, lap_b_row, keys, lo, hi, step, max_points or MAX_POINTS_DEFAULT,
            whole_lap=whole_lap, extras=extras,
        )
        numeric = _compact_json(result)
        analysis_cache.put(cache_key, numeric)

    # 4. {"laps": <fresh headers>, <cached numeric members>}
    body = b'{"laps":' + headers_json + b"," + numeric[1:]
    return Response(content=body, media_type="application/json", headers=response_headers)


@router.get("/line")
def racing_line(
    request: Request,
    lap_a: int = Query(ge=1),
    lap_b: Optional[int] = Query(default=None, ge=1),
    extra: Optional[str] = Query(default=None, max_length=40),
    start: Optional[float] = Query(default=None, ge=0),
    end: Optional[float] = Query(default=None, ge=0),
    step: float = Query(default=LINE_MIN_STEP_M, ge=LINE_MIN_STEP_M, le=20.0),
    viewer: Driver = Depends(get_current_driver),
    db: Session = Depends(get_db),
    _rl: None = Depends(rate_limit("analysis", per_driver=True)),
) -> Response:
    """V0.9.4 — the driven lines of a stretch at high resolution, for the zoomed track map.

    Only positions (+ Lap 2's lateral offset from Lap 1, inside = +, and the inside direction)
    — no channels — on a fine grid (default 0.5 m, at most LINE_MAX_POINTS points; the step is
    widened to fit). The mirror decision and the corner directions come from the WHOLE laps,
    so the detail lies exactly on the overview. Same permissions, caching and ETag as /compare.
      -> {grid, x_a, y_a, x_b, y_b, [x_c, y_c, x_d, y_d], offset_b, side, mirrored}"""
    extra_ids = _parse_extra(extra, lap_a, lap_b)
    lap_a_row, path_a, _ = _open_lap(db, lap_a, viewer)
    lap_b_row = path_b = None
    if lap_b is not None:
        lap_b_row, path_b, _ = _open_lap(db, lap_b, viewer)
        if lap_b_row.track_name != lap_a_row.track_name:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "The laps are from different tracks.")
    extra_rows = []
    for side, lap_id in zip(EXTRA_SIDES, extra_ids):
        row, path, _n = _open_lap(db, lap_id, viewer)
        if row.track_name != lap_a_row.track_name:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Lap {lap_id} is from a different track.")
        extra_rows.append((side, lap_id, row, path))
    cache_key = AnalysisCache.make_key(
        "line", lap_a_row.telemetry_hash, lap_b_row.telemetry_hash if lap_b_row is not None else None,
        tuple((side, row.telemetry_hash) for side, _i, row, _p in extra_rows), start, end, round(step, 4),
    )
    etag = AnalysisCache.etag_for(cache_key, "")
    headers = {"ETag": etag, "Cache-Control": PRIVATE_REVALIDATE, "Vary": VARY_AUTH}
    if _etag_matches(request, etag):
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)
    body = analysis_cache.get(cache_key)
    if body is None:
        series_a = _load(lap_a, path_a, ["speed"])
        series_b = _load(lap_b, path_b, ["speed"]) if lap_b is not None else None
        extras = [(side, _load(lap_id, path, ["speed"])) for side, lap_id, _row, path in extra_rows]
        lo, hi = (common_range(series_a, series_b) if series_b is not None
                  else (float(series_a.dist[0]), float(series_a.dist[-1])))
        for _side, series in extras:
            lo, hi = max(lo, float(series.dist[0])), min(hi, float(series.dist[-1]))
        if start is not None:
            lo = max(lo, float(start))
        if end is not None:
            hi = min(hi, float(end))
        try:
            grid, used_step = make_grid(lo, hi, step, LINE_MAX_POINTS)
        except AnalysisError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
        mirror, _info = resolve_mirror(series_a, series_b)
        out: dict = {"grid": {"start": round(float(grid[0]), 3), "step": round(float(used_step), 4), "count": int(len(grid))},
                     "mirrored": mirror}
        xy = {"a": track_xy(series_a, grid, mirror), "b": track_xy(series_b, grid, mirror) if series_b is not None else None}
        for side, series in extras:
            xy[side] = track_xy(series, grid, mirror)
        if xy["a"] is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Lap 1 has no position data.")
        for side, pair in xy.items():
            out[f"x_{side}"] = json_series(pair[0], 2) if pair else None
            out[f"y_{side}"] = json_series(pair[1], 2) if pair else None
        line = _line_offsets(series_a, series_b, xy["a"], xy["b"], mirror, grid, used_step)
        out["offset_b"] = json_series(line["offset"], 2) if line else None
        out["side"] = [int(v) for v in line["side"]] if line else None
        body = _compact_json(out)
        analysis_cache.put(cache_key, body)
    return Response(content=body, media_type="application/json", headers=headers)


# V0.9.5: the track itself behind the lines (server/track_surface.py). Besides the laps on the
# map, a few more recent valid laps of the same layout fill in the edges those laps never drove on.
SURFACE_EXTRA_LAPS = 6
# V0.9.9.3: part of the surface's cache key and ETag only — the sign decision changed, and browsers
# still holding V0.9.5-V0.9.9's empty answer must not get it confirmed with 304. The other analysis
# answers are unchanged, so ANALYSIS_CACHE_VERSION (and their golden reference) stays.
SURFACE_VERSION = 2


@router.get("/track-surface")
def track_surface(
    request: Request,
    lap_a: int = Query(ge=1),
    lap_b: Optional[int] = Query(default=None, ge=1),
    extra: Optional[str] = Query(default=None, max_length=40),
    viewer: Driver = Depends(get_current_driver),
    db: Session = Depends(get_db),
    _rl: None = Depends(rate_limit("analysis", per_driver=True)),
) -> Response:
    """V0.9.5 — asphalt, both edges and the kerbs of the layout, in the map's coordinates.

    Built from LMU's path_lateral / track_edge / surface type of the laps on the map plus up to
    SURFACE_EXTRA_LAPS recent valid laps of the same layout (only geometry is returned — nothing
    about those laps or their drivers). -> {"surface": {...} | null, "mirrored", "reason"}; null when
    the laps were recorded without these fields (client < 0.9) or the data cannot be read reliably —
    "reason" says which (V0.9.9.3; null when there is a surface)."""
    from ..track_surface import SURFACE_COLUMNS_KEYS, build_surface_why

    extra_ids = _parse_extra(extra, lap_a, lap_b)
    lap_a_row, path_a, _ = _open_lap(db, lap_a, viewer)
    rows = [(lap_a, lap_a_row, path_a)]
    for lap_id in ([lap_b] if lap_b is not None else []) + extra_ids:
        row, path, _n = _open_lap(db, lap_id, viewer)
        if row.track_name != lap_a_row.track_name:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Lap {lap_id} is from a different track.")
        rows.append((lap_id, row, path))
    seen = {lap_id for lap_id, _r, _p in rows}
    others = db.execute(
        select(Lap).where(Lap.track_name == lap_a_row.track_name, visible_filter(), Lap.id.not_in(seen))
        .order_by(Lap.id.desc()).limit(SURFACE_EXTRA_LAPS * 3)
    ).scalars().all()
    for row in others:
        if len(rows) >= len(seen) + SURFACE_EXTRA_LAPS:
            break
        try:
            rows.append((row.id, row, _telemetry_file(row)))
        except HTTPException:
            continue          # file gone: just one lap fewer
    cache_key = AnalysisCache.make_key("surface", SURFACE_VERSION, tuple(sorted(r.telemetry_hash or str(i) for i, r, _p in rows)), rows[0][1].telemetry_hash,
                                       rows[1][1].telemetry_hash if lap_b is not None else None)
    etag = AnalysisCache.etag_for(cache_key, "")
    headers = {"ETag": etag, "Cache-Control": PRIVATE_REVALIDATE, "Vary": VARY_AUTH}
    if _etag_matches(request, etag):
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)
    body = analysis_cache.get(cache_key)
    if body is None:
        loaded: Dict[int, LapSeries] = {}
        for lap_id, _row, path in rows:
            try:
                loaded[lap_id] = load_lap(path, list(SURFACE_COLUMNS_KEYS))
            except AnalysisError:
                if lap_id == lap_a:
                    raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Lap {lap_id} cannot be analysed.")
        # the same mirror decision as /compare and /line, so the surface lies under the lines
        mirror, _info = resolve_mirror(loaded[lap_a], loaded.get(lap_b) if lap_b is not None else None)
        surface, reason = build_surface_why(list(loaded.values()), mirror)
        body = _compact_json({"surface": surface, "mirrored": mirror, "reason": reason})
        analysis_cache.put(cache_key, body)
    return Response(content=body, media_type="application/json", headers=headers)


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


# ---------------------------------------------------------------------------
# V0.9.3 — distributions (damper histograms ...), lap events, theoretical best, sessions
# ---------------------------------------------------------------------------

MAX_DISTRIBUTION_CHANNELS = 8


@router.get("/distribution")
def channel_distribution(
    request: Request,
    lap_a: int = Query(ge=1),
    lap_b: Optional[int] = Query(default=None, ge=1),
    channels: str = Query(min_length=1, max_length=600),
    bins: int = Query(default=30, ge=6, le=80),
    lo: Optional[float] = Query(default=None, ge=-1e6, le=1e6),
    hi: Optional[float] = Query(default=None, ge=-1e6, le=1e6),
    knee: float = Query(default=DAMPER_KNEE_MM_S, ge=1.0, le=500.0),
    viewer: Driver = Depends(get_current_driver),
    db: Session = Depends(get_db),
    _rl: None = Depends(rate_limit("analysis", per_driver=True)),
) -> Response:
    """Histograms of channels over the whole lap — the share of the lap's TIME in each
    bin, both laps on the same bins. The classic use is the damper histogram (damper
    velocity per wheel: bump/rebound and low/high-speed shares, `knee` mm/s being the
    split), but any catalog channel works (gear = time per gear, tyre temperatures, ...).

    Small response (a few dozen numbers per channel and lap), cached like /compare."""
    keys = _parse_channels(channels)
    if any(key in PAIR_CHANNELS for key in keys):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The line offset has no histogram (it is not a channel of one lap).")
    if len(keys) > MAX_DISTRIBUTION_CHANNELS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"At most {MAX_DISTRIBUTION_CHANNELS} channels per request.")
    lap_a_row, path_a, name_a = _open_lap(db, lap_a, viewer)
    lap_b_row = path_b = name_b = None
    if lap_b is not None:
        lap_b_row, path_b, name_b = _open_lap(db, lap_b, viewer)
    cache_key = AnalysisCache.make_key(
        "distribution", lap_a_row.telemetry_hash, lap_b_row.telemetry_hash if lap_b_row is not None else None,
        tuple(keys), bins, lo, hi, round(knee, 3),
    )
    etag = AnalysisCache.etag_for(cache_key, "")
    headers = {"ETag": etag, "Cache-Control": PRIVATE_REVALIDATE, "Vary": VARY_AUTH}
    if _etag_matches(request, etag):
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)
    body = analysis_cache.get(cache_key)
    if body is None:
        series = {"a": _load(lap_a, path_a, keys), "b": _load(lap_b, path_b, keys) if lap_b is not None else None}
        out: Dict[str, dict] = {}
        for key in keys:
            channel = CATALOG[key]
            native = {side: channel_values(s, key) if s is not None else None for side, s in series.items()}
            edges = histogram_edges(
                list(native.values()), discrete=channel.discrete, symmetric=key.startswith("damper_vel_"),
                bins=bins, lo=lo, hi=hi,
            )
            entry = {"label": channel.label, "unit": channel.unit, "group": channel.group, "discrete": channel.discrete,
                     "edges": json_series(edges, 3) if edges is not None else None}
            for side, s in series.items():
                entry[side] = distribution(s, key, edges, knee) if (s is not None and edges is not None) else None
            out[key] = entry
        body = _compact_json({"channels": out, "knee": knee, "analysis_version": ANALYSIS_CACHE_VERSION})
        analysis_cache.put(cache_key, body)
    return Response(content=body, media_type="application/json", headers=headers)


def _validation_findings(lap: Lap) -> dict:
    verdict = lap.validation if isinstance(lap.validation, dict) else None
    if verdict is None:
        return {"status": None, "findings": [], "signals": [], "review_status": lap.review_status}
    # V0.9.3.1: findings linked from the next lap (a report LMU sent after the line) are
    # listed with the lap's own ones
    return {"status": verdict.get("status"), "findings": verdict.get("findings", []) + verdict.get("linked", []),
            "signals": verdict.get("signals", []), "review_status": lap.review_status,
            "review": verdict.get("review")}


@router.get("/events")
def lap_events(
    request: Request,
    lap_a: int = Query(ge=1),
    lap_b: Optional[int] = Query(default=None, ge=1),
    viewer: Driver = Depends(get_current_driver),
    db: Session = Depends(get_db),
    _rl: None = Depends(rate_limit("analysis", per_driver=True)),
) -> Response:
    """What happened where on the lap: the track-limits findings the server stored at
    upload (server/track_limits.py — LMU's own flags, wheels off, beyond the edge, ...)
    and the wheel events measured from the telemetry (lock-ups and wheelspin per wheel).
    Every event has its lap distance, so the views can mark it and jump to it."""
    lap_a_row, path_a, _ = _open_lap(db, lap_a, viewer)
    lap_b_row = path_b = None
    if lap_b is not None:
        lap_b_row, path_b, _ = _open_lap(db, lap_b, viewer)
    rows = {"a": (lap_a, lap_a_row, path_a), "b": (lap_b, lap_b_row, path_b) if lap_b is not None else None}
    cache_key = AnalysisCache.make_key(
        "events", lap_a_row.telemetry_hash, lap_b_row.telemetry_hash if lap_b_row is not None else None,
    )
    validation_json = _compact_json({side: _validation_findings(r[1]) if r else None for side, r in rows.items()})
    etag = AnalysisCache.etag_for(cache_key, validation_json.decode("utf-8"))
    headers = {"ETag": etag, "Cache-Control": PRIVATE_REVALIDATE, "Vary": VARY_AUTH}
    if _etag_matches(request, etag):
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)
    wheels = analysis_cache.get(cache_key)
    if wheels is None:
        measured = {}
        for side, r in rows.items():
            if r is None:
                measured[side] = None
                continue
            try:
                measured[side] = wheel_events(load_lap(r[2], list(WHEEL_EVENT_CHANNELS)))
            except AnalysisError:
                measured[side] = []
        wheels = _compact_json(measured)
        analysis_cache.put(cache_key, wheels)
    body = b'{"track_limits":' + validation_json + b',"wheels":' + wheels + b"}"
    return Response(content=body, media_type="application/json", headers=headers)


def _driver_laps_for(db: Session, driver_id: int, track_name: str, car_model: Optional[str], valid_only: bool):
    stmt = select(Lap).where(Lap.driver_id == driver_id, Lap.track_name == track_name)
    if car_model:
        stmt = stmt.where(Lap.car_model == car_model)
    if valid_only:
        stmt = stmt.where(visible_filter())
    return db.execute(stmt.order_by(Lap.id.asc()).limit(2000)).scalars().all()


@router.get("/theoretical-best")
def theoretical_best_lap(
    track_name: str = Query(min_length=1, max_length=200),
    # V0.9.3.1: required — a theoretical best only makes sense for ONE car model
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

    V0.9.3.1 — track conditions are not mixed silently: laps are grouped as dry / mixed /
    wet (share of the lap on a wet surface, server/lap_insights.py) or unknown (older laps
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


@router.get("/sessions")
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


# ---------------------------------------------------------------------------
# V0.9.4 — LapTime Analyzer: a corner across earlier laps, sectors of a session
# ---------------------------------------------------------------------------

CORNER_HISTORY_MAX = 20


@router.get("/corner-history")
def corner_history(
    lap_id: int = Query(ge=1),
    limit: int = Query(default=10, ge=1, le=CORNER_HISTORY_MAX),
    viewer: Driver = Depends(get_current_driver),
    db: Session = Depends(get_db),
    _rl: None = Depends(rate_limit("analysis", per_driver=True)),
) -> dict:
    """The corners of the same driver's EARLIER valid laps on the same layout with the same car
    model, as stored at upload (server/lap_insights.py "corners": [apex, braking point, min
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


@router.get("/lap-sectors")
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
