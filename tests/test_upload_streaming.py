"""
Tests for server/routers/telemetry.py's _stream_to_temp_file: the upload
size limit must be enforced *while streaming*, not after buffering the
whole file — and the partial temp file must be cleaned up when it is.

V0.8-NAS: _stream_to_temp_file (and upload_lap) were converted from
`async def` to plain `def` — a real deadlock fix, not a style change; see
upload_lap's docstring in server/routers/telemetry.py for the full story
(mixing blocking SQLite calls with `await` points on FastAPI's single
event-loop thread could freeze it entirely under genuine concurrent
requests). These tests were updated accordingly: the fake now exposes a
synchronous `.file.read(n)`, matching Starlette's real UploadFile.file
(a SpooledTemporaryFile), instead of an async `.read(n)` coroutine.
"""

from pathlib import Path

import pytest
from fastapi import HTTPException

from server.routers.telemetry import _stream_to_temp_file


class _FakeFile:
    """Duck-types just the synchronous `.read(n)` method
    _stream_to_temp_file actually calls via `upload.file.read(n)` —
    mirrors Starlette UploadFile.file (a SpooledTemporaryFile), not the
    UploadFile wrapper itself."""

    def __init__(self, data: bytes):
        self._data = data
        self._pos = 0

    def read(self, n: int) -> bytes:
        chunk = self._data[self._pos : self._pos + n]
        self._pos += len(chunk)
        return chunk


class _FakeUploadFile:
    """Duck-types just the `.file` attribute _stream_to_temp_file actually
    uses, so this doesn't need to build a real ASGI request."""

    def __init__(self, data: bytes):
        self.file = _FakeFile(data)


# (f) upload over the configured size limit.
def test_oversized_upload_rejected_and_temp_file_cleaned_up(tmp_path: Path):
    from server.config import settings

    original_limit = settings.max_upload_bytes
    settings.max_upload_bytes = 10  # bytes — trivially small for this test
    try:
        fake = _FakeUploadFile(b"this is way more than ten bytes of data")
        with pytest.raises(HTTPException) as exc_info:
            _stream_to_temp_file(fake, tmp_path)
        assert exc_info.value.status_code == 413
        # No leftover partial .tmp file after the rejection.
        assert list(tmp_path.glob("*.tmp")) == []
    finally:
        settings.max_upload_bytes = original_limit


def test_upload_within_limit_succeeds_and_hash_is_correct(tmp_path: Path):
    import hashlib

    data = b"small telemetry payload"
    fake = _FakeUploadFile(data)
    tmp_file, sha256_hex, total = _stream_to_temp_file(fake, tmp_path)
    assert total == len(data)
    assert sha256_hex == hashlib.sha256(data).hexdigest()
    assert tmp_file.read_bytes() == data


def test_streaming_reads_in_chunks_not_all_at_once(tmp_path: Path):
    """Confirms the implementation actually streams (multiple bounded
    reads) rather than a single unbounded .read() — the whole point of
    this fix. A naive `upload.file.read()` with no size argument would
    still pass the two tests above but would defeat the memory-safety
    goal entirely.
    """
    read_sizes = []

    class _TrackingFile(_FakeFile):
        def read(self, n: int) -> bytes:
            read_sizes.append(n)
            return super().read(n)

    class _TrackingUploadFile(_FakeUploadFile):
        def __init__(self, data: bytes):
            self.file = _TrackingFile(data)

    fake = _TrackingUploadFile(b"x" * 5000)
    _stream_to_temp_file(fake, tmp_path)
    assert len(read_sizes) >= 1
    assert all(isinstance(n, int) and n > 0 for n in read_sizes)
    # Every requested chunk size must be bounded — never "read everything,
    # whatever that turns out to be" (i.e. never called with no limit).
    assert all(n <= 1024 * 1024 for n in read_sizes)
