"""
server/lap_insights.py — compact figures per lap, and what can be built from them
without opening a single lap file again (V0.9.3).

Computed ONCE, at upload, from the same read of the lap file that the plausibility
and track-limits checks already do (a few dozen of ~190 columns), and stored as a
small JSON (`laps.lap_stats`, typically 2–4 KB). Everything below then works from the
database alone:

  * per-lap figures   fuel / virtual-energy / tyre use, how much of the lap was flat
                      out, braking, coasting or trail-braking, gear changes, peaks
  * time profile      the lap time at every PROFILE_STEP_M metres of the lap (the
                      game's own lap distance, identical for every lap of a layout)
  * theoretical best  the sum of the fastest time over every profile segment across a
                      driver's valid laps — and exactly which lap each segment came from
  * sessions/stints   consecutive laps grouped, with robust consistency figures and the
                      lap-time trend over the stint (tyre / fuel effect)

Definitions are written so they can be checked: every number is a plain function of
the recorded samples; segment times of one lap add up EXACTLY to its lap time (the
last segment is closed with the official lap time), so a theoretical best is the sum
of real, measured segment times and nothing else.
"""

from __future__ import annotations

import math
import statistics
from typing import Dict, Iterable, List, Optional, Sequence

import numpy as np

from .telemetry_channels import G, _to_celsius, _to_kpa

INSIGHTS_VERSION = 3   # 2: track conditions per lap (wet share), V0.9.3.1; 3: corners, fuel per
#                         sector, energy left, pit lane, traffic, engine map (V0.9.4)
PROFILE_STEP_M = 50.0
PROFILE_MAX_START_M = PROFILE_STEP_M / 2   # a lap must begin near the line to have a profile
WHEELS = ("fl", "fr", "rl", "rr")

THROTTLE_FULL = 0.98
PEDAL_ON = 0.05

COLUMNS: tuple = (
    "t", "lap_dist", "speed", "throttle", "brake", "gear", "fuel",
    "accel_x", "accel_z", "virtual_energy", "battery_charge_fraction",
    *(f"wear_{w}" for w in WHEELS),
    *(f"temp_{side}_{w}" for w in WHEELS for side in ("l", "c", "r")),
    *(f"tire_temp_mean_{w}" for w in WHEELS),
    *(f"tire_pressure_{w}" for w in WHEELS),
    *(f"brake_temp_{w}" for w in WHEELS),
    *(f"surface_type_{w}" for w in WHEELS),
    # V0.9.4 (clients 0.9.4+ record in_pits / gaps / place; motor_map since 0.9.0)
    "in_pits", "time_gap_ahead", "time_gap_behind", "place", "motor_map", "flag",
)

# V0.9.4: traffic — a car ahead closer than this (LMU's time gap to the car ahead on track)
TRAFFIC_GAP_S = 1.0
REFUEL_STEP_L = 0.05     # a rise of the fuel level per sample above this is refuelling, not noise
REFUEL_MIN_L = 0.5       # less than this in a whole lap is not reported as a refuel
TYRE_CHANGE_RISE = 0.02  # remaining tread rising by 2 points or more: a new tyre was fitted
STATIONARY_MPS = 0.5


def _round(value: Optional[float], digits: int = 2) -> Optional[float]:
    if value is None or not math.isfinite(value):
        return None
    return round(float(value), digits)


def _weights(t: np.ndarray) -> np.ndarray:
    """Seconds each sample stands for (time-weighted shares, robust to frame gaps)."""
    if len(t) < 2:
        return np.ones(len(t))
    dt = np.diff(t)
    typical = float(np.median(dt[dt > 0])) if np.any(dt > 0) else 0.0
    dt = np.clip(dt, 0.0, max(typical * 5.0, 1e-6))
    return np.append(dt, typical)


def _share(mask: np.ndarray, weights: np.ndarray) -> Optional[float]:
    total = float(weights.sum())
    return _round(100.0 * float(weights[mask].sum()) / total, 1) if total > 0 else None


def _first_last(values: Optional[np.ndarray]) -> Optional[tuple]:
    if values is None:
        return None
    finite = np.flatnonzero(np.isfinite(values))
    if finite.size < 2:
        return None
    return float(values[finite[0]]), float(values[finite[-1]])


def monotonic(lap_dist: np.ndarray, t: np.ndarray) -> np.ndarray:
    """Mask of samples on which distance strictly increases (same rule as the analysis)."""
    finite = np.isfinite(lap_dist) & np.isfinite(t)
    running = np.maximum.accumulate(np.where(finite, lap_dist, -np.inf))
    advances = np.concatenate(([True], np.diff(running) > 1e-6))
    return finite & advances & (running > -np.inf)


def time_profile(t: np.ndarray, lap_dist: np.ndarray, lap_time: float, step: float = PROFILE_STEP_M) -> Optional[dict]:
    """Lap time at every `step` metres: {"step", "start", "end", "cum": [t at step, 2*step, ...]}.

    None when the lap cannot be split reliably (starts too far from the line, too short,
    or its recorded duration does not fit its lap time)."""
    keep = monotonic(lap_dist, t)
    if keep.sum() < 10:
        return None
    dist, tt = np.maximum.accumulate(lap_dist[keep]), t[keep]
    start, end = float(dist[0]), float(dist[-1])
    if start > PROFILE_MAX_START_M or end - start < 4 * step or not (lap_time > 0):
        return None
    if float(tt[-1]) > lap_time + 1.0:      # the samples run past the official time: no clean split
        return None
    marks = np.arange(step, end, step)
    marks = marks[marks < end - 1.0]
    cum = np.interp(marks, dist, tt)
    if cum.size and float(cum[-1]) >= lap_time:
        return None
    return {"step": step, "start": round(start, 1), "end": round(end, 1),
            "cum": [round(float(v), 3) for v in cum]}


def segment_times(profile: dict, lap_time: float) -> Optional[List[float]]:
    """The lap's time per profile segment; sums to lap_time exactly (up to rounding)."""
    if not profile or not profile.get("cum"):
        return None
    cum = [0.0] + [float(v) for v in profile["cum"]] + [float(lap_time)]
    segments = [b - a for a, b in zip(cum, cum[1:])]
    return segments if all(s > 0 for s in segments) else None


def compute(columns: Dict[str, np.ndarray], *, lap_time: float, tail_samples: int = 0,
            sector_times: Optional[Sequence[float]] = None) -> dict:
    """The stored figures for one lap. `columns` may lack anything but t/lap_dist."""
    t = columns.get("t")
    lap_dist = columns.get("lap_dist")
    out: dict = {"version": INSIGHTS_VERSION}
    if t is None or lap_dist is None or len(t) < 3:
        return out
    n = len(t)
    end = max(n - max(int(tail_samples or 0), 0), 2)
    t = np.asarray(t[:end], dtype=float)
    lap_dist = np.asarray(lap_dist[:end], dtype=float)

    def col(name):
        values = columns.get(name)
        if values is None:
            return None
        arr = np.asarray(values[:end], dtype=float)
        return arr if np.isfinite(arr).any() else None

    weights = _weights(t)
    speed = col("speed")
    if speed is not None:
        mask = np.isfinite(speed) & np.isfinite(t)
        trapz = getattr(np, "trapezoid", None) or np.trapz
        driven = float(trapz(np.clip(speed[mask], 0, None), t[mask])) if mask.sum() > 1 else None
        out["driven_m"] = _round(driven, 1)
        out["speed_max_kmh"] = _round(float(np.nanmax(speed)) * 3.6, 1)
        duration = float(t[-1] - t[0])
        out["speed_avg_kmh"] = _round(driven / duration * 3.6, 1) if driven and duration > 0 else None
    keep = monotonic(lap_dist, t)
    out["distance_m"] = _round(float(np.max(lap_dist[keep])), 1) if keep.any() else None

    throttle, brake = col("throttle"), col("brake")
    if throttle is not None and brake is not None:
        th, br = np.nan_to_num(throttle), np.nan_to_num(brake)
        out["full_throttle_pct"] = _share(th >= THROTTLE_FULL, weights)
        out["braking_pct"] = _share(br >= PEDAL_ON, weights)
        out["coasting_pct"] = _share((th < PEDAL_ON) & (br < PEDAL_ON), weights)
        out["overlap_pct"] = _share((th >= PEDAL_ON) & (br >= PEDAL_ON), weights)
    gear = col("gear")
    if gear is not None:
        g = gear[np.isfinite(gear)]
        out["gear_changes"] = int(np.count_nonzero(np.diff(g))) if g.size > 1 else 0

    fuel = _first_last(col("fuel"))
    # V0.9.4: refuelling during the lap, MEASURED — the sum of the rises of the fuel level
    # (a pit stop fills the tank over several seconds; noise below 0.05 l per sample is ignored).
    # The lap's consumption is then first - last + what was added.
    added = 0.0
    fuel_col = col("fuel")
    if fuel_col is not None:
        level = fuel_col[np.isfinite(fuel_col)]
        if level.size > 1:
            rises = np.diff(level)
            added = float(rises[rises > REFUEL_STEP_L].sum())
    if added >= REFUEL_MIN_L:
        out["fuel_added_l"] = _round(added, 1)
    else:
        added = 0.0
    if fuel and 0 <= fuel[0] - fuel[1] + added < 100:
        out["fuel_used_l"] = _round(fuel[0] - fuel[1] + added, 2)
        out["fuel_end_l"] = _round(fuel[1], 1)
    energy = _first_last(col("virtual_energy"))
    if energy and -0.01 <= energy[0] - energy[1] <= 1.0 and max(energy) <= 1.5:
        out["energy_used_pct"] = _round((energy[0] - energy[1]) * 100.0, 2)
        out["energy_end_pct"] = _round(energy[1] * 100.0, 1)   # V0.9.4: what is left
    # V0.9.4: fuel per sector (the game's sector times -> the fuel level at the boundaries)
    fuel_values = col("fuel")
    if fuel_values is not None and sector_times and len(sector_times) >= 2 and all(
            isinstance(x, (int, float)) and x > 0 for x in sector_times[:2]):
        ok = np.isfinite(fuel_values)
        if ok.sum() > 2:
            bounds = [float(t[0]), float(t[0]) + float(sector_times[0]),
                      float(t[0]) + float(sector_times[0]) + float(sector_times[1]), float(t[-1])]
            if bounds[2] < bounds[3]:
                levels = np.interp(bounds, t[ok], fuel_values[ok])
                used = [float(levels[k] - levels[k + 1]) for k in range(3)]
                if all(-0.05 <= u < 50 for u in used):
                    out["fuel_sector_l"] = [_round(max(u, 0.0), 3) for u in used]
    battery = _first_last(col("battery_charge_fraction"))
    if battery and max(battery) <= 1.5:
        out["battery_delta_pct"] = _round((battery[1] - battery[0]) * 100.0, 1)

    accel_x, accel_z = col("accel_x"), col("accel_z")
    if accel_x is not None and accel_z is not None:
        combined = np.sqrt(accel_x ** 2 + accel_z ** 2) / G
        if combined.size >= 5:   # smoothed over 5 samples: a single-frame spike is not a peak
            combined = np.convolve(np.nan_to_num(combined), np.ones(5) / 5.0, mode="same")
        out["peak_g"] = _round(float(np.nanmax(combined)), 2)

    wheels: Dict[str, dict] = {}
    changed = []
    for w in WHEELS:
        entry: dict = {}
        wear_col = col(f"wear_{w}")
        if wear_col is not None:
            level = wear_col[np.isfinite(wear_col)]
            # V0.9.4: a new tyre, MEASURED — the remaining tread rises (LMU: 1.0 = new)
            changed.append(bool(level.size > 1 and np.diff(level).max() >= TYRE_CHANGE_RISE))
        wear = _first_last(wear_col)
        if wear and max(wear) <= 1.5:
            entry["wear_used_pct"] = _round((wear[0] - wear[1]) * 100.0, 3)
            entry["wear_left_pct"] = _round(wear[1] * 100.0, 1)
        sides = [col(f"temp_{s}_{w}") for s in ("l", "c", "r")]
        if all(s is not None for s in sides):
            entry["temp_avg_c"] = _round(float(np.nanmean(_to_celsius((sides[0] + sides[1] + sides[2]) / 3.0))), 1)
            entry["temp_lcr_c"] = [_round(float(np.nanmean(_to_celsius(s))), 1) for s in sides]  # left / centre / right
        elif col(f"tire_temp_mean_{w}") is not None:
            entry["temp_avg_c"] = _round(float(np.nanmean(_to_celsius(col(f"tire_temp_mean_{w}")))), 1)
        pressure = col(f"tire_pressure_{w}")
        if pressure is not None:
            entry["pressure_avg_kpa"] = _round(float(np.nanmean(_to_kpa(pressure))), 1)
        brake_temp = col(f"brake_temp_{w}")
        if brake_temp is not None:
            entry["brake_max_c"] = _round(float(np.nanmax(_to_celsius(brake_temp))), 0)
        if entry:
            wheels[w] = entry
    if wheels:
        out["wheels"] = wheels
    if changed and any(changed):
        out["tyres_changed"] = [w for w, c in zip(WHEELS, changed) if c]

    # V0.9.3.1: track conditions — the share of the lap on a wet surface (LMU's mSurfaceType
    # 1 = wet, per wheel). Recorded by clients 0.9.0+; laps without it are "unknown".
    surfaces = [col(f"surface_type_{w}") for w in WHEELS]
    if all(s is not None for s in surfaces):
        stack = np.vstack(surfaces)
        known = np.isfinite(stack).all(axis=0)
        if known.any():
            wet = (stack == 1.0).any(axis=0) & known
            out["conditions"] = {"wet_share": _round(float(weights[known].dot(wet[known]) / max(weights[known].sum(), 1e-9)), 3)}

    # V0.9.4: pit lane — time in the pit lane, time standing still there, entry / exit
    in_pits = col("in_pits")
    if in_pits is not None:
        pits = np.nan_to_num(in_pits) >= 0.5
        if pits.any():
            stopped = pits & (np.nan_to_num(speed if speed is not None else np.full(len(t), np.inf)) < STATIONARY_MPS)
            hits = np.flatnonzero(pits)
            out["pit"] = {
                "lane_s": _round(float(weights[pits].sum()), 1),
                "stationary_s": _round(float(weights[stopped].sum()), 1),
                "entry_m": _round(float(lap_dist[hits[0]]), 0),
                "exit_m": _round(float(lap_dist[hits[-1]]), 0),
                "starts_in_pits": bool(pits[0]),
                "ends_in_pits": bool(pits[-1]),
            }
        else:
            out["pit"] = {"lane_s": 0.0}

    # V0.9.4: traffic — LMU's time gap to the car ahead / behind on track, and positions
    ahead, behind = col("time_gap_ahead"), col("time_gap_behind")
    if ahead is not None or behind is not None:
        traffic: dict = {}
        total = float(weights.sum()) or 1.0
        for name, values in (("ahead", ahead), ("behind", behind)):
            if values is None:
                continue
            close = np.isfinite(values) & (values > 0) & (values < TRAFFIC_GAP_S)
            traffic[f"{name}_close_share"] = _round(float(weights[close].sum()) / total, 3)
            valid = values[np.isfinite(values) & (values > 0)]
            if valid.size:
                traffic[f"min_gap_{name}_s"] = _round(float(valid.min()), 2)
        place = col("place")
        if place is not None:
            p = place[np.isfinite(place) & (place > 0)]
            if p.size:
                traffic["place_start"] = int(p[0])
                traffic["place_end"] = int(p[-1])
                changes = np.diff(p)
                traffic["places_gained"] = int(-changes[changes < 0].sum())
                traffic["places_lost"] = int(changes[changes > 0].sum())
        flag = col("flag")
        if flag is not None:
            known = np.isfinite(flag)
            if known.any():   # LMU mFlag: 0 = green, 6 = blue (the only two it reports)
                traffic["blue_flag_share"] = _round(float(weights[known & (flag == 6)].sum()) / total, 3)
        out["traffic"] = traffic

    # V0.9.4: the engine map used most of the lap (to compare consumption by map)
    motor_map = col("motor_map")
    if motor_map is not None:
        m = motor_map[np.isfinite(motor_map)]
        if m.size:
            values, counts = np.unique(m.astype(int), return_counts=True)
            out["engine_map"] = int(values[int(np.argmax(counts))])

    # V0.9.4: the lap's own corners, compact (comparing a corner across a driver's laps)
    if speed is not None:
        from .telemetry_analysis import lap_corners  # local: telemetry_analysis imports this module's neighbours

        keep_c = monotonic(lap_dist, t)
        if keep_c.sum() > 20:
            dist_c = np.maximum.accumulate(lap_dist[keep_c])
            corners = lap_corners(
                dist_c, t[keep_c], speed[keep_c] * 3.6,
                brake[keep_c] * 100.0 if brake is not None else None,
                throttle[keep_c] * 100.0 if throttle is not None else None,
            )
            if corners:
                out["corners"] = corners

    profile = time_profile(t, lap_dist, lap_time)
    if profile:
        out["profile"] = profile
    return {k: v for k, v in out.items() if v is not None}


# ---------------------------------------------------------------------------
# Track conditions (V0.9.3.1)
# ---------------------------------------------------------------------------

WET_DRY_BELOW = 0.02       # less than 2 % of the lap on a wet surface: dry
WET_FROM = 0.5             # at least half of the lap: wet; in between: mixed
CONDITION_CLASSES = ("dry", "mixed", "wet", "unknown")
TRACK_TEMP_SPREAD_C = 10.0


def condition_class(lap_stats) -> str:
    """dry / mixed / wet from the stored wet share; unknown without it (older laps)."""
    cond = (lap_stats or {}).get("conditions") if isinstance(lap_stats, dict) else None
    share = cond.get("wet_share") if isinstance(cond, dict) else None
    if not isinstance(share, (int, float)):
        return "unknown"
    if share < WET_DRY_BELOW:
        return "dry"
    if share >= WET_FROM:
        return "wet"
    return "mixed"


def conditions_summary(laps: Sequence[dict]) -> dict:
    """laps: [{"lap_time", "conditions": class, "grip": int|None, "track_temp": float|None}].
    Per class the number of laps and the best time, plus the spread of grip and track
    temperature — so a theoretical best never mixes conditions without saying so."""
    groups: Dict[str, dict] = {}
    for lap in laps:
        g = groups.setdefault(lap["conditions"], {"laps": 0, "best": None})
        g["laps"] += 1
        if lap.get("lap_time") and (g["best"] is None or lap["lap_time"] < g["best"]):
            g["best"] = round(float(lap["lap_time"]), 3)
    grips = sorted({lap["grip"] for lap in laps if isinstance(lap.get("grip"), int)})
    temps = [float(lap["track_temp"]) for lap in laps if isinstance(lap.get("track_temp"), (int, float))]
    return {
        "groups": {k: groups[k] for k in CONDITION_CLASSES if k in groups},
        "grip_levels": grips,
        "track_temp_c": [round(min(temps), 1), round(max(temps), 1)] if temps else None,
        "varying": len(grips) > 1 or (bool(temps) and max(temps) - min(temps) > TRACK_TEMP_SPREAD_C),
    }


# ---------------------------------------------------------------------------
# Theoretical best
# ---------------------------------------------------------------------------

def theoretical_best(laps: Iterable[dict]) -> Optional[dict]:
    """laps: [{"id", "lap_time", "profile", "sector_times"}] — valid laps of ONE driver,
    track layout and car. Uses the profile shape most laps share (same step and number of
    marks — i.e. the same layout length), so segments are compared like for like.

      -> {"time", "best_lap_time", "best_lap_id", "gain", "segments": n, "step",
          "laps_used", "from_laps": {lap_id: segments taken}, "sector_ideal": ...,
          "segment_best": [[lap_id, seconds, start_m, end_m], ...]}
    """
    laps = [lap for lap in laps if lap.get("lap_time") and lap["lap_time"] > 0]
    if not laps:
        return None
    best = min(laps, key=lambda lap: (lap["lap_time"], lap["id"]))
    out = {"best_lap_time": round(best["lap_time"], 3), "best_lap_id": best["id"], "laps_considered": len(laps)}

    sector_laps = [lap for lap in laps
                   if isinstance(lap.get("sector_times"), list) and len(lap["sector_times"]) == 3
                   and all(isinstance(s, (int, float)) and s > 0 for s in lap["sector_times"])]
    if sector_laps:
        # V0.9.4: which lap each best sector comes from, and the potential per sector against the
        # best lap's own sectors (best lap's sector − best sector, >= 0)
        picks = [min(sector_laps, key=lambda lap: (lap["sector_times"][i], lap["id"])) for i in range(3)]
        mins = [pick["sector_times"][i] for i, pick in enumerate(picks)]
        ideal = {"time": round(sum(mins), 3), "sectors": [round(m, 3) for m in mins],
                 "gain": round(best["lap_time"] - sum(mins), 3), "from_laps": [pick["id"] for pick in picks]}
        if best in sector_laps:
            ideal["potential"] = [round(best["sector_times"][i] - mins[i], 3) for i in range(3)]
        out["sector_ideal"] = ideal

    shaped = [(lap, lap.get("profile")) for lap in laps]
    shapes: Dict[tuple, list] = {}
    for lap, profile in shaped:
        if not profile or not profile.get("cum"):
            continue
        segments = segment_times(profile, lap["lap_time"])
        if segments is None:
            continue
        shapes.setdefault((float(profile["step"]), len(profile["cum"])), []).append((lap, segments))
    if not shapes:
        return out
    (step, marks), usable = max(shapes.items(), key=lambda item: (len(item[1]), -item[0][1]))
    count = marks + 1
    best_segments = []
    for k in range(count):
        lap, segments = min(usable, key=lambda pair: (pair[1][k], pair[0]["lap_time"], pair[0]["id"]))
        best_segments.append((lap["id"], segments[k]))
    ideal = sum(seconds for _, seconds in best_segments)
    from_laps: Dict[int, int] = {}
    for lap_id, _ in best_segments:
        from_laps[lap_id] = from_laps.get(lap_id, 0) + 1
    best_in_shape = min((pair for pair in usable), key=lambda pair: (pair[0]["lap_time"], pair[0]["id"]))[0]
    out.update({
        "time": round(ideal, 3),
        "gain": round(best_in_shape["lap_time"] - ideal, 3),
        "reference_lap_id": best_in_shape["id"],
        "reference_lap_time": round(best_in_shape["lap_time"], 3),
        "step": step,
        "segments": count,
        "laps_used": len(usable),
        "from_laps": {str(k): v for k, v in sorted(from_laps.items(), key=lambda kv: -kv[1])},
        "segment_best": [
            [lap_id, round(seconds, 3), round(k * step, 0), (round((k + 1) * step, 0) if k + 1 < count else None)]
            for k, (lap_id, seconds) in enumerate(best_segments)
        ],
    })
    return out


# ---------------------------------------------------------------------------
# Sessions / stints and consistency
# ---------------------------------------------------------------------------

SESSION_GAP_SECONDS = 15 * 60       # a pause longer than this starts a new session
OUTLIER_FRACTION = 1.07             # slower than 107 % of the session's median = not a clean lap


def _mad(values: Sequence[float]) -> float:
    median = statistics.median(values)
    return statistics.median([abs(v - median) for v in values])


def consistency(times: Sequence[float]) -> Optional[dict]:
    """Robust figures over clean lap times (in the order driven)."""
    times = [float(x) for x in times if x and x > 0]
    if not times:
        return None
    median = statistics.median(times)
    clean = [x for x in times if x <= median * OUTLIER_FRACTION]
    best = min(times)
    out = {"laps": len(times), "clean_laps": len(clean), "best": round(best, 3), "median": round(median, 3)}
    if len(clean) >= 2:
        out["stdev"] = round(statistics.stdev(clean), 3)
        out["mad"] = round(_mad(clean), 3)
        out["within_half_second"] = sum(1 for x in clean if x - best <= 0.5)
    if len(clean) >= 4:
        xs = list(range(len(clean)))
        mean_x, mean_y = statistics.fmean(xs), statistics.fmean(clean)
        denom = sum((x - mean_x) ** 2 for x in xs)
        if denom > 0:
            out["trend_per_lap"] = round(sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, clean)) / denom, 3)
    return out


def group_sessions(laps: Iterable[dict], gap_seconds: float = SESSION_GAP_SECONDS) -> List[dict]:
    """laps: [{"id", "lap_number", "lap_time", "is_valid", "recorded_at", "session_type",
    "track_name", "car_model", "stats"}] of ONE driver. Consecutive laps on the same track,
    car and session type, without a pause longer than `gap_seconds`, form a session.
    Newest session first."""
    ordered = sorted(laps, key=lambda lap: (lap.get("recorded_at") or 0, lap["id"]))
    sessions: List[List[dict]] = []
    for lap in ordered:
        if sessions:
            last = sessions[-1][-1]
            same = (last.get("track_name") == lap.get("track_name") and last.get("car_model") == lap.get("car_model")
                    and last.get("session_type") == lap.get("session_type"))
            if same and (lap.get("recorded_at") or 0) - (last.get("recorded_at") or 0) <= gap_seconds:
                sessions[-1].append(lap)
                continue
        sessions.append([lap])

    out = []
    for group in reversed(sessions):
        valid = [lap for lap in group if lap.get("is_valid")]
        fuel = [lap["stats"]["fuel_used_l"] for lap in group
                if isinstance(lap.get("stats"), dict) and isinstance(lap["stats"].get("fuel_used_l"), (int, float))]
        wear = []
        for lap in group:
            wheels = (lap.get("stats") or {}).get("wheels") if isinstance(lap.get("stats"), dict) else None
            used = [w.get("wear_used_pct") for w in (wheels or {}).values() if isinstance(w.get("wear_used_pct"), (int, float))]
            if used:
                wear.append(sum(used) / len(used))
        energy = [lap["stats"]["energy_used_pct"] for lap in group
                  if isinstance(lap.get("stats"), dict) and isinstance(lap["stats"].get("energy_used_pct"), (int, float))]
        out.append({
            "track_name": group[0].get("track_name"),
            "car_model": group[0].get("car_model"),
            "session_type": group[0].get("session_type"),
            "started": group[0].get("recorded_at"),
            "ended": group[-1].get("recorded_at"),
            "laps": [{"id": lap["id"], "lap_number": lap.get("lap_number"), "lap_time": lap.get("lap_time"),
                      "is_valid": bool(lap.get("is_valid"))} for lap in group],
            "consistency": consistency([lap["lap_time"] for lap in valid]),
            "fuel_per_lap_l": round(statistics.median(fuel), 2) if fuel else None,
            "energy_per_lap_pct": round(statistics.median(energy), 2) if energy else None,
            "wear_per_lap_pct": round(statistics.median(wear), 3) if wear else None,
        })
    return out
