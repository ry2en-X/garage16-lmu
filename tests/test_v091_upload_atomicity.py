"""V0.9.1 §46 / §43 / §62 — the upload is atomic, leaves one canonical copy, and survives
bad input. Crash scenarios are simulated by making the exact step that would fail, fail:

  during the upload           the stream breaks            -> no temp file, no row
  hash does not match         client/transport damage      -> 400, no file, no row
  during the final move       os.replace fails             -> no temp file, no row
  before the DB commit        record evaluation raises     -> no file, no row
  at the DB commit            commit raises                -> no file, no row
  after the DB commit         a normal success             -> row AND identical file, nothing else
  lost race                   a concurrent identical upload committed first -> its file is NOT deleted
  duplicate                   the same lap twice           -> one row, one file
  damaged / truncated file    valid hash, unreadable parquet -> stored as invalid with a reason, no 500
  too large                   over max_upload_bytes        -> 413, nothing left behind

The invariant behind all of them: a lap row never exists without its file, and a file never
sits next to an uncommitted row for longer than the request that created it.
"""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path

import pytest

from client.telemetry.recorder import LapRecorder
from client.uploader.uploader import Uploader
from server.config import settings
from tests.test_v091_pipeline import _write_lap_file


def _setup(client, tmp_path, name="Atomic", **lap_kwargs):
    account = client.post("/accounts/register", params={"display_name": name}).json()
    meta_path = _write_lap_file(tmp_path, **lap_kwargs)
    uploader = Uploader(recorder=LapRecorder(data_dir=str(tmp_path)), auth_token=account["auth_token"], client_secret=account["client_secret"])
    return account, meta_path, uploader


def _post(client, account, uploader, meta_path, *, content=None, envelope_hash=None):
    """Posts a lap the way the uploader would. `content` replaces the file bytes
    (the envelope — and so the signature — still describes `envelope_hash` or the real file)."""
    payload = uploader._build_payload(meta_path)
    envelope = dict(payload["envelope"])
    data = content if content is not None else payload["telemetry_path"].read_bytes()
    if content is not None and envelope_hash is None:
        envelope["telemetry_hash"] = hashlib.sha256(content).hexdigest()   # a consistent, re-signed envelope
        payload["signature"] = uploader._sign(json.dumps(envelope, sort_keys=True).encode())
    if envelope_hash is not None:
        envelope["telemetry_hash"] = envelope_hash
        payload["signature"] = uploader._sign(json.dumps(envelope, sort_keys=True).encode())
    return client.post(
        "/telemetry/upload",
        data={"envelope": json.dumps(envelope), "signature": payload["signature"]},
        files={"telemetry": ("lap.parquet", io.BytesIO(data))},
        headers={"Authorization": f"Bearer {account['auth_token']}"},
    )


def _laps():
    from server.database import SessionLocal
    from server.models import Lap

    with SessionLocal() as db:
        return db.query(Lap).all()


def _stored_files():
    # V0.9.8: the write lock (.garage16-write.lock, server/write_lock.py) is infrastructure, not a lap file
    root = settings.telemetry_storage_dir
    return sorted(p for p in root.rglob("*") if p.is_file() and not p.relative_to(root).parts[0].startswith("."))


@pytest.fixture(autouse=True)
def _clean_storage():
    import shutil

    root = settings.telemetry_storage_dir
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)
    yield


def test_success_leaves_exactly_one_file_and_one_row_and_nothing_else(client, tmp_path):
    account, meta, uploader = _setup(client, tmp_path)
    original = (tmp_path / "lap.parquet").read_bytes()
    resp = _post(client, account, uploader, meta)
    assert resp.status_code == 200 and resp.json()["duplicate"] is False
    laps = _laps()
    assert len(laps) == 1
    files = _stored_files()
    assert files == [Path(laps[0].telemetry_path)], "one canonical file: no temp file, no decompressed or JSON copy"
    assert files[0].name == f"{hashlib.sha256(original).hexdigest()}.parquet"
    assert files[0].read_bytes() == original
    assert not any(p.suffix in (".json", ".tmp", ".csv", ".gz", ".zip") for p in files)


def test_the_same_lap_twice_is_one_row_and_one_file(client, tmp_path):
    account, meta, uploader = _setup(client, tmp_path)
    first = _post(client, account, uploader, meta).json()
    second = _post(client, account, uploader, meta).json()
    assert first["lap_id"] == second["lap_id"] and second["duplicate"] is True
    assert len(_laps()) == 1 and len(_stored_files()) == 1


def test_hash_mismatch_is_rejected_and_leaves_nothing(client, tmp_path):
    account, meta, uploader = _setup(client, tmp_path)
    resp = _post(client, account, uploader, meta, envelope_hash="0" * 64)
    assert resp.status_code == 400 and "does not match" in resp.json()["detail"]
    assert _laps() == [] and _stored_files() == []


def test_a_stream_that_breaks_mid_upload_removes_its_temp_file(tmp_path):
    from server.routers.telemetry import _stream_to_temp_file

    class Breaking:
        def __init__(self):
            self.calls = 0

        def read(self, n):
            self.calls += 1
            if self.calls == 1:
                return b"x" * 1000
            raise ConnectionResetError("client went away")

    class Upload:
        file = Breaking()

    with pytest.raises(ConnectionResetError):
        _stream_to_temp_file(Upload(), tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_a_failing_final_move_removes_the_temp_file_and_creates_no_row(client, tmp_path, monkeypatch):
    account, meta, uploader = _setup(client, tmp_path)
    real_replace = Path.replace

    def failing(self, target):
        if self.name.startswith(".upload-"):
            raise OSError("disk went read-only")
        return real_replace(self, target)

    monkeypatch.setattr(Path, "replace", failing)
    with pytest.raises(OSError):
        _post(client, account, uploader, meta)
    monkeypatch.undo()
    assert _laps() == []
    assert _stored_files() == [], "neither a temp file nor a final file is left"


def test_a_crash_before_the_commit_leaves_no_file_and_no_row(client, tmp_path, monkeypatch):
    import server.routers.telemetry as router

    account, meta, uploader = _setup(client, tmp_path)

    def boom(db, lap):
        raise RuntimeError("record evaluation crashed")

    monkeypatch.setattr(router, "evaluate_and_record", boom)
    with pytest.raises(RuntimeError):
        _post(client, account, uploader, meta)
    monkeypatch.undo()
    assert _laps() == [] and _stored_files() == []
    # and the very same upload works afterwards: nothing was half-recorded
    assert _post(client, account, uploader, meta).status_code == 200
    assert len(_laps()) == 1 and len(_stored_files()) == 1


def test_a_commit_that_fails_leaves_no_file_and_no_row(client, tmp_path, monkeypatch):
    from sqlalchemy.orm import Session

    account, meta, uploader = _setup(client, tmp_path)
    real_commit = Session.commit

    def failing(self):
        raise RuntimeError("database went away at COMMIT")

    monkeypatch.setattr(Session, "commit", failing)
    with pytest.raises(RuntimeError):
        _post(client, account, uploader, meta)
    monkeypatch.setattr(Session, "commit", real_commit)
    assert _laps() == [] and _stored_files() == []


def test_a_failure_after_the_commit_keeps_row_and_file_together(client, tmp_path, monkeypatch):
    from sqlalchemy.orm import Session

    account, meta, uploader = _setup(client, tmp_path)
    real_refresh = Session.refresh

    def failing(self, instance, *args, **kwargs):
        raise RuntimeError("connection lost while building the response")

    monkeypatch.setattr(Session, "refresh", failing)
    with pytest.raises(RuntimeError):
        _post(client, account, uploader, meta)
    monkeypatch.setattr(Session, "refresh", real_refresh)
    laps = _laps()
    assert len(laps) == 1 and Path(laps[0].telemetry_path).is_file(), "the row is committed, so its file must be there"
    # the client never saw the answer and retries: that is a duplicate, not a second lap
    again = _post(client, account, uploader, meta).json()
    assert again["duplicate"] is True and len(_laps()) == 1 and len(_stored_files()) == 1


def test_a_failed_upload_does_not_delete_a_file_that_a_committed_lap_points_at(client, tmp_path, monkeypatch):
    """The lost-race case. Files are content-addressed, so two uploads of the same bytes
    share one path. If the slower one fails after the faster one committed its row for that
    path, deleting the file would leave a committed lap with no telemetry.

    Set up deterministically (a second database session cannot write while the upload's own
    transaction holds SQLite's write lock): the "winner" row is committed BEFORE the upload
    and points at the exact path the upload is going to write; the upload then fails at the
    last step before its commit."""
    import server.routers.telemetry as router
    from server.database import SessionLocal
    from server.models import Driver, Lap
    from server.storage import telemetry_storage_dir

    account, meta, uploader = _setup(client, tmp_path, n=800, extra_columns=2)
    content_hash = hashlib.sha256((tmp_path / "lap.parquet").read_bytes()).hexdigest()
    with SessionLocal() as db:
        driver = db.query(Driver).one()
        shared_path = telemetry_storage_dir(driver.id, "Le Mans", "499P Custom Team 2025 #397") / f"{content_hash}.parquet"
        shared_path.write_bytes((tmp_path / "lap.parquet").read_bytes())          # the winner's file
        db.add(Lap(
            driver_id=driver.id, track_name="Le Mans", car_name="499P Custom Team 2025 #397", session_type=10, lap_number=1,
            lap_time=90.0, sector_times=[30, 30, 30], is_valid=True, client_claimed_valid=True, started_at=0.0, recorded_at=1,
            sample_count=800, telemetry_hash=f"winner-{content_hash}", telemetry_path=str(shared_path),
        ))
        db.commit()

    def slower_upload_fails(db, lap):
        raise RuntimeError("the slower upload fails before its commit")

    monkeypatch.setattr(router, "evaluate_and_record", slower_upload_fails)
    with pytest.raises(RuntimeError):
        _post(client, account, uploader, meta)
    monkeypatch.undo()

    laps = _laps()
    assert len(laps) == 1, "only the winner's row exists"
    assert Path(laps[0].telemetry_path) == shared_path and shared_path.is_file(), "a committed lap never loses its file"


def test_a_failed_upload_does_delete_a_file_nobody_else_points_at(client, tmp_path, monkeypatch):
    """The other half of the rule: with no committed row for the path, the file goes."""
    import server.routers.telemetry as router

    account, meta, uploader = _setup(client, tmp_path, n=800, extra_columns=2)
    monkeypatch.setattr(router, "evaluate_and_record", lambda db, lap: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        _post(client, account, uploader, meta)
    monkeypatch.undo()
    assert _laps() == [] and _stored_files() == []


@pytest.mark.parametrize("kind", ["garbage", "truncated", "empty"])
def test_damaged_files_are_stored_as_invalid_with_a_reason_not_a_500(client, tmp_path, kind):
    account, meta, uploader = _setup(client, tmp_path, name=f"Dam-{kind}", n=1500, extra_columns=10)
    good = (tmp_path / "lap.parquet").read_bytes()
    content = {"garbage": b"this is not parquet at all" * 50, "truncated": good[: len(good) // 2], "empty": b""}[kind]
    resp = _post(client, account, uploader, meta, content=content)
    assert resp.status_code == 200, resp.text
    lap = _laps()[0]
    assert lap.is_valid is False
    assert any("could not be parsed" in reason for reason in (lap.invalid_reason or []))
    assert "Traceback" not in json.dumps(lap.invalid_reason)
    # the stored bytes are exactly what arrived: nothing was "repaired" or re-encoded
    assert Path(lap.telemetry_path).read_bytes() == content


def test_a_damaged_lap_is_not_analysable_but_does_not_crash_the_analysis(client, tmp_path):
    account, meta, uploader = _setup(client, tmp_path, name="DamAnalysis", n=1500, extra_columns=5)
    good = (tmp_path / "lap.parquet").read_bytes()
    lap_id = _post(client, account, uploader, meta, content=good[: len(good) // 2]).json()["lap_id"]
    headers = {"Authorization": f"Bearer {account['auth_token']}"}
    resp = client.get("/telemetry/analysis/compare", params={"lap_a": lap_id, "channels": "speed"}, headers=headers)
    assert resp.status_code == 422 and "Traceback" not in resp.text


def test_a_large_file_streams_through_and_matches_its_hash(client, tmp_path):
    account, meta, uploader = _setup(client, tmp_path, name="Big", n=20000, extra_columns=120)
    original = (tmp_path / "lap.parquet").read_bytes()
    assert len(original) > 8 * 1024 * 1024, "the fixture really is a large file"
    resp = _post(client, account, uploader, meta)
    assert resp.status_code == 200, resp.text
    stored = Path(_laps()[0].telemetry_path)
    assert stored.read_bytes() == original and stored.name == f"{hashlib.sha256(original).hexdigest()}.parquet"


def test_an_oversized_upload_is_refused_and_leaves_nothing(client, tmp_path, monkeypatch):
    account, meta, uploader = _setup(client, tmp_path, name="TooBig")
    monkeypatch.setattr(settings, "max_upload_bytes", 100_000)
    resp = _post(client, account, uploader, meta)
    assert resp.status_code == 413
    assert _laps() == [] and _stored_files() == []


def test_the_client_never_sends_a_path_and_the_server_never_uses_one(client, tmp_path):
    """Content addressing: the stored name is the hash, whatever the client calls the file."""
    account, meta, uploader = _setup(client, tmp_path, name="Names", n=800, extra_columns=2)
    payload = uploader._build_payload(meta)
    resp = client.post(
        "/telemetry/upload",
        data={"envelope": json.dumps(payload["envelope"]), "signature": payload["signature"]},
        files={"telemetry": ("../../../../etc/passwd.exe", io.BytesIO(payload["telemetry_path"].read_bytes()))},
        headers={"Authorization": f"Bearer {account['auth_token']}"},
    )
    assert resp.status_code == 200
    stored = Path(_laps()[0].telemetry_path).resolve()
    assert settings.telemetry_storage_dir.resolve() in stored.parents
    assert stored.name == f"{payload['envelope']['telemetry_hash']}.parquet"
