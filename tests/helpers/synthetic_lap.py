"""
A physically consistent synthetic lap, for testing the analysis code.

The lap drives a closed loop (two straights joined by two semicircles) at a
speed profile that slows in the corners. Everything the analysis needs is
derived from that one trajectory with the SAME conventions the game uses, so a
test can check that the self-calibration really recovers them:

  * driven clockwise seen from above  -> the first half-turn is a RIGHT turn
  * local axes: +x = driver's left, +z = rearward (rFactor 2 / LMU)
  * world position: (pos_x, pos_z) is a plan view whose handedness is chosen by
    `world_mirrored` — when True the track is written into the file mirrored,
    which the analysis must detect and undo.

`variant` knobs let a test flip the things that are NOT documented facts:
`steering_right_positive`, so a wrong steering sign is caught too.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from client.telemetry.channels import EXTENDED_COLUMNS

G = 9.80665


def make_lap(
    n: int = 3000,
    lap_time: float = 90.0,
    straight: float = 900.0,
    radius: float = 120.0,
    *,
    speed_scale: float = 1.0,
    steering_right_positive: bool = True,
    world_mirrored: bool = False,
    with_extended: bool = True,
    seed: int = 0,
) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    t = np.linspace(0.0, lap_time, n)

    # Path parameter s (metres along the path) from a speed profile that is
    # slow on the two half-circles, fast on the straights.
    arc = np.pi * radius
    total = 2 * straight + 2 * arc
    # speed as a function of position along the path
    def speed_at(s):
        s = np.mod(s, total)
        in_turn1 = (s >= straight) & (s < straight + arc)
        in_turn2 = (s >= 2 * straight + arc)
        v = np.where(in_turn1 | in_turn2, 38.0, 75.0)
        return v * speed_scale

    s = np.zeros(n)
    for i in range(1, n):
        s[i] = s[i - 1] + speed_at(s[i - 1]) * (t[i] - t[i - 1])
    # normalise so the lap closes exactly at `total` metres
    s = s * (total / s[-1])
    v = np.gradient(s, t)

    # Plan-view position and heading along the loop, clockwise.
    x = np.zeros(n)
    z = np.zeros(n)
    heading = np.zeros(n)          # direction of travel, radians, 0 = +z axis, clockwise positive
    turning = np.zeros(n)          # +1 right turn (clockwise), 0 straight
    for i, si in enumerate(s):
        if si < straight:
            x[i], z[i], heading[i] = 0.0, si, 0.0
        elif si < straight + arc:
            a = (si - straight) / radius
            # half circle to the right: centre at (+radius, straight)
            x[i] = radius - radius * np.cos(a)
            z[i] = straight + radius * np.sin(a)
            heading[i] = a
            turning[i] = 1.0
        elif si < 2 * straight + arc:
            d = si - straight - arc
            x[i], z[i], heading[i] = 2 * radius, straight - d, np.pi
        else:
            a = (si - 2 * straight - arc) / radius
            x[i] = 2 * radius - radius + radius * np.cos(a) * 1.0
            x[i] = radius + radius * np.cos(a)
            z[i] = -radius * np.sin(a)
            heading[i] = np.pi + a
            turning[i] = 1.0

    lateral_g = np.where(turning > 0, v ** 2 / radius / G, 0.0)           # right turn = +, in G
    lat_accel_right = lateral_g * G                                         # m/s^2 toward the right
    long_accel = np.gradient(v, t)                                          # m/s^2 forward

    steering_right = np.where(turning > 0, 0.45, 0.0)                       # +1 = right turn
    steering = steering_right if steering_right_positive else -steering_right

    # Local frame: +x = LEFT, +z = REARWARD -> a right-hand corner accelerates toward -x,
    # forward acceleration is -z.
    accel_x = -lat_accel_right
    accel_z = -long_accel
    # angular velocity about local y (up) in a left-handed frame: right turn is negative of the
    # clockwise rate in the documented convention
    yaw_rate = np.where(turning > 0, v / radius, 0.0)
    ang_vel_y = -yaw_rate

    # Per-wheel loads: in a right turn the car loads its LEFT tyres.
    base = 3500.0
    transfer = lateral_g * 900.0
    loads = {
        "fl": base + transfer, "rl": base + transfer,
        "fr": base - transfer, "rr": base - transfer,
    }
    # Slip angle: wheel velocity direction vs wheel heading. Give right turns a lateral
    # ground velocity component toward the OUTSIDE of the turn (the left, +x), which is
    # what a sliding tyre does.
    lat_ground = np.where(turning > 0, 0.06 * v, 0.0)
    long_ground = v

    mirror = -1.0 if world_mirrored else 1.0
    df = pd.DataFrame({
        "t": t,
        "lap_dist": s,
        "speed": v,
        "throttle": np.clip(0.5 + long_accel / 8.0, 0, 1),
        "brake": np.clip(-long_accel / 8.0, 0, 1),
        "clutch": np.zeros(n),
        "steering": steering,
        "gear": np.where(v > 60, 6, 4).astype(float),
        "rpm": 7000.0 + 40.0 * v,
        "fuel": np.linspace(60.0, 58.0, n),
        "pos_x": mirror * x,
        "pos_y": np.zeros(n),
        "pos_z": z,
        "tire_pressures": [(180.0, 181.0, 182.0, 183.0)] * n,
        "tire_temps_outer": [(350.0, 351.0, 352.0, 353.0)] * n,
        "brake_temps": [(600.0 + 0.1 * float(vi), 610.0, 620.0, 630.0) for vi in v],
    })

    if with_extended:
        ext = {name: np.zeros(n, dtype=np.float32) for name in EXTENDED_COLUMNS}
        ext["accel_x"] = (accel_x + rng.normal(0, 0.05, n)).astype(np.float32)
        ext["accel_z"] = (accel_z + rng.normal(0, 0.05, n)).astype(np.float32)
        ext["ang_vel_y"] = (ang_vel_y).astype(np.float32)
        for pos in ("fl", "fr", "rl", "rr"):
            ext[f"tire_load_{pos}"] = loads[pos].astype(np.float32)
            ext[f"lat_ground_vel_{pos}"] = lat_ground.astype(np.float32)
            ext[f"long_ground_vel_{pos}"] = long_ground.astype(np.float32)
            ext[f"susp_deflection_{pos}"] = (0.05 + 0.00001 * loads[pos]).astype(np.float32)
        ext["tc_level"] = np.full(n, 4, dtype=np.float32)
        ext["tc_active"] = (turning > 0).astype(np.float32)
        ext["rear_brake_bias"] = np.full(n, 0.55, dtype=np.float32)
        df = pd.concat([df, pd.DataFrame(ext)], axis=1)
    return df


def write_lap(path, **kwargs) -> pd.DataFrame:
    df = make_lap(**kwargs)
    df.to_parquet(path, index=False)
    return df
