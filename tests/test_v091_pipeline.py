"""V0.9.1 phase 2 — the upload data path must not hold a lap file in memory.

Covers, with real objects (no mocks of the code under test):
  * sha256_file: same digest as hashing read_bytes(), for sizes around the chunk
    boundary, and bounded memory on a file much bigger than one chunk
  * StreamingMultipart: a well-formed multipart body, exact length, identical
    bytes however it is read, a fresh start per attempt, and clean failures
  * a real Uploader talking to the real upload endpoint over real HTTP: the
    stored file is byte-identical and its hash is the content address
  * validate_telemetry_file: same verdicts as validate_telemetry (bytes), no
    exception on damaged files
"""

from __future__ import annotations

import hashlib
import io
import json
import tracemalloc
from email import policy
from email.parser import BytesParser
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import requests

from client.uploader.uploader import STREAM_CHUNK_BYTES, StreamingMultipart, Uploader, sha256_file
from server.validation import validate_telemetry, validate_telemetry_file

MIB = 1024 * 1024


# ---------------------------------------------------------------------------
# sha256_file
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "size",
    [0, 1, STREAM_CHUNK_BYTES - 1, STREAM_CHUNK_BYTES, STREAM_CHUNK_BYTES + 1, 3 * STREAM_CHUNK_BYTES + 17],
)
def test_sha256_file_matches_hashing_the_bytes(tmp_path, size):
    path = tmp_path / "f.bin"
    data = np.random.default_rng(size).bytes(size)
    path.write_bytes(data)
    assert sha256_file(path) == hashlib.sha256(data).hexdigest()


def test_sha256_file_memory_is_bounded_by_the_chunk_not_the_file(tmp_path):
    path = tmp_path / "big.bin"
    path.write_bytes(np.random.default_rng(1).bytes(24 * MIB))
    tracemalloc.start()
    try:
        digest = sha256_file(path)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert digest == hashlib.sha256(path.read_bytes()).hexdigest()
    assert peak < 4 * MIB, f"hashing a 24 MiB file peaked at {peak / MIB:.1f} MiB"


# ---------------------------------------------------------------------------
# StreamingMultipart
# ---------------------------------------------------------------------------

FIELDS = {"envelope": json.dumps({"metadata": {"a": 1}, "client_version": "0.9.1"}), "signature": "ab12"}


def _read_all(body: StreamingMultipart, piece: int) -> bytes:
    out = bytearray()
    while True:
        chunk = body.read(piece)
        if not chunk:
            return bytes(out)
        out += chunk


def test_streaming_multipart_is_a_wellformed_multipart_body(tmp_path):
    payload = np.random.default_rng(2).bytes(2 * STREAM_CHUNK_BYTES + 123)
    path = tmp_path / "lap.parquet"
    path.write_bytes(payload)
    with StreamingMultipart(FIELDS, "telemetry", path) as body:
        raw = _read_all(body, 65536)
        assert len(raw) == len(body)
        content_type = body.content_type

    message = BytesParser(policy=policy.HTTP).parsebytes(b"Content-Type: " + content_type.encode() + b"\r\n\r\n" + raw)
    parts = {part.get_param("name", header="content-disposition"): part for part in message.iter_parts()}
    assert set(parts) == {"envelope", "signature", "telemetry"}
    assert parts["envelope"].get_content() == FIELDS["envelope"]
    assert parts["signature"].get_content() == FIELDS["signature"]
    assert parts["telemetry"].get_filename() == "lap.parquet"
    assert parts["telemetry"].get_payload(decode=True) == payload


@pytest.mark.parametrize("piece", [1, 7, 4096, STREAM_CHUNK_BYTES, 10 * STREAM_CHUNK_BYTES])
def test_streaming_multipart_bytes_do_not_depend_on_how_it_is_read(tmp_path, piece):
    path = tmp_path / "lap.parquet"
    path.write_bytes(np.random.default_rng(3).bytes(300_000))
    # Each instance has its own random boundary, so compare with the boundary normalised.
    with StreamingMultipart(FIELDS, "telemetry", path) as a, StreamingMultipart(FIELDS, "telemetry", path) as b:
        raw_a = _read_all(a, piece)      # read(n) path, n varies
        raw_b = b"".join(b)              # __iter__ path (what http.client / requests use)
        assert len(raw_a) == len(a) and len(raw_b) == len(b)
        assert raw_a.replace(a.boundary.encode(), b"B") == raw_b.replace(b.boundary.encode(), b"B")


def test_streaming_multipart_empty_file_still_valid(tmp_path):
    path = tmp_path / "empty.parquet"
    path.write_bytes(b"")
    with StreamingMultipart(FIELDS, "telemetry", path) as body:
        raw = _read_all(body, 1024)
        assert len(raw) == len(body)
        assert raw.endswith(f"\r\n--{body.boundary}--\r\n".encode())


def test_streaming_multipart_requires_a_with_block(tmp_path):
    path = tmp_path / "lap.parquet"
    path.write_bytes(b"x")
    body = StreamingMultipart(FIELDS, "telemetry", path)
    with pytest.raises(ValueError):
        body.read(10)


def test_streaming_multipart_reports_a_file_that_shrinks_mid_upload(tmp_path):
    path = tmp_path / "lap.parquet"
    path.write_bytes(b"x" * 100_000)
    with StreamingMultipart(FIELDS, "telemetry", path, chunk_size=10_000) as body:
        body.read(20_000)
        with open(path, "r+b") as handle:
            handle.truncate(30_000)
        with pytest.raises(OSError):
            _read_all(body, 10_000)


def test_streaming_multipart_closes_the_file(tmp_path):
    path = tmp_path / "lap.parquet"
    path.write_bytes(b"x" * 10)
    with StreamingMultipart(FIELDS, "telemetry", path) as body:
        handle = body._handle
    assert handle.closed


def test_streaming_beats_requests_files_on_memory(tmp_path):
    """The reason this class exists: requests' own `files=` copies the file into
    the request body. Both numbers are measured, so the test also proves the
    comparison is meaningful (the old way really is O(file))."""
    path = tmp_path / "big.parquet"
    path.write_bytes(np.random.default_rng(4).bytes(24 * MIB))

    tracemalloc.start()
    try:
        with path.open("rb") as handle:
            requests.Request("POST", "http://x.invalid/u", data=FIELDS, files={"telemetry": ("big.parquet", handle)}).prepare()
        old_peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()

    tracemalloc.start()
    try:
        with StreamingMultipart(FIELDS, "telemetry", path) as body:
            prepared = requests.Request(
                "POST", "http://x.invalid/u", data=body, headers={"Content-Type": body.content_type}
            ).prepare()
            assert prepared.headers["Content-Length"] == str(len(body))
            assert "Transfer-Encoding" not in prepared.headers  # an exact length, not chunked
            sent = 0
            while True:
                chunk = body.read(8192)
                if not chunk:
                    break
                sent += len(chunk)
        new_peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()

    assert sent == int(prepared.headers["Content-Length"])
    assert old_peak > 24 * MIB          # the old way holds (at least) the whole file
    assert new_peak < 3 * MIB           # the new way holds about one chunk


# ---------------------------------------------------------------------------
# A real Uploader against the real endpoint, over real HTTP
# ---------------------------------------------------------------------------

def _write_lap_file(directory: Path, n: int = 8000, extra_columns: int = 60) -> Path:
    """A lap file that passes server validation and is several MiB (random
    float32 columns barely compress), like a real extended recording."""
    rng = np.random.default_rng(5)
    lap_time = 90.0
    data = {
        "t": np.linspace(0, lap_time, n),
        "lap_dist": np.linspace(0, 4500, n),
        "speed": np.full(n, 50.0),
        "throttle": np.full(n, 0.8),
        "brake": np.zeros(n),
        "clutch": np.zeros(n),
        "steering": np.zeros(n),
        "gear": np.full(n, 4),
        "rpm": np.full(n, 6000.0),
        "fuel": np.linspace(50, 48, n),
    }
    for i in range(extra_columns):
        data[f"ext_{i}"] = rng.normal(size=n).astype(np.float32)
    df = pd.DataFrame(data)
    parquet = directory / "lap.parquet"
    df.to_parquet(parquet, index=False, compression="zstd")
    (directory / "lap.json").write_text(json.dumps({
        "track_name": "Le Mans", "car_name": "499P Custom Team 2025 #397", "car_class": "Hypercar",
        "car_model": "Ferrari 499P", "session_type": 10, "lap_number": 1, "lap_time": lap_time,
        "sector_times": [30.0, 30.0, 30.0], "is_valid": True, "started_at": 0.0, "recorded_at": 1700000000,
        "ambient_temp": 22.0, "track_temp": 30.0, "sample_count": n, "telemetry_file": "lap.parquet",
        "uploaded": False,
    }), encoding="utf-8")
    return directory / "lap.json"


def test_real_uploader_streams_a_lap_to_the_real_endpoint(client, tmp_path):
    from server.database import SessionLocal
    from server.models import Lap
    from tests.local_servers import RealAppServer
    from client.telemetry.recorder import LapRecorder

    account = client.post("/accounts/register", params={"display_name": "Streamer"}).json()
    meta_path = _write_lap_file(tmp_path)
    original = (tmp_path / "lap.parquet").read_bytes()
    assert len(original) > 2 * STREAM_CHUNK_BYTES  # spans several chunks, so streaming means something

    server = RealAppServer().start()
    try:
        uploader = Uploader(
            recorder=LapRecorder(data_dir=str(tmp_path)), backend_url=server.url,
            auth_token=account["auth_token"], client_secret=account["client_secret"],
        )
        assert uploader.upload_one(meta_path) is True
    finally:
        server.stop()

    with SessionLocal() as db:
        lap = db.query(Lap).one()
        stored = Path(lap.telemetry_path)
        assert stored.name == f"{hashlib.sha256(original).hexdigest()}.parquet"  # content-addressed
        assert stored.read_bytes() == original                                   # byte-identical
        assert lap.is_valid is True
    assert json.loads(meta_path.read_text())["uploaded"] is True


def test_real_uploader_second_upload_is_a_duplicate_not_a_second_file(client, tmp_path):
    from server.database import SessionLocal
    from server.models import Lap
    from tests.local_servers import RealAppServer
    from client.telemetry.recorder import LapRecorder

    account = client.post("/accounts/register", params={"display_name": "Twice"}).json()
    meta_path = _write_lap_file(tmp_path, n=500, extra_columns=5)
    server = RealAppServer().start()
    try:
        uploader = Uploader(
            recorder=LapRecorder(data_dir=str(tmp_path)), backend_url=server.url,
            auth_token=account["auth_token"], client_secret=account["client_secret"],
        )
        assert uploader.upload_one(meta_path) is True
        # reset the local flag as if the first response had been lost
        meta = json.loads(meta_path.read_text())
        meta["uploaded"] = False
        meta_path.write_text(json.dumps(meta))
        assert uploader.upload_one(meta_path) is True
    finally:
        server.stop()
    with SessionLocal() as db:
        assert db.query(Lap).count() == 1


# ---------------------------------------------------------------------------
# validate_telemetry_file
# ---------------------------------------------------------------------------

class _Meta:
    def __init__(self, lap_time=90.0, sample_count=None):
        self.lap_time = lap_time
        self.sector_times = [lap_time / 3] * 3
        self.sample_count = sample_count


def _frame(n=400, lap_time=90.0, speed=50.0, extra=3):
    t = np.linspace(0, lap_time, n)
    data = {
        "t": t, "lap_dist": speed * t, "speed": np.full(n, speed), "throttle": np.full(n, 0.5),
        "brake": np.zeros(n), "clutch": np.zeros(n), "steering": np.zeros(n), "gear": np.full(n, 3),
        "rpm": np.full(n, 5000.0), "fuel": np.linspace(50, 49, n),
    }
    for i in range(extra):
        data[f"ext_{i}"] = np.random.default_rng(i).normal(size=n).astype(np.float32)
    return pd.DataFrame(data)


def _verdicts_agree(path: Path, meta) -> None:
    from_bytes = validate_telemetry(path.read_bytes(), meta)
    from_file = validate_telemetry_file(path, meta)
    assert from_file.is_valid == from_bytes.is_valid
    if from_bytes.is_valid:
        assert from_file.reasons == []


def test_file_validation_agrees_with_bytes_validation_for_a_good_lap(tmp_path):
    path = tmp_path / "ok.parquet"
    _frame().to_parquet(path, index=False)
    _verdicts_agree(path, _Meta(sample_count=400))
    assert validate_telemetry_file(path, _Meta(sample_count=400)).is_valid


def test_file_validation_agrees_for_each_kind_of_bad_lap(tmp_path):
    bad = {
        "nan": lambda d: d.assign(speed=np.where(np.arange(len(d)) == 5, np.nan, d["speed"])),
        "too_fast": lambda d: d.assign(speed=500.0),
        "backward": lambda d: d.assign(lap_dist=d["lap_dist"].iloc[::-1].to_numpy()),
        "no_samples": lambda d: d.iloc[0:0],
        "missing_column": lambda d: d.drop(columns=["rpm"]),
    }
    for name, mutate in bad.items():
        path = tmp_path / f"{name}.parquet"
        mutate(_frame()).to_parquet(path, index=False)
        from_bytes = validate_telemetry(path.read_bytes(), _Meta())
        from_file = validate_telemetry_file(path, _Meta())
        assert from_bytes.is_valid is False, name
        assert from_file.is_valid is False, name
        assert from_file.reasons == from_bytes.reasons, name


def test_file_validation_never_raises_on_damaged_files(tmp_path):
    good = tmp_path / "good.parquet"
    _frame().to_parquet(good, index=False)
    raw = good.read_bytes()

    cases = {
        "garbage": b"this is not a parquet file at all",
        "empty": b"",
        "truncated": raw[: len(raw) // 2],
        "footer_cut": raw[:-20],
        "head_only": raw[:4],
    }
    for name, content in cases.items():
        path = tmp_path / f"{name}.parquet"
        path.write_bytes(content)
        result = validate_telemetry_file(path, _Meta())
        assert result.is_valid is False, name
        assert "could not be parsed" in result.reasons[0], name

    missing = validate_telemetry_file(tmp_path / "does_not_exist.parquet", _Meta())
    assert missing.is_valid is False


def test_file_validation_reads_only_the_checked_columns(tmp_path, monkeypatch):
    import pyarrow.parquet as pq

    path = tmp_path / "wide.parquet"
    _frame(extra=40).to_parquet(path, index=False)
    requested = {}
    original = pq.ParquetFile.read

    def spy(self, columns=None, *args, **kwargs):
        requested["columns"] = columns
        return original(self, columns=columns, *args, **kwargs)

    monkeypatch.setattr(pq.ParquetFile, "read", spy)
    assert validate_telemetry_file(path, _Meta()).is_valid
    assert len(requested["columns"]) == 10 and not any(c.startswith("ext_") for c in requested["columns"])

    validate_telemetry_file(path, _Meta(), full_check=True)
    assert requested["columns"] is None  # full check decodes everything


def test_upload_endpoint_validates_from_the_file_not_from_memory(client, tmp_path, monkeypatch):
    """Guards against a regression to read_bytes() in the upload endpoint."""
    import server.routers.telemetry as telemetry_router
    from client.telemetry.recorder import LapRecorder

    seen = []
    real = telemetry_router.validate_telemetry_file

    def spy(path, metadata, **kwargs):
        seen.append(Path(path))
        return real(path, metadata, **kwargs)

    monkeypatch.setattr(telemetry_router, "validate_telemetry_file", spy)
    assert not hasattr(telemetry_router, "validate_telemetry"), "the bytes-based validator must not be wired in again"

    account = client.post("/accounts/register", params={"display_name": "FileCheck"}).json()
    meta_path = _write_lap_file(tmp_path, n=500, extra_columns=3)
    uploader = Uploader(recorder=LapRecorder(data_dir=str(tmp_path)), auth_token=account["auth_token"],
                        client_secret=account["client_secret"])
    payload = uploader._build_payload(meta_path)
    resp = client.post(
        "/telemetry/upload",
        data={"envelope": json.dumps(payload["envelope"]), "signature": payload["signature"]},
        files={"telemetry": ("lap.parquet", io.BytesIO(payload["telemetry_path"].read_bytes()))},
        headers={"Authorization": f"Bearer {account['auth_token']}"},
    )
    assert resp.status_code == 200, resp.text
    assert len(seen) == 1 and seen[0].suffix == ".parquet"
