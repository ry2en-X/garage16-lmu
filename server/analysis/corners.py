"""Corners of a lap: where they are, braking/turn-in/apex/throttle points, phases, and the corner table of a comparison."""

from __future__ import annotations

import math
from typing import List, Optional, Tuple

import numpy as np

from server.analysis.distance_axis import distance_axis, fill_short_gaps  # noqa: F401  (re-exported)
from server.analysis.telemetry_analysis import (
    LapSeries,
    channel_values,
    common_range,
    delta_series,
)
from server.analysis.telemetry_channels import (
    CATALOG,
    WHEEL_POSITIONS,
)

# Biggest gain / loss over a corner-sized stretch. The browser used to find them
# on the chart's grid, so the numbers moved by a millisecond or a few metres with the screen
# width; measured here on a fixed 0.5 m grid they are the same on every device.
# Same constants as web/js/analysis/insights.js FACT_WINDOW.
FACT_WINDOW_FRACTION, FACT_WINDOW_MIN_M, FACT_WINDOW_MAX_M = 1 / 40, 100.0, 400.0
APEX_ZONE_MIN_M, APEX_ZONE_MAX_M = 10.0, 30.0   # the apex phase is kept within these limits

CORNER_MIN_DROP_KMH = 10.0   # a corner = a speed minimum with at least this drop before AND after


CORNER_MIN_GAP_M = 120.0     # minima closer than this are one corner


CORNER_SMOOTH_M = 30.0       # speed is smoothed over this length to find corners (not to measure them)


BRAKE_ON_PCT = 10.0          # braking point: first sample with at least this much brake


THROTTLE_FULL_PCT = 95.0     # throttle point: first sample after the apex with at least this much throttle


FACT_STEP_M = 0.5


def delta_facts(a: "LapSeries", b: "LapSeries") -> Optional[dict]:
    """Where Lap A gained and lost the most time over a stretch of about one corner, and the net.

      -> {"window", "gain": {"seconds", "from", "to"} | None, "loss": {...} | None, "net"};
         seconds are magnitudes (> 0), metres; net = delta at the end (A − B, negative = A ahead).
    The stretch is FACT_WINDOW_FRACTION of the common range, clamped to [MIN, MAX] metres."""
    lo, hi = common_range(a, b)
    if not hi > lo:
        return None
    grid = np.arange(lo, hi + FACT_STEP_M / 2, FACT_STEP_M)
    grid[-1] = min(grid[-1], hi)
    delta = delta_series(a, b, grid)
    if not np.isfinite(delta).any():
        return None
    finite = np.flatnonzero(np.isfinite(delta))
    net = float(delta[finite[-1]])
    window = min(FACT_WINDOW_MAX_M, max(FACT_WINDOW_MIN_M, (hi - lo) * FACT_WINDOW_FRACTION))
    span = max(1, min(len(grid) - 1, int(round(window / FACT_STEP_M))))
    change = delta[span:] - delta[:-span]          # NaN where either end is missing

    def pick(values: np.ndarray, sign: float) -> Optional[dict]:
        scored = np.where(np.isfinite(values), values * sign, -np.inf)
        i = int(np.argmax(scored))
        if not np.isfinite(scored[i]) or scored[i] < 0.0005:
            return None
        return {"seconds": round(float(scored[i]), 3), "from": round(float(grid[i]), 1),
                "to": round(float(grid[i + span]), 1)}

    if len(change) == 0:
        return {"window": round(window, 1), "gain": None, "loss": None, "net": round(net, 3)}
    return {"window": round(window, 1), "gain": pick(change, -1.0), "loss": pick(change, 1.0), "net": round(net, 3)}


# Kurvenphasen: what each lap did in each phase of a corner, measured on the raw
# samples, and where in the corner the time was gained or lost.
BRAKE_RUN_GAP_S = 0.15       # a braking run may dip below BRAKE_ON_PCT this long and still be one run


PEDAL_ON_PCT = 5.0           # "on the throttle" / "still braking" (trail braking) threshold


THROTTLE_HALF_PCT = 50.0


TURN_IN_FRACTION = 0.25      # turn-in: steering reaches this share of the corner's peak steering


MIN_PEAK_STEER_PCT = 3.0     # a corner with less peak steering has no measurable turn-in (a kink)


EXIT_ACCEL_M = 100.0         # "acceleration after the apex" is the mean long G over this stretch


APEX_ZONE_FRACTION = 0.2     # half width of the apex phase: this share of turn-in -> apex ...


PHASES = ("braking", "entry", "apex", "exit", "straight")


CORNER_KEYS = ("speed", "throttle", "brake", "steering", "long_g",
               "brake_pressure_fl", "brake_pressure_fr", "brake_pressure_rl", "brake_pressure_rr")


def _corner_channels(series: "LapSeries") -> Optional[dict]:
    """The arrays _corner_lap needs, converted once per lap (not once per corner).
    Missing channels (older recordings) are None and the metrics that need them are None."""
    speed = channel_values(series, "speed")
    if speed is None:
        return None
    pressures = [channel_values(series, f"brake_pressure_{w}") if f"brake_pressure_{w}" in CATALOG else None
                 for w in WHEEL_POSITIONS]
    pressures = [p for p in pressures if p is not None]
    long_g = channel_values(series, "long_g") if "accel_z" in series.raw else None
    return {"dist": series.dist, "t": series.raw["t"], "speed": speed,
            # Smoothed once per lap for the peak deceleration (see DECEL_WINDOW_S)
            "long_g_smooth": _window_mean(long_g, series.raw["t"], DECEL_WINDOW_S) if long_g is not None else None,
            "brake": channel_values(series, "brake"), "throttle": channel_values(series, "throttle"),
            "steering": channel_values(series, "steering") if "steering" in series.raw else None,
            "long_g": long_g,
            "brake_pressure": np.nanmax(np.vstack(pressures), axis=0) if pressures else None}


DECEL_WINDOW_S = 0.10   # Peak deceleration = the highest 0.1 s average, not one frame


def _crossing(values: np.ndarray, dist: np.ndarray, i: int, threshold: float, absolute: bool = False) -> float:
    """Where `values` crosses `threshold` between sample i-1 and sample i (i = the first
    sample at/above it), linearly interpolated — sub-sample instead of "the next sample". At 80 m/s
    and 50-60 Hz the samples lie ~1.5 m apart, the same order as the braking-point differences the
    corner table shows. Falls back to dist[i] when there is nothing to interpolate."""
    if i is None or i <= 0 or i >= len(dist):
        return float(dist[min(max(i or 0, 0), len(dist) - 1)])
    a, b = float(values[i - 1]), float(values[i])
    if absolute:
        a, b = abs(a), abs(b)
    if not (math.isfinite(a) and math.isfinite(b)) or a >= threshold or b <= a:
        return float(dist[i])
    f = min(max((threshold - a) / (b - a), 0.0), 1.0)
    return float(dist[i - 1] + f * (dist[i] - dist[i - 1]))


def _time_mean(values: np.ndarray, t: np.ndarray) -> Optional[float]:
    """Mean over TIME (each sample weighted by the time it stands for), not over samples."""
    if values.size == 0:
        return None
    w = np.diff(t, append=t[-1]) if len(t) > 1 else np.ones(1)
    if len(t) > 1:
        w[-1] = w[-2] if len(w) > 1 else 1.0
    ok = np.isfinite(values) & (w > 0)
    if not ok.any():
        return None
    return float(np.sum(values[ok] * w[ok]) / np.sum(w[ok]))


def _window_mean(values: np.ndarray, t: np.ndarray, seconds: float) -> np.ndarray:
    """Centred moving average over ~`seconds` (sample count from the median frame time)."""
    if len(t) < 3:
        return values
    dt = np.diff(t)
    dt = dt[dt > 0]
    if not dt.size:
        return values
    window = int(round(seconds / float(np.median(dt))))
    if window <= 1:
        return values
    filled = np.where(np.isfinite(values), values, np.nan)
    # NaN-aware: average of the finite samples in the window
    ok = np.isfinite(filled).astype(float)
    vals = np.nan_to_num(filled)
    kernel = np.ones(window)
    pad = (window // 2, window - 1 - window // 2)
    num = np.convolve(np.pad(vals, pad, mode="edge"), kernel, mode="valid")
    den = np.convolve(np.pad(ok, pad, mode="edge"), kernel, mode="valid")
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(den > 0, num / den, np.nan)


def _first(mask: np.ndarray) -> Optional[int]:
    hits = np.flatnonzero(mask)
    return int(hits[0]) if len(hits) else None


def _brake_run(brake: np.ndarray, t: np.ndarray, start: int, stop: int) -> int:
    """Last index of the braking run that begins at `start` (gaps < BRAKE_RUN_GAP_S bridged)."""
    last = start
    for i in range(start + 1, stop):
        if brake[i] >= BRAKE_ON_PCT:
            last = i
        elif t[i] - t[last] > BRAKE_RUN_GAP_S:
            break
    return last


def _r(value, digits=1):
    return None if value is None or not np.isfinite(value) else round(float(value), digits)


RESOLUTION_KEYS = ("brake", "brake_end", "turn_in", "apex", "throttle_on", "throttle_50", "throttle")


def _spacing(dist: np.ndarray, x: float) -> float:
    """Distance between the two samples around position x."""
    i = int(np.clip(np.searchsorted(dist, x, "left"), 1, len(dist) - 1))
    if i < len(dist) - 1 and dist[i] == x:
        # exactly on a sample: the wider of its two neighbours' intervals
        return round(float(max(dist[i] - dist[i - 1], dist[i + 1] - dist[i])), 2)
    return round(float(dist[i] - dist[i - 1]), 2)


def _corner_lap(lap: dict, start: float, entry_at: float, end: float) -> Optional[dict]:
    """What one lap did in one corner, from its raw samples (full sample rate).

    `lap` comes from _corner_channels. `start`..`end` is the stretch the corner's time is
    measured over; `entry_at` is where its braking zone begins (the same as `start` except for
    the first corner, whose stretch also holds the straight from the line).

    Positions are metres of lap distance, speeds km/h, pedals %, accelerations G, times s.
    Every metric is None when the lap lacks what it needs — never guessed. turn_in is an
    ESTIMATE (from the steering trace), and labelled so in the views."""
    dist, t, speed = lap["dist"], lap["t"], lap["speed"]
    # dist is monotonic (load_lap guarantees it): the corner's samples are one slice
    i0, i1 = np.searchsorted(dist, entry_at, "left"), np.searchsorted(dist, end, "right")
    if i1 - i0 < 3:
        return None
    segment = speed[i0:i1]
    if not np.isfinite(segment).any():
        return None
    k = int(np.nanargmin(segment))
    ia = i0 + k                                  # apex index
    apex = float(dist[ia])
    out = {
        "entry": round(float(np.interp(entry_at, dist, speed)), 1),
        "min": round(float(segment[k]), 1),
        "apex": round(apex, 1),
        "time": round(float(np.interp(end, dist, t) - np.interp(start, dist, t)), 3),
        "brake": None, "throttle": None, "exit": None,
        # V0.9.4
        "brake_speed": None, "brake_end": None, "brake_len": None, "brake_max": None, "brake_pressure_max": None,
        "decel_peak": None, "decel_mean": None, "turn_in": None, "trail_m": None, "trail_s": None,
        "brake_at_turn_in": None, "throttle_on": None, "throttle_50": None, "coast_m": None, "coast_s": None,
        "accel_exit": None,
    }
    brake, throttle, steering, long_g = lap["brake"], lap["throttle"], lap["steering"], lap["long_g"]
    pressure = lap["brake_pressure"]

    # --- braking phase
    ib0 = ib1 = None
    if brake is not None:
        on = _first(brake[i0:ia + 1] >= BRAKE_ON_PCT)   # up to and including the apex
        if on is not None:
            ib0 = i0 + on
            ib1 = _brake_run(brake, t, ib0, i1)
            brake_at = _crossing(brake, dist, ib0, BRAKE_ON_PCT)
            out["brake"] = round(brake_at, 1)
            out["brake_speed"] = round(float(np.interp(brake_at, dist, speed)), 1)
            out["brake_end"] = round(float(dist[ib1]), 1)
            out["brake_len"] = round(float(dist[ib1] - brake_at), 1)
            out["brake_max"] = _r(np.nanmax(brake[ib0:ib1 + 1]), 0)
            if pressure is not None:
                out["brake_pressure_max"] = _r(np.nanmax(pressure[ib0:ib1 + 1]), 0)
            if long_g is not None and ib1 > ib0:
                decel = -long_g[ib0:ib1 + 1]
                smooth = lap.get("long_g_smooth")
                peak_source = -smooth[ib0:ib1 + 1] if smooth is not None else decel
                if np.isfinite(decel).any():
                    out["decel_peak"] = _r(np.nanmax(peak_source), 2) if np.isfinite(peak_source).any() else None
                    out["decel_mean"] = _r(_time_mean(decel, t[ib0:ib1 + 1]), 2)

    # --- turn-in (estimated) and trail braking
    it = None
    if steering is not None:
        search_from = ib0 if ib0 is not None else i0
        reach = min(i1, ia + 1 + max(1, int(np.searchsorted(dist, apex + 30.0, "right") - ia - 1)))
        window = np.abs(steering[search_from:reach])
        if window.size and np.isfinite(window).any():
            peak = float(np.nanmax(window))
            if peak >= MIN_PEAK_STEER_PCT:
                found = _first(window >= TURN_IN_FRACTION * peak)
                if found is not None and search_from + found <= ia:
                    it = search_from + found
                    out["turn_in"] = round(_crossing(steering, dist, it, TURN_IN_FRACTION * peak, absolute=True), 1)
                    if brake is not None:
                        out["brake_at_turn_in"] = _r(brake[it], 0)
                        if ib0 is not None:
                            run = slice(ib0, ib1 + 1)
                            both = (brake[run] >= PEDAL_ON_PCT) & (np.abs(steering[run]) >= TURN_IN_FRACTION * peak)
                            idx = np.flatnonzero(both)
                            if len(idx):
                                d = dist[run]
                                tt = t[run]
                                ds = np.diff(d, append=d[-1])
                                dt = np.diff(tt, append=tt[-1])
                                out["trail_m"] = round(float(ds[idx].sum()), 1)
                                out["trail_s"] = round(float(dt[idx].sum()), 2)

    # --- throttle: first application, 50 %, full
    if throttle is not None:
        lift_from = ib1 if ib1 is not None else i0
        lift = throttle[lift_from:ia + 1]
        lowest = lift_from + int(np.nanargmin(lift)) if lift.size and np.isfinite(lift).any() else ia
        if np.nanmin(throttle[i0:ia + 1]) < PEDAL_ON_PCT:      # the driver lifted at all
            on = _first(throttle[lowest:i1] >= PEDAL_ON_PCT)
            if on is not None:
                ion = lowest + on
                out["throttle_on"] = round(_crossing(throttle, dist, ion, PEDAL_ON_PCT), 1)
                half = _first(throttle[ion:i1] >= THROTTLE_HALF_PCT)
                if half is not None:
                    out["throttle_50"] = round(_crossing(throttle, dist, ion + half, THROTTLE_HALF_PCT), 1)
                if ib1 is not None and ion > ib1:
                    seg = slice(ib1, ion + 1)
                    idle = (throttle[seg] < PEDAL_ON_PCT) & ((brake[seg] if brake is not None else 0) < PEDAL_ON_PCT)
                    d = dist[seg]
                    tt = t[seg]
                    out["coast_m"] = round(float(np.diff(d, append=d[-1])[idle].sum()), 1)
                    out["coast_s"] = round(float(np.diff(tt, append=tt[-1])[idle].sum()), 2)
    exit_at = None
    if throttle is not None:
        full = np.flatnonzero(throttle[ia + 1:i1] >= THROTTLE_FULL_PCT)  # after the apex
        if len(full):
            exit_at = _crossing(throttle, dist, ia + 1 + int(full[0]), THROTTLE_FULL_PCT)
            out["throttle"] = round(exit_at, 1)
    if exit_at is None:
        exit_at = min(apex + 100.0, end)
    out["exit"] = round(float(np.interp(exit_at, dist, speed)), 1)

    # --- V0.9.7: how precisely each point is placed — the distance between the two recorded
    # samples it lies between (a crossing is interpolated in there; it cannot be more precise than
    # that). Normally speed / frame rate (~1.4 m at 260 km/h and 50 Hz); wider where samples are
    # missing. The hints only call a difference a difference when it is bigger than this.
    out["res"] = {key: _spacing(dist, out[key]) for key in RESOLUTION_KEYS if out.get(key) is not None}

    # --- acceleration after the apex
    if long_g is not None:
        ie = int(np.searchsorted(dist, min(apex + EXIT_ACCEL_M, end), "right"))
        seg = long_g[ia:max(ie, ia + 1)]
        if seg.size and np.isfinite(seg).any():
            out["accel_exit"] = _r(_time_mean(seg, t[ia:ia + seg.size]), 2)
    return out


def corner_phases(corner: dict, lap_a: dict, lap_b: dict, time_a, time_b) -> List[dict]:
    """Splits a corner's stretch into braking / entry / apex / exit / straight with boundaries
    COMMON to both laps, and gives each phase's time difference (A − B, s).

    Boundaries: start | turn-in (mean of both laps; fallback: halfway braking point -> apex, or
    50 m before the apex) | apex − w | apex + w | exit (the later full-throttle point of the two,
    else apex + 100 m) | end; w = APEX_ZONE_FRACTION of turn-in -> apex within
    [APEX_ZONE_MIN_M, APEX_ZONE_MAX_M]. Because the boundaries are shared and every phase is
    t_A − t_B over its own stretch, the phases add up EXACTLY to the corner's delta.
    time_a / time_b: callables distance -> seconds since lap start (raw samples)."""
    start, apex, end = corner["start"], corner["apex"], corner["end"]
    vals = lambda key: [v for v in (lap_a.get(key), lap_b.get(key)) if v is not None]  # noqa: E731
    turn = vals("turn_in")
    if turn:
        b1 = sum(turn) / len(turn)
    elif vals("brake"):
        b1 = (sum(vals("brake")) / len(vals("brake")) + apex) / 2.0
    else:
        b1 = apex - 50.0
    b1 = min(max(b1, start), apex)
    w = min(max(APEX_ZONE_FRACTION * (apex - b1), APEX_ZONE_MIN_M), APEX_ZONE_MAX_M)
    b2 = min(max(apex - w, b1), apex)
    b3 = min(apex + w, end)
    full = vals("throttle")
    b4 = max(full) if full else apex + 100.0
    b4 = min(max(b4, b3), end)
    bounds = [start, b1, b2, b3, b4, end]
    diff = [time_a(x) - time_b(x) for x in bounds]
    return [{"phase": name, "from": round(bounds[k], 1), "to": round(bounds[k + 1], 1),
             "delta": round(diff[k + 1] - diff[k], 3)} for k, name in enumerate(PHASES)]


def _find_corners(grid: np.ndarray, v: np.ndarray, step: float) -> List[Tuple[int, int]]:
    """(apex index, braking-zone start index) of every corner on a speed trace sampled on `grid`.

    A corner is a local minimum of the speed smoothed over CORNER_SMOOTH_M with a drop of at
    least CORNER_MIN_DROP_KMH both before and after it (only one side counts for a corner
    right after the start / before the finish, whose other side lies outside the lap)."""
    k = max(1, int(round(CORNER_SMOOTH_M / step / 2)))
    smooth = np.convolve(np.pad(v, (k, k), mode="edge"), np.ones(2 * k + 1) / (2 * k + 1), mode="valid")
    w = max(1, int(round(CORNER_MIN_GAP_M / step / 2)))
    padded = np.pad(smooth, (w, w), mode="edge")
    windows = np.lib.stride_tricks.sliding_window_view(padded, 2 * w + 1)
    candidates = np.flatnonzero(smooth <= windows.min(axis=1) + 1e-9)
    # one candidate per flat bottom
    candidates = [int(c) for i, c in enumerate(candidates) if i == 0 or c - candidates[i - 1] > w]

    def prominent(apices: List[int]) -> List[int]:
        kept = []
        for j, apex in enumerate(apices):
            first, last = j == 0, j + 1 == len(apices)
            left = apices[j - 1] if not first else 0
            right = apices[j + 1] if not last else len(smooth) - 1
            drop_in = smooth[left:apex + 1].max() - smooth[apex]
            drop_out = smooth[apex:right + 1].max() - smooth[apex]
            if first and last:
                enough = max(drop_in, drop_out) >= CORNER_MIN_DROP_KMH
            elif first:
                enough = drop_out >= CORNER_MIN_DROP_KMH and (drop_in >= CORNER_MIN_DROP_KMH or smooth[left:apex + 1].argmax() == 0)
            elif last:
                enough = drop_in >= CORNER_MIN_DROP_KMH and (drop_out >= CORNER_MIN_DROP_KMH or smooth[apex:right + 1].argmax() == right - apex)
            else:
                enough = min(drop_in, drop_out) >= CORNER_MIN_DROP_KMH
            if enough:
                kept.append(apex)
        return kept

    apices = prominent(candidates)
    apices = prominent(apices)  # once more against the neighbours that survived
    if not apices:
        return []
    # The smoothing only FINDS corners: it shifts the minimum towards the flatter side of an
    # asymmetric corner (short braking, long acceleration). The apex itself is the minimum of
    # the unsmoothed speed near the smoothed one.
    reach = 2 * k
    apices = [max(0, ap - reach) + int(np.argmin(v[max(0, ap - reach):ap + reach + 1])) for ap in apices]
    # A braking zone begins at the LAST point before the apex that is (within 0.5 km/h) as fast
    # as the stretch since the previous corner: on a flat-out straight that is where the lift
    # comes, not where the straight's top speed was first reached.
    out = []
    for j, apex in enumerate(apices):
        left = apices[j - 1] if j > 0 else 0
        before = smooth[left:apex + 1]
        out.append((apex, left + int(np.flatnonzero(before >= before.max() - 0.5)[-1])))
    return out


def _finite_interp(grid: np.ndarray, x: np.ndarray, values: np.ndarray) -> np.ndarray:
    ok = np.isfinite(values)
    if ok.sum() < 2:
        return np.full(len(grid), np.nan)
    return np.interp(grid, x[ok], values[ok])


def corner_analysis(a: "LapSeries", b: "LapSeries") -> List[dict]:
    """Corners of the common stretch and, for each, how both laps drove it (V0.9.1; phases and
    the braking / throttle details since V0.9.4).

    Corners are found on the two laps' average speed (smoothed over CORNER_SMOOTH_M): a local
    minimum with a drop of at least CORNER_MIN_DROP_KMH both before and after it. A corner runs
    from its braking zone (the speed maximum before it) to the next corner's braking zone; the
    first corner's stretch also holds the straight from the start of the common range, the last
    one runs to its end. So the corners cover the lap without gaps and their times add up to the
    delta over the whole lap (the lap-time difference for two complete laps).
    All measurements (speeds, braking / throttle points, times) come from each lap's raw samples,
    not from the smoothed curve.
      -> [{"n", "start", "entry", "apex", "end", "delta", "a": {...}, "b": {...}, "phases": [...]}];
         start..end is the stretch the time is measured over, entry where the braking zone begins;
         delta = time A − time B (negative = Lap 1 faster in that corner); speeds km/h, positions
         metres; phases: see corner_phases (they add up to delta).
    """
    speed_a, speed_b = channel_values(a, "speed"), channel_values(b, "speed")
    if speed_a is None or speed_b is None:
        return []
    lo, hi = common_range(a, b)
    if hi - lo < 4 * CORNER_MIN_GAP_M:
        return []
    step = 2.0
    grid = np.arange(lo, hi, step)
    # Detection needs a speed at every grid point — a NaN would spread through the
    # smoothing and hide every corner. Interpolated over the samples that HAVE a speed.
    v = (_finite_interp(grid, a.dist, speed_a) + _finite_interp(grid, b.dist, speed_b)) / 2.0
    if not np.isfinite(v).all():
        return []
    found = _find_corners(grid, v, step)
    if not found:
        return []
    chan_a, chan_b = _corner_channels(a), _corner_channels(b)
    time_a = lambda x: float(np.interp(x, a.dist, a.raw["t"]))  # noqa: E731
    time_b = lambda x: float(np.interp(x, b.dist, b.raw["t"]))  # noqa: E731
    corners = []
    for j, (apex, entry_index) in enumerate(found):
        entry = float(grid[entry_index])
        start = float(lo) if j == 0 else entry
        end = float(grid[found[j + 1][1]]) if j + 1 < len(found) else float(hi)
        ref_apex = float(grid[apex])
        lap_a = _corner_lap(chan_a, start, entry, end)
        lap_b = _corner_lap(chan_b, start, entry, end)
        if lap_a is None or lap_b is None:
            continue
        corner = {
            "n": len(corners) + 1, "start": round(start, 1), "entry": round(entry, 1),
            "apex": round(ref_apex, 1), "end": round(end, 1),
            "delta": round(lap_a["time"] - lap_b["time"], 3), "a": lap_a, "b": lap_b,
        }
        corner["phases"] = corner_phases(corner, lap_a, lap_b, time_a, time_b)
        corners.append(corner)
    return corners


def lap_corners(dist: np.ndarray, t: np.ndarray, speed_kmh: np.ndarray, brake_pct: Optional[np.ndarray],
                throttle_pct: Optional[np.ndarray]) -> List[list]:
    """The corners of ONE lap, compact, for storing with the lap (server/analysis/lap_insights.py) and
    comparing a corner across a driver's laps without opening their files:
    [[apex m, braking point m | None, min speed km/h, full-throttle point m | None, exit km/h], ...].
    Same detection and the same measurements as corner_analysis, on this lap's own speed."""
    if len(dist) < 10 or float(dist[-1] - dist[0]) < 4 * CORNER_MIN_GAP_M:
        return []
    step = 2.0
    grid = np.arange(float(dist[0]), float(dist[-1]), step)
    v = _finite_interp(grid, dist, speed_kmh)      # NaN-safe, as in corner_analysis
    if not np.isfinite(v).all():
        return []
    found = _find_corners(grid, v, step)
    lap = {"dist": dist, "t": t, "speed": speed_kmh, "brake": brake_pct, "throttle": throttle_pct,
           "steering": None, "long_g": None, "long_g_smooth": None, "brake_pressure": None}
    out = []
    for j, (apex, entry_index) in enumerate(found):
        entry = float(grid[entry_index])
        start = float(grid[0]) if j == 0 else entry
        end = float(grid[found[j + 1][1]]) if j + 1 < len(found) else float(grid[-1])
        m = _corner_lap(lap, start, entry, end)
        if m is not None:
            out.append([m["apex"], m["brake"], m["min"], m["throttle"], m["exit"]])
    return out
