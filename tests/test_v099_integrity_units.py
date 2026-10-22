"""V0.9.9 — server/integrity.py's edge paths: where the keys come from, and every way reading a
stored file can fail. Each failure must end in a defined, quarantining status — never in an
exception and never in "ok"."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from server import integrity
from server.config import settings


@pytest.fixture()
def stored(tmp_path):
    root = Path(settings.telemetry_storage_dir)
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"units-{os.getpid()}-{tmp_path.name}.parquet"
    path.write_bytes(b"PAR1" + b"x" * 1000 + b"PAR1")
    integrity.clear_cache()
    yield path
    path.unlink(missing_ok=True)
    integrity.clear_cache()


def _lap(path, **kw):
    data = path.read_bytes()
    lap = SimpleNamespace(id=7, driver_id=3, telemetry_path=str(path), telemetry_hash=hashlib.sha256(data).hexdigest(),
                          telemetry_bytes=len(data), lap_time=91.25, integrity_mac=None)
    lap.__dict__.update(kw)
    return lap


def test_keys_come_from_the_integrity_key_then_the_secret_key(monkeypatch):
    monkeypatch.setenv("LMU_GARAGE_INTEGRITY_KEY", " first , second ,")
    assert integrity.load_keys() == [b"first", b"second"]
    monkeypatch.setenv("LMU_GARAGE_INTEGRITY_KEY", "")
    monkeypatch.setenv("LMU_GARAGE_SECRET_KEY", "s1,s2")
    derived = integrity.load_keys()
    assert len(derived) == 2 and b"s1" not in derived, "the secret key itself is never the MAC key"
    monkeypatch.setenv("LMU_GARAGE_SECRET_KEY", "")
    assert len(integrity.load_keys()) == 1


def test_a_missing_mac_is_unsigned_and_a_malformed_one_invalid():
    assert integrity.check_mac(b"m", None) == integrity.UNSIGNED
    assert integrity.check_mac(b"m", "") == integrity.UNSIGNED
    assert integrity.check_mac(b"m", "v1:abc") == integrity.MAC_INVALID
    assert integrity.check_mac(b"m", "v0:a:b") == integrity.MAC_INVALID


def test_an_unusable_stored_path_is_outside_not_an_exception():
    assert integrity.resolve_inside_storage("bad\x00name") == (None, integrity.OUTSIDE)


def test_an_unreadable_directory_entry_is_unreadable_and_quarantines(stored, monkeypatch):
    def denied(*a, **k):
        raise PermissionError("denied")

    monkeypatch.setattr(integrity.os, "lstat", denied)
    assert integrity.resolve_inside_storage(str(stored)) == (None, integrity.UNREADABLE)
    result = integrity.verify(_lap(stored))
    assert result.status == integrity.UNREADABLE and result.quarantine


def test_a_given_path_that_does_not_exist_is_missing(stored, tmp_path):
    assert integrity.verify(_lap(stored), path=tmp_path / "gone.parquet").status == integrity.MISSING


def test_a_file_vanishing_between_the_checks_is_missing(stored, monkeypatch):
    monkeypatch.setattr(integrity, "_fingerprint", lambda path: None)
    assert integrity.verify(_lap(stored)).status == integrity.MISSING


def test_a_read_error_while_hashing_is_unreadable_and_names_only_the_error_type(stored, monkeypatch):
    def broken(path):
        raise OSError(5, "I/O error", str(path))

    lap = _lap(stored)
    monkeypatch.setattr(integrity, "sha256_file", broken)
    result = integrity.verify(lap)
    assert result.status == integrity.UNREADABLE and result.detail == {"error": "OSError"}
    assert str(stored) not in str(result.as_dict()), "no internal path in the result"


def test_a_mac_without_a_stored_size_is_invalid(stored):
    lap = _lap(stored, telemetry_bytes=None, integrity_mac="v1:00000000:" + "0" * 64)
    assert integrity.verify(lap).status == integrity.MAC_INVALID


def test_quarantine_by_id_never_raises(monkeypatch, caplog):
    import server.database as database

    def no_db():
        raise RuntimeError("database down")

    monkeypatch.setattr(database, "SessionLocal", no_db)
    integrity.quarantine_by_id(1, integrity.Result(integrity.HASH_MISMATCH))
    assert "Could not quarantine lap 1" in caplog.text


def test_the_cache_is_emptied_when_full(stored, monkeypatch):
    monkeypatch.setattr(integrity, "CACHE_MAX_ENTRIES", 1)
    integrity._cache["other|x"] = ((0,), 0.0)
    assert integrity.verify(_lap(stored), use_cache=True).ok
    assert list(integrity._cache) == [f"{stored.resolve()}|{_lap(stored).telemetry_hash}"]


@pytest.mark.parametrize("broken", ["upload_log", "audit"])
def test_a_failing_prune_never_raises_and_the_audit_prune_runs_on_its_own(monkeypatch, caplog, broken):
    from server import audit, upload_log

    def boom(db):
        raise RuntimeError("disk full")

    calls = []
    monkeypatch.setattr(upload_log, "prune", boom if broken == "upload_log" else (lambda db: 3))
    monkeypatch.setattr(audit, "prune", boom if broken == "audit" else (lambda db: calls.append(1)))
    removed = upload_log.maybe_prune(force=True)
    if broken == "upload_log":
        assert removed == 0 and "Pruning the upload log failed" in caplog.text and calls == []
    else:
        assert removed == 3 and "Pruning the audit log failed" in caplog.text
