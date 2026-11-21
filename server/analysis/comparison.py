"""Lap-vs-lap comparison on a common distance grid: channels, delta, the plan view, the line offset and the corner table (GET /api/v1/analysis/compare)."""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence

import numpy as np
from fastapi import HTTPException, status

from server.analysis.analysis_cache import ANALYSIS_CACHE_VERSION
from server.analysis.corners import _find_corners, corner_analysis, delta_facts
from server.analysis.lap_access import PAIR_CHANNELS, _recording
from server.analysis.racing_line import (
    corner_turns,
    inside_sign,
    line_offset,
    orientation_report,
    resolve_mirror,
    track_xy,
)
from server.analysis.telemetry_analysis import (
    AnalysisError,
    LapSeries,
    channel_decimals,
    channel_span,
    channel_values,
    common_range,
    delta_series,
    json_series,
    make_grid,
    resample,
    sector_distances,
    spikes,
    time_at,
)
from server.analysis.telemetry_channels import (
    CATALOG,
)
from server.core.models import Lap

# Up to two more laps drawn over Lap 1 / Lap 2 ("c", "d"); delta to the reference lap
EXTRA_SIDES = ("c", "d")


def _inside_for(series_a: LapSeries, series_b: Optional[LapSeries], mirror: bool, grid: np.ndarray) -> np.ndarray:
    """For every point of `grid` the direction of the nearest corner (+1 left, -1 right,
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
    laps "c" / "d"."""
    try:
        grid, used_step = make_grid(lo, hi, step, max_points)
    except AnalysisError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc))

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
    # Lap 2's line relative to Lap 1's (inside = +), and the inside direction
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
            # What the grid would hide (brake taps, kerbs, spikes between grid points)
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
        # Every corner of the common stretch, measured on the raw samples (whole lap only;
        # a zoom stretch has no corners of its own). V0.9.4: + phases and braking/throttle details
        "corners": corner_analysis(series_a, series_b) if series_b is not None and whole_lap else [],
        "delta_facts": delta_facts(series_a, series_b) if series_b is not None and whole_lap else None,
        "analysis_version": ANALYSIS_CACHE_VERSION,
    }
    # The additional laps' delta to the reference lap (Lap 2, or Lap 1 without one)
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
