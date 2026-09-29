"""
Vertragstest: client envelope → server schema.

This is the test that would have caught V0.5.2's P0-1 bug: the client
included the local-only `uploaded` field in the metadata, and the server's
LapMetadata (extra="forbid") rejected every upload with 400.
"""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from server.schemas import LapMetadata


def _write_fake_lap(tmp_dir: Path) -> Path:
    """Simulates what recorder.save() writes — the exact JSON format
    the real client produces, including the local-only `uploaded` field."""
    telemetry_path = tmp_dir / "lap.parquet"
    telemetry_path.write_bytes(b"fake-parquet-content")
    meta = {
        "track_name": "Le Mans",
        "car_name": "GT3",
        "session_type": 10,
        "lap_number": 3,
        "lap_time": 90.123,
        "sector_times": [30.0, 30.0, 30.123],
        "is_valid": True,
        "started_at": 120.5,
        "recorded_at": 1700000000,
        "ambient_temp": 22.0,
        "track_temp": 30.0,
        "sample_count": 5400,
        "telemetry_file": "lap.parquet",
        "uploaded": False,  # <-- the field that broke V0.5.2
    }
    meta_path = tmp_dir / "lap.json"
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return meta_path


def test_client_envelope_passes_server_schema():
    """The metadata the client sends (after stripping local-only fields)
    must pass LapMetadata.model_validate without error."""
    from client.uploader.uploader import Uploader
    from client.telemetry.recorder import LapRecorder

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        meta_path = _write_fake_lap(tmp_dir)
        uploader = Uploader(
            recorder=LapRecorder(data_dir=str(tmp_dir)),
            auth_token="tok",
            client_secret="secret",
        )
        payload = uploader._build_payload(meta_path)
        wire_metadata = payload["envelope"]["metadata"]

        # This is the exact check the server does.  If `uploaded` leaks
        # through, this raises ValidationError.
        validated = LapMetadata.model_validate(wire_metadata)
        assert validated.track_name == "Le Mans"
        assert validated.lap_time == 90.123
        # `uploaded` must not be present
        assert "uploaded" not in wire_metadata


def test_uploaded_field_stripped_from_wire_metadata():
    """Directly verify _build_payload strips the `uploaded` key."""
    from client.uploader.uploader import Uploader
    from client.telemetry.recorder import LapRecorder

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        meta_path = _write_fake_lap(tmp_dir)
        uploader = Uploader(
            recorder=LapRecorder(data_dir=str(tmp_dir)),
            auth_token="tok",
            client_secret="secret",
        )
        payload = uploader._build_payload(meta_path)
        assert "uploaded" not in payload["envelope"]["metadata"]


def test_client_version_is_current():
    """client_version should reflect the actual release, not 0.1.0."""
    from client.uploader.uploader import Uploader
    from client.telemetry.recorder import LapRecorder

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        meta_path = _write_fake_lap(tmp_dir)
        uploader = Uploader(
            recorder=LapRecorder(data_dir=str(tmp_dir)),
            auth_token="tok",
            client_secret="secret",
        )
        payload = uploader._build_payload(meta_path)
        assert payload["envelope"]["client_version"] == "0.8.6"
