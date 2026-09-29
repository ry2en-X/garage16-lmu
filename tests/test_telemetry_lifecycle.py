"""Tests for server/telemetry_lifecycle.py (V0.7.2 §12 — telemetry
storage lifecycle / orphan detection).

Note: the test suite's storage dir (conftest.py's _tmp_storage_dir) is
shared across the WHOLE session and never cleaned between tests, while
each test's DB tables ARE dropped/recreated — so files from earlier
tests' lap uploads can legitimately linger on disk with no matching DB
row by the time this file runs. That's fine and expected: everything
written during a test run is only seconds old, well under the default
1-hour orphan-age threshold, so it's invisible to find_orphaned_files()
unless a test explicitly backdates a file's mtime. Assertions here check
for a SPECIFIC path's presence/absence rather than exact list lengths,
so they aren't fragile to whatever else the storage dir accumulates.
"""

from __future__ import annotations

import os
import time
from datetime import timedelta
from pathlib import Path

from server.config import settings
from server.database import SessionLocal
from server.models import Driver
from server.security import generate_token, hash_token
from server.telemetry_lifecycle import find_missing_files, find_orphaned_files


def _backdate(path: Path, hours: float) -> None:
    old = time.time() - hours * 3600
    os.utime(path, (old, old))


def _make_driver(db) -> Driver:
    driver = Driver(
        display_name="OrphanTestDriver",
        token_hash=hash_token(generate_token()),
        client_secret="unused",
    )
    db.add(driver)
    db.flush()
    return driver


def test_recent_file_with_no_db_row_is_not_yet_an_orphan(client):
    """Safety mechanism: a file could just be mid-upload (see
    routers/telemetry.py — the file is renamed to its final path BEFORE
    the Lap row commits). A fresh file must never be reported."""
    stray_dir = settings.telemetry_storage_dir / "driver_999999" / "orphan_track" / "orphan_car"
    stray_dir.mkdir(parents=True, exist_ok=True)
    fresh_file = stray_dir / "fresh_no_backdate.parquet"
    fresh_file.write_bytes(b"fake telemetry")

    db = SessionLocal()
    try:
        orphans = find_orphaned_files(db)
    finally:
        db.close()

    assert fresh_file.resolve() not in {o.path.resolve() for o in orphans}


def test_old_file_with_no_db_row_is_reported_as_orphaned(client):
    stray_dir = settings.telemetry_storage_dir / "driver_999999" / "orphan_track" / "orphan_car"
    stray_dir.mkdir(parents=True, exist_ok=True)
    old_file = stray_dir / "genuinely_old_orphan.parquet"
    old_file.write_bytes(b"fake telemetry")
    _backdate(old_file, hours=2)

    db = SessionLocal()
    try:
        orphans = find_orphaned_files(db, min_age=timedelta(hours=1))
    finally:
        db.close()

    assert old_file.resolve() in {o.path.resolve() for o in orphans}


def test_old_file_with_a_matching_lap_row_is_never_an_orphan(client):
    """The core correctness guarantee: even an old file is excluded the
    moment a Lap row actually points at it."""
    stray_dir = settings.telemetry_storage_dir / "driver_999999" / "orphan_track" / "orphan_car"
    stray_dir.mkdir(parents=True, exist_ok=True)
    linked_file = stray_dir / "has_a_real_lap.parquet"
    linked_file.write_bytes(b"fake telemetry")
    _backdate(linked_file, hours=5)

    db = SessionLocal()
    try:
        driver = _make_driver(db)
        from server.models import Lap
        db.add(Lap(
            driver_id=driver.id, track_name="T", car_name="C", session_type=10, lap_number=1,
            lap_time=90.0, sector_times=[30, 30, 30], is_valid=True, started_at=0.0,
            recorded_at=1700000000, sample_count=10, telemetry_hash="deadbeef",
            telemetry_path=str(linked_file),
        ))
        db.commit()

        orphans = find_orphaned_files(db, min_age=timedelta(hours=1))
    finally:
        db.close()

    assert linked_file.resolve() not in {o.path.resolve() for o in orphans}


def test_lap_row_pointing_at_a_missing_file_is_reported(client):
    db = SessionLocal()
    try:
        driver = _make_driver(db)
        from server.models import Lap
        lap = Lap(
            driver_id=driver.id, track_name="T", car_name="C", session_type=10, lap_number=1,
            lap_time=90.0, sector_times=[30, 30, 30], is_valid=True, started_at=0.0,
            recorded_at=1700000000, sample_count=10, telemetry_hash="cafef00d",
            telemetry_path=str(settings.telemetry_storage_dir / "does" / "not" / "exist.parquet"),
        )
        db.add(lap)
        db.commit()
        lap_id = lap.id

        missing = find_missing_files(db)
    finally:
        db.close()

    assert any(m.lap_id == lap_id for m in missing)


def test_lap_row_with_a_real_file_is_not_reported_missing(client):
    real_dir = settings.telemetry_storage_dir / "driver_999999" / "present_track" / "present_car"
    real_dir.mkdir(parents=True, exist_ok=True)
    real_file = real_dir / "present.parquet"
    real_file.write_bytes(b"fake telemetry")

    db = SessionLocal()
    try:
        driver = _make_driver(db)
        from server.models import Lap
        lap = Lap(
            driver_id=driver.id, track_name="T", car_name="C", session_type=10, lap_number=1,
            lap_time=90.0, sector_times=[30, 30, 30], is_valid=True, started_at=0.0,
            recorded_at=1700000000, sample_count=10, telemetry_hash="abc123",
            telemetry_path=str(real_file),
        )
        db.add(lap)
        db.commit()
        lap_id = lap.id

        missing = find_missing_files(db)
    finally:
        db.close()

    assert not any(m.lap_id == lap_id for m in missing)
