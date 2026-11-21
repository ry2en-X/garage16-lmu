"""The racing line: plan view of a lap, mirroring, the lateral offset between two lines and which side is inside."""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

import numpy as np

from server.analysis.distance_axis import distance_axis, fill_short_gaps  # noqa: F401  (re-exported)
from server.analysis.telemetry_analysis import LapSeries
from server.analysis.telemetry_channels import (
    _decide,
    _smooth,
)

LINE_WINDOW_M = 50.0          # Lap 2's line is searched within this distance of the same lap metre


LINE_MAX_OFFSET_M = 15.0      # further apart than this is not "another line" (pit lane, off, spin)


LINE_CHUNK = 2000             # rows per vectorised block (bounds the memory of the search)


TURN_SMOOTH_M = 20.0


TURN_MIN_CURVATURE = 1.0 / 800.0   # radius above 800 m counts as straight


def line_offset(xa: np.ndarray, ya: np.ndarray, xb: np.ndarray, yb: np.ndarray, step: float) -> np.ndarray:
    """Signed lateral distance of Lap 2's line from Lap 1's, in metres, at every point of Lap 1
    (both on the same distance grid and in the same plan-view frame, Y up).

    For each point of A the nearest point of B's POLYLINE is taken (projection onto the two
    segments around B's nearest vertex), searched only within LINE_WINDOW_M of the same lap
    distance so a hairpin's other side is never matched. + = B lies LEFT of A's direction of
    travel. NaN where A has no position, no direction, or B is further than LINE_MAX_OFFSET_M."""
    xa, ya, xb, yb = (np.asarray(v, dtype=float) for v in (xa, ya, xb, yb))
    n = len(xa)
    out = np.full(n, np.nan)
    if n < 3 or len(xb) != n:
        return out
    tx = np.gradient(xa)
    ty = np.gradient(ya)
    w = max(1, int(math.ceil(LINE_WINDOW_M / max(step, 1e-6))))
    offsets = np.arange(-w, w + 1)
    for c0 in range(0, n, LINE_CHUNK):
        rows = np.arange(c0, min(n, c0 + LINE_CHUNK))
        idx = np.clip(rows[:, None] + offsets[None, :], 0, n - 1)
        dx = xb[idx] - xa[rows, None]
        dy = yb[idx] - ya[rows, None]
        d2 = np.where(np.isfinite(dx) & np.isfinite(dy), dx * dx + dy * dy, np.inf)
        j = idx[np.arange(len(rows)), np.argmin(d2, axis=1)]
        px, py = xa[rows], ya[rows]
        best = np.full(len(rows), np.inf)
        best_x = np.full(len(rows), np.nan)
        best_y = np.full(len(rows), np.nan)
        for j0, j1 in ((np.maximum(j - 1, 0), j), (j, np.minimum(j + 1, n - 1))):
            sx, sy = xb[j1] - xb[j0], yb[j1] - yb[j0]
            length2 = sx * sx + sy * sy
            with np.errstate(invalid="ignore", divide="ignore"):
                u = np.where(length2 > 0, ((px - xb[j0]) * sx + (py - yb[j0]) * sy) / length2, 0.0)
            u = np.clip(np.nan_to_num(u), 0.0, 1.0)
            qx, qy = xb[j0] + u * sx, yb[j0] + u * sy
            d = np.hypot(qx - px, qy - py)
            better = np.isfinite(d) & (d < best)
            best = np.where(better, d, best)
            best_x = np.where(better, qx, best_x)
            best_y = np.where(better, qy, best_y)
        cross = tx[rows] * (best_y - py) - ty[rows] * (best_x - px)
        tangent = np.hypot(tx[rows], ty[rows]) > 1e-9
        ok = np.isfinite(best) & (best <= LINE_MAX_OFFSET_M) & tangent & np.isfinite(px) & np.isfinite(py)
        out[rows] = np.where(ok, np.sign(cross) * best, np.nan)
    return out


def turn_direction(x: np.ndarray, y: np.ndarray, step: float) -> np.ndarray:
    """+1 left-hand, -1 right-hand, 0 straight — the local direction of the line (plan view,
    Y up), from its curvature over distance after smoothing over TURN_SMOOTH_M."""
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    out = np.zeros(len(x), dtype=int)
    ok = np.isfinite(x) & np.isfinite(y)
    if ok.sum() < 5:
        return out
    k = max(3, int(round(TURN_SMOOTH_M / max(step, 1e-6))) | 1)
    xs = _smooth(np.where(ok, x, np.interp(np.arange(len(x)), np.flatnonzero(ok), x[ok])), k)
    ys = _smooth(np.where(ok, y, np.interp(np.arange(len(y)), np.flatnonzero(ok), y[ok])), k)
    with np.errstate(divide="ignore", invalid="ignore"):
        dx, dy = np.gradient(xs, step), np.gradient(ys, step)
        ddx, ddy = np.gradient(dx, step), np.gradient(dy, step)
        kappa = (dx * ddy - dy * ddx) / np.power(dx * dx + dy * dy, 1.5)
    out[(kappa > TURN_MIN_CURVATURE) & ok] = 1
    out[(kappa < -TURN_MIN_CURVATURE) & ok] = -1
    return out


def corner_turns(corners: List[dict], x: np.ndarray, y: np.ndarray, grid: np.ndarray, step: float) -> List[int]:
    """The direction of each corner (+1 left, -1 right, 0 unknown): the majority of the local
    directions within 30 m of its apex."""
    local = turn_direction(x, y, step)
    out = []
    for c in corners:
        near = np.abs(grid - c["apex"]) <= 30.0
        s = int(np.sign(local[near].sum())) if near.any() else 0
        out.append(s)
    return out


def inside_sign(grid: np.ndarray, corners: List[dict], turns: List[int]) -> np.ndarray:
    """For every grid point the direction of the NEAREST corner (by apex distance): +1 left, -1
    right, 0 when there are no corners. "Inside" is the side of the corner you are heading into
    or coming out of — on a straight between a left and a right corner the first half belongs to
    the left one, the second half to the right one."""
    # only corners whose direction is known (a slow section without a clear turn has none)
    known = [(c["apex"], t) for c, t in zip(corners, turns) if t]
    if not known:
        return np.zeros(len(grid), dtype=int)
    apices = np.array([apex for apex, _ in known], dtype=float)
    nearest = np.argmin(np.abs(grid[:, None] - apices[None, :]), axis=1)
    return np.array([t for _, t in known], dtype=int)[nearest]


def _curvature(x: np.ndarray, y: np.ndarray, t: np.ndarray) -> np.ndarray:
    xs, ys = _smooth(x, 9), _smooth(y, 9)
    with np.errstate(divide="ignore", invalid="ignore"):
        dx, dy = np.gradient(xs, t), np.gradient(ys, t)
        ddx, ddy = np.gradient(dx, t), np.gradient(dy, t)
        return (dx * ddy - dy * ddx) / np.power(dx * dx + dy * dy, 1.5)


def map_mirror(series: LapSeries) -> Tuple[bool, bool]:
    """(mirror, decided_from_data). The map is drawn with screen X = world x and
    screen Y = world z (Y pointing up). Whether that reproduces the real track or
    its mirror image depends on LMU's world handedness, so it is checked against
    the anchored steering: a right-hand corner is a clockwise turn, which on a
    Y-up screen has NEGATIVE curvature. If steering and curvature correlate
    positively instead, the picture is mirrored and X must be negated."""
    raw = series.raw
    if not all(c in raw for c in ("pos_x", "pos_z", "steering", "speed", "t")):
        return False, False
    kappa = _curvature(raw["pos_x"], raw["pos_z"], raw["t"])
    steering = series.orientation.steering * raw["steering"]
    moving = (raw["speed"] > 15.0) & np.isfinite(kappa) & (np.abs(kappa) < 0.2)
    sign, decided = _decide(_smooth(steering), kappa, 0.0, moving)
    if not decided:
        return False, False
    # sign > 0: steering tracks +kappa  -> counter-clockwise right turns -> mirrored
    return sign > 0, True


def track_xy(series: LapSeries, grid: np.ndarray, mirror: bool) -> Optional[Tuple[np.ndarray, np.ndarray]]:
    if "pos_x" not in series.raw or "pos_z" not in series.raw:
        return None
    x = np.interp(grid, series.dist, series.raw["pos_x"])
    y = np.interp(grid, series.dist, series.raw["pos_z"])
    return (-x if mirror else x), y


def resolve_mirror(a: LapSeries, b: Optional[LapSeries] = None) -> Tuple[bool, Dict[str, bool]]:
    """One mirror decision for every line drawn on the same map: lap A's if it
    came from data, otherwise lap B's, otherwise not mirrored."""
    mirror_a, decided_a = map_mirror(a)
    info = {"a_decided": decided_a}
    if decided_a:
        return mirror_a, info
    if b is not None:
        mirror_b, decided_b = map_mirror(b)
        info["b_decided"] = decided_b
        if decided_b:
            return mirror_b, info
    return False, info


def orientation_report(series: LapSeries) -> dict:
    o = series.orientation
    return {
        "steering": o.steering, "lat_g": o.lat_g, "long_g": o.long_g, "slip": o.slip, "yaw": o.yaw, "susp": o.susp,
        "decided_from_data": dict(o.decided_from_data),
        # How the distance axis was built (anchors = samples with a fresh lap_dist)
        "distance_axis": dict(series.axis),
    }
