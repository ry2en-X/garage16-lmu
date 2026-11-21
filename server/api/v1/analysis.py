"""
server/api/v1/analysis.py — lap analysis: channel catalog, lap-vs-lap
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
  * A lap whose file is gone (retention cleanup, see server/ops/telemetry_lifecycle.py)
    answers 404 with a clear message instead of failing.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from server.accounts.auth import get_current_driver
from server.analysis.analysis_cache import (
    ANALYSIS_CACHE_VERSION,
    AnalysisCache,
    analysis_cache,
)
from server.analysis.analysis_reference import LapContext, quick_pair, resolve_reference
from server.analysis.comparison import (
    EXTRA_SIDES,
    _compute_comparison,
    _line_offsets,
    _parse_extra,
)
from server.analysis.corners import CORNER_KEYS
from server.analysis.distribution import (
    DAMPER_KNEE_MM_S,
    WHEEL_EVENT_CHANNELS,
    distribution,
    histogram_edges,
    wheel_events,
)
from server.analysis.lap_access import (
    PAIR_CHANNELS,
    PRIVATE_REVALIDATE,
    VARY_AUTH,
    _available_keys,
    _compact_json,
    _etag_matches,
    _lap_for_viewer,
    _lap_header,
    _open_lap,
    _parse_channels,
    _recording,
    _telemetry_file,
)
from server.analysis.racing_line import resolve_mirror, track_xy
from server.analysis.telemetry_analysis import (
    DEFAULT_STEP_M,
    AnalysisError,
    LapSeries,
    channel_values,
    common_range,
    json_series,
    load_lap,
    logical_columns,
    make_grid,
)
from server.analysis.telemetry_channels import (
    CATALOG,
    DEFAULT_CHANNELS,
    catalog_listing,
)
from server.core.database import get_db
from server.core.models import Driver, Lap
from server.core.rate_limit import rate_limit
from server.core.versioning import data_level_for_client_version
from server.laps.official import visible_filter

logger = logging.getLogger("lmu_garage.analysis")

router = APIRouter(tags=["analysis"])

LINE_MAX_POINTS = 4000
LINE_MIN_STEP_M = 0.5

MAX_POINTS_DEFAULT = 8000  # = telemetry_analysis.MAX_GRID_POINTS, the V0.9.0 ceiling


# ---------------------------------------------------------------------------
# Access + files
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------

def _catalog_payload() -> tuple:
    body = _compact_json({"channels": catalog_listing() + list(PAIR_CHANNELS.values()), "default": list(DEFAULT_CHANNELS)})
    return body, AnalysisCache.etag_for(AnalysisCache.make_key("catalog"), body.decode("utf-8"))


@router.get("/public/analysis/channels")
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


@router.get("/laps/{lap_id}/analysis")
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
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc))
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
    """How trustworthy this lap's recording is (server/analysis/telemetry_health.py): channels,
    samples, gaps, time/distance only forward, distance-axis anchoring, speed vs lap distance,
    duration vs lap time, implausible jumps, the resolution of a braking point — plus the lap's
    validation verdict next to it. The file part is cached by the file's content hash."""
    from server.analysis.telemetry_health import (
        HEALTH_VERSION,
        report,
        safe_file_health,
    )

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
# Reference laps: "compare with Garage16 Best / Team Best / My Best"
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


@router.get("/analysis/reference")
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


@router.get("/analysis/quick")
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


def _load(lap_id: int, path: Path, channels: List[str]) -> LapSeries:
    try:
        return load_lap(path, channels)
    except AnalysisError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Lap {lap_id}: {exc}")


@router.get("/analysis/compare")
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
      laps        {a, b} headers (driver, car, lap time, sectors ...); + c, d
      delta       seconds, A minus B, null without lap_b; positive = A is behind
      time        {a, b} seconds since lap start at each grid point
      channels    {key: {label, unit, group, discrete, fixed_range, a, b}} — a/b are
                  value lists, or null when that lap's file lacks the channel
      recording   {a, b}: {level: "full"|"basic", client_version, channels_available,
                  channels_total} — "basic" = recorded by a client older than 0.9.0,
                  so only the basic channels exist for that lap
      unavailable channel keys missing in at least one of the laps
      map         {x_a, y_a, x_b, y_b, mirrored, ...} plan view, Y up, same frame for both;
                  Offset_b (Lap 2's line vs Lap 1's, m, inside = +) and side
                  (direction of the nearest corner, +1 left / -1 right)
      sectors     {a: [d1, d2], b: [...]} distances of the sector boundaries
      orientation how each lap's sign conventions were decided (for transparency)
      corners     V0.9.1 corner table; V0.9.4 adds braking/throttle details and phases
      extra_delta V0.9.4, with `extra`: {c: [...], d: [...]} each extra lap minus the reference
      analysis_version  version of the computation

    How much data is sent: only the requested channels are read and
    returned; `max_points` bounds the points per series (the grid is widened to
    fit — the raw data in the file is untouched); `start`/`end` return just a
    stretch of track, at the same or a finer `step`, for zoom-in refinement. Without
    these parameters the response is what V0.9.0 returned.

    Caching: the numeric part is cached in memory (server/analysis/analysis_cache.py), keyed
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
        # The corner table needs pedals, steering and long G even when no chart shows them
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
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "The requested section does not overlap the lap.")
        result = _compute_comparison(
            series_a, series_b, lap_a_row, lap_b_row, keys, lo, hi, step, max_points or MAX_POINTS_DEFAULT,
            whole_lap=whole_lap, extras=extras,
        )
        numeric = _compact_json(result)
        analysis_cache.put(cache_key, numeric)

    # 4. {"laps": <fresh headers>, <cached numeric members>}
    body = b'{"laps":' + headers_json + b"," + numeric[1:]
    return Response(content=body, media_type="application/json", headers=response_headers)


@router.get("/analysis/line")
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
    """The driven lines of a stretch at high resolution, for the zoomed track map.

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
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc))
        mirror, _info = resolve_mirror(series_a, series_b)
        out: dict = {"grid": {"start": round(float(grid[0]), 3), "step": round(float(used_step), 4), "count": int(len(grid))},
                     "mirrored": mirror}
        xy = {"a": track_xy(series_a, grid, mirror), "b": track_xy(series_b, grid, mirror) if series_b is not None else None}
        for side, series in extras:
            xy[side] = track_xy(series, grid, mirror)
        if xy["a"] is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Lap 1 has no position data.")
        for side, pair in xy.items():
            out[f"x_{side}"] = json_series(pair[0], 2) if pair else None
            out[f"y_{side}"] = json_series(pair[1], 2) if pair else None
        line = _line_offsets(series_a, series_b, xy["a"], xy["b"], mirror, grid, used_step)
        out["offset_b"] = json_series(line["offset"], 2) if line else None
        out["side"] = [int(v) for v in line["side"]] if line else None
        body = _compact_json(out)
        analysis_cache.put(cache_key, body)
    return Response(content=body, media_type="application/json", headers=headers)


# The track itself behind the lines (server/laps/track_surface.py). Besides the laps on the
# map, a few more recent valid laps of the same layout fill in the edges those laps never drove on.
SURFACE_EXTRA_LAPS = 6
# Part of the surface's cache key and ETag only — the sign decision changed, and browsers
# still holding V0.9.5-V0.9.9's empty answer must not get it confirmed with 304. The other analysis
# answers are unchanged, so ANALYSIS_CACHE_VERSION (and their golden reference) stays.
SURFACE_VERSION = 2


@router.get("/analysis/track-surface")
def track_surface(
    request: Request,
    lap_a: int = Query(ge=1),
    lap_b: Optional[int] = Query(default=None, ge=1),
    extra: Optional[str] = Query(default=None, max_length=40),
    viewer: Driver = Depends(get_current_driver),
    db: Session = Depends(get_db),
    _rl: None = Depends(rate_limit("analysis", per_driver=True)),
) -> Response:
    """Asphalt, both edges and the kerbs of the layout, in the map's coordinates.

    Built from LMU's path_lateral / track_edge / surface type of the laps on the map plus up to
    SURFACE_EXTRA_LAPS recent valid laps of the same layout (only geometry is returned — nothing
    about those laps or their drivers). -> {"surface": {...} | null, "mirrored", "reason"}; null when
    the laps were recorded without these fields (client < 0.9) or the data cannot be read reliably —
    "reason" says which."""
    from server.laps.track_surface import SURFACE_COLUMNS_KEYS, build_surface_why

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
                    raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Lap {lap_id} cannot be analysed.")
        # the same mirror decision as /compare and /line, so the surface lies under the lines
        mirror, _info = resolve_mirror(loaded[lap_a], loaded.get(lap_b) if lap_b is not None else None)
        surface, reason = build_surface_why(list(loaded.values()), mirror)
        body = _compact_json({"surface": surface, "mirrored": mirror, "reason": reason})
        analysis_cache.put(cache_key, body)
    return Response(content=body, media_type="application/json", headers=headers)


# ---------------------------------------------------------------------------
# Circuit library
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Distributions (damper histograms ...), lap events, theoretical best, sessions
# ---------------------------------------------------------------------------

MAX_DISTRIBUTION_CHANNELS = 8


@router.get("/analysis/distribution")
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
    # Findings linked from the next lap (a report LMU sent after the line) are
    # listed with the lap's own ones
    return {"status": verdict.get("status"), "findings": verdict.get("findings", []) + verdict.get("linked", []),
            "signals": verdict.get("signals", []), "review_status": lap.review_status,
            "review": verdict.get("review")}


@router.get("/analysis/events")
def lap_events(
    request: Request,
    lap_a: int = Query(ge=1),
    lap_b: Optional[int] = Query(default=None, ge=1),
    viewer: Driver = Depends(get_current_driver),
    db: Session = Depends(get_db),
    _rl: None = Depends(rate_limit("analysis", per_driver=True)),
) -> Response:
    """What happened where on the lap: the track-limits findings the server stored at
    upload (server/laps/track_limits.py — LMU's own flags, wheels off, beyond the edge, ...)
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


# ---------------------------------------------------------------------------
# LapTime Analyzer: a corner across earlier laps, sectors of a session
# ---------------------------------------------------------------------------


