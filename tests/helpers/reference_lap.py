"""
Reference laps with KNOWN values (V0.9.7) — the analysis is checked against a truth that does not
come from the analysis code.

How a reference lap is made
---------------------------
1. A car is driven in a simulation at 2 kHz (much finer than any recording): pedals follow a
   schedule (brake: 0 -> 90 % in 80 ms at the braking point, held, released over 0.6 s before the
   apex; a short coast; throttle 0 -> 100 % in 0.4 s), and the speed follows from them (braking up
   to 18 m/s², accelerating up to 6 m/s², drag, a top speed). This dense trajectory is the TRUTH:
   every braking point, minimum, throttle point, the time at every metre and the lap time are read
   from it directly (`truth_*` below) — plain interpolation on 2 kHz data, no analysis code.
2. The recording is then made the way LMU and the client make it: physics frames at 50 Hz (with
   a random phase, so nothing lines up with an event), the client polling at 60 Hz (so every 5th
   poll repeats a frame — same game time), and lap_dist from the SCORING block, refreshed every
   10th frame (5 Hz) and held in between — the case V0.9.5's distance axis is built for.
3. Faults can be injected on that recording (`inject`): duplicate polls, dropped stretches,
   NaN values, timestamp jitter, a single-sample spike, a session restart, a truncated file.

The tolerances the tests use are derived from the recording, not tuned to pass:
  * a pedal point is linearly interpolated between two samples: it can be off by at most the
    distance between those samples (speed × frame time) — `resolution_m(d)`;
  * the minimum speed is the lowest SAMPLE: at most (deceleration × frame time) above the truth;
  * times are interpolated along a smooth time(distance) curve: well below a millisecond.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

DENSE_HZ = 2000.0
FRAME_HZ = 50.0
POLL_HZ = 60.0
SCORING_EVERY = 10              # frames per lap_dist update (5 Hz)
VMAX = 260.0 / 3.6
BRAKE_DECEL = 18.0              # m/s² at 90 % brake
ACCEL = 6.0                     # m/s² at full throttle (minus drag)
DRAG = 0.5                      # m/s² coasting
BRAKE_RAMP_S, BRAKE_HOLD = 0.08, 0.9
RELEASE_S = 0.6
COAST_S = 0.15
THROTTLE_RAMP_S = 0.4

BRAKE_ON = 0.10      # the server's thresholds (server/telemetry_analysis.py), as fractions
THROTTLE_ON = 0.05
THROTTLE_HALF = 0.50
THROTTLE_FULL = 0.95


@dataclass
class Corner:
    brake_at: float      # metres: where the brake pedal starts to move
    release_kmh: float   # the brake is released (over RELEASE_S) once the car is this slow


@dataclass
class ReferenceLap:
    length: float
    corners: List[Corner]
    # dense truth
    t: np.ndarray = field(default=None, repr=False)
    d: np.ndarray = field(default=None, repr=False)
    v: np.ndarray = field(default=None, repr=False)
    brake: np.ndarray = field(default=None, repr=False)
    throttle: np.ndarray = field(default=None, repr=False)
    accel: np.ndarray = field(default=None, repr=False)

    @property
    def lap_time(self) -> float:
        return float(np.interp(self.length, self.d, self.t))

    # ------------------------------------------------------------- truth, from the dense data
    def time_at(self, dist) -> np.ndarray:
        return np.interp(dist, self.d, self.t)

    def speed_at(self, dist) -> np.ndarray:
        return np.interp(dist, self.d, self.v)

    def _first(self, mask: np.ndarray, start_index: int = 0) -> Optional[int]:
        idx = np.flatnonzero(mask[start_index:])
        return start_index + int(idx[0]) if len(idx) else None

    def _crossing_d(self, values: np.ndarray, i: int, level: float) -> float:
        """Distance where `values` crosses `level` between dense samples i-1 and i."""
        if i == 0:
            return float(self.d[0])
        v0, v1 = values[i - 1], values[i]
        f = (level - v0) / (v1 - v0) if v1 != v0 else 0.0
        return float(self.d[i - 1] + f * (self.d[i] - self.d[i - 1]))

    def truth_corner(self, start: float, end: float) -> Dict[str, Optional[float]]:
        """The true values of the corner whose stretch is start..end metres (as the server's
        definitions read them, measured on the dense trajectory)."""
        i0, i1 = np.searchsorted(self.d, start), np.searchsorted(self.d, end)
        seg = slice(i0, i1)
        k = i0 + int(np.argmin(self.v[seg]))
        out = {"apex": float(self.d[k]), "min": float(self.v[k] * 3.6),
               "time": float(self.time_at(end) - self.time_at(start))}
        ib = self._first(self.brake[i0:k + 1] >= BRAKE_ON)
        out["brake"] = self._crossing_d(self.brake, i0 + ib, BRAKE_ON) if ib is not None else None
        lowest = (i0 + ib if ib is not None else i0)
        lowest = lowest + int(np.argmin(self.throttle[lowest:k + 1]))
        on = self._first(self.throttle >= THROTTLE_ON, lowest)
        out["throttle_on"] = self._crossing_d(self.throttle, on, THROTTLE_ON) if on is not None and on < i1 else None
        half = self._first(self.throttle >= THROTTLE_HALF, on or lowest)
        out["throttle_50"] = self._crossing_d(self.throttle, half, THROTTLE_HALF) if half is not None and half < i1 else None
        full = self._first(self.throttle >= THROTTLE_FULL, k + 1)
        out["throttle"] = self._crossing_d(self.throttle, full, THROTTLE_FULL) if full is not None and full < i1 else None
        out["exit"] = float(self.speed_at(out["throttle"]) * 3.6) if out["throttle"] is not None else None
        return out

    def resolution_m(self, dist: float) -> float:
        """Distance between two recorded frames at this point (speed × frame time)."""
        return float(self.speed_at(dist) / FRAME_HZ)


def simulate(length: float, corners: Sequence[Corner]) -> ReferenceLap:
    """Drives the lap at DENSE_HZ. Starts at the line at top speed (a flying lap)."""
    dt = 1.0 / DENSE_HZ
    corners = sorted(corners, key=lambda c: c.brake_at)
    n_max = int(length / 20.0 * DENSE_HZ) + 10
    t = np.empty(n_max); d = np.empty(n_max); v = np.empty(n_max)
    br = np.empty(n_max); th = np.empty(n_max); ac = np.empty(n_max)
    ti, di, vi = 0.0, 0.0, VMAX
    phase, phase_t, c_idx = "straight", 0.0, 0
    brake_v = throttle_v = 0.0
    release_from = 0.0
    n = 0
    while di < length + 30.0 and n < n_max:
        # --- controls
        if phase == "straight":
            if c_idx < len(corners) and di >= corners[c_idx].brake_at:
                phase, phase_t = "brake", 0.0
            brake_v, throttle_v = 0.0, 1.0 if phase == "straight" else 0.0
        if phase == "brake":
            brake_v = min(BRAKE_HOLD, BRAKE_HOLD * phase_t / BRAKE_RAMP_S)
            throttle_v = 0.0
            if vi * 3.6 <= corners[c_idx].release_kmh and phase_t >= BRAKE_RAMP_S:
                phase, phase_t, release_from = "release", 0.0, brake_v
        if phase == "release":
            brake_v = max(0.0, release_from * (1.0 - phase_t / RELEASE_S))
            throttle_v = 0.0
            if brake_v <= 0.0:
                phase, phase_t = "coast", 0.0
        if phase == "coast":
            brake_v = throttle_v = 0.0
            if phase_t >= COAST_S:
                phase, phase_t = "throttle", 0.0
        if phase == "throttle":
            throttle_v = min(1.0, phase_t / THROTTLE_RAMP_S)
            brake_v = 0.0
            if throttle_v >= 1.0:
                phase, c_idx = "straight", c_idx + 1
        # --- physics
        a = -BRAKE_DECEL * brake_v / BRAKE_HOLD + ACCEL * throttle_v - DRAG
        if vi >= VMAX and a > 0:
            a = 0.0
        t[n], d[n], v[n], br[n], th[n], ac[n] = ti, di, vi, brake_v, throttle_v, a
        n += 1
        v_new = min(max(vi + a * dt, 1.0), VMAX)
        di += 0.5 * (vi + v_new) * dt
        vi = v_new
        ti += dt
        phase_t += dt
    return ReferenceLap(length=length, corners=list(corners), t=t[:n], d=d[:n], v=v[:n],
                        brake=br[:n], throttle=th[:n], accel=ac[:n])


def record(lap: ReferenceLap, *, phase: float = 0.37, scoring_every: int = SCORING_EVERY,
           poll_hz: Optional[float] = POLL_HZ) -> pd.DataFrame:
    """What the client writes: frames at FRAME_HZ (phase = fraction of a frame), polled at
    poll_hz (None: one row per frame), lap_dist refreshed every `scoring_every` frames."""
    frame_t = (np.arange(0, lap.lap_time * FRAME_HZ + 1) + phase) / FRAME_HZ
    frame_t = frame_t[frame_t <= lap.lap_time]
    fd = np.interp(frame_t, lap.t, lap.d)
    fields = {
        "speed": np.interp(frame_t, lap.t, lap.v),
        "brake": np.interp(frame_t, lap.t, lap.brake),
        "throttle": np.interp(frame_t, lap.t, lap.throttle),
        "accel": np.interp(frame_t, lap.t, lap.accel),
    }
    scoring = fd[(np.arange(len(fd)) // scoring_every) * scoring_every]
    if poll_hz:
        polls = np.arange(0, lap.lap_time, 1.0 / poll_hz)
        idx = np.clip(np.searchsorted(frame_t, polls, side="right") - 1, 0, len(frame_t) - 1)
        idx = idx[polls >= frame_t[0]]
    else:
        idx = np.arange(len(frame_t))
    n = len(idx)
    df = pd.DataFrame({
        "t": frame_t[idx],
        "lap_dist": scoring[idx],
        "speed": fields["speed"][idx],
        "throttle": fields["throttle"][idx],
        "brake": fields["brake"][idx],
        "clutch": np.zeros(n),
        "steering": np.zeros(n),
        "gear": np.where(fields["speed"][idx] > 50, 6.0, 3.0),
        "rpm": 6000.0 + 30.0 * fields["speed"][idx],
        "fuel": np.linspace(60.0, 57.5, n),
        "pos_x": np.zeros(n),
        "pos_y": np.zeros(n),
        "pos_z": fd[idx],
        "tire_pressures": [(180.0, 181.0, 182.0, 183.0)] * n,
        "tire_temps_outer": [(350.0, 351.0, 352.0, 353.0)] * n,
        "brake_temps": [(600.0, 610.0, 620.0, 630.0)] * n,
    })
    # local frame: +z rearward -> forward acceleration is -accel_z
    df["accel_z"] = (-fields["accel"][idx]).astype(np.float32)
    df["accel_x"] = np.zeros(n, dtype=np.float32)
    return df


def inject(df: pd.DataFrame, fault: str, *, at: float = 0.0, rng_seed: int = 1, **kw) -> pd.DataFrame:
    """Faults seen (or plausible) in real recordings. `at` is a lap distance in metres."""
    out = df.copy()
    rng = np.random.default_rng(rng_seed)
    near = int(np.searchsorted(out["pos_z"].to_numpy(), at))
    if fault == "drop":                 # a stretch of samples missing (client hiccup)
        seconds = kw.get("seconds", 0.3)
        t0 = out["t"].iloc[near]
        out = out[(out["t"] < t0) | (out["t"] > t0 + seconds)]
    elif fault == "nan":                # a few rows without a value
        cols = kw.get("columns", ["speed", "brake"])
        rows = out.index[near:near + kw.get("rows", 3)]
        out.loc[rows, cols] = np.nan
    elif fault == "jitter":             # timestamps with noise (still increasing)
        noise = rng.uniform(-kw.get("ms", 1.5), kw.get("ms", 1.5), len(out)) / 1000.0
        t = out["t"].to_numpy() + noise
        out["t"] = np.maximum.accumulate(t)
    elif fault == "spike":              # one sample with a nonsense speed
        out.iloc[near, out.columns.get_loc("speed")] = kw.get("value", 300.0)
    elif fault == "restart":            # the session restarts: time and distance start again
        tail = out.iloc[near:].copy()
        tail["t"] = tail["t"] - tail["t"].iloc[0]
        tail["lap_dist"] = tail["lap_dist"] - tail["lap_dist"].iloc[0]
        out = pd.concat([out.iloc[:near], tail], ignore_index=True)
    elif fault == "truncate":           # the file ends early
        out = out.iloc[:near]
    elif fault == "duplicates":         # every row polled twice
        out = pd.concat([out, out]).sort_values("t", kind="stable")
    else:
        raise ValueError(fault)
    return out.reset_index(drop=True)


# Two laps of the same 4.2 km track: B is the reference (brakes later in T1, carries more speed
# in T2, the same in T3).
TRACK_M = 4200.0
LAP_A = [Corner(812.37, 118.0), Corner(2033.9, 171.0), Corner(3301.15, 92.0)]
LAP_B = [Corner(824.61, 118.0), Corner(2041.2, 178.0), Corner(3301.15, 92.0)]
