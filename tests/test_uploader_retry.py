"""
Tests for client/uploader/uploader.py's retry policy: transient failures
(408/429/5xx, network errors) get retried; permanent 4xx failures don't.

Runs against the real Uploader class with requests.post mocked out (no
actual network calls) — `requests` itself is a real dependency here, just
not the network. Deliberately fixture-free (plain helper function instead
of a pytest fixture) so this also runs under tests/run_stdlib_tests.py
without pytest installed.
"""

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from client.uploader.uploader import Uploader, _is_transient_status


def test_is_transient_status_classification():
    assert _is_transient_status(408) is True
    assert _is_transient_status(429) is True
    assert _is_transient_status(500) is True
    assert _is_transient_status(503) is True
    assert _is_transient_status(400) is False
    assert _is_transient_status(401) is False
    assert _is_transient_status(403) is False
    assert _is_transient_status(404) is False
    assert _is_transient_status(413) is False
    assert _is_transient_status(422) is False


def _make_fake_lap(tmp_dir: Path) -> Path:
    """A minimal on-disk lap (metadata + a stand-in telemetry file) good
    enough for Uploader._build_payload, which only reads bytes and never
    parses the telemetry file itself (the server does that)."""
    telemetry_path = tmp_dir / "lap.parquet"
    telemetry_path.write_bytes(b"not-real-parquet-bytes-but-build_payload-only-hashes-them")
    meta_path = tmp_dir / "lap.json"
    meta_path.write_text(json.dumps({
        "track_name": "Le Mans", "car_name": "GT3", "session_type": 10,
        "lap_number": 1, "lap_time": 90.0, "sector_times": [30.0, 30.0, 30.0],
        "is_valid": True, "started_at": 0.0, "recorded_at": 0,
        "telemetry_file": "lap.parquet", "uploaded": False,
    }), encoding="utf-8")
    return meta_path


def _make_uploader(tmp_dir: Path) -> Uploader:
    from client.telemetry.recorder import LapRecorder
    return Uploader(recorder=LapRecorder(data_dir=str(tmp_dir)), auth_token="tok", client_secret="secret")


def test_permanent_4xx_is_not_retried():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        fake_lap = _make_fake_lap(tmp_dir)
        uploader = _make_uploader(tmp_dir)
        response = MagicMock(status_code=400, text="bad request")
        with patch("client.uploader.uploader.requests.post", return_value=response) as mock_post, \
             patch("client.uploader.uploader.time.sleep") as mock_sleep:
            result = uploader.upload_one(fake_lap)
        assert result is False
        assert mock_post.call_count == 1  # exactly one attempt, no retry loop
        mock_sleep.assert_not_called()


def test_500_is_retried_up_to_max_attempts():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        fake_lap = _make_fake_lap(tmp_dir)
        uploader = _make_uploader(tmp_dir)
        response = MagicMock(status_code=500, text="server error")
        with patch("client.uploader.uploader.requests.post", return_value=response) as mock_post, \
             patch("client.uploader.uploader.time.sleep"):
            result = uploader.upload_one(fake_lap)
        assert result is False
        assert mock_post.call_count == 5  # MAX_RETRIES


def test_429_is_retried():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        fake_lap = _make_fake_lap(tmp_dir)
        uploader = _make_uploader(tmp_dir)
        response = MagicMock(status_code=429, text="rate limited")
        with patch("client.uploader.uploader.requests.post", return_value=response) as mock_post, \
             patch("client.uploader.uploader.time.sleep"):
            result = uploader.upload_one(fake_lap)
        assert result is False
        assert mock_post.call_count == 5


def test_success_after_transient_failure():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        fake_lap = _make_fake_lap(tmp_dir)
        uploader = _make_uploader(tmp_dir)
        failing = MagicMock(status_code=503, text="unavailable")
        succeeding = MagicMock(status_code=200, text="ok")
        with patch("client.uploader.uploader.requests.post", side_effect=[failing, succeeding]) as mock_post, \
             patch("client.uploader.uploader.time.sleep"):
            result = uploader.upload_one(fake_lap)
        assert result is True
        assert mock_post.call_count == 2
        assert json.loads(fake_lap.read_text(encoding="utf-8"))["uploaded"] is True


def test_200_marks_uploaded_and_does_not_retry():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        fake_lap = _make_fake_lap(tmp_dir)
        uploader = _make_uploader(tmp_dir)
        response = MagicMock(status_code=200, text="ok")
        with patch("client.uploader.uploader.requests.post", return_value=response) as mock_post:
            result = uploader.upload_one(fake_lap)
        assert result is True
        assert mock_post.call_count == 1


# V0.6.0: 401 aborts the whole run_once() cycle instead of retrying with
# the same dead token, and a permanent 4xx marks the lap `rejected` on
# disk instead of retrying forever every cycle.
def test_401_raises_auth_abort_and_stops_cycle():
    from client.uploader.uploader import _AuthAbort
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        fake_lap = _make_fake_lap(tmp_dir)
        uploader = _make_uploader(tmp_dir)
        response = MagicMock(status_code=401, text="revoked")
        with patch("client.uploader.uploader.requests.post", return_value=response), \
             patch("client.uploader.uploader.time.sleep"):
            try:
                uploader.upload_one(fake_lap)
                assert False, "expected _AuthAbort"
            except _AuthAbort:
                pass


def test_run_once_stops_on_auth_abort_without_touching_other_laps():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        lap_a = _make_fake_lap(tmp_dir)
        # second lap in a separate subdir so both are discovered by pending_uploads()
        sub = tmp_dir / "other"
        sub.mkdir()
        lap_b = _make_fake_lap(sub)
        uploader = _make_uploader(tmp_dir)
        response = MagicMock(status_code=401, text="revoked")
        with patch("client.uploader.uploader.requests.post", return_value=response) as mock_post, \
             patch("client.uploader.uploader.time.sleep"):
            successes = uploader.run_once()
        assert successes == 0
        # Aborted after the first 401 — must not have tried to POST for both laps
        assert mock_post.call_count == 1


def test_permanent_failure_marks_rejected_and_excluded_from_pending():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        fake_lap = _make_fake_lap(tmp_dir)
        uploader = _make_uploader(tmp_dir)
        response = MagicMock(status_code=400, text="bad request")
        with patch("client.uploader.uploader.requests.post", return_value=response), \
             patch("client.uploader.uploader.time.sleep"):
            uploader.upload_one(fake_lap)
        meta = json.loads(fake_lap.read_text(encoding="utf-8"))
        assert meta["rejected"] is True
        assert "400" in meta["rejected_reason"]
        # No longer offered by pending_uploads()
        assert list(uploader.recorder.pending_uploads()) == []


def test_poisoned_lap_does_not_block_the_rest_of_the_queue():
    """A corrupt lap.json (unreadable / missing telemetry_file) must not
    abort the whole run_once() cycle — the audit finding this fixes."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        poisoned_dir = tmp_dir / "poisoned"
        poisoned_dir.mkdir()
        poisoned_meta = poisoned_dir / "lap.json"
        poisoned_meta.write_text(json.dumps({
            "telemetry_file": "does-not-exist.parquet", "uploaded": False,
        }), encoding="utf-8")

        good_dir = tmp_dir / "good"
        good_dir.mkdir()
        good_lap = _make_fake_lap(good_dir)

        uploader = _make_uploader(tmp_dir)
        response = MagicMock(status_code=200, text="ok")
        with patch("client.uploader.uploader.requests.post", return_value=response), \
             patch("client.uploader.uploader.time.sleep"):
            successes = uploader.run_once()
        # The good lap still uploaded despite the poisoned one failing first
        # (iteration order isn't guaranteed, but both must be attempted).
        assert successes == 1
        assert json.loads(good_lap.read_text(encoding="utf-8"))["uploaded"] is True
