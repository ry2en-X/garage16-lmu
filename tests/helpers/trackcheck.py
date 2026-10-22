"""
Track-limit reference laps with a KNOWN truth (V0.9.9) — tests/test_v099_trackcheck_reference.py
and scripts/trackcheck_eval.py.

The truth is defined by geometry, independently of Garage16's rules
--------------------------------------------------------------------
A track is a sequence of sections (straight / corner with a radius), each with its own half width
W (centre line to the white line), kerb width K beyond the line and what lies beyond the kerb
(grass or gravel). A car follows a lateral offset o(s) from the centre line; its wheels sit at
o ± h (h = half the wheel track, per car). LMU's surface type per wheel follows from that:
|x| <= W asphalt (0), W < |x| <= W + K kerb (5), beyond: grass (2) or gravel (4).

LABEL — the motorsport definition (FIA International Sporting Code, Appendix L, ch. IV: a car has
left the track when no part of it is in contact with the track; the white lines are the track
edge, kerbs beyond them are not track): **violation** when all four wheels are beyond the white
line (|x| > W for every wheel) for at least 0.25 s, otherwise **clean**. Touching the line counts
as on track. The 0.25 s is a property of the DATASET, not of Garage16's rules: every scenario is
built with either no excursion or one of >= 0.6 s, except the explicit boundary cases.

What the game reports (modes, because LMU's own verdict is not geometry — it scores time gained):
  strict    lap_invalidated rises 3 s after a violation, the step counter 2.5 s after it
  lenient   LMU adds steps but does not invalidate (it judged no time gained)
  silent    LMU reports nothing at all — only the geometry signals can show it
Recording like the client: physics frames at `frame_hz` (random phase), polls at `poll_hz` (repeats
share the frame's time), lap_dist from the scoring block every `scoring_every` frames,
path_lateral relative to LMU's APPROXIMATE path (centre line + a slowly varying bias), track_edge
= W.

Manipulations (`manipulate`) change the recording afterwards, the way a forged file would.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

G = 9.80665
VIOLATION_MIN_S = 0.25
DS = 0.5                    # metres between the dense samples of the truth


@dataclass(frozen=True)
class Section:
    length: float                 # metres along the centre line
    radius: Optional[float] = None    # None = straight; >0 left turn, <0 right turn
    half_width: float = 6.0
    kerb: float = 1.0
    beyond: float = 2.0           # LMU surface beyond the kerb: 2 grass, 4 gravel


@dataclass(frozen=True)
class Car:
    name: str
    half_track: float             # metres from the car's centre to a wheel's contact patch
    lat_g: float                  # max lateral acceleration in G
    accel: float                  # m/s²
    decel: float                  # m/s²
    vmax: float                   # m/s


LMP = Car("LMP/Hypercar", 0.95, 3.2, 7.0, 30.0, 92.0)
GT3 = Car("GT3", 0.82, 2.0, 5.0, 17.0, 78.0)
CARS = (LMP, GT3)

TRACKS: Dict[str, Tuple[Section, ...]] = {
    # a fast circuit: long straights, a fast sweeper, a medium corner, a chicane
    "fast": (Section(600), Section(320, 200.0, 7.0), Section(400), Section(160, -60.0, 6.0, 1.2, 4.0),
             Section(500), Section(40, 25.0, 5.5), Section(30), Section(40, -25.0, 5.5), Section(700),
             Section(280, 90.0, 6.5, 1.0, 2.0), Section(400)),
    # a tight street-like layout: a hairpin, short straights, narrow
    "tight": (Section(300, None, 5.0), Section(47, 15.0, 5.0, 0.8, 4.0), Section(250, None, 5.0),
              Section(120, -40.0, 5.0), Section(350, None, 5.5), Section(90, 30.0, 5.0, 1.5, 2.0),
              Section(200, None, 5.5), Section(110, -35.0, 5.0), Section(260, None, 5.5)),
    # mixed widths: wide run-offs with gravel, a narrow section with grass
    "mixed": (Section(500, None, 8.0), Section(200, -120.0, 8.0, 2.5, 4.0), Section(300, None, 6.0),
              Section(80, 30.0, 4.5, 0.6, 2.0), Section(450, None, 6.0), Section(150, -70.0, 7.0, 1.0, 4.0),
              Section(500, None, 7.0)),
}


@dataclass
class Excursion:
    """Where and how the car leaves its line: in section `section`, centred at `where` (0..1 of
    the section), over `length_m`; `depth` = how far the INNER-most wheel is beyond the white line
    (negative: still inside), `side` +1 outside of the corner / left on a straight, -1 inside."""
    section: int
    depth: float
    length_m: float = 150.0
    where: float = 0.8
    side: int = 1


@dataclass
class Scenario:
    name: str
    track: str = "fast"
    car: Car = LMP
    line: str = "clean"           # clean | kerbs (inner wheels on the kerb at every apex)
    excursions: Tuple[Excursion, ...] = ()
    lmu: str = "strict"           # strict | lenient | silent
    frame_hz: float = 50.0
    poll_hz: Optional[float] = 60.0
    scoring_every: int = 10
    noise: float = 0.0            # lateral noise (m, sd) and timestamp jitter (ms) scale
    drop: Optional[Tuple[float, float]] = None   # (lap distance m, seconds) of samples missing
    manipulations: Tuple[str, ...] = ()
    seed: int = 0
    client_version: str = "0.9.4"
    expected: str = "valid"       # what Garage16 must decide: valid | review | invalid
    why: str = ""                 # the independent reason for `expected`
    label: str = "clean"          # filled by build(): clean | violation (the geometric truth)

    def with_(self, **kw) -> "Scenario":
        return replace(self, **kw)


# ----------------------------------------------------------------------------- the truth

@dataclass
class Lap:
    s: np.ndarray                 # dense centre-line distance
    t: np.ndarray
    v: np.ndarray                 # progress along the centre line (m/s)
    v_car: np.ndarray             # the car's own speed: shorter on an inside line, longer outside
    offset: np.ndarray            # car centre, lateral from the centre line (+ left)
    half_width: np.ndarray
    kerb: np.ndarray
    beyond: np.ndarray
    curvature_sign: np.ndarray
    length: float
    label: str = "clean"
    violations: List[Tuple[float, float]] = field(default_factory=list)   # (t0, t1)

    @property
    def lap_time(self) -> float:
        return float(self.t[-1])


def _layout(sections: Sequence[Section]):
    s = np.arange(0.0, sum(sec.length for sec in sections), DS)
    bounds = np.cumsum([0.0] + [sec.length for sec in sections])
    idx = np.clip(np.searchsorted(bounds, s, side="right") - 1, 0, len(sections) - 1)
    radius = np.array([abs(sections[i].radius) if sections[i].radius else np.inf for i in idx])
    sign = np.array([np.sign(sections[i].radius) if sections[i].radius else 0.0 for i in idx])
    hw = np.array([sections[i].half_width for i in idx])
    kerb = np.array([sections[i].kerb for i in idx])
    beyond = np.array([sections[i].beyond for i in idx])
    frac = (s - bounds[idx]) / np.array([sections[i].length for i in idx])
    return s, bounds, idx, radius, sign, hw, kerb, beyond, frac


def _speed_profile(s, radius, car: Car) -> np.ndarray:
    limit = np.minimum(np.sqrt(car.lat_g * G * radius), car.vmax)
    v = limit.copy()
    for i in range(1, len(v)):                      # accelerate
        v[i] = min(v[i], np.sqrt(v[i - 1] ** 2 + 2 * car.accel * DS))
    for i in range(len(v) - 2, -1, -1):             # brake
        v[i] = min(v[i], np.sqrt(v[i + 1] ** 2 + 2 * car.decel * DS))
    for i in range(1, len(v)):                      # a flying lap: the loop closes
        v[i] = min(v[i], np.sqrt(v[i - 1] ** 2 + 2 * car.accel * DS))
    return v


def build(sc: Scenario) -> Lap:
    sections = TRACKS[sc.track]
    s, bounds, idx, radius, sign, hw, kerb, beyond, frac = _layout(sections)
    h = sc.car.half_track
    # the line: towards the inside of every corner, peaking at its middle
    corner = np.isfinite(radius)
    if sc.line == "clean":
        peak = hw - h - 0.4                          # every wheel on the asphalt
    elif sc.line == "kerbs":
        peak = hw - h + 0.5 * kerb                   # inner wheels on the kerb, outer ones on the track
    else:
        raise ValueError(sc.line)
    offset = np.where(corner, sign * peak * np.sin(np.pi * frac), 0.0)
    for ex in sc.excursions:
        sec = sections[ex.section]
        centre = bounds[ex.section] + ex.where * sec.length
        side = (np.sign(sec.radius) * -1 if sec.radius else 1) * ex.side   # +1 = outside of the corner
        depth_offset = side * (sec.half_width + ex.depth + h)               # centre so the inner wheel sits at depth
        u = (s - centre) / (ex.length_m / 2.0)
        ramp = np.clip(1.6 * (1.0 - np.abs(u)), 0.0, 1.0)                    # flat top, ramps of 0.375 * length
        bump = 0.5 - 0.5 * np.cos(np.pi * ramp)                               # ... smooth (no kink in the path)
        offset = offset * (1 - bump) + depth_offset * bump
    v = _speed_profile(s, radius, sc.car)
    t = np.concatenate(([0.0], np.cumsum(DS / (0.5 * (v[1:] + v[:-1])))))
    # the car's own speed: a path at lateral offset o around a centre-line curvature k is (1 - k o)
    # times as long (k > 0 left, o > 0 left), plus the lateral movement itself
    kappa = np.where(np.isfinite(radius), sign / np.where(np.isfinite(radius), radius, 1.0), 0.0)
    # real tracks ease into a corner (transition curves): curvature changes over ~30 m, not in a step
    width = int(30.0 / DS)
    kappa = np.convolve(np.pad(kappa, width, mode="wrap"), np.ones(width) / width, mode="same")[width:-width]
    slope = np.gradient(offset, DS)
    v_car = v * np.sqrt(np.clip(1.0 - kappa * offset, 0.3, 1.7) ** 2 + slope ** 2)
    lap = Lap(s=s, t=t, v=v, v_car=v_car, offset=offset, half_width=hw, kerb=kerb, beyond=beyond, curvature_sign=sign,
              length=float(s[-1]))
    # the geometric truth
    all_beyond = (np.abs(offset) - h) > hw          # inner wheel beyond the line (outer even further)
    lap.violations = _runs_t(all_beyond, t)
    lap.violations = [(a, b) for a, b in lap.violations if b - a >= VIOLATION_MIN_S]
    lap.label = "violation" if lap.violations else "clean"
    return lap


def _runs_t(mask: np.ndarray, t: np.ndarray) -> List[Tuple[float, float]]:
    padded = np.concatenate(([False], mask, [False]))
    edges = np.flatnonzero(padded[1:] != padded[:-1])
    return [(float(t[a]), float(t[min(b, len(t) - 1)])) for a, b in zip(edges[::2], edges[1::2])]


# --------------------------------------------------------------------------- the recording

def _surface(x: np.ndarray, hw: np.ndarray, kerb: np.ndarray, beyond: np.ndarray) -> np.ndarray:
    out = np.zeros_like(x)
    over = np.abs(x) - hw
    out[(over > 0) & (over <= kerb)] = 5.0
    out[over > kerb] = beyond[over > kerb]
    return out


def record(sc: Scenario, lap: Lap) -> pd.DataFrame:
    rng = np.random.default_rng(sc.seed)
    phase = rng.uniform(0, 1)
    frame_t = (np.arange(0, lap.lap_time * sc.frame_hz) + phase) / sc.frame_hz
    frame_t = frame_t[frame_t <= lap.lap_time]
    fs = np.interp(frame_t, lap.t, lap.s)
    fv = np.interp(frame_t, lap.t, lap.v_car)
    fo = np.interp(frame_t, lap.t, lap.offset)
    nearest = np.clip(np.searchsorted(lap.t, frame_t), 0, len(lap.t) - 1)   # section properties: no blending
    hw, kerb, beyond = lap.half_width[nearest], lap.kerb[nearest], lap.beyond[nearest]
    h = sc.car.half_track
    scoring = fs[(np.arange(len(fs)) // sc.scoring_every) * sc.scoring_every]
    if sc.poll_hz:
        polls = np.arange(frame_t[0], lap.lap_time, 1.0 / sc.poll_hz)
        idx = np.clip(np.searchsorted(frame_t, polls, side="right") - 1, 0, len(frame_t) - 1)
    else:
        idx = np.arange(len(frame_t))
    n = len(idx)
    left, right = fo[idx] + h, fo[idx] - h
    surf_left = _surface(left, hw[idx], kerb[idx], beyond[idx])
    surf_right = _surface(right, hw[idx], kerb[idx], beyond[idx])
    # LMU's path is approximate: a slowly varying bias of up to 0.4 m against the centre line
    bias = 0.4 * np.sin(fs[idx] / 400.0)
    lateral = fo[idx] - bias + (rng.normal(0, sc.noise * 0.15, n) if sc.noise else 0.0)
    t = frame_t[idx]
    if sc.noise:
        jitter = rng.uniform(-1.5, 1.5, n) * sc.noise / 1000.0
        fresh = np.concatenate(([True], np.diff(t) > 1e-9))
        t = t + np.where(fresh, jitter, 0.0)
        t = np.maximum.accumulate(t)
    speed = fv[idx] + (rng.normal(0, 0.02 * sc.noise, n) if sc.noise else 0.0)
    df = pd.DataFrame({
        "t": t, "lap_dist": scoring[idx], "speed": speed,
        "throttle": np.clip(np.gradient(fv[idx]) * 10 + 0.5, 0, 1), "brake": np.clip(-np.gradient(fv[idx]) * 10, 0, 1),
        "clutch": np.zeros(n), "steering": np.clip(np.interp(frame_t[idx], lap.t, lap.curvature_sign) * 0.3, -1, 1),
        "gear": np.where(fv[idx] > 50, 6.0, 4.0), "rpm": 6000.0 + 40.0 * fv[idx] + np.arange(n) % 7,
        "fuel": np.linspace(60.0, 58.0, n),
        "lap_invalidated": np.zeros(n), "track_limits_steps": np.zeros(n), "count_lap_flag": np.full(n, 2.0),
        "num_penalties": np.zeros(n), "path_lateral": lateral, "track_edge": hw[idx],
        "surface_type_fl": surf_left, "surface_type_rl": surf_left,
        "surface_type_fr": surf_right, "surface_type_rr": surf_right,
    })
    # what LMU itself reports after a violation
    for (_t0, t1) in lap.violations:
        if sc.lmu in ("strict", "lenient"):
            df.loc[df["t"] >= t1 + 2.5, "track_limits_steps"] += 1
        if sc.lmu == "strict":
            df.loc[df["t"] >= t1 + 3.0, "lap_invalidated"] = 1.0
    if sc.drop:
        at, seconds = sc.drop
        t0 = float(np.interp(at, lap.s, lap.t))
        df = df[(df["t"] < t0) | (df["t"] > t0 + seconds)].reset_index(drop=True)
    for m in sc.manipulations:
        df = manipulate(df, m, lap, rng)
    return df.reset_index(drop=True)


def manipulate(df: pd.DataFrame, kind: str, lap: Lap, rng) -> pd.DataFrame:
    out = df.copy()
    n = len(out)
    if kind == "strip_flags":              # LMU's own verdict removed from the file
        out = out.drop(columns=["lap_invalidated", "track_limits_steps", "count_lap_flag"])
    elif kind == "strip_all":              # every track-limits signal removed
        out = out.drop(columns=["lap_invalidated", "track_limits_steps", "count_lap_flag", "num_penalties",
                                "path_lateral", "track_edge", "surface_type_fl", "surface_type_fr",
                                "surface_type_rl", "surface_type_rr"])
    elif kind == "drop_093":               # a 0.9.0 client's file: no 0.9.3 scoring columns
        out = out.drop(columns=["count_lap_flag", "num_penalties", "path_lateral", "track_edge"])
    elif kind == "forge_flags":            # the game's verdict and the surfaces say "clean"
        out["lap_invalidated"] = 0.0
        out["track_limits_steps"] = 0.0
        for w in ("fl", "fr", "rl", "rr"):
            out[f"surface_type_{w}"] = 0.0
    elif kind == "forge_all":              # ... and the position too: a consistent forgery
        out = manipulate(out, "forge_flags", lap, rng)
        out["path_lateral"] = np.clip(out["path_lateral"], -3.0, 3.0)
    elif kind == "garbage_flags":          # values LMU never writes
        out["lap_invalidated"] = rng.uniform(2, 9, n)
    elif kind == "teleport":               # the lap distance jumps 300 m ahead at a third of the lap
        k = n // 3
        out.loc[k:, "lap_dist"] = out.loc[k:, "lap_dist"] + 300.0
        keep = out["lap_dist"] <= lap.length
        out = out[keep]
    elif kind == "time_compress":          # timestamps squeezed by 7 % (and the claimed lap time with them)
        out["t"] = out["t"] * 0.93
    elif kind == "speed_spike":            # three samples with an impossible speed jump (within 130 m/s)
        k = n // 2
        out.loc[k:k + 2, "speed"] = out.loc[k:k + 2, "speed"] + 40.0
    elif kind == "shuffle":                # two blocks of samples swapped
        k = n // 3
        a, b = out.iloc[k:k + 60].copy(), out.iloc[k + 60:k + 120].copy()
        out = pd.concat([out.iloc[:k], b, a, out.iloc[k + 120:]])
    elif kind == "duplicate_rows":         # every row twice (like a polled frame) — harmless
        out = pd.concat([out, out]).sort_values("t", kind="stable")
    elif kind == "truncate":               # the file ends early (the claimed lap time does not)
        out = out.iloc[: int(n * 0.8)]
    elif kind == "replay":                 # a stretch pasted again later: time goes on, distance back
        k = n // 2
        piece = out.iloc[k - 120:k].copy()
        piece["t"] = piece["t"] + (out["t"].iloc[k] - out["t"].iloc[k - 120])
        tail = out.iloc[k:].copy()
        tail["t"] = tail["t"] + (piece["t"].iloc[-1] - out["t"].iloc[k - 1])
        out = pd.concat([out.iloc[:k], piece, tail])
    elif kind == "freeze_excursion":       # the excursion pasted over with copies of the last clean frame
        beyond = (out[[f"surface_type_{w}" for w in ("fl", "fr", "rl", "rr")]].isin([2.0, 4.0]).any(axis=1)
                  | (out["path_lateral"].abs() > out["track_edge"]))
        if beyond.any():
            first = int(np.flatnonzero(beyond.to_numpy())[0])
            last = int(np.flatnonzero(beyond.to_numpy())[-1])
            a, b = max(first - 30, 0), min(last + 30, n - 1)
            frozen = out.iloc[a].drop(labels=["t"])
            for col in frozen.index:
                out.iloc[a:b + 1, out.columns.get_loc(col)] = frozen[col]
    else:
        raise ValueError(kind)
    return out.reset_index(drop=True)


def lap_time_of(df: pd.DataFrame, lap: Lap, sc: Scenario) -> float:
    """The lap time the client would claim: LMU's own time, except where a manipulation squeezes
    the clock (then the forged file claims the forged time, consistently)."""
    if "time_compress" in sc.manipulations:
        return lap.lap_time * 0.93
    if "teleport" in sc.manipulations:                   # the forged file ends earlier: it claims that
        return float(df["t"].iloc[-1] - df["t"].iloc[0])
    return lap.lap_time


def reference_distance(sc: Scenario) -> float:
    """The layout's usual driven distance (what the server learns from earlier laps): the clean
    line of the same car."""
    base = build(sc.with_(excursions=(), manipulations=(), line="clean"))
    trapz = getattr(np, "trapezoid", None) or np.trapz
    return float(trapz(base.v_car, base.t))


# ------------------------------------------------------------------------------- judging

def judge(sc: Scenario, *, with_reference: bool = True):
    """Runs the server's real lap check (server/lap_check.check_lap, without a database) on the
    scenario's recording. Returns (LapCheck, Lap, DataFrame)."""
    import tempfile
    from pathlib import Path
    from types import SimpleNamespace

    from server.lap_check import check_lap

    lap = build(sc)
    df = record(sc, lap)
    lap_time = lap_time_of(df, lap, sc)
    meta = SimpleNamespace(track_name=f"ref-{sc.track}", lap_time=lap_time, sector_times=[lap_time / 3] * 3,
                           sample_count=len(df), tail_samples=None, game_lap_state=None, lap_report=None,
                           track_grip_level=None, fixed_setup=None, track_limits_steps_per_point=None,
                           track_limits_steps_per_penalty=None, car_model=sc.car.name)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "lap.parquet"
        df.to_parquet(path, index=False)
        check = check_lap(None, path, meta, client_valid=True, client_version=sc.client_version,
                          reference=reference_distance(sc) if with_reference else None, promote=frozenset())
    return check, lap, df
