"""
stints.py — the Stint Analyzer's view of one driving session (V0.9.4).

From the database only: every figure comes from what was stored per lap at upload —
laps.lap_stats (server/lap_insights.py: pit lane, refuelling and tyre changes MEASURED in the
telemetry, fuel / energy / wear, traffic, conditions) and laps.lap_context (what LMU reported
at the end of the lap: pit stops so far, compound, damage, tank, time left). No telemetry
file is read, so a whole session costs a few milliseconds.

What this module decides, and from what
---------------------------------------
  * Lap flags — each one from a measured value, never from the lap time alone:
      invalid      the lap is not valid (server verdict)
      outlap       the lap started in the pit lane            (lap_stats.pit.starts_in_pits)
      inlap        the lap ended in the pit lane              (lap_stats.pit.ends_in_pits)
      pit_stop     the car stood still in the pit lane >= 1 s (lap_stats.pit.stationary_s)
      traffic      >= 10 % of the lap less than 1 s behind another car (LMU's time gap)
      blue_flag    a blue flag was shown for >= 5 % of the lap
      race_start   the first lap of a race session
      wet          the track was wet / mixed for this lap     (lap_stats.conditions)
      damage       LMU reported more body damage than on the lap before
      gap_before   laps are missing before this one (not recorded / not uploaded)
      outlier      slower than 107 % of the median of the otherwise clean laps
    "clean" = none of invalid, outlap, inlap, pit_stop, traffic, blue_flag, race_start,
    outlier. The web page lets the user change that set; this is only the default.
  * Stints — a new stint starts after a pit stop (pit lane + standing still, or LMU's pit
    stop counter rising), and — for laps of older clients without pit lane data — when new
    tyres or fuel were MEASURED (tread rising, fuel level rising) or the compound changed.
    The reasons are listed per stint.
  * Tyre sets — a set starts when tyres were changed (measured or compound change); the age
    of the set in laps is counted from there. Without a measured change the set continues.
  * Pit stops — time in the pit lane and standing still (measured), fuel added and tyres
    changed (measured), and the pit loss: the lap times of the in- and out-lap minus as many
    median clean laps. The pit loss is an ESTIMATE (it also contains the slower out-lap on
    cold tyres) and is only given with at least 3 clean laps in the session.

Statistics over the clean laps (rolling averages, degradation, forecasts, the strategy
calculator) are done in the browser (web/js/stints/stats.js) so that every filter the user
changes is applied at once without asking the server again.
"""

from __future__ import annotations

import statistics
from typing import Iterable, List, Optional, Sequence

from .lap_insights import OUTLIER_FRACTION, WHEELS, condition_class

STATIONARY_STOP_S = 1.0       # standing still this long in the pit lane = a pit stop
TRAFFIC_LAP_SHARE = 0.10      # share of the lap closer than 1 s behind a car = traffic lap
BLUE_FLAG_LAP_SHARE = 0.05
MIN_CLEAN_FOR_PIT_LOSS = 3
TYRE_JUMP_PCT = 2.0           # remaining tread up by 2 points from one lap to the next: new tyres
REFUEL_JUMP_L = 1.0           # fuel level up by 1 l from one lap to the next: refuelled
RACE_SESSIONS = range(10, 14)  # LMU mSession 10..13 = race

NOT_CLEAN = ("invalid", "outlap", "inlap", "pit_stop", "traffic", "blue_flag", "race_start", "outlier")
FLAGS = ("invalid", "outlap", "inlap", "pit_stop", "traffic", "blue_flag", "race_start", "wet", "damage",
         "gap_before", "outlier")


def _num(value) -> Optional[float]:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _mean(values: Iterable[Optional[float]], digits: int = 2) -> Optional[float]:
    values = [v for v in values if v is not None]
    return round(sum(values) / len(values), digits) if values else None


def _median(values: Iterable[Optional[float]], digits: int = 3) -> Optional[float]:
    values = [v for v in values if v is not None]
    return round(statistics.median(values), digits) if values else None


def _compound(context: dict) -> Optional[str]:
    """A readable compound label from LMU's names (or indices), front/rear when they differ."""
    front = context.get("compound_front_name") or (
        f"#{context['compound_front']}" if isinstance(context.get("compound_front"), int) else None)
    rear = context.get("compound_rear_name") or (
        f"#{context['compound_rear']}" if isinstance(context.get("compound_rear"), int) else None)
    if front is None and rear is None:
        return None
    return front if front == rear or rear is None else (rear if front is None else f"{front} / {rear}")


def lap_row(lap: dict) -> dict:
    """The figures of one lap the Stint Analyzer shows (lap: id, lap_number, lap_time, is_valid,
    recorded_at, session_type, sector_times, track_temp, stats, context)."""
    stats = lap.get("stats") if isinstance(lap.get("stats"), dict) else {}
    context = lap.get("context") if isinstance(lap.get("context"), dict) else {}
    pit = stats.get("pit") if isinstance(stats.get("pit"), dict) else None
    traffic = stats.get("traffic") if isinstance(stats.get("traffic"), dict) else {}
    wheels = stats.get("wheels") if isinstance(stats.get("wheels"), dict) else {}

    def wheel(key):
        return {w: _num((wheels.get(w) or {}).get(key)) for w in WHEELS if _num((wheels.get(w) or {}).get(key)) is not None}

    fuel_end, fuel_used = _num(stats.get("fuel_end_l")), _num(stats.get("fuel_used_l"))
    fuel_added = _num(stats.get("fuel_added_l")) or 0.0
    fuel_start = round(fuel_end + fuel_used - fuel_added, 2) if fuel_end is not None and fuel_used is not None else None
    wear_left = wheel("wear_left_pct")
    wear_used = {w: v for w, v in wheel("wear_used_pct").items() if v >= 0}
    damage = context.get("damage") if isinstance(context.get("damage"), dict) else None
    sectors = lap.get("sector_times")
    return {
        "id": lap["id"],
        "lap_number": lap.get("lap_number"),
        "lap_time": _num(lap.get("lap_time")),
        "is_valid": bool(lap.get("is_valid")),
        "invalid_reason": lap.get("invalid_reason") if isinstance(lap.get("invalid_reason"), list) else None,
        "recorded_at": lap.get("recorded_at"),
        "sector_times": [round(float(s), 3) for s in sectors] if isinstance(sectors, list) and len(sectors) == 3
        and all(_num(s) and s > 0 for s in sectors) else None,
        "track_temp": _num(lap.get("track_temp")),
        "condition": condition_class(stats),
        # pit lane (clients 0.9.4+; None = not recorded)
        "pit": {
            "lane_s": _num(pit.get("lane_s")) or 0.0,
            "stationary_s": _num(pit.get("stationary_s")) or 0.0,
            "starts_in_pits": bool(pit.get("starts_in_pits")),
            "ends_in_pits": bool(pit.get("ends_in_pits")),
        } if pit is not None else None,
        "pitstops": context.get("pitstops") if isinstance(context.get("pitstops"), int) else None,
        "place": context.get("place") if isinstance(context.get("place"), int) else traffic.get("place_end"),
        "compound": _compound(context),
        "fuel_start_l": fuel_start,
        "fuel_end_l": fuel_end,
        "fuel_used_l": fuel_used,
        "fuel_added_l": fuel_added or None,
        "fuel_sector_l": stats.get("fuel_sector_l") if isinstance(stats.get("fuel_sector_l"), list) else None,
        "fuel_capacity_l": _num(context.get("fuel_capacity_l")),
        "energy_used_pct": _num(stats.get("energy_used_pct")),
        "energy_end_pct": _num(stats.get("energy_end_pct")),
        "engine_map": stats.get("engine_map") if isinstance(stats.get("engine_map"), int) else None,
        "tyres_changed": stats.get("tyres_changed") if isinstance(stats.get("tyres_changed"), list) else None,
        "wear_left_pct": wear_left or None,
        "wear_left_avg": _mean(wear_left.values(), 1),
        "wear_used_avg": _mean(wear_used.values(), 3),
        "tyre_temp_c": wheel("temp_avg_c") or None,
        "tyre_temp_avg": _mean(wheel("temp_avg_c").values(), 1),
        "tyre_pressure_avg": _mean(wheel("pressure_avg_kpa").values(), 1),
        "brake_max_c": wheel("brake_max_c") or None,
        "traffic_ahead_share": _num(traffic.get("ahead_close_share")),
        "traffic_behind_share": _num(traffic.get("behind_close_share")),
        "blue_flag_share": _num(traffic.get("blue_flag_share")),
        "places_gained": traffic.get("places_gained"),
        "places_lost": traffic.get("places_lost"),
        "damage": damage,
        "session_left_s": _num(context.get("session_left_s")),
        "max_laps": context.get("max_laps") if isinstance(context.get("max_laps"), int) else None,
    }


def _flags(rows: List[dict], session_type: Optional[int]) -> None:
    previous = None
    for i, row in enumerate(rows):
        flags = []
        pit = row["pit"]
        if not row["is_valid"]:
            flags.append("invalid")
        if pit and pit["starts_in_pits"]:
            flags.append("outlap")
        if pit and pit["ends_in_pits"]:
            flags.append("inlap")
        if pit and pit["stationary_s"] >= STATIONARY_STOP_S:
            flags.append("pit_stop")
        if (row["traffic_ahead_share"] or 0) >= TRAFFIC_LAP_SHARE:
            flags.append("traffic")
        if (row["blue_flag_share"] or 0) >= BLUE_FLAG_LAP_SHARE:
            flags.append("blue_flag")
        if session_type in RACE_SESSIONS and i == 0 and (row["lap_number"] or 0) <= 1:
            flags.append("race_start")
        if row["condition"] in ("wet", "mixed"):
            flags.append("wet")
        dent = (row["damage"] or {}).get("dent_max")
        before = ((previous or {}).get("damage") or {}).get("dent_max")
        if dent is not None and before is not None and dent > before:
            flags.append("damage")
        if previous is not None and isinstance(row["lap_number"], int) and isinstance(previous["lap_number"], int) \
                and row["lap_number"] - previous["lap_number"] > 1:
            flags.append("gap_before")
        row["flags"] = flags
        previous = row
    # outliers: slower than 107 % of the median of the laps that are clean otherwise
    candidates = [r["lap_time"] for r in rows if r["lap_time"] and not any(f in NOT_CLEAN for f in r["flags"])]
    if candidates:
        limit = statistics.median(candidates) * OUTLIER_FRACTION
        for row in rows:
            if row["lap_time"] and row["lap_time"] > limit and "outlier" not in row["flags"]:
                row["flags"].append("outlier")
    for row in rows:
        row["clean"] = bool(row["lap_time"]) and not any(f in NOT_CLEAN for f in row["flags"])


def _pit_visits(rows: List[dict]) -> List[List[int]]:
    """Laps that belong to one pass through the pit lane: a lap that ends in the pit lane and
    the next one that starts there are the same visit. Indices into rows."""
    visits: List[List[int]] = []
    for i, row in enumerate(rows):
        pit = row["pit"]
        if not pit or pit["lane_s"] <= 0:
            continue
        if i == 0 and pit["starts_in_pits"] and not pit["ends_in_pits"] and pit["stationary_s"] < STATIONARY_STOP_S:
            continue   # the session's first lap out of the garage: not a stop
        if visits and visits[-1][-1] == i - 1 and rows[i - 1]["pit"]["ends_in_pits"] and pit["starts_in_pits"]:
            visits[-1].append(i)
        else:
            visits.append([i])
    return visits


def _changed_between(before: dict, after: dict) -> dict:
    """What changed from one lap to the next, measured from the stored values."""
    out = {}
    if before["compound"] and after["compound"] and before["compound"] != after["compound"]:
        out["compound"] = True
    # the tread left at the end of each lap: rising by TYRE_JUMP_PCT means a new tyre
    wb, wa = before["wear_left_pct"] or {}, after["wear_left_pct"] or {}
    if any(wa[w] - wb[w] >= TYRE_JUMP_PCT for w in wb if w in wa):
        out["tyres"] = True
    if before["fuel_end_l"] is not None and after["fuel_start_l"] is not None \
            and after["fuel_start_l"] - before["fuel_end_l"] >= REFUEL_JUMP_L:
        out["refuel"] = True
    if isinstance(before["pitstops"], int) and isinstance(after["pitstops"], int) and after["pitstops"] > before["pitstops"]:
        out["pit_counter"] = True
    return out


def analyse_session(laps: Sequence[dict], session_type: Optional[int] = None) -> dict:
    """laps: one session of one driver, in the order driven (see lap_row for the keys).
    Returns {"laps", "stints", "pit_stops", "tyre_sets", "reference_lap_time", "summary"}."""
    rows = [lap_row(lap) for lap in laps]
    if not rows:
        return {"laps": [], "stints": [], "pit_stops": [], "tyre_sets": [], "reference_lap_time": None, "summary": {}}
    _flags(rows, session_type)
    clean_times = [r["lap_time"] for r in rows if r["clean"]]
    reference = round(statistics.median(clean_times), 3) if len(clean_times) >= MIN_CLEAN_FOR_PIT_LOSS else None

    # --- pit stops (from pit lane data) -----------------------------------------------------
    starts = {0: ["session_start"]}
    pit_stops = []
    for visit in _pit_visits(rows):
        first, last = visit[0], visit[-1]
        laps_in = [rows[i] for i in visit]
        stationary = sum(r["pit"]["stationary_s"] for r in laps_in)
        new_from = last if (len(visit) > 1 or (rows[last]["pit"]["starts_in_pits"] and last > 0)) else last + 1
        if len(visit) == 1 and rows[last]["pit"]["starts_in_pits"] and not rows[last]["pit"]["ends_in_pits"]:
            new_from = last   # an out-lap whose in-lap was not recorded
        before = rows[first - 1] if first > 0 else None
        after = rows[new_from] if new_from < len(rows) else None
        changed = _changed_between(before, after) if before and after else {}
        fuel_added = sum(r["fuel_added_l"] or 0.0 for r in laps_in) or None
        tyres_measured = any(r["tyres_changed"] for r in laps_in)
        stop = stationary >= STATIONARY_STOP_S or changed.get("pit_counter") or fuel_added or tyres_measured
        loss = None
        if reference is not None and all(r["lap_time"] for r in laps_in):
            loss = round(sum(r["lap_time"] for r in laps_in) - reference * len(laps_in), 2)
        pit_stops.append({
            "kind": "stop" if stop else "drive_through",
            "lap_ids": [r["id"] for r in laps_in],
            "lap_number": laps_in[0]["lap_number"],
            "lane_s": round(sum(r["pit"]["lane_s"] for r in laps_in), 1),
            "stationary_s": round(stationary, 1),
            "fuel_added_l": round(fuel_added, 1) if fuel_added else None,
            "tyres_changed": True if (tyres_measured or changed.get("tyres") or changed.get("compound")) else (
                False if before and before["wear_left_pct"] and after and after["wear_left_pct"] else None),
            "compound_before": before["compound"] if before else None,
            "compound_after": after["compound"] if after else None,
            "loss_s": loss,
            "loss_estimated": True,
            "next_lap_id": rows[new_from]["id"] if stop and new_from < len(rows) else None,
        })
        if stop and new_from < len(rows):
            starts.setdefault(new_from, []).append("pit")

    # --- fallback for laps without pit lane data: measured changes between laps --------------
    for i in range(1, len(rows)):
        changed = _changed_between(rows[i - 1], rows[i])
        reasons = [k for k in ("pit_counter", "tyres", "refuel", "compound") if changed.get(k)]
        if reasons and not any(j in starts for j in (i - 1, i, i + 1) if j > 0):
            starts[i] = reasons
        elif reasons and i in starts:
            starts[i] = list(dict.fromkeys(starts[i] + reasons))

    # --- stints ------------------------------------------------------------------------------
    bounds = sorted(starts)
    stints = []
    for n, begin in enumerate(bounds):
        end = (bounds[n + 1] if n + 1 < len(bounds) else len(rows)) - 1
        members = rows[begin:end + 1]
        for r in members:
            r["stint"] = n + 1
        clean = [r for r in members if r["clean"]]
        stints.append({
            "number": n + 1,
            "reasons": starts[begin],
            "lap_ids": [r["id"] for r in members],
            "first_lap": members[0]["lap_number"],
            "last_lap": members[-1]["lap_number"],
            "laps": len(members),
            "clean_laps": len(clean),
            "compound": next((r["compound"] for r in members if r["compound"]), None),
            "best": round(min(r["lap_time"] for r in clean), 3) if clean else None,
            "median": _median(r["lap_time"] for r in clean),
            "fuel_per_lap_l": _median((r["fuel_used_l"] for r in clean), 2),
            "energy_per_lap_pct": _median((r["energy_used_pct"] for r in clean), 2),
            "wear_per_lap_pct": _median((r["wear_used_avg"] for r in clean), 3),
            "wear_left_start": members[0]["wear_left_avg"],
            "wear_left_end": members[-1]["wear_left_avg"],
            "fuel_start_l": members[0]["fuel_start_l"],
            "fuel_end_l": members[-1]["fuel_end_l"],
            "duration_s": round(sum(r["lap_time"] or 0 for r in members), 1),
        })

    # --- tyre sets: a new set when tyres were changed, its age in laps ------------------------
    tyre_sets = []
    for n, stint in enumerate(stints):
        first_id = stint["lap_ids"][0]
        fresh = "tyres" in stint["reasons"] or "compound" in stint["reasons"] or any(
            stop["tyres_changed"] for stop in pit_stops if stop["next_lap_id"] == first_id)
        if n == 0 or fresh:
            tyre_sets.append({"number": len(tyre_sets) + 1, "compound": stint["compound"], "stints": [stint["number"]],
                              "wear_left_start": stint["wear_left_start"], "laps": 0,
                              "new_at_start": None if n == 0 else True})
        else:
            tyre_sets[-1]["stints"].append(stint["number"])
        stint["tyre_set"] = tyre_sets[-1]["number"]
        for row in rows:
            if row.get("stint") != stint["number"]:
                continue
            tyre_sets[-1]["laps"] += 1
            row["tyre_set"] = tyre_sets[-1]["number"]
            row["tyre_age"] = tyre_sets[-1]["laps"]
    for tyre_set in tyre_sets:
        members = [r for r in rows if r.get("tyre_set") == tyre_set["number"]]
        tyre_set["wear_left_end"] = members[-1]["wear_left_avg"] if members else None

    last = rows[-1]
    summary = {
        "laps": len(rows),
        "clean_laps": len(clean_times),
        "valid_laps": sum(1 for r in rows if r["is_valid"]),
        "best": round(min(r["lap_time"] for r in rows if r["is_valid"] and r["lap_time"]), 3)
        if any(r["is_valid"] and r["lap_time"] for r in rows) else None,
        "median_clean": round(statistics.median(clean_times), 3) if clean_times else None,
        "fuel_per_lap_l": _median((r["fuel_used_l"] for r in rows if r["clean"]), 2),
        "energy_per_lap_pct": _median((r["energy_used_pct"] for r in rows if r["clean"]), 2),
        "fuel_capacity_l": next((r["fuel_capacity_l"] for r in reversed(rows) if r["fuel_capacity_l"]), None),
        "fuel_end_l": last["fuel_end_l"],
        "energy_end_pct": last["energy_end_pct"],
        "session_left_s": last["session_left_s"],
        "max_laps": last["max_laps"],
        "has_pit_data": any(r["pit"] is not None for r in rows),
        "has_context": any(r["pitstops"] is not None or r["compound"] for r in rows),
        "has_traffic": any(r["traffic_ahead_share"] is not None for r in rows),
    }
    return {"laps": rows, "stints": stints, "pit_stops": pit_stops, "tyre_sets": tyre_sets,
            "reference_lap_time": reference, "summary": summary}
