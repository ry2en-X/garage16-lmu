"""Value distributions (histograms weighted by time) and wheel events (lock-ups, wheel spin) of a lap."""

from __future__ import annotations

import math
from typing import List, Optional, Sequence, Tuple

import numpy as np

from server.analysis.distance_axis import distance_axis, fill_short_gaps  # noqa: F401  (re-exported)
from server.analysis.telemetry_analysis import LapSeries, channel_values, json_series
from server.analysis.telemetry_channels import (
    WHEEL_POSITIONS,
)

DAMPER_KNEE_MM_S = 25.0     # usual split between low- and high-speed damping


MAX_HIST_BINS = 80


MIN_HIST_BINS = 6


def sample_weights(t: np.ndarray) -> np.ndarray:
    """Seconds each sample stands for, so a histogram shows SHARE OF TIME (a stretch the
    game recorded more densely does not count more). Gaps are capped at 5 typical steps."""
    if len(t) < 2:
        return np.ones(len(t))
    dt = np.diff(t)
    positive = dt[dt > 0]
    typical = float(np.median(positive)) if positive.size else 1.0
    dt = np.clip(dt, 0.0, typical * 5.0)
    return np.append(dt, typical)


def _weighted_quantiles(values: np.ndarray, weights: np.ndarray, qs: Sequence[float]) -> List[float]:
    order = np.argsort(values)
    v, w = values[order], weights[order]
    cum = np.cumsum(w)
    if cum[-1] <= 0:
        return [float("nan")] * len(qs)
    cum /= cum[-1]
    return [float(v[min(int(np.searchsorted(cum, q, side="left")), len(v) - 1)]) for q in qs]


def histogram_edges(values_list: Sequence[Optional[np.ndarray]], *, discrete: bool, symmetric: bool,
                    bins: int, lo: Optional[float] = None, hi: Optional[float] = None) -> Optional[np.ndarray]:
    """Bin edges shared by both laps (so the two histograms are directly comparable)."""
    finite = [v[np.isfinite(v)] for v in values_list if v is not None]
    finite = [v for v in finite if v.size]
    if not finite:
        return None
    joined = np.concatenate(finite)
    if discrete:
        first = math.floor(float(joined.min())) if lo is None else math.floor(lo)
        last = math.ceil(float(joined.max())) if hi is None else math.ceil(hi)
        if last - first > MAX_HIST_BINS:
            last = first + MAX_HIST_BINS
        return np.arange(first - 0.5, last + 1.0, 1.0)
    bins = int(min(max(bins, MIN_HIST_BINS), MAX_HIST_BINS))
    if lo is None or hi is None:
        p_lo, p_hi = np.percentile(joined, [0.5, 99.5])
        if symmetric:
            reach = max(abs(float(p_lo)), abs(float(p_hi)))
            step = 10 ** math.floor(math.log10(reach)) if reach > 0 else 1.0
            reach = math.ceil(reach / step) * step if reach > 0 else 1.0
            p_lo, p_hi = -reach, reach
        lo = float(p_lo) if lo is None else lo
        hi = float(p_hi) if hi is None else hi
    if not (math.isfinite(lo) and math.isfinite(hi)) or hi <= lo:
        hi = lo + 1.0
    if symmetric and bins % 2:
        bins += 1   # an even count puts a bin edge exactly at 0 (bump / rebound split)
    return np.linspace(lo, hi, bins + 1)


def distribution(series: "LapSeries", key: str, edges: np.ndarray, knee: float = DAMPER_KNEE_MM_S) -> Optional[dict]:
    """Share of the lap's TIME per bin (percent), plus what lies outside the range and a few
    robust figures. Damper channels also get bump/rebound and low/high-speed shares."""
    values = channel_values(series, key)
    if values is None:
        return None
    weights = sample_weights(series.raw["t"])
    finite = np.isfinite(values)
    if not finite.any():
        return None
    v, w = values[finite], weights[finite]
    total = float(w.sum())
    if total <= 0:
        return None
    counts, _ = np.histogram(v, bins=edges, weights=w)
    p5, p50, p95 = _weighted_quantiles(v, w, (0.05, 0.5, 0.95))
    out = {
        "pct": json_series(100.0 * counts / total, 2),
        "below": round(100.0 * float(w[v < edges[0]].sum()) / total, 2),
        "above": round(100.0 * float(w[v > edges[-1]].sum()) / total, 2),
        "stats": {
            "mean": round(float(np.sum(v * w) / total), 3), "min": round(float(v.min()), 3),
            "max": round(float(v.max()), 3), "p5": round(p5, 3), "p50": round(p50, 3), "p95": round(p95, 3),
            "seconds": round(total, 2),
        },
    }
    if key.startswith("damper_vel_"):
        share = lambda mask: round(100.0 * float(w[mask].sum()) / total, 1)  # noqa: E731
        out["damper"] = {
            "bump": share(v > 0), "rebound": share(v < 0),
            "low_bump": share((v > 0) & (v <= knee)), "high_bump": share(v > knee),
            "low_rebound": share((v < 0) & (v >= -knee)), "high_rebound": share(v < -knee),
            "knee": knee,
        }
    return out


LOCK_SLIP_PCT = -15.0        # a wheel turning 15 % slower than the ground under it ...


LOCK_MIN_BRAKE = 0.10        # ... while braking ...


SPIN_SLIP_PCT = 15.0         # ... or 15 % faster while on the throttle


SPIN_MIN_THROTTLE = 0.30


EVENT_MIN_SECONDS = 0.10


EVENT_MERGE_SECONDS = 0.20


MAX_WHEEL_EVENTS = 40


WHEEL_EVENT_CHANNELS: Tuple[str, ...] = tuple(f"slip_ratio_{pos}" for pos in WHEEL_POSITIONS)


def _event_runs(mask: np.ndarray, t: np.ndarray) -> List[Tuple[int, int]]:
    if not mask.any():
        return []
    padded = np.concatenate(([False], mask, [False]))
    edges = np.flatnonzero(padded[1:] != padded[:-1])
    runs = list(zip(edges[::2].tolist(), edges[1::2].tolist()))
    merged: List[List[int]] = []
    for start, end in runs:
        if merged and t[start] - t[merged[-1][1] - 1] <= EVENT_MERGE_SECONDS:
            merged[-1][1] = end
        else:
            merged.append([start, end])
    return [(a, b) for a, b in merged if t[b - 1] - t[a] >= EVENT_MIN_SECONDS]


def wheel_events(series: "LapSeries") -> List[dict]:
    """[{"type": "lockup"|"wheelspin", "wheel", "at", "until", "t", "seconds", "peak"}],
    ordered along the lap. Needs the wheel-slip channels (clients 0.9.0+)."""
    from server.analysis.telemetry_channels import _slip_ratio

    raw, t, dist = series.raw, series.raw["t"], series.dist
    brake = np.nan_to_num(raw.get("brake", np.zeros(len(t))))
    throttle = np.nan_to_num(raw.get("throttle", np.zeros(len(t))))
    events: List[dict] = []
    for pos in WHEEL_POSITIONS:
        slip = _slip_ratio(raw, pos)
        if slip is None:
            continue
        finite = np.isfinite(slip)
        for kind, mask, pick in (
            ("lockup", finite & (slip <= LOCK_SLIP_PCT) & (brake >= LOCK_MIN_BRAKE), np.nanmin),
            ("wheelspin", finite & (slip >= SPIN_SLIP_PCT) & (throttle >= SPIN_MIN_THROTTLE), np.nanmax),
        ):
            for start, end in _event_runs(mask, t):
                events.append({
                    "type": kind, "wheel": pos, "at": round(float(dist[start]), 1), "until": round(float(dist[end - 1]), 1),
                    "t": round(float(t[start]), 2), "seconds": round(float(t[end - 1] - t[start]), 2),
                    "peak": round(float(pick(slip[start:end])), 1),
                })
    events.sort(key=lambda e: (e["at"], e["wheel"]))
    return events[:MAX_WHEEL_EVENTS]
