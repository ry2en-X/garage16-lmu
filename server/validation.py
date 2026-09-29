"""
validation.py — Server-side plausibility check for uploaded telemetry.

This is what makes Lap.is_valid an actual server-verified fact instead of
whatever the client claimed (see routers/telemetry.py: metadata.is_valid
is stored as client_claimed_valid only, never as is_valid).

Explicitly NOT anti-cheat: this catches corrupted, truncated, or
obviously-impossible telemetry (missing columns, NaN/Inf, a lap_time that
doesn't match the recorded duration, physically-impossible speed) — it
does not and cannot detect a sophisticated forged file with internally
consistent but fabricated numbers. Combined with the HMAC signature check
in routers/telemetry.py (which proves the upload came from a driver who
holds that client_secret, not that the driver's own client didn't lie to
it), this is "plausible and from an authenticated driver", not "verified
legitimate racing".

Two layers, split so the plausibility logic itself (`validate_dataframe`)
is testable with a plain, hand-built DataFrame — no parquet/pyarrow
required, unlike `validate_telemetry`, which needs pyarrow (or another
pandas parquet engine) to actually parse the uploaded bytes.
"""

from __future__ import annotations

import io
import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, List

import numpy as np
import pandas as pd

if TYPE_CHECKING:
    # Only needed for the type hint below — validate_dataframe only ever
    # duck-types (.lap_time / .sector_times / .sample_count), so importing
    # schemas.py (and therefore pydantic) at runtime isn't actually
    # necessary here, and this module is easier to unit-test without it.
    from .schemas import LapMetadata

REQUIRED_COLUMNS = {
    "t", "lap_dist", "speed", "throttle", "brake", "clutch",
    "steering", "gear", "rpm", "fuel",
}

# Generous physical bounds — wide enough to never flag a genuine lap in any
# car LMU ships, tight enough to catch garbage/fabricated data. Speed is in
# m/s: 130 m/s ≈ 468 km/h, well above anything on a closed circuit.
MAX_SPEED_MS = 130.0
MAX_RPM = 20000.0
MAX_GEAR = 8
MIN_GEAR = -1  # reverse

# Tolerances for cross-checks between metadata and the telemetry itself.
# V0.5.3 -> V0.6.0: tightened per audit finding that ±10%/9%-equivalent
# tolerances let a materially-wrong claimed lap_time or sector sum pass.
# These are still generous relative to LMU's own timing precision — if a
# genuine lap starts tripping these, tighten further only after checking
# real telemetry, not by guessing.
SAMPLE_COUNT_TOLERANCE_FRACTION = 0.05
SAMPLE_COUNT_TOLERANCE_MIN = 5
DURATION_TOLERANCE_FRACTION = 0.03
DURATION_TOLERANCE_MIN_SECONDS = 1.0
SECTOR_SUM_TOLERANCE_FRACTION = 0.02
SECTOR_SUM_TOLERANCE_MIN_SECONDS = 1.0
MAX_LAP_DIST_REGRESSION_METERS = 5.0
# ∫speed dt vs (lap_dist[-1] - lap_dist[0]): catches a claimed lap_dist
# that doesn't match the recorded speed trace at all (e.g. a fabricated
# or corrupted lap_dist column with otherwise-plausible speed values).
DISTANCE_INTEGRAL_TOLERANCE_FRACTION = 0.15
DISTANCE_INTEGRAL_TOLERANCE_MIN_METERS = 50.0


@dataclass
class ValidationResult:
    is_valid: bool
    reasons: List[str] = field(default_factory=list)

    def fail(self, reason: str) -> None:
        self.is_valid = False
        self.reasons.append(reason)


def validate_telemetry(telemetry_bytes: bytes, metadata: LapMetadata) -> ValidationResult:
    """Parse the uploaded parquet bytes and run the full plausibility
    check. Requires pandas + a parquet engine (pyarrow) to be installed —
    see server/requirements.txt."""
    try:
        df = pd.read_parquet(io.BytesIO(telemetry_bytes))
    except Exception as exc:  # noqa: BLE001 — any parse failure means "not usable"
        return ValidationResult(is_valid=False, reasons=[f"telemetry file could not be parsed: {exc}"])
    return validate_dataframe(df, metadata)


def validate_dataframe(df: pd.DataFrame, metadata: LapMetadata) -> ValidationResult:
    """The actual plausibility checks, against an already-parsed
    DataFrame. Split out from validate_telemetry() so this — the part
    with real logic worth testing — doesn't need pyarrow to test."""
    result = ValidationResult(is_valid=True)

    if len(df) == 0:
        result.fail("telemetry file has no samples")
        return result  # nothing further can be checked meaningfully

    missing = REQUIRED_COLUMNS - set(df.columns)
    if missing:
        result.fail(f"telemetry is missing required columns: {sorted(missing)}")
        return result

    numeric_cols = ["t", "lap_dist", "speed", "throttle", "brake", "clutch", "steering", "gear", "rpm", "fuel"]
    numeric = df[numeric_cols].to_numpy(dtype=float)
    if not np.isfinite(numeric).all():
        result.fail("telemetry contains NaN/Inf values")
        # Non-finite values would poison every check below (e.g. a NaN
        # compares False against every bound, silently "passing" range
        # checks) — stop here rather than reporting misleading secondary
        # failures on data we already know is unusable.
        return result

    if metadata.sample_count is not None:
        tolerance = max(SAMPLE_COUNT_TOLERANCE_MIN, SAMPLE_COUNT_TOLERANCE_FRACTION * metadata.sample_count)
        if abs(len(df) - metadata.sample_count) > tolerance:
            result.fail(f"sample_count mismatch: metadata claims {metadata.sample_count}, file has {len(df)}")

    if (df["speed"] < -0.5).any() or (df["speed"] > MAX_SPEED_MS).any():
        result.fail("speed out of realistic range")
    if not df["throttle"].between(-0.01, 1.01).all():
        result.fail("throttle out of [0,1] range")
    if not df["brake"].between(-0.01, 1.01).all():
        result.fail("brake out of [0,1] range")
    if not df["steering"].between(-1.01, 1.01).all():
        result.fail("steering out of [-1,1] range")
    if not df["gear"].between(MIN_GEAR, MAX_GEAR).all():
        result.fail("gear out of plausible range")
    if (df["rpm"] < -0.5).any() or (df["rpm"] > MAX_RPM).any():
        result.fail("rpm out of plausible range")

    t = df["t"].to_numpy(dtype=float)
    if len(t) > 1:
        if not np.all(np.diff(t) >= -1e-6):
            result.fail("telemetry timestamps are not monotonically increasing")
        duration = float(t[-1] - t[0])
        if metadata.lap_time > 0 and duration > 0:
            tolerance = max(DURATION_TOLERANCE_MIN_SECONDS, DURATION_TOLERANCE_FRACTION * metadata.lap_time)
            if abs(duration - metadata.lap_time) > tolerance:
                result.fail(
                    f"telemetry duration ({duration:.1f}s) does not match claimed lap_time ({metadata.lap_time:.1f}s)"
                )

    lap_dist = df["lap_dist"].to_numpy(dtype=float)
    if len(lap_dist) > 1:
        # Small backward jitter (sensor noise) is already excluded by the
        # 5m threshold itself — anything that jumps back by more than that
        # in a single sample is implausible regardless of how often it
        # happens, so any occurrence at all fails this check.
        backward_jumps = int(np.sum(np.diff(lap_dist) < -MAX_LAP_DIST_REGRESSION_METERS))
        if backward_jumps > 0:
            result.fail("lap_dist has implausible backward jumps")

        # Cross-check lap_dist against the integral of speed over time —
        # catches a lap_dist column that's internally consistent (no
        # backward jumps) but doesn't match the recorded speed at all.
        # Trapezoidal integration of speed*dt vs. actual displacement.
        speed_arr = df["speed"].to_numpy(dtype=float)
        _trapz = getattr(np, "trapezoid", None) or np.trapz  # NumPy 2.x renamed trapz -> trapezoid
        dist_from_speed = float(_trapz(speed_arr, t)) if len(t) == len(speed_arr) else None
        claimed_dist = float(lap_dist[-1] - lap_dist[0])
        if dist_from_speed is not None and claimed_dist > 0:
            tolerance = max(
                DISTANCE_INTEGRAL_TOLERANCE_MIN_METERS,
                DISTANCE_INTEGRAL_TOLERANCE_FRACTION * claimed_dist,
            )
            if abs(dist_from_speed - claimed_dist) > tolerance:
                result.fail(
                    f"lap_dist ({claimed_dist:.0f}m) does not match distance implied "
                    f"by the speed trace ({dist_from_speed:.0f}m)"
                )

    sector_sum = sum(metadata.sector_times)
    if metadata.lap_time > 0:
        # No longer gated on `sector_sum > 0` — an all-zero sector_times
        # list for a lap with a real lap_time is itself implausible and
        # was previously let through silently (V0.5.2 audit finding).
        tolerance = max(SECTOR_SUM_TOLERANCE_MIN_SECONDS, SECTOR_SUM_TOLERANCE_FRACTION * metadata.lap_time)
        if abs(sector_sum - metadata.lap_time) > tolerance:
            result.fail(f"sector_times sum ({sector_sum:.1f}s) does not match lap_time ({metadata.lap_time:.1f}s)")

    return result
