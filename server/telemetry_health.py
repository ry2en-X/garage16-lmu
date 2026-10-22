"""
telemetry_health.py — how trustworthy one lap's recording is (V0.9.7).

Before anybody suspects the analysis of an odd result, this answers whether the INPUT is sound:
which channels the file holds and which of them are empty, how many samples it has and how
regularly they come, whether time and distance only move forward, how well the distance axis is
anchored, whether speed and lap distance agree, whether the recorded duration fits the lap time,
implausible jumps, and how finely a braking point can be placed at all. The lap's validation
verdict is reported next to it (a valid lap can have a poor recording and vice versa — the two
are different questions).

Every check has a status (ok / warn / bad) and the measured numbers it is based on; the texts are
written by the page from those numbers. Thresholds are listed below with their reasoning. Nothing
here changes a lap or its validity — it is a diagnosis.

Cost: one read of the file (all columns, memory-mapped) and a few vectorised passes; the result
is cached by the file's content hash (routers/analysis.py).
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

import numpy as np

from .distance_axis import distance_axis
from .telemetry_analysis import (
    AnalysisError,
    TIME_RESTART_S,
    _logical_from_names,
    _open,
    read_raw,
    recording_level,
)
from .telemetry_channels import CATALOG
from .validation import MAX_LAP_DIST_REGRESSION_METERS, trailing_reset

HEALTH_VERSION = 1

CORE = ("t", "lap_dist", "speed", "throttle", "brake", "steering")
GAP_S = 0.25                 # no new frame for this long = samples missing (LMU sends ~50 per s)
GAP_BAD_S = 1.0
RATE_OK_HZ = 30.0            # frames per second: below, braking points get coarse
RATE_BAD_HZ = 10.0
ANCHOR_OK_HZ = 3.0           # lap_dist updates per second (LMU's scoring: ~5)
SPEED_RATIO_WARN = 0.02      # speed integral vs lap_dist over the lap
SPEED_RATIO_BAD = 0.05
DURATION_WARN_S = 0.5        # recorded duration vs official lap time
START_WARN_M = 50.0          # first sample this far after the line
MAX_ACCEL = 8.0 * 9.80665    # between two frames: more than 8 G is not a car
MAX_SPEED = 120.0            # m/s (432 km/h)
RESOLUTION_OK_M = 2.0        # metres between frames at speed (> 50 m/s)
RESOLUTION_BAD_M = 5.0
NAN_WARN = 0.0
NAN_BAD = 0.01

_ORDER = {"ok": 0, "warn": 1, "bad": 2}


def _check(key: str, status: str, **values) -> dict:
    return {"key": key, "status": status, "values": {k: _plain(v) for k, v in values.items()}}


def _plain(value):
    if isinstance(value, (np.floating, float)):
        return None if not np.isfinite(value) else round(float(value), 4)
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value


def _worst(statuses) -> str:
    return max(statuses, key=lambda s: _ORDER[s]) if statuses else "ok"


def file_health(path: Path, lap_time: Optional[float]) -> dict:
    """The file part of the health report (cacheable by content hash)."""
    parquet = _open(path)
    names = set(parquet.schema_arrow.names)
    offered = _logical_from_names(names)
    raw = read_raw(path, sorted(offered), parquet)
    checks: List[dict] = []

    # --- channels: what the file offers, and which of it is empty
    level = recording_level(offered)
    available = [k for k, c in CATALOG.items() if all(col in offered for col in c.needs)]
    missing = [k for k, c in CATALOG.items() if k not in available]
    empty = sorted(col for col, values in raw.items() if len(values) and not np.isfinite(values).any())
    # a column without a single value only matters when a channel is drawn from it (it would
    # stay blank); other empty columns (a field LMU did not fill in this session) are listed only
    blank = [k for k in available if any(col in empty for col in CATALOG[k].needs)]
    status = "ok" if level == "full" and not missing and not blank else "warn"
    checks.append(_check("channels", status, level=level, available=len(available) - len(blank), total=len(CATALOG),
                         missing=missing[:12], missing_count=len(missing), blank=blank[:12], blank_count=len(blank),
                         empty_columns=len(empty)))

    t = raw.get("t")
    lap_dist = raw.get("lap_dist")
    if t is None or lap_dist is None or len(t) < 10:
        checks.append(_check("samples", "bad", rows=0 if t is None else len(t)))
        return {"version": HEALTH_VERSION, "checks": checks}
    n = len(t)

    # --- NaN in the channels every analysis uses
    core = {c: raw[c] for c in CORE if c in raw}
    nan_share = {c: float(np.mean(~np.isfinite(v))) for c, v in core.items()}
    worst_nan = max(nan_share.values()) if nan_share else 0.0
    checks.append(_check("nan", "ok" if worst_nan <= NAN_WARN else ("warn" if worst_nan <= NAN_BAD else "bad"),
                         worst_share=worst_nan, by_channel={c: round(s, 4) for c, s in nan_share.items() if s > 0}))

    finite_t = t[np.isfinite(t)]
    dt = np.diff(finite_t)
    frames = int(np.sum(dt > 1e-9)) + 1
    duration = float(finite_t[-1] - finite_t[0]) if len(finite_t) > 1 else 0.0
    rate = frames / duration if duration > 0 else 0.0
    checks.append(_check("samples", "ok" if rate >= RATE_OK_HZ else ("warn" if rate >= RATE_BAD_HZ else "bad"),
                         rows=n, frames=frames, duration_s=duration, frame_rate_hz=rate,
                         duplicate_share=1.0 - frames / n if n else 0.0))

    # --- gaps (no new frame for a while)
    fresh_t = finite_t[np.concatenate(([True], dt > 1e-9))]
    fdt = np.diff(fresh_t)
    gaps = np.flatnonzero(fdt > GAP_S)
    longest = float(fdt.max()) if fdt.size else 0.0
    where = None
    if gaps.size:
        k = int(gaps[np.argmax(fdt[gaps])])
        at_t = fresh_t[k]
        idx = int(np.searchsorted(t, at_t))
        where = float(lap_dist[min(idx, n - 1)]) if np.isfinite(lap_dist[min(idx, n - 1)]) else None
    checks.append(_check("gaps", "ok" if not gaps.size else ("warn" if longest <= GAP_BAD_S else "bad"),
                         count=int(gaps.size), longest_s=longest if gaps.size else 0.0, at_m=where))

    # --- time only forward
    back = dt[dt < -1e-6]
    restart = bool(back.size and float(back.min()) < -TIME_RESTART_S)
    checks.append(_check("time", "bad" if restart else ("warn" if back.size else "ok"),
                         backward_steps=int(back.size), largest_back_s=float(-back.min()) if back.size else 0.0,
                         restart=restart))

    # --- distance only forward (the reset at the very end — the next lap's first metres — is allowed)
    tail = trailing_reset(lap_dist, t)
    body = lap_dist[: n - tail] if tail else lap_dist
    body = body[np.isfinite(body)]
    jumps = np.diff(body)
    backward = jumps[jumps < -MAX_LAP_DIST_REGRESSION_METERS]
    checks.append(_check("distance", "ok" if not backward.size else "bad",
                         backward_jumps=int(backward.size), largest_back_m=float(-backward.min()) if backward.size else 0.0,
                         tail_samples=int(tail), start_m=float(body[0]) if body.size else None,
                         end_m=float(body[-1]) if body.size else None))

    # --- the distance axis (V0.9.5): how well it is anchored
    speed = raw.get("speed")
    keep, dist, axis = distance_axis(lap_dist, t, speed)
    anchor_rate = axis["anchors"] / duration if duration > 0 else 0.0
    kept_share = axis["kept"] / n if n else 0.0
    checks.append(_check("axis", "ok" if anchor_rate >= ANCHOR_OK_HZ else "warn",
                         anchors=axis["anchors"], anchor_rate_hz=anchor_rate, kept=axis["kept"], kept_share=kept_share,
                         filled=axis["filled"], duplicates=axis["duplicates"]))

    # --- speed and lap distance tell the same story
    if speed is not None and int(keep.sum()) > 10:
        ts, vs = t[keep], speed[keep]
        ok = np.isfinite(vs)
        driven = float(np.sum(0.5 * (vs[ok][1:] + vs[ok][:-1]) * np.diff(ts[ok]))) if ok.sum() > 1 else 0.0
        span = float(dist[-1] - dist[0])
        ratio = driven / span if span > 0 else float("nan")
        off = abs(ratio - 1.0) if np.isfinite(ratio) else 1.0
        checks.append(_check("speed_distance", "ok" if off <= SPEED_RATIO_WARN else ("warn" if off <= SPEED_RATIO_BAD else "bad"),
                             ratio=ratio, by_speed_m=driven, by_lap_dist_m=span))

        # --- implausible values / jumps between frames
        fresh = np.concatenate(([True], np.diff(ts) > 1e-9))
        fv, ft = vs[fresh], ts[fresh]
        good = np.isfinite(fv)
        fv, ft = fv[good], ft[good]
        accel = np.abs(np.diff(fv)) / np.maximum(np.diff(ft), 1e-3)
        bad_accel = int(np.sum(accel > MAX_ACCEL))
        bad_speed = int(np.sum((speed[np.isfinite(speed)] > MAX_SPEED) | (speed[np.isfinite(speed)] < -1.0)))
        pedals = 0
        for c in ("throttle", "brake"):
            if c in raw:
                v = raw[c][np.isfinite(raw[c])]
                pedals += int(np.sum((v < -0.01) | (v > 1.01)))
        total = bad_accel + bad_speed + pedals
        checks.append(_check("jumps", "ok" if total == 0 else "warn", speed_jumps=bad_accel,
                             speed_out_of_range=bad_speed, pedal_out_of_range=pedals))

        # --- resolution: metres between two frames at speed — the precision of a braking point
        fast = fv[:-1] > 50.0
        spacing = (fv[:-1] * np.diff(ft))[fast]
        res = float(np.median(spacing)) if spacing.size else None
        if res is not None:
            checks.append(_check("resolution", "ok" if res <= RESOLUTION_OK_M else ("warn" if res <= RESOLUTION_BAD_M else "bad"),
                                 metres=res, worst_m=float(np.percentile(spacing, 99))))

    # --- recorded duration vs the official lap time, start at the line
    if lap_time and lap_time > 0 and int(keep.sum()) > 10:
        t_kept = t[keep]
        covered = float(np.interp(dist[-1], dist, t_kept) - t_kept[0])
        start_m = float(dist[0])
        diff = covered - float(lap_time)
        status = "ok" if abs(diff) <= DURATION_WARN_S and start_m <= START_WARN_M else "warn"
        checks.append(_check("duration", status, recorded_s=covered, lap_time_s=float(lap_time),
                             difference_s=diff, start_m=start_m))

    return {"version": HEALTH_VERSION, "checks": checks}


def validation_part(lap) -> dict:
    """The lap's verdict, from the database (not cached with the file part)."""
    validation = lap.validation if isinstance(lap.validation, dict) else {}
    reasons = lap.invalid_reason if isinstance(lap.invalid_reason, list) else []
    return {
        "is_valid": bool(lap.is_valid),
        "client_claimed_valid": bool(lap.client_claimed_valid),
        "reasons": reasons[:10],
        "review_status": lap.review_status,
        "track_limits": validation.get("status"),
        "client_version": lap.client_version,
    }


def report(file_part: dict, lap) -> dict:
    """Overall status = the worst data check (validity is shown next to it, not mixed in)."""
    statuses = [c["status"] for c in file_part.get("checks", [])]
    worst = _worst(statuses)
    overall = {"ok": "good", "warn": "limited", "bad": "unreliable"}[worst]
    return {**file_part, "status": overall, "validation": validation_part(lap)}


def safe_file_health(path: Path, lap_time: Optional[float]) -> dict:
    try:
        return file_health(path, lap_time)
    except AnalysisError as exc:
        return {"version": HEALTH_VERSION, "checks": [_check("readable", "bad", error=str(exc))]}
