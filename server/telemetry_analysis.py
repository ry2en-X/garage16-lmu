"""
telemetry_analysis.py — turns stored lap files into what the analysis views
plot (V0.9.0). No HTTP, no database: pure functions over a parquet path, so
every step is unit-testable with a synthetic lap.

Pipeline for one lap
--------------------
  1. read only the parquet columns that are needed (a lap file holds ~190
     columns; a comparison of 8 channels reads about a tenth of them)
  2. expand the three per-wheel list columns of the core format
     (tire_pressures, tire_temps_outer, brake_temps) into per-wheel columns
  3. make the distance axis usable: lap_dist is only monotonic up to sensor
     jitter, so it is replaced by its running maximum and duplicate positions
     (standing car, stalled frames) are dropped. Interpolation needs a strictly
     increasing axis.
  4. calibrate the sign conventions from the lap itself
     (telemetry_channels.calibrate_orientation)
  5. compute each requested channel at the native sample rate, THEN resample to
     a common distance grid. Order matters: a derived channel such as a slip
     angle must be computed from the raw values and not from interpolated ones.

Comparing two laps puts both on the same grid, so graph position i means the
same metre of track for both, and the delta is simply a difference of times.

Delta convention: delta(d) = t_A(d) - t_B(d) - (the same difference at the first
grid point). Positive = lap A is BEHIND lap B at that point (it needed more
time to get there). At the end of the lap it is close to lap_time_A - lap_time_B.
"""

from __future__ import annotations

import math
import os
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from .telemetry_channels import (
    CATALOG,
    WHEEL_POSITIONS,
    Orientation,
    _decide,
    _smooth,
    calibrate_orientation,
    required_raw_columns,
)

# Core-format columns that hold one value per wheel as a list: name -> prefix of
# the per-wheel columns they expand into (FL, FR, RL, RR order).
NESTED_WHEEL_COLUMNS: Dict[str, str] = {
    "tire_pressures": "tire_pressure",
    "tire_temps_outer": "tire_temp_mean",
    "brake_temps": "brake_temp",
}

# Needed to decide the sign conventions; read whenever they exist.
CALIBRATION_COLUMNS: Tuple[str, ...] = (
    "t", "lap_dist", "speed", "steering", "accel_x", "accel_z", "ang_vel_y",
    "pos_x", "pos_z",  # track map + its mirror check
    "tire_load_fl", "tire_load_fr", "tire_load_rl", "tire_load_rr",
    "lat_ground_vel_fl", "lat_ground_vel_fr", "lat_ground_vel_rl", "lat_ground_vel_rr",
    "long_ground_vel_fl", "long_ground_vel_fr", "long_ground_vel_rl", "long_ground_vel_rr",
)

# Columns only a V0.9.0+ client writes. A file that has all of them was recorded
# with the full channel set; one that lacks them is a "basic" lap from an older
# client. Chosen from different families (suspension, tyre load, accelerations,
# driver aids) so a partial file is not mistaken for a full one.
FULL_RECORDING_MARKERS: Tuple[str, ...] = (
    "susp_deflection_fl", "susp_deflection_fr", "susp_deflection_rl", "susp_deflection_rr",
    "tire_load_fl", "tire_load_fr", "tire_load_rl", "tire_load_rr",
    "accel_x", "accel_z", "tc_level", "abs_level",
)

MIN_POINTS = 10
MAX_GRID_POINTS = 8000
DEFAULT_STEP_M = 2.0
MIN_STEP_M = 0.5
MAX_STEP_M = 20.0


class AnalysisError(Exception):
    """A lap file that cannot be analysed (unreadable, too short, no distance
    axis). The message is safe to show to the user."""


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

def logical_columns(path: Path) -> set:
    """The column names a lap file offers after wheel-list expansion."""
    try:
        names = set(pq.read_schema(path).names)
    except Exception as exc:  # noqa: BLE001 — any unreadable file is "not analysable"
        raise AnalysisError(f"Telemetry file could not be read: {exc}") from exc
    return _logical_from_names(names)


def _logical_from_names(names: set) -> set:
    out = set(names)
    for nested, prefix in NESTED_WHEEL_COLUMNS.items():
        if nested in names:
            out.discard(nested)
            out.update(f"{prefix}_{pos}" for pos in WHEEL_POSITIONS)
    return out


def recording_level(offered: set) -> str:
    """"full" if the file holds every extended-channel marker, else "basic"."""
    return "full" if all(marker in offered for marker in FULL_RECORDING_MARKERS) else "basic"


def _wheel_list_to_array(column: pa.ChunkedArray) -> Optional[np.ndarray]:
    """list<4 numbers> per row -> (rows, 4) float array; None if the shape is wrong."""
    array = column.combine_chunks()
    if len(array) == 0:
        return np.zeros((0, 4))
    if pa.types.is_fixed_size_list(array.type):
        flat = array.values
    else:
        if not np.all(pc.list_value_length(array).to_numpy(zero_copy_only=False) == 4):
            return None
        flat = pc.list_flatten(array)
    values = np.asarray(flat.to_numpy(zero_copy_only=False), dtype=float)
    if values.size != 4 * len(array):
        return None
    return values.reshape(-1, 4)


def _open(path: Path) -> "pq.ParquetFile":
    """Opens a lap file once, memory-mapped (the OS pages in only the column chunks that are read)."""
    try:
        return pq.ParquetFile(str(path), memory_map=True)
    except Exception as exc:  # noqa: BLE001
        raise AnalysisError(f"Telemetry file could not be read: {exc}") from exc


def read_raw(path: Path, wanted: Sequence[str], parquet: "Optional[pq.ParquetFile]" = None) -> Dict[str, np.ndarray]:
    """Reads the wanted logical columns that exist in the file (missing ones are
    simply absent from the result). Everything is returned as float64.
    `parquet`: the already opened file, to avoid opening it again (V0.9.1)."""
    pf = parquet if parquet is not None else _open(path)
    schema_names = set(pf.schema_arrow.names)

    physical: List[str] = []
    virtual: Dict[str, Tuple[str, int]] = {}
    for column in dict.fromkeys(wanted):
        if column in schema_names and column not in NESTED_WHEEL_COLUMNS:
            physical.append(column)
            continue
        for nested, prefix in NESTED_WHEEL_COLUMNS.items():
            if nested in schema_names and column.startswith(prefix + "_"):
                pos = column[len(prefix) + 1:]
                if pos in WHEEL_POSITIONS:
                    virtual[column] = (nested, WHEEL_POSITIONS.index(pos))

    to_read = sorted(set(physical) | {nested for nested, _ in virtual.values()})
    if not to_read:
        return {}
    try:
        table = pf.read(columns=to_read)
    except Exception as exc:  # noqa: BLE001
        raise AnalysisError(f"Telemetry file could not be read: {exc}") from exc

    out: Dict[str, np.ndarray] = {}
    for column in physical:
        out[column] = np.asarray(table.column(column).to_numpy(zero_copy_only=False), dtype=float)
    expanded: Dict[str, np.ndarray] = {}
    for column, (nested, index) in virtual.items():
        if nested not in expanded:
            arr = _wheel_list_to_array(table.column(nested))
            if arr is None:
                continue
            expanded[nested] = arr
        out[column] = expanded[nested][:, index]
    return out


# ---------------------------------------------------------------------------
# One lap, prepared for analysis
# ---------------------------------------------------------------------------

@dataclass
class LapSeries:
    """A lap on a strictly increasing distance axis, plus its sign calibration."""

    dist: np.ndarray
    raw: Dict[str, np.ndarray]
    orientation: Orientation
    available: set = field(default_factory=set)  # logical columns the file offers

    @property
    def length(self) -> float:
        return float(self.dist[-1] - self.dist[0])


def _monotonic_axis(lap_dist: np.ndarray, t: np.ndarray) -> np.ndarray:
    """Boolean mask of the rows to keep so that distance is strictly increasing."""
    finite = np.isfinite(lap_dist) & np.isfinite(t)
    running = np.maximum.accumulate(np.where(finite, lap_dist, -np.inf))
    keep = finite.copy()
    # A row survives only if it advances the running maximum by a real amount.
    advances = np.concatenate(([True], np.diff(running) > 1e-6))
    keep &= advances
    keep &= running > -np.inf
    return keep


# Prepared laps, kept briefly (V0.9.1). Zooming in sends several requests for the same two laps
# and channels (different stretches); without this each one re-read and re-prepared both files.
# Keyed by path + size + mtime + the raw columns: lap files are content-addressed and never
# change in place, and a replaced file has a new key. Bounded by bytes; least recently used goes
# first. An optimisation only — a miss simply reads the file.
LAP_CACHE_MAX_BYTES = 32 * 1024 * 1024
_lap_cache: "OrderedDict[tuple, Tuple[LapSeries, int]]" = OrderedDict()
_lap_cache_bytes = 0
_lap_cache_lock = threading.Lock()


def _series_bytes(series: "LapSeries") -> int:
    return int(series.dist.nbytes + sum(v.nbytes for v in series.raw.values()))


def clear_lap_cache() -> None:
    global _lap_cache_bytes
    with _lap_cache_lock:
        _lap_cache.clear()
        _lap_cache_bytes = 0


def load_lap(path: Path, channel_keys: Sequence[str]) -> LapSeries:
    """Reads and prepares one lap for the given catalog channel keys."""
    global _lap_cache_bytes
    wanted = list(CALIBRATION_COLUMNS) + required_raw_columns(list(channel_keys))
    try:
        stat = os.stat(path)
        key = (str(path), stat.st_size, stat.st_mtime_ns, tuple(sorted(set(wanted))))
    except OSError:
        key = None
    if key is not None:
        with _lap_cache_lock:
            hit = _lap_cache.get(key)
            if hit is not None:
                _lap_cache.move_to_end(key)
                return hit[0]
    series = _load_lap_uncached(path, wanted)
    if key is not None:
        size = _series_bytes(series)
        if size <= LAP_CACHE_MAX_BYTES // 4:
            with _lap_cache_lock:
                if key not in _lap_cache:
                    _lap_cache[key] = (series, size)
                    _lap_cache_bytes += size
                while _lap_cache_bytes > LAP_CACHE_MAX_BYTES and _lap_cache:
                    _, (_, old) = _lap_cache.popitem(last=False)
                    _lap_cache_bytes -= old
    return series


def _load_lap_uncached(path: Path, wanted: Sequence[str]) -> LapSeries:
    parquet = _open(path)
    available = _logical_from_names(set(parquet.schema_arrow.names))
    if "lap_dist" not in available or "t" not in available:
        raise AnalysisError("Telemetry file has no distance/time axis.")

    raw = read_raw(path, wanted, parquet)
    if "lap_dist" not in raw or "t" not in raw or len(raw["t"]) < MIN_POINTS:
        raise AnalysisError("Telemetry file has too few samples.")

    keep = _monotonic_axis(raw["lap_dist"], raw["t"])
    if int(keep.sum()) < MIN_POINTS:
        raise AnalysisError("Telemetry file has no usable distance axis.")
    raw = {name: values[keep] for name, values in raw.items()}
    dist = np.maximum.accumulate(raw["lap_dist"])
    if float(dist[-1] - dist[0]) < 50.0:
        raise AnalysisError("Lap is too short to analyse.")
    t = raw["t"]
    if np.any(np.diff(t) < -1e-6):
        raise AnalysisError("Telemetry timestamps are not increasing.")

    return LapSeries(dist=dist, raw=raw, orientation=calibrate_orientation(raw), available=available)


# ---------------------------------------------------------------------------
# Resampling, channels, delta
# ---------------------------------------------------------------------------

def make_grid(lo: float, hi: float, step: float, max_points: int = MAX_GRID_POINTS) -> Tuple[np.ndarray, float]:
    """Uniform distance grid from lo to hi. The step is clamped to a sensible
    range and widened if it would exceed `max_points` (default MAX_GRID_POINTS);
    the step actually used is returned.

    `max_points` is the server-side downsampling knob (V0.9.1): the browser asks
    for roughly as many points as it can draw, the grid is widened to fit, and
    the raw data stays untouched in the file."""
    if not (math.isfinite(lo) and math.isfinite(hi)) or hi - lo < 10.0:
        raise AnalysisError("The two laps share no common stretch of track.")
    max_points = int(min(max(max_points, 50), MAX_GRID_POINTS))
    step = min(max(float(step), MIN_STEP_M), MAX_STEP_M)
    step = max(step, (hi - lo) / (max_points - 1))
    count = int(math.floor((hi - lo) / step)) + 1
    return lo + step * np.arange(count), step


def common_range(a: LapSeries, b: LapSeries) -> Tuple[float, float]:
    return max(float(a.dist[0]), float(b.dist[0])), min(float(a.dist[-1]), float(b.dist[-1]))


def resample(series: LapSeries, values: np.ndarray, grid: np.ndarray, discrete: bool) -> np.ndarray:
    """Values at the grid positions. Continuous channels are interpolated
    linearly; discrete ones (gear, driver-aid levels, flags) keep the last known
    value — a gear of 3.4 does not exist."""
    if discrete:
        idx = np.clip(np.searchsorted(series.dist, grid, side="right") - 1, 0, len(series.dist) - 1)
        return values[idx]
    return np.interp(grid, series.dist, values)


def channel_values(series: LapSeries, key: str) -> Optional[np.ndarray]:
    """A catalog channel at native sample rate, or None if this lap's file does
    not contain what it needs (e.g. a lap recorded before V0.9.0)."""
    channel = CATALOG[key]
    if not all(column in series.raw for column in channel.needs):
        return None
    with np.errstate(divide="ignore", invalid="ignore"):
        values = channel.compute(series.raw, series.orientation)
    # V0.9.3: a derived channel may decline a lap it cannot compute honestly (e.g. a wheel
    # slip without a single free-rolling stretch to estimate the tyre radius from)
    return None if values is None else np.asarray(values, dtype=float)


def time_at(series: LapSeries, grid: np.ndarray) -> np.ndarray:
    return np.interp(grid, series.dist, series.raw["t"])


def delta_series(a: LapSeries, b: LapSeries, grid: np.ndarray) -> np.ndarray:
    """See the module docstring for the convention."""
    raw_delta = time_at(a, grid) - time_at(b, grid)
    return raw_delta - raw_delta[0]


def sector_distances(series: LapSeries, sector_times: Sequence[float]) -> List[float]:
    """Track distance at which the lap crossed sector 1 and sector 2, from the
    sector times the game reported (S1, S2, S3 — see client/telemetry/parser.py).
    Empty when the sector data is missing or does not fit the lap."""
    if len(sector_times) < 2:
        return []
    s1, s2 = float(sector_times[0]), float(sector_times[1])
    if not (s1 > 0 and s2 > 0):
        return []
    t = series.raw["t"]
    out = []
    for boundary in (s1, s1 + s2):
        if boundary >= float(t[-1]):
            return []
        out.append(float(np.interp(boundary, t, series.dist)))
    return out


# ---------------------------------------------------------------------------
# Spikes the overview grid would hide (V0.9.1)
# ---------------------------------------------------------------------------

SPIKE_TOLERANCE = 0.02  # of the channel's span: smaller deviations are invisible in a chart anyway


def spikes(series: "LapSeries", values: np.ndarray, grid: np.ndarray, step: float, line: np.ndarray,
           span: float, decimals: int) -> Optional[dict]:
    """Grid cells in which the RAW samples reach further than the resampled line that is drawn.

    The overview is sampled at grid points (e.g. every 6 m on a 14 km lap, at ~60 Hz the car
    covers ~1.3 m per sample), so a brake tap, a kerb strike or a wheel spike between two grid
    points would be lost or shown too small. For every cell [g - step/2, g + step/2) this takes
    the min and max of the raw samples and returns only the cells where they deviate from the
    drawn value by more than SPIKE_TOLERANCE of the channel span — usually a handful, so the
    response stays small. None when the grid is as fine as the samples (nothing can hide) or
    nothing deviates. -> {"i": [grid indices], "lo": [...], "hi": [...]}
    """
    dist = series.dist
    n = len(grid)
    if n < 2 or len(dist) < 2 or not (span > 0):
        return None
    spacing = float(dist[-1] - dist[0]) / (len(dist) - 1)
    if step < 1.5 * spacing:
        return None
    starts = np.searchsorted(dist, grid - step / 2.0, side="left")
    ends = np.searchsorted(dist, grid + step / 2.0, side="left")
    cells = np.flatnonzero(ends > starts)
    if not len(cells):
        return None
    # Cells are contiguous, so each non-empty cell's samples run up to the next non-empty cell's
    # start; cutting the array at the last cell's end keeps samples beyond the grid out.
    vals = np.asarray(values, dtype=float)[: int(ends[cells[-1]])]
    with np.errstate(invalid="ignore"):
        hi = np.fmax.reduceat(vals, starts[cells])
        lo = np.fmin.reduceat(vals, starts[cells])
    drawn = line[cells]
    tolerance = span * SPIKE_TOLERANCE
    with np.errstate(invalid="ignore"):
        hit = np.isfinite(hi) & np.isfinite(lo) & np.isfinite(drawn) & (((hi - drawn) > tolerance) | ((drawn - lo) > tolerance))
    chosen = np.flatnonzero(hit)
    if not len(chosen):
        return None
    return {"i": cells[chosen].tolist(), "lo": json_series(lo[chosen], decimals), "hi": json_series(hi[chosen], decimals)}


def channel_span(values: Sequence[Optional[np.ndarray]], fixed_range: Optional[Tuple[float, float]]) -> float:
    if fixed_range:
        return float(fixed_range[1] - fixed_range[0])
    finite = [v[np.isfinite(v)] for v in values if v is not None]
    finite = [v for v in finite if len(v)]
    if not finite:
        return 0.0
    return float(max(v.max() for v in finite) - min(v.min() for v in finite))


# ---------------------------------------------------------------------------
# Corner analysis (V0.9.1)
# ---------------------------------------------------------------------------

CORNER_MIN_DROP_KMH = 10.0   # a corner = a speed minimum with at least this drop before AND after
CORNER_MIN_GAP_M = 120.0     # minima closer than this are one corner
CORNER_SMOOTH_M = 30.0       # speed is smoothed over this length to find corners (not to measure them)
BRAKE_ON_PCT = 10.0          # braking point: first sample with at least this much brake
THROTTLE_FULL_PCT = 95.0     # throttle point: first sample after the apex with at least this much throttle


# Biggest gain / loss over a corner-sized stretch (V0.9.1 review). The browser used to find them
# on the chart's grid, so the numbers moved by a millisecond or a few metres with the screen
# width; measured here on a fixed 0.5 m grid they are the same on every device.
# Same constants as web/js/analysis/insights.js FACT_WINDOW.
FACT_WINDOW_FRACTION, FACT_WINDOW_MIN_M, FACT_WINDOW_MAX_M = 1 / 40, 100.0, 400.0
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


# Kurvenphasen (V0.9.4): what each lap did in each phase of a corner, measured on the raw
# samples, and where in the corner the time was gained or lost.
BRAKE_RUN_GAP_S = 0.15       # a braking run may dip below BRAKE_ON_PCT this long and still be one run
PEDAL_ON_PCT = 5.0           # "on the throttle" / "still braking" (trail braking) threshold
THROTTLE_HALF_PCT = 50.0
TURN_IN_FRACTION = 0.25      # turn-in: steering reaches this share of the corner's peak steering
MIN_PEAK_STEER_PCT = 3.0     # a corner with less peak steering has no measurable turn-in (a kink)
EXIT_ACCEL_M = 100.0         # "acceleration after the apex" is the mean long G over this stretch
APEX_ZONE_FRACTION = 0.2     # half width of the apex phase: this share of turn-in -> apex ...
APEX_ZONE_MIN_M, APEX_ZONE_MAX_M = 10.0, 30.0   # ... within these limits
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
    return {"dist": series.dist, "t": series.raw["t"], "speed": speed,
            "brake": channel_values(series, "brake"), "throttle": channel_values(series, "throttle"),
            "steering": channel_values(series, "steering") if "steering" in series.raw else None,
            "long_g": channel_values(series, "long_g") if "accel_z" in series.raw else None,
            "brake_pressure": np.nanmax(np.vstack(pressures), axis=0) if pressures else None}


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
            out["brake"] = round(float(dist[ib0]), 1)
            out["brake_speed"] = round(float(speed[ib0]), 1)
            out["brake_end"] = round(float(dist[ib1]), 1)
            out["brake_len"] = round(float(dist[ib1] - dist[ib0]), 1)
            out["brake_max"] = _r(np.nanmax(brake[ib0:ib1 + 1]), 0)
            if pressure is not None:
                out["brake_pressure_max"] = _r(np.nanmax(pressure[ib0:ib1 + 1]), 0)
            if long_g is not None and ib1 > ib0:
                decel = -long_g[ib0:ib1 + 1]
                if np.isfinite(decel).any():
                    out["decel_peak"] = _r(np.nanmax(decel), 2)
                    out["decel_mean"] = _r(np.nanmean(decel), 2)

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
                    out["turn_in"] = round(float(dist[it]), 1)
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
                out["throttle_on"] = round(float(dist[ion]), 1)
                half = _first(throttle[ion:i1] >= THROTTLE_HALF_PCT)
                if half is not None:
                    out["throttle_50"] = round(float(dist[ion + half]), 1)
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
            exit_at = float(dist[ia + 1 + full[0]])
            out["throttle"] = round(exit_at, 1)
    if exit_at is None:
        exit_at = min(apex + 100.0, end)
    out["exit"] = round(float(np.interp(exit_at, dist, speed)), 1)

    # --- acceleration after the apex
    if long_g is not None:
        ie = int(np.searchsorted(dist, min(apex + EXIT_ACCEL_M, end), "right"))
        seg = long_g[ia:max(ie, ia + 1)]
        if seg.size and np.isfinite(seg).any():
            out["accel_exit"] = _r(np.nanmean(seg), 2)
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
    v = (np.interp(grid, a.dist, speed_a) + np.interp(grid, b.dist, speed_b)) / 2.0
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
    """The corners of ONE lap, compact, for storing with the lap (server/lap_insights.py) and
    comparing a corner across a driver's laps without opening their files (V0.9.4):
    [[apex m, braking point m | None, min speed km/h, full-throttle point m | None, exit km/h], ...].
    Same detection and the same measurements as corner_analysis, on this lap's own speed."""
    if len(dist) < 10 or float(dist[-1] - dist[0]) < 4 * CORNER_MIN_GAP_M:
        return []
    step = 2.0
    grid = np.arange(float(dist[0]), float(dist[-1]), step)
    v = np.interp(grid, dist, speed_kmh)
    found = _find_corners(grid, v, step)
    lap = {"dist": dist, "t": t, "speed": speed_kmh, "brake": brake_pct, "throttle": throttle_pct,
           "steering": None, "long_g": None, "brake_pressure": None}
    out = []
    for j, (apex, entry_index) in enumerate(found):
        entry = float(grid[entry_index])
        start = float(grid[0]) if j == 0 else entry
        end = float(grid[found[j + 1][1]]) if j + 1 < len(found) else float(grid[-1])
        m = _corner_lap(lap, start, entry, end)
        if m is not None:
            out.append([m["apex"], m["brake"], m["min"], m["throttle"], m["exit"]])
    return out


# ---------------------------------------------------------------------------
# Racing line: how far apart two laps drove (V0.9.4)
# ---------------------------------------------------------------------------

LINE_WINDOW_M = 50.0          # Lap 2's line is searched within this distance of the same lap metre
LINE_MAX_OFFSET_M = 15.0      # further apart than this is not "another line" (pit lane, off, spin)
LINE_CHUNK = 2000             # rows per vectorised block (bounds the memory of the search)
TURN_SMOOTH_M = 20.0
TURN_MIN_CURVATURE = 1.0 / 800.0   # radius above 800 m counts as straight


def line_offset(xa: np.ndarray, ya: np.ndarray, xb: np.ndarray, yb: np.ndarray, step: float) -> np.ndarray:
    """Signed lateral distance of Lap 2's line from Lap 1's, in metres, at every point of Lap 1
    (both on the same distance grid and in the same plan-view frame, Y up).

    For each point of A the nearest point of B's POLYLINE is taken (projection onto the two
    segments around B's nearest vertex), searched only within LINE_WINDOW_M of the same lap
    distance so a hairpin's other side is never matched. + = B lies LEFT of A's direction of
    travel. NaN where A has no position, no direction, or B is further than LINE_MAX_OFFSET_M."""
    xa, ya, xb, yb = (np.asarray(v, dtype=float) for v in (xa, ya, xb, yb))
    n = len(xa)
    out = np.full(n, np.nan)
    if n < 3 or len(xb) != n:
        return out
    tx = np.gradient(xa)
    ty = np.gradient(ya)
    w = max(1, int(math.ceil(LINE_WINDOW_M / max(step, 1e-6))))
    offsets = np.arange(-w, w + 1)
    for c0 in range(0, n, LINE_CHUNK):
        rows = np.arange(c0, min(n, c0 + LINE_CHUNK))
        idx = np.clip(rows[:, None] + offsets[None, :], 0, n - 1)
        dx = xb[idx] - xa[rows, None]
        dy = yb[idx] - ya[rows, None]
        d2 = np.where(np.isfinite(dx) & np.isfinite(dy), dx * dx + dy * dy, np.inf)
        j = idx[np.arange(len(rows)), np.argmin(d2, axis=1)]
        px, py = xa[rows], ya[rows]
        best = np.full(len(rows), np.inf)
        best_x = np.full(len(rows), np.nan)
        best_y = np.full(len(rows), np.nan)
        for j0, j1 in ((np.maximum(j - 1, 0), j), (j, np.minimum(j + 1, n - 1))):
            sx, sy = xb[j1] - xb[j0], yb[j1] - yb[j0]
            length2 = sx * sx + sy * sy
            with np.errstate(invalid="ignore", divide="ignore"):
                u = np.where(length2 > 0, ((px - xb[j0]) * sx + (py - yb[j0]) * sy) / length2, 0.0)
            u = np.clip(np.nan_to_num(u), 0.0, 1.0)
            qx, qy = xb[j0] + u * sx, yb[j0] + u * sy
            d = np.hypot(qx - px, qy - py)
            better = np.isfinite(d) & (d < best)
            best = np.where(better, d, best)
            best_x = np.where(better, qx, best_x)
            best_y = np.where(better, qy, best_y)
        cross = tx[rows] * (best_y - py) - ty[rows] * (best_x - px)
        tangent = np.hypot(tx[rows], ty[rows]) > 1e-9
        ok = np.isfinite(best) & (best <= LINE_MAX_OFFSET_M) & tangent & np.isfinite(px) & np.isfinite(py)
        out[rows] = np.where(ok, np.sign(cross) * best, np.nan)
    return out


def turn_direction(x: np.ndarray, y: np.ndarray, step: float) -> np.ndarray:
    """+1 left-hand, -1 right-hand, 0 straight — the local direction of the line (plan view,
    Y up), from its curvature over distance after smoothing over TURN_SMOOTH_M."""
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    out = np.zeros(len(x), dtype=int)
    ok = np.isfinite(x) & np.isfinite(y)
    if ok.sum() < 5:
        return out
    k = max(3, int(round(TURN_SMOOTH_M / max(step, 1e-6))) | 1)
    xs = _smooth(np.where(ok, x, np.interp(np.arange(len(x)), np.flatnonzero(ok), x[ok])), k)
    ys = _smooth(np.where(ok, y, np.interp(np.arange(len(y)), np.flatnonzero(ok), y[ok])), k)
    with np.errstate(divide="ignore", invalid="ignore"):
        dx, dy = np.gradient(xs, step), np.gradient(ys, step)
        ddx, ddy = np.gradient(dx, step), np.gradient(dy, step)
        kappa = (dx * ddy - dy * ddx) / np.power(dx * dx + dy * dy, 1.5)
    out[(kappa > TURN_MIN_CURVATURE) & ok] = 1
    out[(kappa < -TURN_MIN_CURVATURE) & ok] = -1
    return out


def corner_turns(corners: List[dict], x: np.ndarray, y: np.ndarray, grid: np.ndarray, step: float) -> List[int]:
    """The direction of each corner (+1 left, -1 right, 0 unknown): the majority of the local
    directions within 30 m of its apex."""
    local = turn_direction(x, y, step)
    out = []
    for c in corners:
        near = np.abs(grid - c["apex"]) <= 30.0
        s = int(np.sign(local[near].sum())) if near.any() else 0
        out.append(s)
    return out


def inside_sign(grid: np.ndarray, corners: List[dict], turns: List[int]) -> np.ndarray:
    """For every grid point the direction of the NEAREST corner (by apex distance): +1 left, -1
    right, 0 when there are no corners. "Inside" is the side of the corner you are heading into
    or coming out of — on a straight between a left and a right corner the first half belongs to
    the left one, the second half to the right one."""
    # only corners whose direction is known (a slow section without a clear turn has none)
    known = [(c["apex"], t) for c, t in zip(corners, turns) if t]
    if not known:
        return np.zeros(len(grid), dtype=int)
    apices = np.array([apex for apex, _ in known], dtype=float)
    nearest = np.argmin(np.abs(grid[:, None] - apices[None, :]), axis=1)
    return np.array([t for _, t in known], dtype=int)[nearest]


# ---------------------------------------------------------------------------
# Track map
# ---------------------------------------------------------------------------

def _curvature(x: np.ndarray, y: np.ndarray, t: np.ndarray) -> np.ndarray:
    xs, ys = _smooth(x, 9), _smooth(y, 9)
    with np.errstate(divide="ignore", invalid="ignore"):
        dx, dy = np.gradient(xs, t), np.gradient(ys, t)
        ddx, ddy = np.gradient(dx, t), np.gradient(dy, t)
        return (dx * ddy - dy * ddx) / np.power(dx * dx + dy * dy, 1.5)


def map_mirror(series: LapSeries) -> Tuple[bool, bool]:
    """(mirror, decided_from_data). The map is drawn with screen X = world x and
    screen Y = world z (Y pointing up). Whether that reproduces the real track or
    its mirror image depends on LMU's world handedness, so it is checked against
    the anchored steering: a right-hand corner is a clockwise turn, which on a
    Y-up screen has NEGATIVE curvature. If steering and curvature correlate
    positively instead, the picture is mirrored and X must be negated."""
    raw = series.raw
    if not all(c in raw for c in ("pos_x", "pos_z", "steering", "speed", "t")):
        return False, False
    kappa = _curvature(raw["pos_x"], raw["pos_z"], raw["t"])
    steering = series.orientation.steering * raw["steering"]
    moving = (raw["speed"] > 15.0) & np.isfinite(kappa) & (np.abs(kappa) < 0.2)
    sign, decided = _decide(_smooth(steering), kappa, 0.0, moving)
    if not decided:
        return False, False
    # sign > 0: steering tracks +kappa  -> counter-clockwise right turns -> mirrored
    return sign > 0, True


def track_xy(series: LapSeries, grid: np.ndarray, mirror: bool) -> Optional[Tuple[np.ndarray, np.ndarray]]:
    if "pos_x" not in series.raw or "pos_z" not in series.raw:
        return None
    x = np.interp(grid, series.dist, series.raw["pos_x"])
    y = np.interp(grid, series.dist, series.raw["pos_z"])
    return (-x if mirror else x), y


def resolve_mirror(a: LapSeries, b: Optional[LapSeries] = None) -> Tuple[bool, Dict[str, bool]]:
    """One mirror decision for every line drawn on the same map: lap A's if it
    came from data, otherwise lap B's, otherwise not mirrored."""
    mirror_a, decided_a = map_mirror(a)
    info = {"a_decided": decided_a}
    if decided_a:
        return mirror_a, info
    if b is not None:
        mirror_b, decided_b = map_mirror(b)
        info["b_decided"] = decided_b
        if decided_b:
            return mirror_b, info
    return False, info


# ---------------------------------------------------------------------------
# JSON helpers
# ---------------------------------------------------------------------------

def json_series(values: np.ndarray, decimals: int = 3) -> List[Optional[float]]:
    """Floats rounded for transport; NaN/Inf become null; whole numbers when decimals == 0.

    Vectorised (V0.9.1): the finite check runs in numpy instead of one Python call per value
    (that loop was a fifth of a comparison's server time)."""
    arr = np.asarray(values, dtype=float)
    finite = np.isfinite(arr)
    rounded = np.round(np.where(finite, arr, 0.0), decimals)
    out = rounded.astype(np.int64).tolist() if decimals <= 0 else rounded.tolist()
    if not finite.all():
        for index in np.flatnonzero(~finite).tolist():
            out[index] = None
    return out


# Transport precision per unit: never below what the browser shows (formatValue in
# web/js/analysis/model.js shows up to 2 decimals below 10, 1 below 1000, 0 above).
DECIMALS_BY_UNIT = {
    "km/h": 1, "%": 2, "rpm": 0, "G": 3, "mm": 2, "N": 1, "°C": 1, "°": 2, "kPa": 1, "Nm": 1,
    "°/s": 2, "s": 3, "kW": 1, "bar": 2, "L": 2,
    "mm/s": 1, "m": 2, "wheels": 0,  # V0.9.3
}


def channel_decimals(unit: str, discrete: bool) -> int:
    return 0 if discrete else DECIMALS_BY_UNIT.get(unit, 3)


def orientation_report(series: LapSeries) -> dict:
    o = series.orientation
    return {
        "steering": o.steering, "lat_g": o.lat_g, "long_g": o.long_g, "slip": o.slip, "yaw": o.yaw, "susp": o.susp,
        "decided_from_data": dict(o.decided_from_data),
    }


# ---------------------------------------------------------------------------
# Distributions (V0.9.3): histograms of a channel over the whole lap
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Wheel events (V0.9.3): lock-ups and wheelspin, measured, with their place on the lap
# ---------------------------------------------------------------------------

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
    from .telemetry_channels import _slip_ratio

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
