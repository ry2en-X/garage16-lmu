"""
Tests for server/validation.py's validate_dataframe — the check that makes
Lap.is_valid a server-verified fact instead of whatever the client claimed.

Pure pandas/numpy, no pyarrow/fastapi/sqlalchemy/pydantic needed: metadata
is passed as a plain SimpleNamespace duck-typing LapMetadata's
lap_time/sector_times/sample_count (validate_dataframe never actually
imports pydantic — see the module's own docstring on why the two layers
are split this way).
"""

from types import SimpleNamespace

import numpy as np
import pandas as pd

from server.validation import validate_dataframe

REQUIRED_COLS = ["t", "lap_dist", "speed", "throttle", "brake", "clutch", "steering", "gear", "rpm", "fuel"]


def _good_dataframe(n=100, lap_time=90.0):
    t = np.linspace(0, lap_time, n)
    return pd.DataFrame({
        "t": t,
        "lap_dist": np.linspace(0, 5000, n),
        "speed": np.full(n, 50.0),
        "throttle": np.full(n, 0.8),
        "brake": np.zeros(n),
        "clutch": np.zeros(n),
        "steering": np.zeros(n),
        "gear": np.full(n, 4),
        "rpm": np.full(n, 6000.0),
        "fuel": np.linspace(50, 48, n),
    })


def _meta(lap_time=90.0, sector_times=(30.0, 30.0, 30.0), sample_count=100):
    return SimpleNamespace(lap_time=lap_time, sector_times=list(sector_times), sample_count=sample_count)


def test_plausible_telemetry_is_valid():
    result = validate_dataframe(_good_dataframe(), _meta())
    assert result.is_valid is True
    assert result.reasons == []


# (a) client claims valid, server disagrees — validate_dataframe doesn't
# even see the client's claim (that's the point: is_valid is computed
# independently of it), so this is really "server correctly flags bad
# telemetry regardless of what the envelope's is_valid says".
def test_server_rejects_bad_telemetry_independent_of_client_claim():
    df = _good_dataframe()
    df.loc[0, "speed"] = 999.0  # implausible regardless of client_claimed_valid
    result = validate_dataframe(df, _meta())
    assert result.is_valid is False
    assert any("speed" in r for r in result.reasons)


# (b) manipulated lap_time: telemetry duration doesn't match the claimed lap_time.
def test_manipulated_lap_time_detected_via_duration_mismatch():
    df = _good_dataframe(lap_time=90.0)  # telemetry actually spans 90s
    result = validate_dataframe(df, _meta(lap_time=40.0))  # claims a suspiciously fast 40s
    assert result.is_valid is False
    assert any("lap_time" in r for r in result.reasons)


# (c) manipulated sector_times: sum doesn't add up to lap_time.
def test_manipulated_sector_times_detected():
    df = _good_dataframe(lap_time=90.0)
    result = validate_dataframe(df, _meta(lap_time=90.0, sector_times=(10.0, 10.0, 10.0)))  # sums to 30, not 90
    assert result.is_valid is False
    assert any("sector_times" in r for r in result.reasons)


# (d) NaN/Inf anywhere in the required numeric columns.
def test_nan_in_telemetry_rejected():
    df = _good_dataframe()
    df.loc[5, "throttle"] = float("nan")
    result = validate_dataframe(df, _meta())
    assert result.is_valid is False
    assert any("NaN" in r or "Inf" in r for r in result.reasons)


def test_inf_in_telemetry_rejected():
    df = _good_dataframe()
    df.loc[5, "speed"] = float("inf")
    result = validate_dataframe(df, _meta())
    assert result.is_valid is False
    assert any("NaN" in r or "Inf" in r for r in result.reasons)


# (e) other implausible/invalid telemetry shapes.
def test_missing_required_column_rejected():
    df = _good_dataframe().drop(columns=["brake"])
    result = validate_dataframe(df, _meta())
    assert result.is_valid is False
    assert any("missing required columns" in r for r in result.reasons)


def test_empty_dataframe_rejected():
    result = validate_dataframe(pd.DataFrame(columns=REQUIRED_COLS), _meta())
    assert result.is_valid is False
    assert any("no samples" in r for r in result.reasons)


def test_out_of_range_throttle_rejected():
    df = _good_dataframe()
    df.loc[0, "throttle"] = 5.0  # way outside [0,1]
    result = validate_dataframe(df, _meta())
    assert result.is_valid is False
    assert any("throttle" in r for r in result.reasons)


def test_out_of_range_gear_rejected():
    df = _good_dataframe()
    df.loc[0, "gear"] = 99
    result = validate_dataframe(df, _meta())
    assert result.is_valid is False
    assert any("gear" in r for r in result.reasons)


def test_non_monotonic_time_rejected():
    df = _good_dataframe()
    df.loc[10, "t"] = df.loc[0, "t"]  # time jumps backwards
    result = validate_dataframe(df, _meta())
    assert result.is_valid is False
    assert any("monotonically" in r for r in result.reasons)


def test_lap_dist_backward_jump_rejected():
    df = _good_dataframe()
    df.loc[50, "lap_dist"] = 0.0  # teleports back to the start mid-lap
    result = validate_dataframe(df, _meta())
    assert result.is_valid is False
    assert any("lap_dist" in r for r in result.reasons)


def test_sample_count_mismatch_rejected():
    df = _good_dataframe(n=100)
    result = validate_dataframe(df, _meta(sample_count=10))  # metadata claims far fewer samples than the file has
    assert result.is_valid is False
    assert any("sample_count" in r for r in result.reasons)


# V0.6.0: closed gap where sector_times=[0,0,0] skipped the sum check entirely.
def test_all_zero_sector_times_rejected_for_real_lap():
    df = _good_dataframe(lap_time=90.0)
    result = validate_dataframe(df, _meta(lap_time=90.0, sector_times=(0.0, 0.0, 0.0)))
    assert result.is_valid is False
    assert any("sector_times" in r for r in result.reasons)


# V0.6.0: lap_dist inconsistent with the speed trace (distance-integral check).
def test_lap_dist_inconsistent_with_speed_trace_rejected():
    df = _good_dataframe(lap_time=90.0)
    # Speed says ~4500m traveled (50 m/s * 90s), but lap_dist claims 50km —
    # wildly inconsistent, no backward jumps involved.
    df["lap_dist"] = np.linspace(0, 50000, len(df))
    result = validate_dataframe(df, _meta(lap_time=90.0))
    assert result.is_valid is False
    assert any("distance implied by the speed trace" in r for r in result.reasons)
