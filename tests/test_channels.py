"""
Tests for the extended-channel registry (client/telemetry/channels.py), the
recorder's column layout and the background saver (V0.9.0).

The central test fills a REAL ctypes TelemInfoV01 with a different number in
every field, then checks that each named column holds the value of the field
it claims to be. That is what protects against the one failure that would be
invisible otherwise: a column labelled "susp_deflection_fl" quietly holding the
rear-right tire load.
"""

import math
import time

import numpy as np
import pandas as pd
import pytest

from client.lmu.structs import TelemInfoV01, VehicleScoringInfoV01
from client.telemetry.async_saver import AsyncLapSaver
from client.telemetry.channels import (
    SCORING_CHANNELS,
    STINT_CHANNELS,
    EXTENDED_COLUMNS,
    ORIENTATION_CHANNELS,
    SCALAR_CHANNELS,
    WHEEL_KEYS,
    WHEEL_POSITIONS,
    extract_extended,
)
from client.telemetry.parser import Lap, TelemetrySample
from client.telemetry.recorder import LapRecorder

# Independent copy of the per-wheel mapping (key -> (TelemWheelV01 field, array index or None)).
# Deliberately NOT imported from channels.py: the test must be able to disagree with it.
WHEEL_FIELD_FOR_KEY = {
    "susp_deflection": ("suspension_deflection", None),
    "ride_height": ("ride_height", None),
    "susp_force": ("susp_force", None),
    "brake_pressure": ("brake_pressure", None),
    "rotation": ("rotation", None),
    "lat_patch_vel": ("lateral_patch_vel", None),
    "long_patch_vel": ("longitudinal_patch_vel", None),
    "lat_ground_vel": ("lateral_ground_vel", None),
    "long_ground_vel": ("longitudinal_ground_vel", None),
    "camber": ("camber", None),
    "lat_force": ("lateral_force", None),
    "long_force": ("longitudinal_force", None),
    "tire_load": ("tire_load", None),
    "grip_fract": ("grip_fract", None),
    "temp_l": ("temperature", 0),
    "temp_c": ("temperature", 1),
    "temp_r": ("temperature", 2),
    "wear": ("wear", None),
    "surface_type": ("surface_type", None),
    "vert_tire_deflection": ("vertical_tire_deflection", None),
    "toe": ("toe", None),
    "carcass_temp": ("tire_carcass_temperature", None),
    "inner_temp_l": ("tire_inner_layer_temperature", 0),
    "inner_temp_c": ("tire_inner_layer_temperature", 1),
    "inner_temp_r": ("tire_inner_layer_temperature", 2),
    "flat": ("flat", None),
    "detached": ("detached", None),
}


def _assign(obj, field, value):
    """ctypes integer fields (uint8 flags/levels) reject floats; set an int there."""
    try:
        setattr(obj, field, value)
    except TypeError:
        setattr(obj, field, int(value))


def _set_path(obj, path, value):
    *parents, leaf = path.split(".")
    for part in parents:
        obj = getattr(obj, part)
    _assign(obj, leaf, value)


def _filled_struct():
    """A TelemInfoV01 where every extended field has a unique value; returns
    (struct, {column name: expected value})."""
    tele = TelemInfoV01()
    expected = {}
    counter = 1.0

    for name, path in SCALAR_CHANNELS:
        value = counter
        counter += 1.0
        _set_path(tele, path, value)
        expected[name] = value

    for row_index, row in enumerate(("x", "y", "z")):
        for col in ("x", "y", "z"):
            value = counter
            counter += 1.0
            setattr(tele.ori[row_index], col, value)
            expected[f"ori_{row}{col}"] = value

    for wheel_index, pos in enumerate(WHEEL_POSITIONS):
        wheel = tele.wheel[wheel_index]
        for key in WHEEL_KEYS:
            field, index = WHEEL_FIELD_FOR_KEY[key]
            value = counter
            counter += 1.0
            if key in ("flat", "detached"):
                # c_bool can only hold 0/1: alternate by wheel so the order is still tested.
                value = 1.0 if wheel_index % 2 == 0 else 0.0
            if index is None:
                _assign(wheel, field, value)
            else:
                getattr(wheel, field)[index] = value
            expected[f"{key}_{pos}"] = value
    return tele, expected


def test_column_names_are_unique_and_cover_every_wheel_key():
    assert len(set(EXTENDED_COLUMNS)) == len(EXTENDED_COLUMNS)
    assert set(WHEEL_KEYS) == set(WHEEL_FIELD_FOR_KEY)
    for pos in WHEEL_POSITIONS:
        for key in WHEEL_KEYS:
            assert f"{key}_{pos}" in EXTENDED_COLUMNS
    for name in ORIENTATION_CHANNELS:
        assert name in EXTENDED_COLUMNS


def test_extracted_values_match_their_column_names():
    tele, expected = _filled_struct()
    # V0.9.3: the four scoring values come from the player's VehicleScoringInfoV01
    scoring = VehicleScoringInfoV01()
    for offset, (name, attr) in enumerate(SCORING_CHANNELS):
        setattr(scoring, attr, 250 + offset)
        expected[name] = 250 + offset
    # V0.9.4: the stint values — from the scoring struct ("s") or the telemetry struct ("t")
    for offset, (name, source, attr) in enumerate(STINT_CHANNELS):
        value = 1 if attr == "in_pits" else 200 + offset
        setattr(scoring if source == "s" else tele, attr, value)
        expected[name] = value
    values = extract_extended(tele, scoring)
    assert len(values) == len(EXTENDED_COLUMNS)
    got = dict(zip(EXTENDED_COLUMNS, values))
    assert set(got) == set(expected)
    # Values are exact small integers, so float equality is safe here (float32
    # is applied later, in the recorder, and 1..300 are exactly representable).
    for name, want in expected.items():
        assert got[name] == want, f"{name}: expected {want}, got {got[name]}"


def test_wheel_order_is_fl_fr_rl_rr():
    tele = TelemInfoV01()
    for index in range(4):
        tele.wheel[index].tire_load = 1000.0 * (index + 1)
    got = dict(zip(EXTENDED_COLUMNS, extract_extended(tele)))
    assert got["tire_load_fl"] == 1000.0
    assert got["tire_load_fr"] == 2000.0
    assert got["tire_load_rl"] == 3000.0
    assert got["tire_load_rr"] == 4000.0


def test_non_finite_values_become_nan():
    tele = TelemInfoV01()
    tele.local_accel.x = float("inf")
    tele.wheel[2].lateral_force = float("nan")
    tele.wheel[3].tire_load = float("-inf")
    got = dict(zip(EXTENDED_COLUMNS, extract_extended(tele)))
    assert math.isnan(got["accel_x"])
    assert math.isnan(got["lat_force_rl"])
    assert math.isnan(got["tire_load_rr"])
    assert got["accel_y"] == 0.0


def test_driver_aid_fields_are_really_read():
    tele = TelemInfoV01()
    tele.tc_level, tele.tc_max, tele.abs_level, tele.abs_max = 4, 12, 9, 12
    tele.tc_active, tele.abs_active = 1, 0
    tele.brake_migration, tele.motor_map = 3, 2
    got = dict(zip(EXTENDED_COLUMNS, extract_extended(tele)))
    assert (got["tc_level"], got["tc_max"], got["abs_level"], got["abs_max"]) == (4, 12, 9, 12)
    assert (got["tc_active"], got["abs_active"]) == (1, 0)
    assert (got["brake_migration"], got["motor_map"]) == (3, 2)


# ---------------------------------------------------------------------------
# Recorder
# ---------------------------------------------------------------------------

def _core_sample(i, ext=None):
    return TelemetrySample(
        t=i / 60.0, lap_dist=i * 1.5, speed=50.0, throttle=0.5, brake=0.0, clutch=0.0,
        steering=0.1, gear=4, rpm=7000.0, fuel=40.0, pos_x=float(i), pos_y=0.0, pos_z=float(2 * i),
        tire_pressures=(1.5, 1.5, 1.5, 1.5), tire_temps_outer=(350.0,) * 4, brake_temps=(600.0,) * 4,
        ext=ext,
    )


def _lap(samples):
    return Lap(
        track_name="Le Mans", car_name="#8 Test Car", session_type=10, lap_number=3, lap_time=100.0,
        sector_times=[30.0, 35.0, 35.0], is_valid=True, started_at=0.0, ambient_temp=20.0, track_temp=30.0,
        car_class="Hypercar", car_model="TestCar", samples=samples,
    )


def test_recorder_writes_core_and_extended_columns(tmp_path):
    ext = tuple(float(k) for k in range(len(EXTENDED_COLUMNS)))
    recorder = LapRecorder(str(tmp_path))
    meta_path = recorder.save(_lap([_core_sample(i, ext) for i in range(50)]))
    parquet = next(tmp_path.rglob("*.parquet"))
    df = pd.read_parquet(parquet)

    for column in ("t", "lap_dist", "speed", "throttle", "brake", "clutch", "steering", "gear", "rpm", "fuel",
                   "pos_x", "pos_y", "pos_z", "tire_pressures", "tire_temps_outer", "brake_temps"):
        assert column in df.columns
    assert "ext" not in df.columns
    assert len(df) == 50
    for column in EXTENDED_COLUMNS:
        assert column in df.columns
        assert df[column].dtype == np.float32
    assert df["tc_level"].iloc[0] == EXTENDED_COLUMNS.index("tc_level")
    assert meta_path is not None and meta_path.exists()


def test_recorder_without_extended_values_writes_only_core_columns(tmp_path):
    recorder = LapRecorder(str(tmp_path))
    recorder.save(_lap([_core_sample(i) for i in range(20)]))
    df = pd.read_parquet(next(tmp_path.rglob("*.parquet")))
    assert not (set(EXTENDED_COLUMNS) & set(df.columns))
    assert df["lap_dist"].dtype == np.float64  # core types untouched


def test_recorder_tolerates_a_sample_with_missing_extended_values(tmp_path):
    ext = tuple(1.0 for _ in EXTENDED_COLUMNS)
    samples = [_core_sample(i, ext) for i in range(10)]
    samples[4] = _core_sample(4, None)
    LapRecorder(str(tmp_path)).save(_lap(samples))
    df = pd.read_parquet(next(tmp_path.rglob("*.parquet")))
    assert len(df) == 10
    assert math.isnan(df["tc_level"].iloc[4])
    assert df["tc_level"].iloc[5] == 1.0


def test_saving_a_full_size_lap_is_fast_enough_for_a_background_thread(tmp_path):
    # ~4 minutes at 60 Hz. The bound is generous (CI machines are slow); it
    # exists to catch a regression back to a per-sample dataclasses.asdict().
    ext = tuple(np.random.default_rng(1).normal(size=len(EXTENDED_COLUMNS)).tolist())
    samples = [_core_sample(i, ext) for i in range(14400)]
    started = time.perf_counter()
    LapRecorder(str(tmp_path)).save(_lap(samples))
    assert time.perf_counter() - started < 20.0


# ---------------------------------------------------------------------------
# AsyncLapSaver
# ---------------------------------------------------------------------------

def test_async_saver_saves_in_order_and_reports_each_lap(tmp_path):
    saved = []
    recorder = LapRecorder(str(tmp_path))
    saver = AsyncLapSaver(recorder, on_saved=lambda lap, path: saved.append((lap.lap_number, path is not None)))
    for number in (1, 2, 3):
        lap = _lap([_core_sample(i) for i in range(5)])
        lap.lap_number = number
        saver.submit(lap)
    assert saver.flush(timeout=20.0)
    assert saved == [(1, True), (2, True), (3, True)]
    assert len(list(tmp_path.rglob("*.json"))) == 3
    saver.close()


def test_async_saver_survives_a_lap_that_fails_to_save(tmp_path):
    saved = []

    class ExplodingRecorder(LapRecorder):
        def save(self, lap):
            if lap.lap_number == 2:
                raise OSError("disk full")
            return super().save(lap)

    saver = AsyncLapSaver(ExplodingRecorder(str(tmp_path)), on_saved=lambda lap, path: saved.append((lap.lap_number, path is not None)))
    for number in (1, 2, 3):
        lap = _lap([_core_sample(i) for i in range(5)])
        lap.lap_number = number
        saver.submit(lap)
    assert saver.flush(timeout=20.0)
    assert saved == [(1, True), (2, False), (3, True)]
    saver.close()


def test_scoring_columns_are_nan_without_a_scoring_struct():
    """V0.9.3: tools/old code paths that pass only the telemetry struct still get a full
    row; the scoring values are "not recorded" (NaN), never a made-up 0."""
    tele, _ = _filled_struct()
    got = dict(zip(EXTENDED_COLUMNS, extract_extended(tele)))
    for name, _attr in SCORING_CHANNELS:
        assert math.isnan(got[name])
    for name, source, _attr in STINT_CHANNELS:
        if source == "s":
            assert math.isnan(got[name])
    # and they come after every telemetry column, so earlier positions never moved (V0.9.4:
    # the stint values are appended after them)
    tail = len(STINT_CHANNELS)
    assert list(EXTENDED_COLUMNS[-len(SCORING_CHANNELS) - tail:-tail]) == [name for name, _ in SCORING_CHANNELS]
    assert list(EXTENDED_COLUMNS[-tail:]) == [name for name, _, _ in STINT_CHANNELS]
