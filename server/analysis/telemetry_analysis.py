"""
telemetry_analysis.py — turns stored lap files into what the analysis views
plot. No HTTP, no database: pure functions over a parquet path, so
every step is unit-testable with a synthetic lap.

Pipeline for one lap
--------------------
  1. read only the parquet columns that are needed (a lap file holds ~190
     columns; a comparison of 8 channels reads about a tenth of them)
  2. expand the three per-wheel list columns of the core format
     (tire_pressures, tire_temps_outer, brake_temps) into per-wheel columns
  3. make the distance axis usable (V0.9.5, see distance_axis()): repeated polls of an
     unchanged telemetry frame are dropped; lap_dist (LMU's SCORING value, which the
     game may refresh less often than the telemetry) is kept wherever it advances, and
     the samples in between get their distance from the integrated speed, scaled so it
     meets lap_dist exactly at the next update. Interpolation needs a strictly
     increasing axis, so rows that do not advance (standing car) are dropped.
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

from server.analysis.distance_axis import distance_axis, fill_short_gaps  # noqa: F401  (re-exported)
from server.analysis.telemetry_channels import (
    CATALOG,
    WHEEL_POSITIONS,
    Orientation,
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


# What a lap file may unpack to. A real lap: ~190 columns, 5,000–35,000 rows (60 Hz polls,
# up to a ~9 min lap), 10–60 MB uncompressed. The limits leave a wide margin and stop a small,
# highly compressed file ("decompression bomb") from taking the server's memory.
MAX_FILE_ROWS = 200_000
MAX_FILE_COLUMNS = 1_000
MAX_FILE_UNCOMPRESSED_BYTES = 512 * 1024 * 1024
LIMIT_MESSAGE = "exceeds the decoding limits"


def check_limits(pf: "pq.ParquetFile") -> None:
    """Raises AnalysisError when the file's own footer announces more than the limits allow
    (checked before any column is decoded)."""
    meta = pf.metadata
    if meta is None:
        return
    uncompressed = sum(meta.row_group(i).total_byte_size for i in range(meta.num_row_groups))
    if meta.num_rows > MAX_FILE_ROWS or meta.num_columns > MAX_FILE_COLUMNS or uncompressed > MAX_FILE_UNCOMPRESSED_BYTES:
        raise AnalysisError(
            f"Telemetry file {LIMIT_MESSAGE} ({meta.num_rows} rows, {meta.num_columns} columns, "
            f"{uncompressed // (1024 * 1024)} MiB uncompressed)")


def _open(path: Path) -> "pq.ParquetFile":
    """Opens a lap file once, memory-mapped (the OS pages in only the column chunks that are read)."""
    try:
        pf = pq.ParquetFile(str(path), memory_map=True)
    except Exception as exc:  # noqa: BLE001
        raise AnalysisError(f"Telemetry file could not be read: {exc}") from exc
    check_limits(pf)
    return pf


def read_raw(path: Path, wanted: Sequence[str], parquet: "Optional[pq.ParquetFile]" = None) -> Dict[str, np.ndarray]:
    """Reads the wanted logical columns that exist in the file (missing ones are
    simply absent from the result). Everything is returned as float64.
    `parquet`: the already opened file, to avoid opening it again."""
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
    axis: dict = field(default_factory=dict)      # Distance_axis() stats

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


# Distance_axis() lives in distance_axis.py (lap_insights uses it too).

TIME_RESTART_S = 0.05     # Time going back more than this = a restart inside the file


# Prepared laps, kept briefly. Zooming in sends several requests for the same two laps
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
    # Time that jumps BACK (a session restart, a rewind) means the file holds more than one
    # attempt. The distance axis would silently keep the first part only and show it as the lap —
    # refused instead (the upload check marks such a lap invalid for the same reason).
    t_raw = raw["t"][np.isfinite(raw["t"])]
    if len(t_raw) > 1 and float(np.min(np.diff(t_raw))) < -TIME_RESTART_S:
        raise AnalysisError("Telemetry time jumps back inside the lap (a restart?) — not one continuous lap.")

    keep, dist, axis_stats = distance_axis(raw["lap_dist"], raw["t"], raw.get("speed"))
    if int(keep.sum()) < MIN_POINTS:
        raise AnalysisError("Telemetry file has no usable distance axis.")
    raw = {name: values[keep] for name, values in raw.items()}
    if float(dist[-1] - dist[0]) < 50.0:
        raise AnalysisError("Lap is too short to analyse.")
    t = raw["t"]
    if np.any(np.diff(t) < -1e-6):
        raise AnalysisError("Telemetry timestamps are not increasing.")

    return LapSeries(dist=dist, raw=raw, orientation=calibrate_orientation(raw), available=available, axis=axis_stats)


# ---------------------------------------------------------------------------
# Resampling, channels, delta
# ---------------------------------------------------------------------------

def make_grid(lo: float, hi: float, step: float, max_points: int = MAX_GRID_POINTS) -> Tuple[np.ndarray, float]:
    """Uniform distance grid from lo to hi. The step is clamped to a sensible
    range and widened if it would exceed `max_points` (default MAX_GRID_POINTS);
    the step actually used is returned.

    `max_points` is the server-side downsampling knob: the browser asks
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
    # A derived channel may decline a lap it cannot compute honestly (e.g. a wheel
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
# Spikes the overview grid would hide
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
# Racing line: how far apart two laps drove
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Track map
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# JSON helpers
# ---------------------------------------------------------------------------

def json_series(values: np.ndarray, decimals: int = 3) -> List[Optional[float]]:
    """Floats rounded for transport; NaN/Inf become null; whole numbers when decimals == 0.

    Vectorised: the finite check runs in numpy instead of one Python call per value
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
