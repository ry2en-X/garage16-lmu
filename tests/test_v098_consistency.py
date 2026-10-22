"""V0.9.8 — consistency check: a copy of the storage under another path (what the restore validates),
deleting only after a fresh look (a file referenced in the meantime stays), impossible metadata,
figures from a newer server, paths outside the storage, unreadable files."""

from __future__ import annotations

import os
import time
from pathlib import Path

from server.database import SessionLocal
from server.models import Lap
from tests.helpers.synthetic_lap import make_lap
from tests.helpers.v093 import register, upload_df


def _laps(client, n, name):
    me = register(client, name)
    return [upload_df(client, me, make_lap(seed=60 + i), lap_number=i + 1, recorded_at=1_700_000_000 + i)["lap_id"] for i in range(n)]


def test_a_copy_of_the_storage_is_checked_against_the_stored_paths(client, tmp_path, monkeypatch):
    import shutil

    from server.config import settings
    from server.consistency import check

    monkeypatch.setattr(settings, "telemetry_storage_dir", tmp_path / "live")
    _laps(client, 2, "Cons Copy")
    copy = tmp_path / "restore"
    shutil.copytree(tmp_path / "live", copy)
    stored = str((tmp_path / "live").resolve())
    monkeypatch.setattr(settings, "telemetry_storage_dir", copy)
    with SessionLocal() as db:
        report = check(db, deep=True, stored_prefix=stored)
        without = check(db, deep=True)
    assert report["ok"] and report["counts"]["missing_files"] == 0 and report["counts"]["orphaned_files"] == 0
    assert without["counts"]["outside_storage"] == 2, "without the mapping the stored paths point elsewhere"


def test_a_file_referenced_between_scan_and_delete_is_kept(client, tmp_path, monkeypatch):
    from server import consistency
    from server.config import settings

    monkeypatch.setattr(settings, "telemetry_storage_dir", tmp_path / "live")
    [lap_id] = _laps(client, 1, "Cons Race")
    loose = tmp_path / "live" / "driver_1" / "loose.parquet"
    loose.write_bytes(b"x")
    old = time.time() - 7200
    os.utime(loose, (old, old))
    real_find = consistency.find_orphaned_files

    def find_then_reference(db, **kw):
        found = real_find(db, **kw)
        db.rollback()                              # (SQLite: one writer at a time — let the other one in)
        with SessionLocal() as other:              # an upload stores a lap for exactly that file now
            other.get(Lap, lap_id).telemetry_path = str(loose)
            other.commit()
        return found

    monkeypatch.setattr(consistency, "find_orphaned_files", find_then_reference)
    with SessionLocal() as db:
        assert consistency.delete_orphans(db) == 0
    assert loose.exists()


def test_impossible_metadata_and_figures_from_a_newer_server_are_reported(client, tmp_path, monkeypatch):
    from server.config import settings
    from server.consistency import check
    from server.lap_insights import INSIGHTS_VERSION

    monkeypatch.setattr(settings, "telemetry_storage_dir", tmp_path / "live")
    a, b, c = _laps(client, 3, "Cons Meta")
    with SessionLocal() as db:
        db.get(Lap, a).sector_times = [30.0, 30.0]
        db.get(Lap, a).lap_time = 0.0
        db.get(Lap, b).lap_stats = {**db.get(Lap, b).lap_stats, "version": INSIGHTS_VERSION + 1}
        db.get(Lap, c).telemetry_path = "/etc/passwd"
        db.commit()
        report = check(db)
    assert report["items"]["bad_metadata"] == [{"lap_id": a, "problems": ["lap_time", "sector_times"]}]
    assert report["items"]["unsupported_figures"] == [{"lap_id": b, "version": INSIGHTS_VERSION + 1}]
    assert report["counts"]["stale_figures"] == 0, "a newer version is never 'stale'"
    assert report["items"]["outside_storage"][0]["lap_id"] == c and not report["ok"]


def test_an_unreadable_file_is_found_by_the_deep_check(client, tmp_path, monkeypatch):
    from server.config import settings
    from server.consistency import check

    monkeypatch.setattr(settings, "telemetry_storage_dir", tmp_path / "live")
    [lap_id] = _laps(client, 1, "Cons Broken")
    with SessionLocal() as db:
        stored = Path(db.get(Lap, lap_id).telemetry_path)
        stored.chmod(0o640)          # V0.9.9: stored originals are read-only
        stored.write_bytes(b"not parquet at all")
        report = check(db, deep=True)
    assert report["items"]["unreadable_files"][0]["lap_id"] == lap_id and not report["ok"]
