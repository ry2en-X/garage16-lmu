"""V0.9.7 — operations: every upload attempt is recorded with its outcome, reason and request id;
the admin operations view summarises them; the consistency check finds every kind of drift between
database and files, changes nothing in a dry run, and repairs only what it is told to."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from server.database import SessionLocal
from server.models import Lap, RecordEvent, UploadEvent
from tests.helpers.synthetic_lap import make_lap
from tests.helpers.v093 import headers, register, upload_df

ADMIN = {"X-Admin-Token": "test-admin-token"}


def _events():
    with SessionLocal() as db:
        return [(e.outcome, e.status_code, e.reason, e.lap_id, e.request_id, e.driver_id)
                for e in db.query(UploadEvent).order_by(UploadEvent.id).all()]


def test_every_upload_attempt_is_recorded_with_outcome_reason_and_request_id(client):
    me = register(client, "Ops Driver")
    lap_id = upload_df(client, me, make_lap(seed=1))["lap_id"]
    assert upload_df(client, me, make_lap(seed=1))["duplicate"] is True
    # an outdated client (426, machine-readable code) and a broken signature (400)
    old = client.post("/telemetry/upload", headers={**headers(me), "X-Request-ID": "trace-me-123"},
                      data={"envelope": json.dumps({"client_version": "0.0.1"}), "signature": "ab"},
                      files={"telemetry": ("lap.parquet", b"x")})
    assert old.status_code == 426 and old.headers["X-Request-ID"] == "trace-me-123"
    bad = client.post("/telemetry/upload", headers=headers(me),
                      data={"envelope": json.dumps({"client_version": "0.9.4", "metadata": {}}), "signature": "zz"},
                      files={"telemetry": ("lap.parquet", b"x")})
    assert bad.status_code == 400
    events = _events()
    assert [e[0] for e in events] == ["accepted", "duplicate", "rejected", "rejected"]
    assert events[0][3] == lap_id and events[1][3] == lap_id
    assert events[2][1:3] == (426, "UPDATE_REQUIRED") and events[2][4] == "trace-me-123", "the client's request id is kept"
    assert events[3][1] == 400 and "hex" in events[3][2]
    assert all(e[5] == me["driver_id"] for e in events)
    # an id that does not look like one is replaced, never stored as sent
    weird = client.post("/telemetry/upload", headers={**headers(me), "X-Request-ID": "<script>x</script>"},
                        data={"envelope": "[]", "signature": "ab"}, files={"telemetry": ("lap.parquet", b"x")})
    assert weird.headers["X-Request-ID"] != "<script>x</script>" and _events()[-1][4] == weird.headers["X-Request-ID"]


def test_the_operations_view_needs_the_admin_token_and_summarises(client):
    me = register(client, "Ops Two")
    upload_df(client, me, make_lap(seed=3))
    client.post("/telemetry/upload", headers=headers(me), data={"envelope": "[]", "signature": "ab"},
                files={"telemetry": ("lap.parquet", b"x")})
    assert client.get("/admin/operations").status_code == 403
    body = client.get("/admin/operations", headers=ADMIN).json()
    u = body["uploads"]
    assert u["total"] == 2 and u["counts"]["accepted"] == 1 and u["counts"]["rejected"] == 1
    assert u["failure_rate"] == 0.5
    assert u["reasons"][0]["reason"].startswith("400") and u["recent_failures"][0]["request_id"]
    assert u["duration_ms"]["p50"] is not None
    from server.main import VERSION

    assert body["server_version"] == VERSION


def test_deleting_a_driver_deletes_their_upload_log(client):
    me = register(client, "Ops Leaver")
    upload_df(client, me, make_lap(seed=4))
    assert _events()
    assert client.delete(f"/admin/drivers/{me['driver_id']}", headers=ADMIN).status_code in (200, 204)
    assert _events() == []


def _age(path: Path, hours: float = 3.0):
    old = time.time() - hours * 3600
    os.utime(path, (old, old))


def test_consistency_finds_every_kind_of_drift_and_the_dry_run_changes_nothing(client, tmp_path, monkeypatch):
    from server.consistency import check, delete_orphans, recompute_figures
    from server.config import settings
    from server.lap_insights import INSIGHTS_VERSION

    # its own storage folder: files other tests left behind must not count here
    monkeypatch.setattr(settings, "telemetry_storage_dir", tmp_path / "storage")

    me = register(client, "Ops Consistency")
    ids = [upload_df(client, me, make_lap(seed=s), lap_number=s, recorded_at=1_700_000_000 + s)["lap_id"] for s in (11, 12, 13, 14)]
    with SessionLocal() as db:
        clean = check(db, deep=True)
    assert clean["ok"] and clean["checked_laps"] == 4
    assert all(clean["counts"][k] == 0 for k in clean["counts"]), clean["counts"]

    with SessionLocal() as db:
        laps = {lap.id: lap for lap in db.query(Lap).filter(Lap.id.in_(ids)).all()}
        Path(laps[ids[0]].telemetry_path).unlink()                                    # missing
        changed = Path(laps[ids[1]].telemetry_path)
        data = bytearray(changed.read_bytes())
        data[len(data) // 2] ^= 0xFF                                                   # changed content
        changed.write_bytes(bytes(data))
        laps[ids[2]].lap_stats = {**laps[ids[2]].lap_stats, "version": INSIGHTS_VERSION - 1}   # old figures
        # a row pointing at nothing — possible on SQLite (foreign keys not enforced); PostgreSQL
        # refuses it outright, so there the check must simply find none
        dangling = db.bind.dialect.name == "sqlite"
        if dangling:
            db.add(RecordEvent(lap_id=999999, driver_id=me["driver_id"], record_type="PR", track_name="x",
                               car_name="x", lap_time=1.0))
        db.commit()
    storage = settings.telemetry_storage_dir
    orphan = storage / "driver_1" / "orphan.parquet"
    orphan.parent.mkdir(parents=True, exist_ok=True)
    orphan.write_bytes(b"orphan")
    _age(orphan)
    temp = storage / "driver_1" / ".upload-abc.tmp"
    temp.write_bytes(b"partial")
    _age(temp)
    fresh = storage / "driver_1" / "fresh.parquet"                                   # maybe an upload in flight
    fresh.write_bytes(b"new")

    with SessionLocal() as db:
        quick = check(db)
        deep = check(db, deep=True)
    assert not quick["ok"]
    c = deep["counts"]
    assert c["missing_files"] == 1 and deep["items"]["missing_files"][0]["lap_id"] == ids[0]
    assert c["hash_mismatch"] == 1 and deep["items"]["hash_mismatch"][0]["lap_id"] == ids[1]
    assert quick["counts"]["hash_mismatch"] == 0, "only the deep check reads the files"
    assert c["stale_figures"] == 1 and c["dangling_rows"] == (1 if dangling else 0)
    assert c["orphaned_files"] == 1 and c["stale_uploads"] == 1, "the fresh file is not touched"
    assert orphan.exists() and temp.exists() and fresh.exists(), "a dry run changes nothing"

    # the admin view is the same report, read-only
    assert client.get("/admin/consistency").status_code == 403
    assert client.get("/admin/consistency?deep=true", headers=ADMIN).json()["counts"] == c

    # repairs, only when asked: unreferenced files (older than the margin) and old figures
    with SessionLocal() as db:
        assert delete_orphans(db) == 2
        assert recompute_figures(db) == {"recomputed": 1, "failed": 0, "skipped_without_file": 0}
        after = check(db)
    assert not orphan.exists() and not temp.exists() and fresh.exists()
    assert after["counts"]["stale_figures"] == 0 and after["counts"]["orphaned_files"] == 0
    assert after["counts"]["missing_files"] == 1, "a lap without its file is never 'repaired' away"
    with SessionLocal() as db:
        assert db.get(Lap, ids[2]).lap_stats["version"] == INSIGHTS_VERSION


def test_the_script_reports_and_exits_with_the_verdict(client, capsys):
    from scripts.consistency_check import main

    me = register(client, "Ops Script")
    lap_id = upload_df(client, me, make_lap(seed=21))["lap_id"]
    assert main([]) == 0
    capsys.readouterr()
    with SessionLocal() as db:
        Path(db.get(Lap, lap_id).telemetry_path).unlink()
    assert main(["--json"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["counts"]["missing_files"] == 1 and report["items"]["missing_files"][0]["lap_id"] == lap_id
