"""server/distance_axis.py — the distance axis of a lap at telemetry rate (V0.9.5).

Shared by the analysis (telemetry_analysis.load_lap) and the per-lap figures computed at upload
(lap_insights: time profile, corners), so both measure a lap on the same axis.
"""

from __future__ import annotations

from typing import Optional, Tuple

import numpy as np

# V0.9.5 — the distance axis at TELEMETRY rate.
#
# lap_dist comes from LMU's scoring block, the speed and every other channel from the telemetry
# block. In rFactor 2 (LMU's base) the game refreshes scoring about 5 times a second and
# telemetry about 50 times; if LMU does the same, lap_dist stands still for ~10 recorded samples
# at a time. Up to V0.9.4 every sample whose lap_dist had not moved was DROPPED, so a lap could
# be analysed at the scoring rate only — at 80 m/s one point per ~16 m: braking points, the
# racing line and the delta between those points were straight-line guesses.
#
# Now: each row where lap_dist advances is an ANCHOR (exactly LMU's distance, as before); the
# rows between two anchors get anchor distance + the share of the speed integral covered so far
# (so the two anchors are met exactly, and the speed only decides how the metres are spread over
# the samples in between). Where speed and lap_dist disagree too much (a reset, a tow) the
# distance is spread linearly in time instead. If LMU updates lap_dist every frame, every row is
# an anchor and the result is identical to the old axis.
GAP_FILL_S = 0.5             # V0.9.7: NaN speed stretches up to this long are bridged (axis only)
EXTRAPOLATE_MAX_S = 0.5      # before the first / after the last update: at most this long by speed
SPEED_RATIO_RANGE = (0.5, 2.0)  # integrated speed / lap_dist gap outside this -> linear in time


def fill_short_gaps(values: np.ndarray, x: np.ndarray, max_gap: float) -> np.ndarray:
    """A copy of `values` with NaN stretches no longer than `max_gap` (in units of `x`) linearly
    bridged from their neighbours (V0.9.7). Longer gaps and the ends stay NaN. Only used where
    a value is needed to FIND something (the distance axis, corner detection) — never shown."""
    values = np.asarray(values, dtype=float)
    bad = ~np.isfinite(values)
    if not bad.any():
        return values
    good = np.flatnonzero(~bad)
    if len(good) < 2:
        return values
    out = values.copy()
    idx = np.flatnonzero(bad)
    prev = np.searchsorted(good, idx, side="left") - 1
    nxt = prev + 1
    inside = (prev >= 0) & (nxt < len(good))
    idx, prev, nxt = idx[inside], good[prev[inside]], good[nxt[inside]]
    short = (x[nxt] - x[prev]) <= max_gap
    idx, prev, nxt = idx[short], prev[short], nxt[short]
    with np.errstate(divide="ignore", invalid="ignore"):
        f = (x[idx] - x[prev]) / (x[nxt] - x[prev])
    out[idx] = values[prev] + np.nan_to_num(f) * (values[nxt] - values[prev])
    return out


def distance_axis(lap_dist: np.ndarray, t: np.ndarray, speed: Optional[np.ndarray]) -> Tuple[np.ndarray, np.ndarray, dict]:
    """-> (keep mask over the input rows, distance of the kept rows, stats).

    stats: {"rows", "kept", "anchors", "duplicates", "filled"} — how many rows the file has, how
    many are used, how many carried a fresh lap_dist, how many were repeated polls of the same
    telemetry frame, and how many got their distance from the speed."""
    n = len(t)
    finite = np.isfinite(lap_dist) & np.isfinite(t)
    # 1. repeated polls of the same telemetry frame (the client polls at 60 Hz, the game may
    #    produce fewer frames): same game time = same physics state. The newest lap_dist seen at
    #    that game time is kept with the first row.
    rows = np.flatnonzero(finite)
    if len(rows) == 0:
        return np.zeros(n, dtype=bool), np.zeros(0), {"rows": n, "kept": 0, "anchors": 0, "duplicates": 0, "filled": 0}
    tt = t[rows]
    fresh = np.concatenate(([True], np.diff(tt) > 1e-9))
    first_rows = rows[fresh]
    ld = np.maximum.reduceat(lap_dist[rows], np.flatnonzero(fresh))
    duplicates = int(len(rows) - len(first_rows))
    t1 = t[first_rows]
    # 2. anchors: lap_dist advances its running maximum
    running = np.maximum.accumulate(ld)
    is_anchor = np.concatenate(([True], np.diff(running) > 1e-6))
    anchors = np.flatnonzero(is_anchor)
    dist = np.full(len(first_rows), np.nan)
    dist[anchors] = running[anchors]
    filled = 0
    usable_speed = speed is not None and len(speed) == n
    if usable_speed and len(anchors) < len(first_rows):
        # V0.9.7: a few rows without a speed are bridged from their neighbours (a NaN counted as
        # 0 m/s would bunch the metres up around it); the speed channel itself stays as recorded
        raw_v = np.abs(np.asarray(speed[first_rows], dtype=float))
        raw_v[~np.isfinite(raw_v)] = np.nan                      # an infinite speed is no speed
        filled_v = fill_short_gaps(raw_v, t1, GAP_FILL_S)
        unknown = ~np.isfinite(filled_v)                          # V0.9.8: still missing after bridging
        v = np.where(unknown, 0.0, filled_v)
        # cumulative distance by speed (trapezoid) — only its differences are used
        cum = np.concatenate(([0.0], np.cumsum(0.5 * (v[1:] + v[:-1]) * np.diff(t1))))
        # how many samples without a speed up to here: a stretch between two updates that contains
        # one is shared out in TIME (V0.9.8 — before, the missing speed counted as 0 m/s there)
        unknown_cum = np.concatenate(([0], np.cumsum(unknown)))
        idx = np.arange(len(first_rows))
        prev = anchors[np.clip(np.searchsorted(anchors, idx, side="right") - 1, 0, len(anchors) - 1)]
        nxt_pos = np.searchsorted(anchors, idx, side="right")
        between = (idx > prev) & (nxt_pos < len(anchors))      # (the first row always is an anchor)
        if between.any():
            i = idx[between]
            a = prev[between]
            b = anchors[nxt_pos[between]]
            gap = dist[b] - dist[a]
            by_speed = cum[b] - cum[a]
            with np.errstate(divide="ignore", invalid="ignore"):
                ratio = by_speed / gap
                share_speed = (cum[i] - cum[a]) / by_speed
                share_time = (t1[i] - t1[a]) / (t1[b] - t1[a])
            complete = (unknown_cum[b + 1] - unknown_cum[a]) == 0
            ok = complete & np.isfinite(ratio) & (ratio >= SPEED_RATIO_RANGE[0]) & (ratio <= SPEED_RATIO_RANGE[1]) & np.isfinite(share_speed)
            share = np.where(ok, share_speed, share_time)
            share = np.clip(np.nan_to_num(share, nan=0.0), 0.0, 1.0)
            dist[i] = dist[a] + share * gap
            filled += int(len(i))
        # after the last anchor: by speed, for a short while only. (Nothing comes BEFORE the first
        # anchor: the first usable row always is one — V0.9.8 removed a branch for it that could
        # never run, found by scripts/mutation_check.py.)
        last = anchors[-1]
        tail = idx[(idx > last) & (t1 - t1[last] <= EXTRAPOLATE_MAX_S) & (unknown_cum[idx + 1] == unknown_cum[last])]
        if len(tail):
            dist[tail] = dist[last] + (cum[tail] - cum[last])
            filled += int(len(tail))
    # 3. strictly increasing: rows that do not advance (standing car) or have no distance go
    usable = np.isfinite(dist)
    candidate = np.where(usable, dist, -np.inf)
    running2 = np.maximum.accumulate(candidate)
    advance = usable & np.concatenate(([True], np.diff(running2) > 1e-6)) & (candidate >= running2 - 1e-9)
    advance &= running2 > -np.inf
    keep_first = first_rows[advance]
    keep = np.zeros(n, dtype=bool)
    keep[keep_first] = True
    stats = {"rows": int(n), "kept": int(advance.sum()), "anchors": int(len(anchors)), "duplicates": duplicates, "filled": filled}
    # the kept rows' distances, with the lap_dist value of their duplicate group already applied
    return keep, dist[advance], stats
