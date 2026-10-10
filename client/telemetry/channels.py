"""
channels.py — the extended telemetry channels the client records per sample
(V0.9.0), and the one function that reads them out of a TelemInfoV01.

Why a registry instead of 160 hand-written dataclass fields: the order of the
values and the order of the column names MUST agree, and a registry makes that
true by construction (both are built from the same lists below). A test
(tests/test_channels.py) additionally fills a struct with a distinct value in
every field and checks that each column ends up holding the right one.

What is recorded, and in which unit — raw, exactly as LMU's shared memory
delivers it. Nothing is converted on the client; conversion for display
(Kelvin -> Celsius, m/s -> km/h, ...) happens on the server/web side, which
keeps the stored file a faithful copy of the game's data:

  * temperatures ......... Kelvin   (tires, carcass, inner layer, brake discs —
                                     LMU's header comments claim Celsius for the
                                     brake disc, but it is Kelvin in practice;
                                     see the LMU Pitwall project's notes)
  * pressures ............ kPa
  * lengths .............. metres
  * velocities ........... m/s
  * forces / loads ....... Newtons
  * angles ............... radians (camber, toe), rad/s (wheel rotation, angular
                                     velocity)
  * driver inputs ........ -1..1 / 0..1 (as the game reports them)
  * driver aids .......... integer levels (TC, ABS, engine map, brake migration,
                                     anti-roll bars) plus their per-car maximum

Coordinate system (rFactor 2 / LMU "local" space): +x = to the driver's LEFT,
+y = up, +z = REARWARD. Forward speed is therefore -local_vel.z, and a right
turn produces a NEGATIVE local_accel.x. The server turns this into
"lateral G, positive = right turn" — see server/telemetry_channels.py.

All values are stored as float32 in the parquet file (plenty for every one of
these channels, and half the size of float64); the pre-V0.9.0 core columns keep
their original types so older servers, tests and tools read them unchanged.
"""

from __future__ import annotations

import math
from operator import attrgetter
from typing import Callable, List, Sequence, Tuple

WHEEL_POSITIONS: Tuple[str, ...] = ("fl", "fr", "rl", "rr")  # LMU's wheel order: FL, FR, RL, RR

# (column name, attribute path on TelemInfoV01). Order = order of the values.
SCALAR_CHANNELS: Tuple[Tuple[str, str], ...] = (
    # --- filtered driver inputs (the unfiltered ones are the core columns) ---
    ("throttle_filtered", "filtered_throttle"),
    ("brake_filtered", "filtered_brake"),
    ("steering_filtered", "filtered_steering"),
    ("clutch_filtered", "filtered_clutch"),
    # --- motion of the car body (local space) ---
    ("vel_x", "local_vel.x"),
    ("vel_y", "local_vel.y"),
    ("vel_z", "local_vel.z"),
    ("accel_x", "local_accel.x"),
    ("accel_y", "local_accel.y"),
    ("accel_z", "local_accel.z"),
    ("ang_vel_x", "local_rot.x"),
    ("ang_vel_y", "local_rot.y"),
    ("ang_vel_z", "local_rot.z"),
    ("ang_accel_x", "local_rot_accel.x"),
    ("ang_accel_y", "local_rot_accel.y"),
    ("ang_accel_z", "local_rot_accel.z"),
    # --- powertrain ---
    ("engine_water_temp", "engine_water_temp"),
    ("engine_oil_temp", "engine_oil_temp"),
    ("clutch_rpm", "clutch_rpm"),
    ("engine_torque", "engine_torque"),
    ("steering_shaft_torque", "steering_shaft_torque"),
    ("turbo_boost_pressure", "turbo_boost_pressure"),
    ("rear_brake_bias", "rear_brake_bias"),
    # --- chassis / aero ---
    ("front_3rd_deflection", "front_3rd_deflection"),
    ("rear_3rd_deflection", "rear_3rd_deflection"),
    ("front_wing_height", "front_wing_height"),
    ("front_ride_height", "front_ride_height"),
    ("rear_ride_height", "rear_ride_height"),
    ("drag", "drag"),
    ("front_downforce", "front_downforce"),
    ("rear_downforce", "rear_downforce"),
    # --- lap state ---
    ("delta_best", "delta_best"),
    ("lap_invalidated", "lap_invalidated"),
    ("track_limits_steps", "track_limits_steps"),
    # --- hybrid / electric ---
    ("battery_charge_fraction", "battery_charge_fraction"),
    ("electric_motor_torque", "electric_boost_motor_torque"),
    ("electric_motor_rpm", "electric_boost_motor_rpm"),
    ("electric_motor_temp", "electric_boost_motor_temperature"),
    ("electric_water_temp", "electric_boost_water_temperature"),
    ("electric_motor_state", "electric_boost_motor_state"),
    ("regen", "regen"),
    ("state_of_charge", "state_of_charge"),
    ("virtual_energy", "virtual_energy"),
    # --- driver aids / electronics (LMU v1.3+) ---
    ("tc_level", "tc_level"),
    ("tc_max", "tc_max"),
    ("tc_slip_level", "tc_slip_level"),
    ("tc_slip_max", "tc_slip_max"),
    ("tc_cut_level", "tc_cut_level"),
    ("tc_cut_max", "tc_cut_max"),
    ("tc_active", "tc_active"),
    ("abs_level", "abs_level"),
    ("abs_max", "abs_max"),
    ("abs_active", "abs_active"),
    ("motor_map", "motor_map"),
    ("motor_map_max", "motor_map_max"),
    ("brake_migration", "brake_migration"),
    ("brake_migration_max", "brake_migration_max"),
    ("front_anti_sway", "front_anti_sway"),
    ("front_anti_sway_max", "front_anti_sway_max"),
    ("rear_anti_sway", "rear_anti_sway"),
    ("rear_anti_sway_max", "rear_anti_sway_max"),
    ("lift_and_coast_progress", "lift_and_coast_progress"),
    ("speed_limiter_active", "speed_limiter_active"),
)

# Orientation matrix rows (local -> world), 3 rows x 3 components.
ORIENTATION_CHANNELS: Tuple[str, ...] = tuple(
    f"ori_{row}{col}" for row in ("x", "y", "z") for col in ("x", "y", "z")
)

# Per-wheel channels: (key, getter on a TelemWheelV01). Each becomes four
# columns, "<key>_fl", "<key>_fr", "<key>_rl", "<key>_rr".
# The core columns already hold per-wheel tire pressure, mean tire temperature
# and brake disc temperature, so those three are not repeated here.
_WHEEL_SPECS: Tuple[Tuple[str, Callable], ...] = (
    ("susp_deflection", attrgetter("suspension_deflection")),
    ("ride_height", attrgetter("ride_height")),
    ("susp_force", attrgetter("susp_force")),
    ("brake_pressure", attrgetter("brake_pressure")),
    ("rotation", attrgetter("rotation")),
    ("lat_patch_vel", attrgetter("lateral_patch_vel")),
    ("long_patch_vel", attrgetter("longitudinal_patch_vel")),
    ("lat_ground_vel", attrgetter("lateral_ground_vel")),
    ("long_ground_vel", attrgetter("longitudinal_ground_vel")),
    ("camber", attrgetter("camber")),
    ("lat_force", attrgetter("lateral_force")),
    ("long_force", attrgetter("longitudinal_force")),
    ("tire_load", attrgetter("tire_load")),
    ("grip_fract", attrgetter("grip_fract")),
    ("temp_l", lambda w: w.temperature[0]),
    ("temp_c", lambda w: w.temperature[1]),
    ("temp_r", lambda w: w.temperature[2]),
    ("wear", attrgetter("wear")),
    ("surface_type", attrgetter("surface_type")),
    ("vert_tire_deflection", attrgetter("vertical_tire_deflection")),
    ("toe", attrgetter("toe")),
    ("carcass_temp", attrgetter("tire_carcass_temperature")),
    ("inner_temp_l", lambda w: w.tire_inner_layer_temperature[0]),
    ("inner_temp_c", lambda w: w.tire_inner_layer_temperature[1]),
    ("inner_temp_r", lambda w: w.tire_inner_layer_temperature[2]),
    ("flat", attrgetter("flat")),
    ("detached", attrgetter("detached")),
)

WHEEL_KEYS: Tuple[str, ...] = tuple(key for key, _ in _WHEEL_SPECS)

_SCALAR_GETTERS = tuple(attrgetter(path) for _, path in SCALAR_CHANNELS)
_WHEEL_GETTERS = tuple(getter for _, getter in _WHEEL_SPECS)

# Final column order: scalars, orientation (9), then wheel-major
# (all keys of FL, then all keys of FR, ...).
EXTENDED_COLUMNS: Tuple[str, ...] = (
    tuple(name for name, _ in SCALAR_CHANNELS)
    + ORIENTATION_CHANNELS
    + tuple(f"{key}_{pos}" for pos in WHEEL_POSITIONS for key in WHEEL_KEYS)
)

assert len(set(EXTENDED_COLUMNS)) == len(EXTENDED_COLUMNS), "duplicate extended column name"


def _clean(value) -> float:
    """float32-safe: ints/bools become floats, non-finite values become NaN
    (a NaN is stored as 'no data'; an inf would otherwise poison every
    average, plot scale and resample downstream)."""
    number = float(value)
    return number if math.isfinite(number) else math.nan


def extract_extended(tele) -> Tuple[float, ...]:
    """Reads every extended channel from one TelemInfoV01, in EXTENDED_COLUMNS
    order. `tele` may be the real ctypes struct or any object with the same
    attribute layout."""
    values: List[float] = [_clean(getter(tele)) for getter in _SCALAR_GETTERS]
    for row in tele.ori:
        values.append(_clean(row.x))
        values.append(_clean(row.y))
        values.append(_clean(row.z))
    for wheel in tele.wheel:
        for getter in _WHEEL_GETTERS:
            values.append(_clean(getter(wheel)))
    return tuple(values)


def column_count() -> int:
    return len(EXTENDED_COLUMNS)


def split_names(columns: Sequence[str]) -> Tuple[List[str], List[str]]:
    """(known extended columns, everything else) — used by tools/tests."""
    known = set(EXTENDED_COLUMNS)
    return [c for c in columns if c in known], [c for c in columns if c not in known]
