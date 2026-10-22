"""
lap_decision.py — VALID, INVALID or REVIEW: the last step of the lap check (V0.9.9).

The pipeline (server/lap_check.py), each step a module of its own:

  1 input        schema, value ranges, required fields       schemas.LapMetadata, validation.py
  2 time axis    order, restarts, duplicates, gaps            validation.py, here (coverage)
  3 position     lap distance vs speed, jumps, acceleration   validation.py, here (motion)
  4 track limits LMU's flags and Garage16's measurements      track_limits.py
  5 decision     everything above -> one status               HERE
  6 record       verdict with reasons, places, versions,      laps.validation (+ audit log)
                 data quality, evidence

What this step adds to the track-limits verdict (as findings of tier "quality", all REVIEW):

  signals_missing        a signal the client version always records is missing / empty /
                         unreadable (EXPECTED_SIGNALS). Without this, deleting a column from the
                         file would make a lap look cleaner — the opposite must hold.
  insufficient_evidence  only one independent signal family could be checked (FAMILIES)
  coverage_gap           no new frame for > GAP_REVIEW_S inside the lap
  low_frame_rate         fewer than RATE_REVIEW_HZ frames per second
  position_jump          the lap distance advanced >= JUMP_MIN_M AND > JUMP_RATIO x what the speed
                         allows between two of LMU's distance updates
  speed_distance_mismatch distance from the speed trace vs LMU's lap distance over the whole lap
                         off by more than SPEED_RATIO_REVIEW (validation.py rejects > 15 %)
  implausible_acceleration |dv/dt| over ACCEL_WINDOW_S above ACCEL_REVIEW
  frozen_samples         >= FROZEN_MIN_S of new frames (time advancing) whose speed, revs, inputs
                         AND lap distance are bit-for-bit those of the frame before, at more than
                         FROZEN_MIN_SPEED (LMU refreshes the distance ~5 times a second) —
                         a real car's speed changes every physics frame; repeated frames with new
                         timestamps are how an excursion would be pasted over
  telemetry_reused       the same bytes were uploaded by another driver
  unsupported_inputs     stored check inputs written by a newer server

The final status: INVALID when the plausibility check or a rule of the verdict says so; REVIEW
when any finding is REVIEW; VALID only when every check could run and found nothing. A REVIEW lap
is stored and can be analysed, but it is not official (leaderboards, records) until an admin
clears it (server/official.py).

Evidence (`decision.evidence`): how many independent signal families were checked — strong (4–5),
moderate (2–3), weak (1), none. It is a COUNT, not a probability, and it is not calibrated.

Thresholds and where they come from
-----------------------------------
GAP_REVIEW_S, RATE_REVIEW_HZ and SPEED_RATIO_REVIEW are the "bad" limits of the data-quality check
(server/telemetry_health.py, V0.9.7) — no new numbers. JUMP_MIN_M / JUMP_RATIO and ACCEL_REVIEW
are physical bounds with a wide margin (50 m in one 0.2 s distance update = 900 km/h; 8 G over
0.1 s is beyond any braking or traction of an LMU car). None of them has been checked against a
labelled set of REAL laps yet (docs/QUALITY.md, "Tracklimits").
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

from . import track_limits as tl
from .telemetry_health import GAP_BAD_S, RATE_BAD_HZ, SPEED_RATIO_BAD
from .versioning import parse_version

DECISION_VERSION = 1

VALID, INVALID, REVIEW = tl.STATUS_VALID, tl.STATUS_INVALID, tl.STATUS_REVIEW

GAP_REVIEW_S = GAP_BAD_S                 # 1.0 s
RATE_REVIEW_HZ = RATE_BAD_HZ             # 10 frames per second
SPEED_RATIO_REVIEW = SPEED_RATIO_BAD     # 5 % over the lap
JUMP_MIN_M = 50.0
JUMP_RATIO = 2.0
ACCEL_WINDOW_S = 0.1
ACCEL_REVIEW = 8.0 * 9.80665
FROZEN_MIN_S = 0.5
FROZEN_MIN_SPEED = 5.0
FROZEN_COLUMNS = ("speed", "rpm", "throttle", "brake", "steering", "lap_dist")
MAX_EVENTS = 10

# The signals every recording of a client version contains (client/telemetry/channels.py).
EXPECTED_SIGNALS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("0.9.0", ("lap_invalidated", "track_limits_steps", "surface_type_fl", "surface_type_fr",
               "surface_type_rl", "surface_type_rr")),
    ("0.9.3", ("count_lap_flag", "num_penalties", "path_lateral", "track_edge")),
)
# A signal the verdict reported as implausible counts as missing only when it carries LMU's own
# verdict: those must never silently drop out. The geometric ones (surface types, track edge) may
# legitimately be unusable on a track; they then only lower the evidence.
LMU_VERDICT_SIGNALS = ("lap_invalidated", "count_lap_flag")

# independent families of track-limits evidence, from the verdict's `signals`
FAMILIES = {
    "lap_invalidated": "lmu_verdict", "count_lap_flag": "lmu_verdict",
    "track_limits_steps": "lmu_steps", "surface_type": "surface", "track_edge": "track_edge",
    "driven_distance": "distance",
}


def expected_signals(client_version: Optional[str]) -> List[str]:
    if not client_version:
        return []
    version = parse_version(str(client_version))
    out: List[str] = []
    for since, columns in EXPECTED_SIGNALS:
        if version >= parse_version(since):
            out.extend(columns)
    return out


def evidence_level(families: Sequence[str]) -> str:
    n = len(set(families))
    return "strong" if n >= 4 else "moderate" if n >= 2 else "weak" if n == 1 else "none"


def _finding(code: str, **kw) -> tl.Finding:
    return tl.Finding(code, tl.severity_of(code), "decision", **kw)


def _signal_findings(columns: Dict[str, np.ndarray], verdict: tl.Verdict, client_version: Optional[str]) -> List[tl.Finding]:
    expected = expected_signals(client_version)
    if not expected:
        return []
    unreliable = {f.source for f in verdict.findings if f.code == "signal_unreliable"}
    missing, empty, unreadable = [], [], []
    for column in expected:
        values = columns.get(column)
        if values is None:
            missing.append(column)
        elif not np.isfinite(np.asarray(values, dtype=float)).any():
            empty.append(column)
        elif column in LMU_VERDICT_SIGNALS and column in unreliable:
            unreadable.append(column)
    if not (missing or empty or unreadable):
        return []
    return [_finding("signals_missing", detail={
        "client_version": str(client_version)[:32], "missing": missing, "empty": empty, "unreadable": unreadable})]


def _body(columns: Dict[str, np.ndarray], tail: int) -> Optional[Tuple[np.ndarray, np.ndarray, Optional[np.ndarray]]]:
    t, d = columns.get("t"), columns.get("lap_dist")
    if t is None or d is None:
        return None
    t = np.asarray(t, dtype=float)
    d = np.asarray(d, dtype=float)
    v = columns.get("speed")
    v = np.asarray(v, dtype=float) if v is not None else None
    end = max(len(t) - max(int(tail or 0), 0), 0)
    if end < 3:
        return None
    return t[:end], d[:end], (v[:end] if v is not None else None)


def _fresh(t: np.ndarray) -> np.ndarray:
    """Rows that carry a new frame (the client polls faster than LMU sends: repeats share t)."""
    finite = np.isfinite(t)
    keep = finite.copy()
    idx = np.flatnonzero(finite)
    if idx.size > 1:
        keep[idx[1:]] = np.diff(t[idx]) > 1e-9
    return keep


def _quality(t: np.ndarray, d: np.ndarray, v: Optional[np.ndarray], quality: dict) -> List[tl.Finding]:
    out: List[tl.Finding] = []
    fresh = _fresh(t)
    ft, fd = t[fresh], d[fresh]
    if ft.size < 2:
        return [_finding("low_frame_rate", detail={"frames": int(ft.size)})]
    duration = float(ft[-1] - ft[0])
    rate = ft.size / duration if duration > 0 else 0.0
    gaps = np.diff(ft)
    k = int(np.argmax(gaps))
    quality.update({"frames": int(ft.size), "frame_rate_hz": round(rate, 2), "longest_gap_s": round(float(gaps[k]), 3)})
    if rate < RATE_REVIEW_HZ:
        out.append(_finding("low_frame_rate", detail={"frame_rate_hz": round(rate, 2)}))
    if float(gaps[k]) > GAP_REVIEW_S:
        at = fd[k] if np.isfinite(fd[k]) else None
        out.append(_finding("coverage_gap", at=at, t=float(ft[k]), detail={"seconds": round(float(gaps[k]), 2)}))
    return out


def _motion(t: np.ndarray, d: np.ndarray, v: Optional[np.ndarray], quality: dict) -> List[tl.Finding]:
    out: List[tl.Finding] = []
    if v is None:
        return out
    ok = np.isfinite(t) & np.isfinite(d)
    t, d, v = t[ok], d[ok], v[ok]
    if t.size < 2:
        return out
    trapz = getattr(np, "trapezoid", None) or np.trapz

    # --- lap distance against the speed trace, between LMU's distance updates
    anchors = np.concatenate(([0], np.flatnonzero(np.abs(np.diff(d)) > 1e-6) + 1))
    finite = np.isfinite(v)
    vc = np.where(finite, np.clip(v, 0, None), 0.0)
    cum = np.concatenate(([0.0], np.cumsum(0.5 * (vc[1:] + vc[:-1]) * np.diff(t))))
    bad = np.concatenate(([0], np.cumsum(~finite)))               # non-finite speeds in rows [0, i)
    a, b = anchors[:-1], anchors[1:]
    known = (bad[b + 1] - bad[a]) == 0                             # speed known in rows a..b (both included)
    advance = d[b] - d[a]
    expected = cum[b] - cum[a]
    hit = np.flatnonzero(known & (advance - expected >= JUMP_MIN_M) & (advance > JUMP_RATIO * expected))
    quality["position_jumps"] = int(hit.size)
    for i in hit[:MAX_EVENTS]:
        out.append(_finding("position_jump", at=float(d[a[i]]), t=float(t[a[i]]), until=float(d[b[i]]),
                            detail={"advance_m": round(float(advance[i]), 1), "by_speed_m": round(float(expected[i]), 1)}))

    # --- the whole lap
    span = float(d[-1] - d[0])
    if finite.sum() >= 2 and span > 0:
        driven = float(trapz(np.clip(v[finite], 0, None), t[finite]))
        ratio = driven / span
        quality["speed_distance_ratio"] = round(ratio, 4)
        if abs(ratio - 1.0) > SPEED_RATIO_REVIEW:
            out.append(_finding("speed_distance_mismatch", detail={"ratio": round(ratio, 4), "by_speed_m": round(driven, 0),
                                                                   "by_lap_dist_m": round(span, 0)}))

    # --- acceleration: every new frame against the first frame at least ACCEL_WINDOW_S later. The
    #     window spreads one wrong sample over 0.1 s (so a single bad value is REVIEW, not an
    #     arithmetic artefact), and since every sample is a window end, no sample can slip through.
    fresh = _fresh(t) & finite
    ft, fv, fdist = t[fresh], v[fresh], d[fresh]
    j = np.searchsorted(ft, ft + ACCEL_WINDOW_S, side="left")
    i = np.flatnonzero(j < ft.size)
    if i.size:
        j = j[i]
        accel = (fv[j] - fv[i]) / (ft[j] - ft[i])
        worst = int(np.argmax(np.abs(accel)))
        g = float(abs(accel[worst]) / 9.80665)
        quality["max_accel_g"] = round(g, 2)
        if abs(accel[worst]) > ACCEL_REVIEW:
            k = int(i[worst])
            out.append(_finding("implausible_acceleration", at=float(fdist[k]), t=float(ft[k]), detail={"g": round(g, 1)}))
    return out


def _frozen(columns: Dict[str, np.ndarray], tail: int, quality: dict) -> List[tl.Finding]:
    t = columns.get("t")
    speed = columns.get("speed")
    if t is None or speed is None:
        return []
    end = len(t) - min(max(int(tail or 0), 0), len(t))
    t = np.asarray(t, dtype=float)[:end]
    idx = np.flatnonzero(_fresh(t))
    if idx.size < 2:
        return []
    same = np.ones(idx.size - 1, dtype=bool)
    for name in FROZEN_COLUMNS:
        values = columns.get(name)
        if values is None:
            continue
        v = np.asarray(values, dtype=float)[:end][idx]
        same &= (v[1:] == v[:-1])
    fast = np.asarray(speed, dtype=float)[:end][idx][1:] > FROZEN_MIN_SPEED
    stuck = same & fast
    out: List[tl.Finding] = []
    longest = 0.0
    padded = np.concatenate(([False], stuck, [False]))
    edges = np.flatnonzero(padded[1:] != padded[:-1])
    lap_dist = columns.get("lap_dist")
    for start, stop in zip(edges[::2], edges[1::2]):
        seconds = float(t[idx[stop]] - t[idx[start]])
        longest = max(longest, seconds)
        if seconds >= FROZEN_MIN_S and len(out) < MAX_EVENTS:
            at = float(lap_dist[idx[start]]) if lap_dist is not None and np.isfinite(lap_dist[idx[start]]) else None
            out.append(_finding("frozen_samples", at=at, t=float(t[idx[start]]), detail={"seconds": round(seconds, 2)}))
    quality["longest_frozen_s"] = round(longest, 3)
    return out


def assess(columns: Dict[str, np.ndarray], verdict: tl.Verdict, *, client_version: Optional[str] = None,
           tail_samples: int = 0, plausible: bool = True, plausibility_reasons: Iterable[str] = (),
           reused_by_others: int = 0, inputs_version: Optional[int] = None,
           known_inputs_version: int = 1) -> tl.Verdict:
    """Adds the decision layer's findings to `verdict` (in place) and its record (`decision`).
    Returns the verdict."""
    findings: List[tl.Finding] = []
    quality: dict = {}
    findings += _signal_findings(columns, verdict, client_version)
    families = sorted({FAMILIES[s] for s in verdict.signals if s in FAMILIES})
    level = evidence_level(families)
    if level == "weak":
        findings.append(_finding("insufficient_evidence", detail={"families": families}))
    body = _body(columns, tail_samples) if columns else None
    if body is not None:
        t, d, v = body
        findings += _quality(t, d, v, quality)
        findings += _motion(t, d, v, quality)
        findings += _frozen(columns, tail_samples, quality)
    if reused_by_others:
        findings.append(_finding("telemetry_reused", detail={"other_drivers": int(reused_by_others)}))
    if isinstance(inputs_version, int) and inputs_version > known_inputs_version:
        findings.append(_finding("unsupported_inputs", detail={"version": inputs_version}))
    verdict.add(findings)
    status = INVALID if not plausible else verdict.status
    verdict.decision = {
        "version": DECISION_VERSION,
        "status": status,
        "evidence": {"level": level, "families": families,
                     "note": "number of independent signal families checked; not a probability"},
        "quality": quality,
        "sources": list(verdict.signals),
        "client_version": str(client_version)[:32] if client_version else None,
        "plausibility": {"ok": bool(plausible), "reasons": list(plausibility_reasons)[:10]},
        "validators": {"track_limits": tl.TRACK_LIMITS_VERSION, "decision": DECISION_VERSION},
    }
    return verdict
