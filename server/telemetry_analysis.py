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
        return np.asarray(channel.compute(series.raw, series.orientation), dtype=float)


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


def _corner_channels(series: "LapSeries") -> Optional[dict]:
    """The arrays _corner_lap needs, converted once per lap (not once per corner)."""
    speed = channel_values(series, "speed")
    if speed is None:
        return None
    return {"dist": series.dist, "t": series.raw["t"], "speed": speed,
            "brake": channel_values(series, "brake"), "throttle": channel_values(series, "throttle")}


def _corner_lap(lap: dict, start: float, entry_at: float, end: float) -> Optional[dict]:
    """What one lap did in one corner, from its raw samples (full sample rate).

    `lap` comes from _corner_channels. `start`..`end` is the stretch the corner's time is
    measured over; `entry_at` is where its braking zone begins (the same as `start` except for
    the first corner, whose stretch also holds the straight from the line)."""
    dist, t, speed = lap["dist"], lap["t"], lap["speed"]
    # dist is monotonic (load_lap guarantees it): the corner's samples are one slice
    i0, i1 = np.searchsorted(dist, entry_at, "left"), np.searchsorted(dist, end, "right")
    if i1 - i0 < 3:
        return None
    segment = speed[i0:i1]
    if not np.isfinite(segment).any():
        return None
    k = int(np.nanargmin(segment))
    apex = float(dist[i0 + k])
    out = {
        "entry": round(float(np.interp(entry_at, dist, speed)), 1),
        "min": round(float(segment[k]), 1),
        "apex": round(apex, 1),
        "time": round(float(np.interp(end, dist, t) - np.interp(start, dist, t)), 3),
        "brake": None, "throttle": None, "exit": None,
    }
    brake, throttle = lap["brake"], lap["throttle"]
    if brake is not None:
        on = np.flatnonzero(brake[i0:i0 + k + 1] >= BRAKE_ON_PCT)   # up to and including the apex
        if len(on):
            out["brake"] = round(float(dist[i0 + on[0]]), 1)
    exit_at = None
    if throttle is not None:
        full = np.flatnonzero(throttle[i0 + k + 1:i1] >= THROTTLE_FULL_PCT)  # after the apex
        if len(full):
            exit_at = float(dist[i0 + k + 1 + full[0]])
            out["throttle"] = round(exit_at, 1)
    if exit_at is None:
        exit_at = min(apex + 100.0, end)
    out["exit"] = round(float(np.interp(exit_at, dist, speed)), 1)
    return out


def corner_analysis(a: "LapSeries", b: "LapSeries") -> List[dict]:
    """Corners of the common stretch and, for each, how both laps drove it (V0.9.1).

    Corners are found on the two laps' average speed (smoothed over CORNER_SMOOTH_M): a local
    minimum with a drop of at least CORNER_MIN_DROP_KMH both before and after it. A corner runs
    from its braking zone (the speed maximum before it) to the next corner's braking zone; the
    first corner's stretch also holds the straight from the start of the common range, the last
    one runs to its end. So the corners cover the lap without gaps and their times add up to the
    delta over the whole lap (the lap-time difference for two complete laps).
    All measurements (speeds, braking / throttle points, times) come from each lap's raw samples,
    not from the smoothed curve.
      -> [{"n", "start", "entry", "apex", "end", "delta", "a": {...}, "b": {...}}]; start..end is
         the stretch the time is measured over, entry where the braking zone begins; delta = time A − time B
         (negative = Lap 1 faster in that corner); speeds km/h, positions metres.
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
            # A corner right after the start or right before the finish has only one side inside
            # the lap (the lap begins on its exit / ends before its exit): that side decides.
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
    # the unsmoothed average speed near the smoothed one.
    reach = 2 * k
    apices = [max(0, ap - reach) + int(np.argmin(v[max(0, ap - reach):ap + reach + 1])) for ap in apices]
    # A braking zone begins at the LAST point before the apex that is (within 0.5 km/h) as fast
    # as the stretch since the previous corner: on a flat-out straight that is where the lift
    # comes, not where the straight's top speed was first reached.
    starts = []
    for j, apex in enumerate(apices):
        left = apices[j - 1] if j > 0 else 0
        before = smooth[left:apex + 1]
        starts.append(left + int(np.flatnonzero(before >= before.max() - 0.5)[-1]))
    chan_a, chan_b = _corner_channels(a), _corner_channels(b)
    corners = []
    for j, apex in enumerate(apices):
        entry = float(grid[starts[j]])
        start = float(lo) if j == 0 else entry
        end = float(grid[starts[j + 1]]) if j + 1 < len(apices) else float(hi)
        ref_apex = float(grid[apex])
        lap_a = _corner_lap(chan_a, start, entry, end)
        lap_b = _corner_lap(chan_b, start, entry, end)
        if lap_a is None or lap_b is None:
            continue
        corners.append({
            "n": len(corners) + 1, "start": round(start, 1), "entry": round(entry, 1),
            "apex": round(ref_apex, 1), "end": round(end, 1),
            "delta": round(lap_a["time"] - lap_b["time"], 3), "a": lap_a, "b": lap_b,
        })
    return corners


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
}


def channel_decimals(unit: str, discrete: bool) -> int:
    return 0 if discrete else DECIMALS_BY_UNIT.get(unit, 3)


def orientation_report(series: LapSeries) -> dict:
    o = series.orientation
    return {
        "steering": o.steering, "lat_g": o.lat_g, "long_g": o.long_g, "slip": o.slip, "yaw": o.yaw,
        "decided_from_data": dict(o.decided_from_data),
    }
