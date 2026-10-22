"""V0.9.9 — manipulation of stored originals is detected (section 3 and 5 of the V0.9.9 order):
byte changes, swapped files, removed files, hash and MAC changed independently, an edited lap time;
quarantine as the safe state, admin diagnosis and release, the integrity sweep, key rotation, the
audit chain (append-only, chained, driver deletion), errors without paths or secrets, and a process
killed in the middle of storing an upload."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from server import audit, integrity
from server.config import settings
from server.database import SessionLocal
from server.models import AuditEvent, Lap
from tests.helpers.v093 import clean_lap, headers, lap_row, register, upload_df

ADMIN = {"X-Admin-Token": "test-admin-token"}
ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _fresh_integrity_cache():
    integrity.clear_cache()
    yield
    integrity.clear_cache()


def _two_laps(client, name="Owner"):
    owner, viewer = register(client, name), register(client, name + " Viewer")
    lap_id = upload_df(client, owner, clean_lap(seed=41), lap_time=90.0)["lap_id"]
    return owner, viewer, lap_id


def _writable(path: Path) -> Path:
    os.chmod(path, 0o640)          # stored originals are read-only (0440): an attacker changes that first
    return path


def _board(client, lap):
    return [r["lap_time"] for r in client.get(f"/leaderboard/car/{lap.track_name}/{lap.car_model}").json()]


def _audit(kind):
    with SessionLocal() as db:
        return [(e.lap_id, e.request_id, e.detail) for e in db.query(AuditEvent).filter(AuditEvent.kind == kind)]


# ------------------------------------------------------------------ detection on access

def test_a_byte_changed_after_the_upload_is_detected_and_quarantines_the_lap(client):
    owner, viewer, lap_id = _two_laps(client, "Byteflip")
    lap = lap_row(lap_id)
    assert _board(client, lap) == [90.0]
    path = _writable(Path(lap.telemetry_path))
    data = bytearray(path.read_bytes())
    data[len(data) // 2] ^= 0xFF                      # same size, one byte different
    path.write_bytes(bytes(data))
    resp = client.get(f"/telemetry/analysis/laps/{lap_id}", headers={**headers(viewer), "X-Request-ID": "probe-123"})
    assert resp.status_code == 422 and "INTEGRITY_FAILED" in resp.text
    assert str(settings.telemetry_storage_dir) not in resp.text and lap.telemetry_hash not in resp.text
    stored = lap_row(lap_id)
    assert stored.review_status == "quarantined"
    assert stored.validation["quarantine"]["reason"] == integrity.HASH_MISMATCH
    assert stored.validation["quarantine"]["request_id"] == "probe-123"
    [(qlap, rid, detail)] = _audit("lap.quarantined")
    assert qlap == lap_id and rid == "probe-123" and detail["reason"] == "hash_mismatch"
    assert _board(client, lap) == [], "a quarantined lap leaves the leaderboard at once"
    # the owner still sees it (with its state), another driver no longer
    assert client.get("/telemetry/laps", headers=headers(owner)).json()[0]["review_status"] == "quarantined"
    assert client.get(f"/telemetry/analysis/laps/{lap_id}", headers=headers(viewer)).status_code == 404


def test_a_swapped_file_is_detected_by_its_size(client):
    owner, viewer, lap_id = _two_laps(client, "Swap")
    other = upload_df(client, owner, clean_lap(seed=42, n=3500), lap_time=91.0, lap_number=2)["lap_id"]
    target = _writable(Path(lap_row(lap_id).telemetry_path))
    target.write_bytes(Path(lap_row(other).telemetry_path).read_bytes())
    assert client.get(f"/telemetry/analysis/laps/{lap_id}", headers=headers(owner)).status_code == 422
    assert lap_row(lap_id).validation["quarantine"]["reason"] == integrity.SIZE_MISMATCH


def test_a_removed_file_is_not_found_and_quarantined(client):
    owner, viewer, lap_id = _two_laps(client, "Gone")
    os.remove(lap_row(lap_id).telemetry_path)
    resp = client.get(f"/telemetry/analysis/laps/{lap_id}", headers=headers(owner))
    assert resp.status_code == 404 and "no longer stored" in resp.text
    assert lap_row(lap_id).validation["quarantine"]["reason"] == integrity.MISSING


def test_hash_and_file_swapped_together_in_the_database_are_caught_by_the_mac(client):
    """Somebody with database and volume access but without the integrity key."""
    owner, viewer, lap_id = _two_laps(client, "DbSwap")
    other = lap_row(upload_df(client, viewer, clean_lap(seed=43, n=3600), lap_time=89.0, lap_number=2)["lap_id"])
    lap = lap_row(lap_id)
    _writable(Path(lap.telemetry_path)).write_bytes(Path(other.telemetry_path).read_bytes())
    with SessionLocal() as db:                        # hash and size made to match the new content
        row = db.get(Lap, lap_id)
        row.telemetry_hash, row.telemetry_bytes = other.telemetry_hash, other.telemetry_bytes
        db.commit()
    assert integrity.verify(lap_row(lap_id)).status == integrity.MAC_INVALID
    assert client.get(f"/telemetry/analysis/laps/{lap_id}", headers=headers(owner)).status_code == 422
    assert lap_row(lap_id).validation["quarantine"]["reason"] == integrity.MAC_INVALID


def test_an_edited_lap_time_breaks_the_mac(client):
    owner, viewer, lap_id = _two_laps(client, "Faster")
    with SessionLocal() as db:
        db.get(Lap, lap_id).lap_time = 80.0           # "improved" in the database
        db.commit()
    assert integrity.verify(lap_row(lap_id)).status == integrity.MAC_INVALID
    from server.consistency import check

    with SessionLocal() as db:
        report = check(db)
    assert [i["lap_id"] for i in report["items"]["mac_invalid"]] == [lap_id] and not report["ok"]


@pytest.mark.parametrize("change", ["mac", "mac_version", "size_without_mac"])
def test_manifest_fields_changed_independently(client, change):
    owner, viewer, lap_id = _two_laps(client, f"Mac {change}")
    with SessionLocal() as db:
        row = db.get(Lap, lap_id)
        if change == "mac":
            row.integrity_mac = row.integrity_mac[:-4] + ("0000" if not row.integrity_mac.endswith("0000") else "1111")
        elif change == "mac_version":
            row.integrity_mac = "v0" + row.integrity_mac[2:]
        else:
            row.telemetry_bytes = row.telemetry_bytes + 1
        db.commit()
    result = integrity.verify(lap_row(lap_id))
    assert result.status == (integrity.SIZE_MISMATCH if change == "size_without_mac" else integrity.MAC_INVALID)
    assert result.quarantine


def test_a_symlink_in_place_of_the_file_is_refused(client, tmp_path):
    owner, viewer, lap_id = _two_laps(client, "Link")
    path = Path(lap_row(lap_id).telemetry_path)
    copy = tmp_path / "copy.parquet"
    copy.write_bytes(path.read_bytes())
    os.remove(path)
    path.symlink_to(copy)                             # same bytes, but an indirection somebody can repoint
    assert integrity.verify(lap_row(lap_id)).status == integrity.OUTSIDE
    inside = path.with_name("inside-copy.parquet")
    inside.write_bytes(copy.read_bytes())
    path.unlink()
    path.symlink_to(inside)
    assert integrity.verify(lap_row(lap_id)).status == integrity.NOT_REGULAR


def test_a_change_after_a_cached_check_is_found_when_the_file_changes(client):
    owner, viewer, lap_id = _two_laps(client, "Cache")
    assert client.get(f"/telemetry/analysis/laps/{lap_id}", headers=headers(owner)).status_code == 200
    path = _writable(Path(lap_row(lap_id).telemetry_path))
    data = bytearray(path.read_bytes())
    data[100] ^= 0x01
    path.write_bytes(bytes(data))                     # new mtime -> the cache does not answer for it
    assert client.get(f"/telemetry/analysis/laps/{lap_id}", headers=headers(owner)).status_code == 422


# ------------------------------------------------------------------ release, sweep, keys

def test_an_admin_releases_a_quarantined_lap_only_after_the_file_verifies(client):
    owner, viewer, lap_id = _two_laps(client, "Release")
    lap = lap_row(lap_id)
    path = _writable(Path(lap.telemetry_path))
    original = path.read_bytes()
    path.write_bytes(original[:-10] + b"0123456789")
    client.get(f"/telemetry/analysis/laps/{lap_id}", headers=headers(owner))
    assert lap_row(lap_id).review_status == "quarantined"
    still_bad = client.post(f"/admin/laps/{lap_id}/integrity", params={"release": "true"}, headers=ADMIN).json()
    assert still_bad["released"] is False and still_bad["result"]["status"] == integrity.HASH_MISMATCH
    overview = client.get("/admin/integrity", headers=ADMIN).json()
    assert [q["lap_id"] for q in overview["quarantined"]] == [lap_id] and overview["audit_head"]["id"] > 0
    path.write_bytes(original)                        # restored from the backup
    released = client.post(f"/admin/laps/{lap_id}/integrity", params={"release": "true", "note": "restored"},
                           headers=ADMIN).json()
    assert released["released"] is True and released["review_status"] is None
    assert _board(client, lap) == [90.0]
    [(rlap, _, detail)] = _audit("lap.released")
    assert rlap == lap_id and detail["note"] == "restored"
    assert lap_row(lap_id).validation["quarantine_history"][0]["reason"] == integrity.HASH_MISMATCH


def test_the_integrity_sweep_reports_quarantines_and_backfills(client, capsys):
    from scripts import integrity_check

    owner, viewer, lap_id = _two_laps(client, "Sweep")
    legacy = upload_df(client, owner, clean_lap(seed=44), lap_number=2, lap_time=91.0)["lap_id"]
    broken = upload_df(client, owner, clean_lap(seed=45), lap_number=3, lap_time=92.0)["lap_id"]
    with SessionLocal() as db:                        # as stored before V0.9.9
        row = db.get(Lap, legacy)
        row.telemetry_bytes, row.integrity_mac = None, None
        db.commit()
    os.remove(lap_row(broken).telemetry_path)

    assert integrity_check.main([]) == 1               # dry run: found, nothing changed
    out = capsys.readouterr().out
    assert f"FAILED lap {broken}: missing" in out and "Audit chain: OK" in out
    assert lap_row(broken).review_status is None
    assert integrity_check.main(["--apply", "--backfill"]) == 1
    assert lap_row(broken).review_status == "quarantined"
    row = lap_row(legacy)
    assert row.telemetry_bytes == Path(row.telemetry_path).stat().st_size and row.integrity_mac.startswith("v1:")
    assert integrity.verify(row).mac == integrity.OK
    [(_, _, sweep)] = _audit("integrity.sweep")
    assert sweep["backfilled"] == 1 and sweep["quarantined"] == 1


def test_key_rotation_resigns_and_an_unknown_key_needs_explicit_consent(client, monkeypatch):
    from scripts import integrity_check

    owner, viewer, lap_id = _two_laps(client, "Rotate")
    old_keys = integrity.load_keys()
    monkeypatch.setenv("LMU_GARAGE_INTEGRITY_KEY", "brand-new-integrity-key," + old_keys[0].hex())
    monkeypatch.setattr(integrity, "load_keys", lambda: [b"brand-new-integrity-key", old_keys[0]])
    assert integrity.verify(lap_row(lap_id)).mac == integrity.OK          # the old key still verifies
    integrity_check.main(["--apply", "--resign"])
    assert integrity.signed_with_current_key(lap_row(lap_id).integrity_mac)
    # a key that is gone: never re-signed silently
    monkeypatch.setattr(integrity, "load_keys", lambda: [b"third-key"])
    assert integrity.verify(lap_row(lap_id)).mac == integrity.UNKNOWN_KEY
    assert not integrity.verify(lap_row(lap_id)).quarantine
    integrity_check.main(["--apply", "--resign"])
    assert not integrity.signed_with_current_key(lap_row(lap_id).integrity_mac)
    integrity_check.main(["--apply", "--resign", "--accept-unknown-key"])
    assert integrity.signed_with_current_key(lap_row(lap_id).integrity_mac)


def test_a_failed_check_is_never_rejudged(client, capsys):
    from scripts import revalidate_laps

    owner, viewer, lap_id = _two_laps(client, "NoRejudge")
    path = _writable(Path(lap_row(lap_id).telemetry_path))
    path.write_bytes(path.read_bytes()[:-1] + b"X")
    revalidate_laps.main([])
    assert "integrity check" in capsys.readouterr().out and lap_row(lap_id).review_status is None
    revalidate_laps.main(["--apply"])
    assert lap_row(lap_id).review_status == "quarantined"


# ------------------------------------------------------------------ the audit log

def test_the_audit_chain_verifies_and_rows_cannot_be_changed(client):
    import sqlalchemy as sa

    owner, viewer, lap_id = _two_laps(client, "Chain")
    with SessionLocal() as db:
        result = audit.verify_db(db)
        assert result["ok"] and result["rows"] >= 1
        with pytest.raises(Exception, match="append-only"):
            db.execute(sa.text("UPDATE audit_events SET detail = '{}' WHERE id = 1"))
        db.rollback()
        with pytest.raises(Exception, match="append-only"):
            db.execute(sa.text("UPDATE audit_events SET driver_id = 999 WHERE id = 1"))
        db.rollback()


def test_a_removed_or_forged_audit_row_breaks_the_chain(client):
    import sqlalchemy as sa

    owner = register(client, "Forge")
    ids = [upload_df(client, owner, clean_lap(seed=50 + k), lap_number=k + 1, recorded_at=1_700_000_000 + k)["lap_id"]
           for k in range(3)]
    with SessionLocal() as db:
        rows = db.query(AuditEvent).order_by(AuditEvent.id).all()
        middle = rows[1].id
        db.execute(sa.text("DELETE FROM audit_events WHERE id = :i"), {"i": middle})
        db.commit()
        broken = audit.verify_db(db)
    assert not broken["ok"] and broken["first_broken"] == rows[2].id and "removed" in broken["problem"]
    with SessionLocal() as db:
        db.execute(sa.text("DELETE FROM audit_events"))
        db.commit()
        audit.append(db, "lap.accepted", lap_id=ids[0], detail={"sha256": "x"})
        db.commit()
        prev = db.query(AuditEvent.mac).scalar()
        fake = AuditEvent(kind="lap.accepted", actor="system", lap_id=ids[1], detail={"ts": "x"}, prev_mac=prev,
                          mac="v1:00000000:" + "0" * 64)            # inserted without the key
        db.add(fake)
        db.commit()
        forged = audit.verify_db(db)
        fake_id = fake.id
    assert not forged["ok"] and forged["first_broken"] == fake_id and "key" in forged["problem"]
    assert forged["unknown_key"] == 1 and forged["rows"] == 2
    with SessionLocal() as db:                        # forged with the real key id: the content check fails
        last = db.query(AuditEvent).order_by(AuditEvent.id.desc()).first()
        db.execute(sa.text("DELETE FROM audit_events WHERE id = :i"), {"i": last.id})
        kid = integrity.key_id(integrity.load_keys()[0])
        db.add(AuditEvent(kind="lap.accepted", actor="system", lap_id=ids[1], detail={"ts": "x"},
                          prev_mac=db.query(AuditEvent.mac).scalar(), mac=f"v1:{kid}:" + "0" * 64))
        db.commit()
        forged = audit.verify_db(db)
    assert not forged["ok"] and "changed" in forged["problem"]


def test_driver_deletion_keeps_the_chain_but_drops_the_driver(client):
    owner, viewer, lap_id = _two_laps(client, "Leaving")
    export = client.get("/accounts/me/export", headers=headers(owner)).json()
    assert export["lap_files"][0]["sha256"] == lap_row(lap_id).telemetry_hash
    assert [e["kind"] for e in export["audit_events"]] == ["lap.accepted"]
    assert "mac" not in json.dumps(export["audit_events"])
    assert client.delete(f"/admin/drivers/{owner['driver_id']}", headers=ADMIN).status_code == 200
    with SessionLocal() as db:
        assert db.query(AuditEvent).filter(AuditEvent.driver_id == owner["driver_id"]).count() == 0
        kinds = [e.kind for e in db.query(AuditEvent).order_by(AuditEvent.id)]
        assert kinds[-1] == "driver.deleted"
        assert audit.verify_db(db)["ok"]


def test_audit_details_carry_no_secrets(client):
    owner, viewer, lap_id = _two_laps(client, "Secret")
    with SessionLocal() as db:
        text = json.dumps([e.detail for e in db.query(AuditEvent)])
    for secret in (owner["auth_token"], owner["client_secret"], "Secret"):
        assert secret not in text


def test_audit_pruning_records_its_anchor(client):
    from datetime import datetime, timedelta, timezone

    owner, viewer, lap_id = _two_laps(client, "Prune")
    with SessionLocal() as db:
        removed = audit.prune(db, now=datetime.now(timezone.utc) + timedelta(days=400))
        assert removed >= 1
        rows = db.query(AuditEvent).order_by(AuditEvent.id).all()
        assert rows[-1].kind == "audit.pruned" and rows[-1].detail["rows"] == removed
        assert audit.verify_db(db)["ok"]


def test_quarantining_twice_is_one_event(client):
    owner, viewer, lap_id = _two_laps(client, "Twice Q")
    os.remove(lap_row(lap_id).telemetry_path)
    for _ in range(2):
        client.get(f"/telemetry/analysis/laps/{lap_id}", headers=headers(owner))
    assert len(_audit("lap.quarantined")) == 1


# ------------------------------------------------------------------ a killed process

CHILD = r'''
import json, os, sys, tempfile
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from server.main import app
from tests.helpers.v093 import clean_lap

account = json.loads(sys.argv[2])
stage = sys.argv[3]

def die(self, *a, **k):
    os._exit(9)                       # SIGKILL-like: no cleanup, no rollback, no finally

if stage == "commit":
    Session.commit = die
elif stage == "flush":
    Session.flush = die
from tests.helpers.v093 import upload_df
with TestClient(app) as c:
    upload_df(c, account, clean_lap(seed=61), expect=200)
'''


@pytest.mark.parametrize("stage", ["flush", "commit"])
def test_a_process_killed_while_storing_leaves_no_half_lap(client, tmp_path, stage):
    from server.consistency import check
    from datetime import timedelta

    owner = register(client, f"Killed {stage}")
    script = tmp_path / "child.py"
    script.write_text(CHILD, encoding="utf-8")
    proc = subprocess.run([sys.executable, str(script), str(ROOT), json.dumps(owner), stage],
                          capture_output=True, text=True, timeout=120, env=dict(os.environ))
    assert proc.returncode == 9, proc.stderr[-2000:]
    with SessionLocal() as db:
        assert db.query(Lap).count() == 0, "no lap row without its whole transaction"
        assert db.query(AuditEvent).filter(AuditEvent.kind == "lap.accepted").count() == 0
        report = check(db, min_orphan_age=timedelta(0))
    orphans = report["items"]["orphaned_files"] + report["items"]["stale_uploads"]
    assert orphans, "the file the killed process left behind is found by the consistency check"
    # the client retries: the lap is stored normally and points at its (content-addressed) file
    body = upload_df(client, owner, clean_lap(seed=61))
    lap = lap_row(body["lap_id"])
    assert body["duplicate"] is False and integrity.verify(lap).ok


def test_a_chain_whose_oldest_rows_were_pruned_still_verifies_from_its_anchor(client):
    """Pruning removes the OLDEST rows: the first remaining row's prev_mac is the anchor."""
    import sqlalchemy as sa

    owner = register(client, "Anchor")
    for k in range(3):
        upload_df(client, owner, clean_lap(seed=70 + k), lap_number=k + 1, recorded_at=1_700_000_000 + k)
    with SessionLocal() as db:
        first = db.query(AuditEvent).order_by(AuditEvent.id).first()
        removed_mac = first.mac
        db.execute(sa.text("DELETE FROM audit_events WHERE id = :i"), {"i": first.id})
        db.commit()
        result = audit.verify_db(db)
    assert result["ok"] and result["anchor"] == removed_mac and result["rows"] == 2


# ------------------------------------------------------------------ the cache, exactly

def test_the_cache_answers_for_an_unchanged_file_and_expires(client, monkeypatch):
    owner, viewer, lap_id = _two_laps(client, "CacheExact")
    lap = lap_row(lap_id)
    calls = []
    real = integrity.sha256_file
    monkeypatch.setattr(integrity, "sha256_file", lambda p: calls.append(p) or real(p))
    clock = [1000.0]
    monkeypatch.setattr(integrity.time, "monotonic", lambda: clock[0])
    assert integrity.verify(lap, use_cache=True).ok and len(calls) == 1
    clock[0] += integrity.CACHE_SECONDS - 1
    assert integrity.verify(lap, use_cache=True).ok and len(calls) == 1, "within the window: no new hash"
    clock[0] += 1
    assert integrity.verify(lap, use_cache=True).ok and len(calls) == 2, "exactly at the window's end: hashed again"
    assert integrity.verify(lap).ok and len(calls) == 3, "without the cache: always hashed"


def test_a_change_that_keeps_size_and_resets_mtime_is_found_despite_a_cached_entry(client):
    """V0.9.9.1 regression: the file is changed in place (same inode, same size) and its mtime is put
    back with utime() right after a cached check. Size, mtime, inode and device are all unchanged —
    the V0.9.9 fingerprint saw nothing and the cache answered "ok" for up to CACHE_SECONDS. The
    ctime cannot be set back by a program (every write and utime() sets it), so it is part of the
    fingerprint now and the cached access re-hashes and finds the change."""
    owner, viewer, lap_id = _two_laps(client, "Touch")
    lap = lap_row(lap_id)
    path = Path(lap.telemetry_path)
    assert integrity.verify(lap, use_cache=True).ok
    assert len(integrity._cache) == 1, "precondition: a cache entry for this file exists"
    st = path.stat()
    time.sleep(0.02)                                          # a ctime visibly later than the cached one
    _writable(path)
    data = bytearray(path.read_bytes())
    data[200] ^= 0x10
    path.write_bytes(bytes(data))                             # in place: same inode
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))       # mtime put back
    os.chmod(path, 0o440)
    after = path.stat()
    old_view = lambda s: (s.st_size, s.st_mtime_ns, s.st_ino, s.st_dev)  # noqa: E731 — the V0.9.9 fingerprint
    assert old_view(after) == old_view(st), "precondition: size, mtime, inode and device are unchanged"
    assert after.st_ctime_ns != st.st_ctime_ns, "precondition: the file system records the ctime change"
    result = integrity.verify(lap_row(lap_id), use_cache=True)
    assert result.status == integrity.HASH_MISMATCH and result.quarantine, "found through the cache"
    assert integrity.verify(lap_row(lap_id)).status == integrity.HASH_MISMATCH, "and by the full check"


def test_the_cache_is_bounded(client, monkeypatch):
    owner, viewer, lap_id = _two_laps(client, "Bound")
    other = upload_df(client, owner, clean_lap(seed=46), lap_number=2, lap_time=91.0)["lap_id"]
    monkeypatch.setattr(integrity, "CACHE_MAX_ENTRIES", 1)
    integrity.verify(lap_row(lap_id), use_cache=True)
    assert len(integrity._cache) == 1
    integrity.verify(lap_row(other), use_cache=True)
    assert len(integrity._cache) == 1, "full: emptied before the new entry"


def test_a_size_mismatch_reports_both_sizes(client):
    owner, viewer, lap_id = _two_laps(client, "Sizes")
    path = _writable(Path(lap_row(lap_id).telemetry_path))
    expected = path.stat().st_size
    path.write_bytes(path.read_bytes() + b"extra")
    result = integrity.verify(lap_row(lap_id))
    assert result.detail == {"expected_bytes": expected, "found_bytes": expected + 5}


def test_release_changes_only_a_quarantined_lap_and_keeps_ten_history_entries(client):
    owner, viewer, lap_id = _two_laps(client, "History")
    with SessionLocal() as db:
        lap = db.get(Lap, lap_id)
        lap.review_status = "held"
        db.commit()
        assert integrity.release(db, lap).ok and lap.review_status == "held", "not quarantined: untouched"
        for _ in range(12):
            integrity.quarantine(db, lap, integrity.Result(integrity.MISSING))
            integrity.release(db, lap, note="cycle")
        db.commit()
        assert lap.review_status == "held" and len(lap.validation["quarantine_history"]) == 10


def test_a_restore_check_refuses_a_broken_chain_but_not_a_retired_key(client, monkeypatch):
    import sqlalchemy as sa

    from server.consistency import check

    owner, viewer, lap_id = _two_laps(client, "Restore chain")
    with SessionLocal() as db:
        assert check(db, deep=True)["ok"]
    monkeypatch.setattr(integrity, "load_keys", lambda: [b"a-newer-key-only"])   # the old key was removed
    with SessionLocal() as db:
        report = check(db, deep=True)
    assert report["counts"]["audit_unverifiable"] == 1 and report["counts"]["audit_chain"] == 0
    monkeypatch.undo()
    with SessionLocal() as db:
        on = " ON audit_events" if db.get_bind().dialect.name == "postgresql" else ""
        db.execute(sa.text(f"DROP TRIGGER audit_events_no_update{on}"))     # an attacker with DDL rights
        db.execute(sa.text("UPDATE audit_events SET request_id = 'forged'"))
        db.commit()
        report = check(db, deep=True)
    assert report["counts"]["audit_chain"] == 1 and not report["ok"]


def test_the_server_benchmark_runs_read_only_on_stored_laps(client, capsys):
    from scripts import bench_server

    owner, viewer, lap_id = _two_laps(client, "Bench")
    before = lap_row(lap_id).validation
    assert bench_server.main(["--root", str(ROOT), "--laps", "1", "--repeat", "1"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["laps"] == 1 and out["rows"][0]["lap_id"] == lap_id and out["median"]["check_ms"] > 0
    assert lap_row(lap_id).validation == before, "nothing was written"
