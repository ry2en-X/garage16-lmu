"""
telemetry_channels.py — what the analysis views can show, in display units.

The files the clients upload hold the game's RAW values (Kelvin, Newtons,
metres, m/s, rFactor-2 local axes ...; see client/telemetry/channels.py).
This module is the single place that turns them into things a driver wants to
read: km/h, degrees Celsius, millimetres, G, degrees of slip.

Two kinds of channels:
  * direct   — one raw column, scaled/converted (speed -> km/h, ...)
  * derived  — computed from several raw columns (lateral G, tire slip angle,
               ...)

SIGN CONVENTIONS AND SELF-CALIBRATION
-------------------------------------
Several channels depend on a sign convention of LMU's coordinate system
(+x = left, +z = rearward in the car's local frame). That convention is
documented for rFactor 2, but it has not been confirmed against LMU with real
data for every field, and a wrong sign would silently mirror a graph. So the
sign of each such channel is not hard-wired: it is chosen per lap from the lap's
own data so that the physics comes out right. Everything hangs off ONE anchor
that does not depend on any axis convention:

  * steering          "right = +" is anchored on tyre loads: in a right-hand
                      corner the car rolls onto its LEFT tyres, so
                      (load FL + RL) - (load FR + RR) is positive. The wheel
                      order FL, FR, RL, RR is part of the SDK, not a sign
                      convention. (Needs the extended channels; otherwise the
                      documented default, steering right = +, is used.)

and four checks that use the anchored steering:

  * lateral G         positive in right-hand corners  (checked against steering)
  * longitudinal G    positive when accelerating      (checked against dv/dt)
  * tire slip angle   positive in right-hand corners  (checked against lateral G)
  * yaw rate          positive in right-hand corners  (checked against steering)

`calibrate_orientation()` returns which way each one had to be flipped; the API
reports it (`orientation`) so a surprising value is visible rather than hidden.
When a lap does not move enough to decide (|corr| < MIN_CORRELATION) the
documented default is used and `decided_from_data[...]` is False.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np

G = 9.80665
KELVIN_OFFSET = 273.15

WHEEL_POSITIONS: Tuple[str, ...] = ("fl", "fr", "rl", "rr")
WHEEL_LABELS = {"fl": "FL", "fr": "FR", "rl": "RL", "rr": "RR"}

# A correlation weaker than this is not trusted to decide a sign.
MIN_CORRELATION = 0.25

# Documented rFactor-2 defaults, used only when a lap gives no usable signal.
DEFAULT_LAT_G_SIGN = -1.0   # +x is LEFT, so right-hand corners give negative accel_x -> flip
DEFAULT_LONG_G_SIGN = -1.0  # +z is REARWARD, so forward acceleration is negative accel_z -> flip
DEFAULT_SLIP_SIGN = 1.0
DEFAULT_YAW_SIGN = -1.0


@dataclass(frozen=True)
class Orientation:
    steering: float = 1.0
    lat_g: float = DEFAULT_LAT_G_SIGN
    long_g: float = DEFAULT_LONG_G_SIGN
    slip: float = DEFAULT_SLIP_SIGN
    yaw: float = DEFAULT_YAW_SIGN
    # True per entry when the sign came from the data rather than the default.
    decided_from_data: Dict[str, bool] = field(default_factory=dict)


def _correlation(a: np.ndarray, b: np.ndarray) -> Optional[float]:
    mask = np.isfinite(a) & np.isfinite(b)
    if mask.sum() < 20:
        return None
    a, b = a[mask], b[mask]
    if np.std(a) < 1e-9 or np.std(b) < 1e-9:
        return None
    return float(np.corrcoef(a, b)[0, 1])


def _decide(
    signal: np.ndarray, reference: np.ndarray, default: float, mask: Optional[np.ndarray] = None
) -> Tuple[float, bool]:
    """Sign (+1/-1) that makes `signal` correlate positively with `reference`;
    the default when the correlation is missing or weak. `mask` restricts the
    comparison to samples where it is True (e.g. only while the car is moving)."""
    if mask is not None:
        signal, reference = signal[mask], reference[mask]
    corr = _correlation(signal, reference)
    if corr is None or abs(corr) < MIN_CORRELATION:
        return default, False
    return (1.0 if corr > 0 else -1.0), True


def _smooth(values: np.ndarray, window: int = 9) -> np.ndarray:
    if window <= 1 or len(values) < window:
        return values
    kernel = np.ones(window) / window
    padded = np.pad(values, (window // 2, window - 1 - window // 2), mode="edge")
    return np.convolve(padded, kernel, mode="valid")


def _slip_angle_raw(raw: Dict[str, np.ndarray], pos: str) -> Optional[np.ndarray]:
    lat = raw.get(f"lat_ground_vel_{pos}")
    lon = raw.get(f"long_ground_vel_{pos}")
    if lat is None or lon is None:
        return None
    # Angle between the wheel's heading and its direction of travel. The
    # longitudinal speed is floored at 1 m/s so a standing wheel does not
    # produce a meaningless 90 degrees.
    return np.degrees(np.arctan2(lat, np.maximum(np.abs(lon), 1.0)))


def calibrate_orientation(raw: Dict[str, np.ndarray]) -> Orientation:
    """Decides the sign of steering, lateral/longitudinal G, slip angle and yaw
    rate from the lap's own data — see the module docstring."""
    decided: Dict[str, bool] = {}
    n = len(next(iter(raw.values()))) if raw else 0
    moving = (raw["speed"] > 10.0) if "speed" in raw else np.ones(n, dtype=bool)

    # --- steering: anchored on tyre loads (convention-free) ---
    steering_raw = raw.get("steering")
    steer_sign = 1.0
    load_cols = [f"tire_load_{p}" for p in WHEEL_POSITIONS]
    if steering_raw is not None and all(c in raw for c in load_cols):
        left = raw["tire_load_fl"] + raw["tire_load_rl"]
        right = raw["tire_load_fr"] + raw["tire_load_rr"]
        steer_sign, decided["steering"] = _decide(_smooth(steering_raw), _smooth(left - right), 1.0, moving)
    else:
        decided["steering"] = False
    steering = steer_sign * steering_raw if steering_raw is not None else None

    # --- each of these: "which sign makes sign * signal track the reference?" ---
    lat_sign = DEFAULT_LAT_G_SIGN
    if steering is not None and "accel_x" in raw:
        lat_sign, decided["lat_g"] = _decide(_smooth(raw["accel_x"]), _smooth(steering), DEFAULT_LAT_G_SIGN, moving)
    else:
        decided["lat_g"] = False

    long_sign = DEFAULT_LONG_G_SIGN
    if "accel_z" in raw and "speed" in raw and "t" in raw and len(raw["t"]) > 5 and np.all(np.isfinite(raw["t"])):
        with np.errstate(divide="ignore", invalid="ignore"):
            dvdt = np.gradient(_smooth(raw["speed"], 5), raw["t"])
        long_sign, decided["long_g"] = _decide(_smooth(raw["accel_z"]), dvdt, DEFAULT_LONG_G_SIGN, moving)
    else:
        decided["long_g"] = False

    yaw_sign = DEFAULT_YAW_SIGN
    if steering is not None and "ang_vel_y" in raw:
        yaw_sign, decided["yaw"] = _decide(_smooth(raw["ang_vel_y"]), _smooth(steering), DEFAULT_YAW_SIGN, moving)
    else:
        decided["yaw"] = False

    slip_sign = DEFAULT_SLIP_SIGN
    slips = [s for s in (_slip_angle_raw(raw, pos) for pos in WHEEL_POSITIONS) if s is not None]
    if slips and "accel_x" in raw:
        mean_slip = np.nanmean(np.vstack(slips), axis=0)
        slip_sign, decided["slip"] = _decide(
            _smooth(mean_slip), _smooth(lat_sign * raw["accel_x"]), DEFAULT_SLIP_SIGN, moving
        )
    else:
        decided["slip"] = False

    return Orientation(
        steering=steer_sign, lat_g=lat_sign, long_g=long_sign, slip=slip_sign, yaw=yaw_sign,
        decided_from_data=decided,
    )


# ---------------------------------------------------------------------------
# Unit helpers
# ---------------------------------------------------------------------------

def _to_celsius(values: np.ndarray, *, min_if_celsius: float = 200.0) -> np.ndarray:
    """Raw temperatures are Kelvin in practice (LMU's header comments say Celsius
    for the brake discs, but the values are Kelvin — see client/telemetry/
    channels.py). If the whole lap sits below 200 the values cannot be Kelvin, so
    they are taken to be Celsius already: this keeps the display right if LMU
    ever fixes that comment by changing the unit."""
    finite = values[np.isfinite(values)]
    if finite.size and float(np.max(finite)) < min_if_celsius:
        return values
    return values - KELVIN_OFFSET


def _to_kpa(values: np.ndarray) -> np.ndarray:
    """Tire pressure: kPa (~100-300) or bar (~1-3) — the ranges do not overlap,
    so the unit is told apart by magnitude and always shown as kPa."""
    finite = values[np.isfinite(values)]
    if finite.size and float(np.median(finite)) < 50.0:
        return values * 100.0
    return values


# ---------------------------------------------------------------------------
# The catalog
# ---------------------------------------------------------------------------

Compute = Callable[[Dict[str, np.ndarray], Orientation], np.ndarray]


@dataclass(frozen=True)
class Channel:
    key: str
    label: str
    unit: str
    group: str
    needs: Tuple[str, ...]
    compute: Compute
    discrete: bool = False
    # Default scale hint for charts: ("fixed", lo, hi) or None for auto.
    fixed_range: Optional[Tuple[float, float]] = None


CATALOG: Dict[str, Channel] = {}
GROUP_ORDER: List[str] = []


def _add(channel: Channel) -> None:
    assert channel.key not in CATALOG, f"duplicate channel {channel.key}"
    CATALOG[channel.key] = channel
    if channel.group not in GROUP_ORDER:
        GROUP_ORDER.append(channel.group)


def _scaled(key: str, label: str, unit: str, group: str, column: str, factor: float = 1.0,
            offset: float = 0.0, discrete: bool = False, fixed_range=None) -> None:
    _add(Channel(
        key=key, label=label, unit=unit, group=group, needs=(column,), discrete=discrete,
        fixed_range=fixed_range,
        compute=lambda raw, _o, c=column, f=factor, off=offset: raw[c] * f + off,
    ))


# --- Inputs & speed ---------------------------------------------------------
_scaled("speed", "Speed", "km/h", "Speed & inputs", "speed", 3.6)
_scaled("throttle", "Throttle", "%", "Speed & inputs", "throttle", 100.0, fixed_range=(0, 100))
_scaled("brake", "Brake", "%", "Speed & inputs", "brake", 100.0, fixed_range=(0, 100))
_add(Channel("steering", "Steering (right = +)", "%", "Speed & inputs", ("steering",),
             lambda raw, o: o.steering * raw["steering"] * 100.0, fixed_range=(-100, 100)))
_scaled("clutch", "Clutch", "%", "Speed & inputs", "clutch", 100.0, fixed_range=(0, 100))
_scaled("gear", "Gear", "", "Speed & inputs", "gear", discrete=True)
_scaled("rpm", "Engine speed", "rpm", "Speed & inputs", "rpm")

# --- Motion & G -------------------------------------------------------------
_add(Channel("lat_g", "Lateral G (right turn = +)", "G", "Motion & G", ("accel_x",),
             lambda raw, o: o.lat_g * raw["accel_x"] / G))
_add(Channel("long_g", "Longitudinal G (accelerating = +)", "G", "Motion & G", ("accel_z",),
             lambda raw, o: o.long_g * raw["accel_z"] / G))
_add(Channel("yaw_rate", "Yaw rate (right turn = +)", "°/s", "Motion & G", ("ang_vel_y",),
             lambda raw, o: o.yaw * np.degrees(raw["ang_vel_y"])))
_scaled("delta_best", "Delta to own best (game)", "s", "Motion & G", "delta_best")

# --- Driver aids ------------------------------------------------------------
_scaled("tc_level", "Traction control", "level", "Driver aids", "tc_level", discrete=True)
_scaled("tc_slip", "TC slip setting", "level", "Driver aids", "tc_slip_level", discrete=True)
_scaled("tc_cut", "TC cut setting", "level", "Driver aids", "tc_cut_level", discrete=True)
_scaled("tc_active", "TC intervening", "0/1", "Driver aids", "tc_active", discrete=True, fixed_range=(0, 1))
_scaled("abs_level", "ABS", "level", "Driver aids", "abs_level", discrete=True)
_scaled("abs_active", "ABS intervening", "0/1", "Driver aids", "abs_active", discrete=True, fixed_range=(0, 1))
_scaled("engine_map", "Engine / motor map", "level", "Driver aids", "motor_map", discrete=True)
_scaled("brake_migration", "Brake migration", "level", "Driver aids", "brake_migration", discrete=True)
_scaled("brake_bias_rear", "Rear brake bias", "%", "Driver aids", "rear_brake_bias", 100.0)
_scaled("arb_front", "Anti-roll bar front", "level", "Driver aids", "front_anti_sway", discrete=True)
_scaled("arb_rear", "Anti-roll bar rear", "level", "Driver aids", "rear_anti_sway", discrete=True)
_scaled("regen", "Regeneration", "kW", "Driver aids", "regen")

# --- Suspension (per wheel) --------------------------------------------------
for _pos in WHEEL_POSITIONS:
    _scaled(f"susp_travel_{_pos}", f"Suspension travel {WHEEL_LABELS[_pos]}", "mm", "Suspension",
            f"susp_deflection_{_pos}", 1000.0)
for _pos in WHEEL_POSITIONS:
    _scaled(f"ride_height_{_pos}", f"Ride height {WHEEL_LABELS[_pos]}", "mm", "Suspension",
            f"ride_height_{_pos}", 1000.0)
for _pos in WHEEL_POSITIONS:
    _scaled(f"susp_force_{_pos}", f"Pushrod load {WHEEL_LABELS[_pos]}", "N", "Suspension",
            f"susp_force_{_pos}")
_scaled("ride_height_front", "Ride height front (car)", "mm", "Suspension", "front_ride_height", 1000.0)
_scaled("ride_height_rear", "Ride height rear (car)", "mm", "Suspension", "rear_ride_height", 1000.0)
_scaled("third_spring_front", "Third spring front", "mm", "Suspension", "front_3rd_deflection", 1000.0)
_scaled("third_spring_rear", "Third spring rear", "mm", "Suspension", "rear_3rd_deflection", 1000.0)

# --- Tyres (per wheel) -------------------------------------------------------
for _pos in WHEEL_POSITIONS:
    _scaled(f"tire_load_{_pos}", f"Tyre load {WHEEL_LABELS[_pos]}", "N", "Tyres", f"tire_load_{_pos}")
for _pos in WHEEL_POSITIONS:
    _add(Channel(
        f"slip_angle_{_pos}", f"Slip angle {WHEEL_LABELS[_pos]} (right turn = +)", "°", "Tyres",
        (f"lat_ground_vel_{_pos}", f"long_ground_vel_{_pos}"),
        lambda raw, o, p=_pos: o.slip * _slip_angle_raw(raw, p),
    ))
for _pos in WHEEL_POSITIONS:
    _scaled(f"grip_fract_{_pos}", f"Sliding fraction {WHEEL_LABELS[_pos]}", "%", "Tyres",
            f"grip_fract_{_pos}", 100.0, fixed_range=(0, 100))
for _pos in WHEEL_POSITIONS:
    _scaled(f"lat_force_{_pos}", f"Lateral force {WHEEL_LABELS[_pos]}", "N", "Tyres", f"lat_force_{_pos}")
for _pos in WHEEL_POSITIONS:
    _scaled(f"long_force_{_pos}", f"Longitudinal force {WHEEL_LABELS[_pos]}", "N", "Tyres", f"long_force_{_pos}")
for _pos in WHEEL_POSITIONS:
    _scaled(f"camber_{_pos}", f"Camber {WHEEL_LABELS[_pos]}", "°", "Tyres", f"camber_{_pos}", math.degrees(1.0))
for _pos in WHEEL_POSITIONS:
    _add(Channel(
        f"tire_temp_{_pos}", f"Tyre temperature {WHEEL_LABELS[_pos]}", "°C", "Tyres", (f"tire_temp_mean_{_pos}",),
        lambda raw, _o, p=_pos: _to_celsius(raw[f"tire_temp_mean_{p}"]),
    ))
for _pos in WHEEL_POSITIONS:
    _add(Channel(
        f"tire_pressure_{_pos}", f"Tyre pressure {WHEEL_LABELS[_pos]}", "kPa", "Tyres", (f"tire_pressure_{_pos}",),
        lambda raw, _o, p=_pos: _to_kpa(raw[f"tire_pressure_{p}"]),
    ))
for _pos in WHEEL_POSITIONS:
    _scaled(f"tire_wear_{_pos}", f"Tyre wear remaining {WHEEL_LABELS[_pos]}", "%", "Tyres",
            f"wear_{_pos}", 100.0, fixed_range=(0, 100))

# --- Brakes (per wheel) ------------------------------------------------------
for _pos in WHEEL_POSITIONS:
    _add(Channel(
        f"brake_temp_{_pos}", f"Brake disc temperature {WHEEL_LABELS[_pos]}", "°C", "Brakes", (f"brake_temp_{_pos}",),
        lambda raw, _o, p=_pos: _to_celsius(raw[f"brake_temp_{p}"]),
    ))
for _pos in WHEEL_POSITIONS:
    _scaled(f"brake_pressure_{_pos}", f"Brake pressure {WHEEL_LABELS[_pos]}", "%", "Brakes",
            f"brake_pressure_{_pos}", 100.0, fixed_range=(0, 100))

# --- Aero --------------------------------------------------------------------
_scaled("downforce_front", "Downforce front", "N", "Aero", "front_downforce")
_scaled("downforce_rear", "Downforce rear", "N", "Aero", "rear_downforce")
_scaled("drag", "Drag", "N", "Aero", "drag")
_scaled("front_wing_height", "Front wing height", "mm", "Aero", "front_wing_height", 1000.0)

# --- Powertrain & energy -----------------------------------------------------
_scaled("engine_torque", "Engine torque", "Nm", "Powertrain & energy", "engine_torque")
_scaled("engine_water_temp", "Water temperature", "°C", "Powertrain & energy", "engine_water_temp")
_scaled("engine_oil_temp", "Oil temperature", "°C", "Powertrain & energy", "engine_oil_temp")
_scaled("turbo_boost", "Turbo boost", "bar", "Powertrain & energy", "turbo_boost_pressure")
_scaled("fuel", "Fuel", "L", "Powertrain & energy", "fuel")
_scaled("battery", "Battery charge", "%", "Powertrain & energy", "battery_charge_fraction", 100.0, fixed_range=(0, 100))
_scaled("virtual_energy", "Virtual energy", "%", "Powertrain & energy", "virtual_energy", 100.0, fixed_range=(0, 100))
_scaled("electric_motor_torque", "Electric motor torque", "Nm", "Powertrain & energy", "electric_motor_torque")
_scaled("steering_torque", "Steering shaft torque", "Nm", "Powertrain & energy", "steering_shaft_torque")


def channel_info(channel: Channel) -> dict:
    return {
        "key": channel.key,
        "label": channel.label,
        "unit": channel.unit,
        "group": channel.group,
        "discrete": channel.discrete,
        "fixed_range": list(channel.fixed_range) if channel.fixed_range else None,
    }


def catalog_listing() -> List[dict]:
    """Every channel, grouped in GROUP_ORDER, for GET /telemetry/channels."""
    ordered = sorted(CATALOG.values(), key=lambda c: (GROUP_ORDER.index(c.group),))
    return [channel_info(c) for c in ordered]


# Default selection when the client does not ask for specific channels.
DEFAULT_CHANNELS: Tuple[str, ...] = (
    "speed", "throttle", "brake", "steering", "gear", "rpm", "lat_g", "long_g",
)


def required_raw_columns(keys: List[str]) -> List[str]:
    """Raw parquet columns needed to compute `keys` (deduplicated, stable order)."""
    seen: List[str] = []
    for key in keys:
        for column in CATALOG[key].needs:
            if column not in seen:
                seen.append(column)
    return seen
