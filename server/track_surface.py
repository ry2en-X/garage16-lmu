"""server/track_surface.py — the track itself, seen from above (V0.9.5): asphalt between both
edges and the kerbs, rebuilt from what LMU reports while driving, for the background of the
racing-line map.

What LMU gives per sample (rFactor 2 fields, recorded by client 0.9+):
  * path_lateral  the car's lateral distance from LMU's (very approximate) centre path
  * track_edge    the distance of the track edge from that path ON THE CAR'S SIDE
  * surface_type  per wheel, 5 = kerb ("rumble strip")
plus the car's world position (pos_x, pos_z) — accurate to centimetres.

So one lap yields:
  * the centre path: position minus path_lateral along the car's left normal
  * ONE edge at a time (the side the car is on); the other side comes from other laps, or from
    the same lap on the other side of the track — and where no lap ever was, the width of the
    known side is mirrored and marked as an estimate
  * where a wheel touched a kerb, how far from the centre that was — many laps together give
    the kerbs' position and width

The sign of path_lateral (is "+" left or right?) is not documented. It is decided from the data
(V0.9.9.3): with the right sign the centre paths reconstructed from DIFFERENT laps lie on top of
each other (each lap's own line choice cancels out); with the wrong sign they scatter by twice the
line differences. On real LMU data (Le Mans, 12 laps) that is 0.23 m against 1.12 m — unmistakable.
The V0.9.5 test (the right sign gives the SMOOTHER centre path) is kept only as the fallback for a
single lap: on real data path_lateral changes so slowly over 2 m that both signs are equally smooth
(measured ratio 1.00-1.02 against the required 1.15) — that is why the surface never appeared.
Undecided -> no surface (never a guessed one), and the endpoint says why.

Everything is binned on lap_dist (the same track metre in every lap) and returned in the same
display coordinates as the lines (track_xy with the same mirror decision).
"""

from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

import numpy as np

from .telemetry_analysis import LapSeries, json_series, resample, track_xy

BIN_M = 4.0                    # the surface is described every 4 m
SAMPLE_M = 2.0                 # each lap is sampled every 2 m before binning
HALF_TRACK_WIDTH_M = 0.8       # wheel centre from the car's centre line (≈ half the track width of a prototype)
MIN_EDGE_M, MAX_EDGE_M = 2.0, 30.0   # plausible distance centre path -> edge
MIN_SIDE_M = 0.3               # |path_lateral| below this: the car straddles the path, side unclear
FILL_GAP_M = 300.0             # a missing edge is interpolated across at most this much track (between two measurements: not marked)
SIGN_MARGIN = 1.15             # fallback (one lap): the better sign must be this much smoother
CONSISTENCY_MARGIN = 2.0       # main test: the wrong sign must scatter the laps' centre paths at least twice as much
CONSISTENCY_MIN_SPREAD_M = 0.15  # below this for BOTH signs the laps are (nearly) the same line: the test cannot tell
CONSISTENCY_MIN_BINS = 50      # track bins driven by at least two laps
# why there is no surface (returned by the endpoint as "reason")
REASON_NO_DATA = "no_surface_data"        # no lap has path_lateral / track_edge (client < 0.9)
REASON_SIGN = "sign_undecided"            # the sign of path_lateral cannot be read from these laps
REASON_NO_EDGES = "no_edges"              # no plausible edge distance in any lap
KERB_PAD_M = 0.25              # half a tyre: a wheel centre on the kerb means the kerb reaches a bit further
SMOOTH_BINS = 5
SURFACE_COLUMNS_KEYS = ("edge_margin", "wheels_on_kerb", "speed")   # catalog channels whose columns we need
LEFT_WHEELS = ("fl", "rl")
RIGHT_WHEELS = ("fr", "rr")


def _has_columns(series: LapSeries) -> bool:
    raw = series.raw
    return all(c in raw for c in ("pos_x", "pos_z", "path_lateral", "track_edge"))


def _left_normals(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    tx, ty = np.gradient(x), np.gradient(y)
    norm = np.hypot(tx, ty)
    norm[norm == 0] = np.nan
    return np.vstack((-ty / norm, tx / norm))      # left of the direction of travel, Y-up screen


def _moving_mean(values: np.ndarray, window: int) -> np.ndarray:
    if window <= 1 or len(values) < window:
        return values
    ok = np.isfinite(values).astype(float)
    vals = np.nan_to_num(values)
    k = np.ones(window)
    pad = (window // 2, window - 1 - window // 2)
    num = np.convolve(np.pad(vals, pad, mode="edge"), k, mode="valid")
    den = np.convolve(np.pad(ok, pad, mode="edge"), k, mode="valid")
    with np.errstate(invalid="ignore", divide="ignore"):
        out = np.where(den > 0, num / den, np.nan)
    out[~np.isfinite(values)] = np.nan            # never invents a value where there was none
    return out


def _roughness(cx: np.ndarray, cy: np.ndarray) -> float:
    ddx, ddy = np.diff(cx, 2), np.diff(cy, 2)
    r = np.hypot(ddx, ddy)
    r = r[np.isfinite(r)]
    return float(np.median(r)) if r.size > 50 else float("nan")


def _sampled(series: LapSeries, mirror: bool) -> Optional[dict]:
    """One lap on a 2 m grid of its own lap_dist: position, left normal, path_lateral, edge, kerbs."""
    if not _has_columns(series) or series.length < 200:
        return None
    grid = np.arange(float(series.dist[0]), float(series.dist[-1]), SAMPLE_M)
    xy = track_xy(series, grid, mirror)
    if xy is None:
        return None
    x, y = xy
    n = _left_normals(x, y)
    raw = series.raw
    pl = resample(series, raw["path_lateral"].astype(float), grid, False)
    te = np.abs(resample(series, raw["track_edge"].astype(float), grid, False))
    kerb = {}
    for w in LEFT_WHEELS + RIGHT_WHEELS:
        col = f"surface_type_{w}"
        if col in raw:
            kerb[w] = resample(series, raw[col].astype(float), grid, True) == 5.0
    return {"d": grid, "x": x, "y": y, "nx": n[0], "ny": n[1], "pl": pl, "te": te, "kerb": kerb}


def _consistency(laps: Sequence[dict], sign: float) -> Optional[float]:
    """Median scatter (m) of the centre paths of several laps with this sign, per 4 m of track."""
    start = min(float(lap["d"][0]) for lap in laps)
    end = max(float(lap["d"][-1]) for lap in laps)
    count = int(np.floor((end - start) / BIN_M)) + 1
    cxs, cys = [], []
    for lap in laps:
        cx = lap["x"] - sign * lap["pl"] * lap["nx"]
        cy = lap["y"] - sign * lap["pl"] * lap["ny"]
        ok = np.isfinite(cx) & np.isfinite(cy)
        b = np.clip(np.round((lap["d"][ok] - start) / BIN_M).astype(int), 0, count - 1)
        n = np.bincount(b, minlength=count).astype(float)
        with np.errstate(invalid="ignore", divide="ignore"):
            cxs.append(np.where(n > 0, np.bincount(b, cx[ok], count) / n, np.nan))
            cys.append(np.where(n > 0, np.bincount(b, cy[ok], count) / n, np.nan))
    X, Y = np.array(cxs), np.array(cys)
    have = np.isfinite(X) & np.isfinite(Y)
    shared = have.sum(axis=0) >= 2
    if shared.sum() < CONSISTENCY_MIN_BINS:
        return None
    X, Y, have = X[:, shared], Y[:, shared], have[:, shared]
    with np.errstate(invalid="ignore"):
        mx, my = np.nanmean(X, axis=0), np.nanmean(Y, axis=0)
        dist = np.hypot(X - mx, Y - my)
        per_bin = np.nanmean(np.where(have, dist, np.nan), axis=0)
    per_bin = per_bin[np.isfinite(per_bin)]
    return float(np.median(per_bin)) if per_bin.size else None


def _sign_by_consistency(laps: Sequence[dict]) -> Optional[float]:
    if len(laps) < 2:
        return None
    plus, minus = _consistency(laps, 1.0), _consistency(laps, -1.0)
    if plus is None or minus is None or max(plus, minus) < CONSISTENCY_MIN_SPREAD_M:
        return None
    if plus * CONSISTENCY_MARGIN <= minus:
        return 1.0
    if minus * CONSISTENCY_MARGIN <= plus:
        return -1.0
    return None


def _sign_by_smoothness(laps: Sequence[dict]) -> Optional[float]:
    rough = {1.0: [], -1.0: []}
    for lap in laps:
        for s in (1.0, -1.0):
            cx = lap["x"] - s * lap["pl"] * lap["nx"]
            cy = lap["y"] - s * lap["pl"] * lap["ny"]
            r = _roughness(cx, cy)
            if np.isfinite(r):
                rough[s].append(r)
    if not rough[1.0] or not rough[-1.0]:
        return None
    plus, minus = float(np.median(rough[1.0])), float(np.median(rough[-1.0]))
    if plus * SIGN_MARGIN <= minus:
        return 1.0
    if minus * SIGN_MARGIN <= plus:
        return -1.0
    return None


def decide_sign(laps: Sequence[dict]) -> Optional[float]:
    """+1 if path_lateral > 0 means LEFT of the centre path, -1 if right, None if the data cannot tell.
    Several laps: their centre paths must agree (V0.9.9.3). Only when that cannot decide (one lap,
    or laps on exactly the same line): the smoothness test of V0.9.5."""
    sign = _sign_by_consistency(laps)
    return sign if sign is not None else _sign_by_smoothness(laps)


def _fill(values: np.ndarray, est: np.ndarray, length_bins: int) -> None:
    """Interpolates NaN gaps of at most `length_bins` bins (in place) between two measurements of the
    same edge. Not marked as estimated: a track's width changes slowly, and both ends are measured."""
    idx = np.flatnonzero(np.isfinite(values))
    if len(idx) < 2:
        return
    for a, b in zip(idx[:-1], idx[1:]):
        if 1 < b - a <= length_bins + 1:
            values[a + 1:b] = np.linspace(values[a], values[b], b - a + 1)[1:-1]


def build_surface(series_list: Sequence[LapSeries], mirror: bool) -> Optional[dict]:
    """The track surface from several laps of the SAME layout, or None (see build_surface_why)."""
    return build_surface_why(series_list, mirror)[0]


def build_surface_why(series_list: Sequence[LapSeries], mirror: bool) -> Tuple[Optional[dict], Optional[str]]:
    """(surface, None) or (None, reason) — REASON_NO_DATA, REASON_SIGN or REASON_NO_EDGES."""
    laps = [s for s in (_sampled(series, mirror) for series in series_list) if s is not None]
    if not laps:
        return None, REASON_NO_DATA
    sign = decide_sign(laps)
    if sign is None:
        return None, REASON_SIGN
    start = min(float(lap["d"][0]) for lap in laps)
    end = max(float(lap["d"][-1]) for lap in laps)
    count = int(np.floor((end - start) / BIN_M)) + 1
    cx_sum, cy_sum, c_n = np.zeros(count), np.zeros(count), np.zeros(count)
    left: List[List[float]] = [[] for _ in range(count)]
    right: List[List[float]] = [[] for _ in range(count)]
    kerb_left: List[List[float]] = [[] for _ in range(count)]
    kerb_right: List[List[float]] = [[] for _ in range(count)]
    for lap in laps:
        b = np.clip(np.round((lap["d"] - start) / BIN_M).astype(int), 0, count - 1)
        lat = sign * lap["pl"]                       # + = left of the centre path
        cx = lap["x"] - lat * lap["nx"]
        cy = lap["y"] - lat * lap["ny"]
        ok = np.isfinite(cx) & np.isfinite(cy)
        np.add.at(cx_sum, b[ok], cx[ok])
        np.add.at(cy_sum, b[ok], cy[ok])
        np.add.at(c_n, b[ok], 1.0)
        edge_ok = ok & np.isfinite(lap["te"]) & (lap["te"] >= MIN_EDGE_M) & (lap["te"] <= MAX_EDGE_M) & (np.abs(lat) >= MIN_SIDE_M)
        for i in np.flatnonzero(edge_ok & (lat > 0)):
            left[b[i]].append(float(lap["te"][i]))
        for i in np.flatnonzero(edge_ok & (lat < 0)):
            right[b[i]].append(float(lap["te"][i]))
        for wheels, offset in ((LEFT_WHEELS, HALF_TRACK_WIDTH_M), (RIGHT_WHEELS, -HALF_TRACK_WIDTH_M)):
            for w in wheels:
                on = lap["kerb"].get(w)
                if on is None:
                    continue
                for i in np.flatnonzero(on & ok):
                    o = float(lat[i] + offset)
                    (kerb_left if o > 0 else kerb_right)[b[i]].append(abs(o))
    with np.errstate(invalid="ignore", divide="ignore"):
        cx = np.where(c_n > 0, cx_sum / c_n, np.nan)
        cy = np.where(c_n > 0, cy_sum / c_n, np.nan)
    cx, cy = _moving_mean(cx, SMOOTH_BINS), _moving_mean(cy, SMOOTH_BINS)
    med = lambda lists: np.array([float(np.median(v)) if v else np.nan for v in lists])  # noqa: E731
    wl, wr = med(left), med(right)
    wl_est, wr_est = np.zeros(count, bool), np.zeros(count, bool)
    gap_bins = int(FILL_GAP_M / BIN_M)
    _fill(wl, wl_est, gap_bins)
    _fill(wr, wr_est, gap_bins)
    # a side no lap has ever been on: the other side's width, marked as an estimate
    only_r = ~np.isfinite(wl) & np.isfinite(wr)
    wl[only_r], wl_est[only_r] = wr[only_r], True
    only_l = ~np.isfinite(wr) & np.isfinite(wl)
    wr[only_l], wr_est[only_l] = wl[only_l], True
    wl, wr = _moving_mean(wl, SMOOTH_BINS), _moving_mean(wr, SMOOTH_BINS)
    if not np.isfinite(wl).any():
        return None, REASON_NO_EDGES
    n = _left_normals(cx, cy)

    def kerbs(lists, width):
        inner = np.full(count, np.nan)
        outer = np.full(count, np.nan)
        for i, hits in enumerate(lists):
            if not hits or not np.isfinite(width[i]):
                continue
            lo, hi = min(hits) - KERB_PAD_M, max(hits) + KERB_PAD_M
            if lo > width[i] + 3.0 or lo < 0.5:       # far outside the edge / mid-track: not a kerb we can place
                continue
            inner[i] = min(lo, width[i] - 0.3)
            outer[i] = max(hi, width[i])
        return inner, outer

    kl_in, kl_out = kerbs(kerb_left, wl)
    kr_in, kr_out = kerbs(kerb_right, wr)
    r = lambda v: json_series(v, 2)  # noqa: E731
    return {
        "bin": BIN_M, "sign": int(sign), "start": round(start, 2), "count": count, "laps": len(laps),
        "cx": r(cx), "cy": r(cy), "nx": json_series(n[0], 4), "ny": json_series(n[1], 4),
        "wl": r(wl), "wr": r(wr), "wl_est": [int(v) for v in wl_est], "wr_est": [int(v) for v in wr_est],
        "kl_in": r(kl_in), "kl_out": r(kl_out), "kr_in": r(kr_in), "kr_out": r(kr_out),
    }, None
