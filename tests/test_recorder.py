"""
Tests for client/telemetry/recorder.py: filename collision-safety and
atomic JSON metadata writes.

`to_parquet` is mocked out (no pyarrow/fastparquet dependency needed here)
since these tests are about file-naming and JSON-write behavior, not
parquet serialization itself.
"""

import json
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

from client.telemetry.parser import Lap, TelemetrySample
from client.telemetry.recorder import LapRecorder


def _sample_lap(lap_number=1):
    return Lap(
        track_name="Le Mans", car_name="GT3", session_type=10,
        lap_number=lap_number, lap_time=90.0, sector_times=[30.0, 30.0, 30.0],
        is_valid=True, started_at=0.0, ambient_temp=20.0, track_temp=25.0,
        samples=[TelemetrySample(
            t=0.0, lap_dist=0.0, speed=50.0, throttle=1.0, brake=0.0, clutch=0.0,
            steering=0.0, gear=3, rpm=6000.0, fuel=50.0, pos_x=0.0, pos_y=0.0, pos_z=0.0,
            tire_pressures=(1.0, 1.0, 1.0, 1.0), tire_temps_outer=(90.0, 90.0, 90.0, 90.0),
            brake_temps=(300.0, 300.0, 300.0, 300.0),
        )],
    )


# (h) two laps completed within the same wall-clock second must not
# overwrite each other's files.
def test_two_laps_in_same_second_do_not_collide():
    with tempfile.TemporaryDirectory() as tmp:
        recorder = LapRecorder(data_dir=tmp)
        with patch("pandas.DataFrame.to_parquet"), patch("time.time", return_value=1_700_000_000.0):
            path_a = recorder.save(_sample_lap(lap_number=1))
            path_b = recorder.save(_sample_lap(lap_number=1))  # same second, same lap_number
        assert path_a is not None and path_b is not None
        assert path_a != path_b
        assert path_a.exists() and path_b.exists()


def test_save_returns_none_for_lap_with_no_samples():
    with tempfile.TemporaryDirectory() as tmp:
        recorder = LapRecorder(data_dir=tmp)
        lap = _sample_lap()
        lap.samples = []
        assert recorder.save(lap) is None


def test_metadata_write_is_atomic_no_partial_file_left_on_crash():
    """Simulates a crash mid-write: os.replace should mean the destination
    either doesn't exist yet, or is the fully-written previous version —
    never a truncated partial write."""
    with tempfile.TemporaryDirectory() as tmp:
        recorder = LapRecorder(data_dir=tmp)
        with patch("pandas.DataFrame.to_parquet"):
            meta_path = recorder.save(_sample_lap())

        original_content = meta_path.read_text(encoding="utf-8")

        with patch("pathlib.Path.write_text", side_effect=OSError("simulated crash")):
            try:
                recorder.mark_uploaded(meta_path)
            except OSError:
                pass

        # Original file must be untouched — the failed write happened on a
        # temp file that was never renamed into place.
        assert meta_path.read_text(encoding="utf-8") == original_content
        assert json.loads(original_content)["uploaded"] is False


def test_mark_uploaded_sets_flag():
    with tempfile.TemporaryDirectory() as tmp:
        recorder = LapRecorder(data_dir=tmp)
        with patch("pandas.DataFrame.to_parquet"):
            meta_path = recorder.save(_sample_lap())
        recorder.mark_uploaded(meta_path)
        assert json.loads(meta_path.read_text(encoding="utf-8"))["uploaded"] is True


def test_pending_uploads_excludes_uploaded_laps():
    with tempfile.TemporaryDirectory() as tmp:
        recorder = LapRecorder(data_dir=tmp)
        with patch("pandas.DataFrame.to_parquet"):
            path_a = recorder.save(_sample_lap(lap_number=1))
            path_b = recorder.save(_sample_lap(lap_number=2))
        recorder.mark_uploaded(path_a)
        pending = list(recorder.pending_uploads())
        assert path_a not in pending
        assert path_b in pending
