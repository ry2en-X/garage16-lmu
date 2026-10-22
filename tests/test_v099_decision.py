"""V0.9.9 — the lap decision end to end: REVIEW holds a lap out of leaderboards and records until
an admin clears it, existing records are never changed silently, re-judging is reproducible, the
upload is hardened (decoding limits, symlinks, read-only originals, traversal), and laps around an
invalid lap / an incomplete last lap behave."""
from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pytest

from server import track_limits as tl
from server.database import SessionLocal
from server.models import AuditEvent, Lap, RecordEvent
from tests.helpers.v093 import clean_lap, headers, lap_row, register, upload_df

ADMIN = {"X-Admin-Token": "test-admin-token"}
TRACK, MODEL = "Le Mans", "397_25_499P"


def _events(lap_id):
    with SessionLocal() as db:
        return sorted(e.record_type for e in db.query(RecordEvent).filter(RecordEvent.lap_id == lap_id))


def _audit(kind=None, lap_id=None):
    with SessionLocal() as db:
        q = db.query(AuditEvent)
        if kind:
            q = q.filter(AuditEvent.kind == kind)
        if lap_id is not None:
            q = q.filter(AuditEvent.lap_id == lap_id)
        return [(e.kind, e.lap_id, e.request_id, e.detail) for e in q.order_by(AuditEvent.id)]


def _board():
    with SessionLocal() as db:
        models = {m for (m,) in db.query(Lap.car_model).distinct()}
    rows = []
    for model in sorted(m for m in models if m):
        rows += [(r["driver_name"], r["lap_time"]) for r in client_get(f"/leaderboard/car/Le%20Mans/{model}")]
    return rows


_client = None


def client_get(url, **kw):
    return _client.get(url, **kw).json()


@pytest.fixture()
def c(client):
    global _client
    _client = client
    yield client
    _client = None


def _grass_lap(seed):
    df = clean_lap(seed=seed)
    for w in tl.WHEELS:
        col = np.zeros(len(df), dtype=np.float32)
        col[1000:1010] = 2.0
        df[f"surface_type_{w}"] = col
    return df


# ----------------------------------------------------------------- REVIEW holds, admin releases

def test_a_review_lap_is_stored_but_sets_no_record_until_an_admin_clears_it(c):
    me = register(c, "Held Driver")
    body = upload_df(c, me, _grass_lap(11), lap_time=88.0)
    lap = lap_row(body["lap_id"])
    assert lap.is_valid is True and lap.review_status == "held" and lap.validation["status"] == "review"
    assert _events(lap.id) == [] and _board() == []
    # the driver still sees and analyses it
    mine = c.get("/telemetry/laps", headers=headers(me)).json()
    assert mine[0]["review_status"] == "held" and mine[0]["validation_status"] == "review"
    assert c.get(f"/telemetry/analysis/laps/{lap.id}", headers=headers(me)).status_code == 200
    [(kind, lap_id, request_id, detail)] = _audit("lap.accepted", lap.id)
    assert detail["status"] == "review" and detail["review_status"] == "held" and detail["records"] == []
    assert "four_wheels_off" in detail["codes"] and request_id

    resp = c.post(f"/admin/laps/{lap.id}/review", json={"decision": "clear", "note": "kerb, fine"}, headers=ADMIN)
    assert resp.status_code == 200 and resp.json()["records"] == ["PR", "WR"]
    assert _events(lap.id) == ["PR", "WR"] and _board() == [("Held Driver", 88.0)]
    [(_, _, _, reviewed)] = _audit("lap.reviewed", lap.id)
    assert reviewed["decision"] == "clear" and reviewed["previous"] == "held" and reviewed["records"] == ["PR", "WR"]


def test_clearing_late_does_not_announce_a_record_a_faster_lap_already_holds(c):
    me, rival = register(c, "Late Clear"), register(c, "Fast Rival")
    held = upload_df(c, me, _grass_lap(12), lap_time=88.0)["lap_id"]
    upload_df(c, rival, clean_lap(seed=13), lap_time=87.5)
    c.post(f"/admin/laps/{held}/review", json={"decision": "clear"}, headers=ADMIN)
    assert _events(held) == ["PR"], "a personal best, but not the WR the rival holds"


def test_an_invalid_review_decision_and_quarantine_cannot_be_cleared(c):
    me = register(c, "Inv Review")
    held = upload_df(c, me, _grass_lap(14))["lap_id"]
    assert c.post(f"/admin/laps/{held}/review", json={"decision": "invalidate", "note": "cut"}, headers=ADMIN).json()["records"] == []
    assert lap_row(held).is_valid is False and _events(held) == []
    with SessionLocal() as db:
        lap = db.get(Lap, held)
        lap.review_status = "quarantined"
        db.commit()
    resp = c.post(f"/admin/laps/{held}/review", json={"decision": "clear"}, headers=ADMIN)
    assert resp.status_code == 409 and resp.json()["detail"]["code"] == "QUARANTINED"


def test_the_review_queue_lists_held_and_older_pending_laps(c):
    me = register(c, "Queue")
    held = upload_df(c, me, _grass_lap(15), lap_number=1, recorded_at=1_700_000_001)["lap_id"]
    legacy = upload_df(c, me, clean_lap(seed=16), lap_number=2, recorded_at=1_700_000_002)["lap_id"]
    with SessionLocal() as db:
        db.get(Lap, legacy).review_status = "pending"      # flagged before V0.9.9
        db.commit()
    pending = {q["id"] for q in c.get("/admin/review", headers=ADMIN).json()}
    assert pending == {held, legacy}
    assert [q["id"] for q in c.get("/admin/review", params={"status": "held"}, headers=ADMIN).json()] == [held]


def test_a_legacy_pending_lap_keeps_its_record_and_revalidation_never_holds_it_silently(c, capsys):
    """'Keine stillschweigende Änderung bestehender Bestzeiten'."""
    from scripts import revalidate_laps

    me = register(c, "Legacy PB")
    lap_id = upload_df(c, me, _grass_lap(17), lap_time=89.0)["lap_id"]
    with SessionLocal() as db:                          # as V0.9.8 stored it: counting provisionally
        lap = db.get(Lap, lap_id)
        lap.review_status = "pending"
        db.commit()
    assert _board() == [("Legacy PB", 89.0)]
    revalidate_laps.main(["--apply"])
    assert lap_row(lap_id).review_status == "pending" and _board() == [("Legacy PB", 89.0)]
    revalidate_laps.main(["--hold"])                    # dry run: shows the effect, changes nothing
    out = capsys.readouterr().out
    assert "of which hold a current record" in out and lap_row(lap_id).review_status == "pending"
    revalidate_laps.main(["--apply", "--hold"])         # explicit: held, audited
    assert lap_row(lap_id).review_status == "held" and _board() == []
    [(_, _, _, detail)] = [e for e in _audit("lap.revalidated", lap_id) if e[3]["after"]["review_status"] == "held"]
    assert detail["before"]["review_status"] == "pending"


def test_rejudging_the_same_file_with_the_same_inputs_is_identical(c):
    from types import SimpleNamespace

    from server.config import settings
    from server.lap_check import check_lap, inputs_for_lap

    me = register(c, "Repro")
    lap_id = upload_df(c, me, _grass_lap(18))["lap_id"]
    lap = lap_row(lap_id)
    meta = SimpleNamespace(track_name=lap.track_name, lap_time=lap.lap_time, sector_times=lap.sector_times,
                           sample_count=lap.sample_count)
    runs = []
    with SessionLocal() as db:
        for _ in range(3):
            check = check_lap(db, Path(lap.telemetry_path), meta, client_valid=True, exclude_lap_id=lap.id,
                              inputs=inputs_for_lap(lap), client_version=lap.client_version)
            runs.append(json.dumps(check.verdict.as_dict(), sort_keys=True))
    assert runs[0] == runs[1] == runs[2]
    stored = {k: v for k, v in lap.validation.items() if k in ("version", "status", "findings", "signals", "decision")}
    assert json.loads(runs[0]) == stored, "the upload stored exactly what a re-check computes"
    assert settings.telemetry_storage_dir


def test_the_same_upload_twice_is_one_lap_and_one_audit_event(c):
    me = register(c, "Twice")
    df = clean_lap(seed=19)
    first = upload_df(c, me, df)
    second = upload_df(c, me, df)
    assert second["duplicate"] is True and second["lap_id"] == first["lap_id"]
    assert len(_audit("lap.accepted", first["lap_id"])) == 1


def test_another_drivers_file_is_held_for_review(c):
    a, b = register(c, "Original"), register(c, "Copier")
    df = clean_lap(seed=20)
    upload_df(c, a, df)
    copy = lap_row(upload_df(c, b, df)["lap_id"])
    assert copy.review_status == "held"
    [finding] = [f for f in copy.validation["findings"] if f["code"] == "telemetry_reused"]
    assert finding["detail"]["other_drivers"] == 1


def test_an_invalid_lap_between_two_valid_laps_touches_neither(c):
    me = register(c, "Middle")
    ids = []
    for n, seed in ((1, 21), (2, 22), (3, 23)):
        df = clean_lap(seed=seed)
        if n == 2:
            flag = np.zeros(len(df), dtype=np.float32)
            flag[1500:] = 1.0
            df["lap_invalidated"] = flag
        ids.append(upload_df(c, me, df, lap_number=n, lap_time=90.0, started_at=100.0 + 90.0 * (n - 1),
                             recorded_at=1_700_000_000 + 90 * n)["lap_id"])
    rows = [lap_row(i) for i in ids]
    assert [r.is_valid for r in rows] == [True, False, True]
    assert rows[0].review_status is None and rows[2].review_status is None
    assert "game_invalidated" in [f["code"] for f in rows[1].validation["findings"]]


def test_an_incomplete_last_lap_is_invalid_and_not_a_clean_stint_lap(c):
    from server.stints import analyse_session

    me = register(c, "Stint End")
    full = clean_lap(seed=24)
    cut = full.iloc[: int(len(full) * 0.6)]               # the session ended mid-lap
    lap_ids = [upload_df(c, me, clean_lap(seed=25 + k), lap_number=k + 1, recorded_at=1_700_000_100 + k)["lap_id"]
               for k in range(3)]
    lap_ids.append(upload_df(c, me, cut, lap_time=90.0, lap_number=4, recorded_at=1_700_000_200)["lap_id"])
    last = lap_row(lap_ids[-1])
    assert last.is_valid is False and any("duration" in r for r in last.invalid_reason)
    rows = [lap_row(i) for i in lap_ids]
    result = analyse_session([{"id": r.id, "lap_number": r.lap_number, "lap_time": r.lap_time, "is_valid": r.is_valid,
                               "recorded_at": r.recorded_at, "sector_times": r.sector_times, "stats": r.lap_stats}
                              for r in rows])
    assert [row["clean"] for row in result["laps"]][-1] is False


# ----------------------------------------------------------------- upload hardening

def test_a_decompression_bomb_is_stored_invalid_and_never_decoded(c, monkeypatch):
    from server import telemetry_analysis

    me = register(c, "Bomb")
    monkeypatch.setattr(telemetry_analysis, "MAX_FILE_ROWS", 1000)   # the real limit is 200,000 rows
    lap = lap_row(upload_df(c, me, clean_lap(seed=30))["lap_id"])
    assert lap.is_valid is False and any(telemetry_analysis.LIMIT_MESSAGE in r for r in lap.invalid_reason)
    resp = c.get("/telemetry/analysis/compare", params={"lap_a": lap.id, "channels": "speed"}, headers=headers(me))
    assert resp.status_code == 422 and telemetry_analysis.LIMIT_MESSAGE in resp.text


def test_a_symlinked_storage_folder_is_refused_and_nothing_is_written_outside(c, tmp_path):
    from server.config import settings

    me = register(c, "Symlink")
    outside = tmp_path / "outside"
    outside.mkdir()
    target = settings.telemetry_storage_dir / f"driver_{me['driver_id']}"
    target.parent.mkdir(parents=True, exist_ok=True)
    aside = None
    if target.exists():                        # left by an earlier test with the same driver id
        aside = target.with_name(target.name + ".aside")
        target.rename(aside)
    target.symlink_to(outside, target_is_directory=True)
    try:
        resp = upload_df(c, me, clean_lap(seed=31), expect=500)
        assert resp["detail"]["code"] == "STORAGE_UNAVAILABLE"
        assert list(outside.rglob("*")) == [] or all(p.is_dir() for p in outside.rglob("*"))
        with SessionLocal() as db:
            assert db.query(Lap).count() == 0
    finally:
        target.unlink()
        if aside is not None:
            aside.rename(target)


def test_the_temp_file_is_never_written_through_a_planted_symlink(tmp_path, monkeypatch):
    import io
    import uuid as uuid_module

    from server.routers import telemetry

    fixed = uuid_module.UUID("12345678123456781234567812345678")
    monkeypatch.setattr(telemetry.uuid, "uuid4", lambda: fixed)
    victim = tmp_path / "victim.txt"
    victim.write_text("untouched")
    (tmp_path / f".upload-{fixed.hex}.tmp").symlink_to(victim)

    class Up:
        file = io.BytesIO(b"x" * 100)
    with pytest.raises(OSError):
        telemetry._stream_to_temp_file(Up(), tmp_path)
    assert victim.read_text() == "untouched"


def test_stored_originals_are_read_only_and_traversal_names_stay_inside(c):
    from server.config import settings

    me = register(c, "Traversal")
    lap = lap_row(upload_df(c, me, clean_lap(seed=32), track="../../etc/passwd", car_name="..\\..\\x")["lap_id"])
    path = Path(lap.telemetry_path)
    root = settings.telemetry_storage_dir.resolve()
    assert root in path.resolve().parents and path.name == f"{lap.telemetry_hash}.parquet"
    assert ".." not in path.relative_to(settings.telemetry_storage_dir).parts
    assert (os.stat(path).st_mode & 0o777) == 0o440
    assert lap.telemetry_bytes == path.stat().st_size and lap.upload_request_id and lap.integrity_mac.startswith("v1:")


def test_the_upload_response_contract_is_unchanged(c):
    me = register(c, "Contract")
    body = upload_df(c, me, _grass_lap(33))
    assert set(body) == {"status", "lap_id", "duplicate"}


def test_an_upload_during_a_backup_is_refused_and_retried_later(c, monkeypatch):
    from server import write_lock

    me = register(c, "Maint")
    monkeypatch.setattr(write_lock, "maintenance_active", lambda *_a, **_kw: True)
    resp = upload_df(c, me, clean_lap(seed=34), expect=503)
    assert resp["detail"]["code"] == "MAINTENANCE"
    with SessionLocal() as db:
        assert db.query(Lap).count() == 0


@pytest.mark.parametrize("rows,columns,mib,ok", [
    (200_000, 1_000, 512, True),            # exactly at every limit: still read
    (200_001, 10, 1, False), (10, 1_001, 1, False), (10, 10, 513, False),
])
def test_the_decoding_limits_at_their_exact_values(rows, columns, mib, ok):
    from types import SimpleNamespace

    from server import telemetry_analysis as ta

    group = SimpleNamespace(total_byte_size=mib * 1024 * 1024 // 2)
    meta = SimpleNamespace(num_rows=rows, num_columns=columns, num_row_groups=2, row_group=lambda i: group)
    pf = SimpleNamespace(metadata=meta)
    if ok:
        ta.check_limits(pf)
    else:
        with pytest.raises(ta.AnalysisError, match=ta.LIMIT_MESSAGE):
            ta.check_limits(pf)
    ta.check_limits(SimpleNamespace(metadata=None))      # no footer statistics: nothing to judge here


# ----------------------------------------------------------------- late findings from the next lap

def _stored(**kw):
    from types import SimpleNamespace

    base = dict(id=1, is_valid=True, review_status=None,
                validation={"version": 3, "status": "valid", "findings": [], "signals": []})
    base.update(kw)
    return SimpleNamespace(**base)


REPORT = [{"code": "track_limits_points_previous_lap", "t": 0.4, "detail": {"added": 1, "points": 0.25}}]


@pytest.mark.parametrize("hold,expected", [(False, "pending"), (True, "held")])
def test_a_late_finding_holds_only_the_lap_being_uploaded(hold, expected):
    from server.lap_check import _link

    target = _stored()
    assert _link(target, _stored(id=2), REPORT, hold=hold) is True
    assert target.review_status == expected and target.validation["status"] == "review"
    assert target.validation["linked"][0]["detail"] == {"added": 1, "seconds_after_line": 0.4, "attribution": "this_lap",
                                                       "from": "next_lap", "next_lap_id": 2, "points": 0.25}


def test_a_late_finding_never_touches_an_invalid_or_already_decided_lap():
    from server.lap_check import _link

    invalid = _stored(is_valid=False)
    assert _link(invalid, _stored(id=2), REPORT, hold=True) is True
    assert invalid.review_status is None, "an invalid lap is not put into the review queue"
    cleared = _stored(review_status="cleared")
    _link(cleared, _stored(id=2), REPORT, hold=True)
    assert cleared.review_status == "cleared"


def test_linking_is_idempotent_keeps_earlier_links_and_reports_what_it_did():
    from server.lap_check import _link

    target = _stored()
    assert _link(target, _stored(id=2), REPORT) is True
    assert _link(target, _stored(id=2), REPORT) is False, "the same next lap twice: nothing new"
    assert _link(target, _stored(id=3), REPORT) is True
    assert [f["detail"]["next_lap_id"] for f in target.validation["linked"]] == [2, 3]
    late = _stored(validation={"version": 3, "status": "valid", "signals": [],
                               "findings": [{"code": "late_track_limits", "source": "late_window", "severity": "review"}]})
    assert _link(late, _stored(id=2), REPORT) is False, "the client observed the late window itself"
    assert _link(_stored(validation=None), _stored(id=2), REPORT) is False, "a lap without a verdict is left alone"


# ----------------------------------------------------------------- a crashing step is never a pass

def _recheck(c, monkeypatch, target, attr, *, client_valid=True, metadata_extra=None):
    """Re-checks a stored clean lap with one step of the pipeline raising."""
    from types import SimpleNamespace

    from server import lap_check
    from server.lap_check import check_lap, inputs_for_lap

    me = register(c, f"Crash {attr}")
    lap = lap_row(upload_df(c, me, clean_lap(seed=31))["lap_id"])

    def boom(*a, **k):
        raise RuntimeError("boom")

    if target is not None:
        monkeypatch.setattr(target if not isinstance(target, str) else getattr(lap_check, target), attr, boom)
    meta = SimpleNamespace(track_name=lap.track_name, lap_time=lap.lap_time, sector_times=lap.sector_times,
                           sample_count=lap.sample_count, **(metadata_extra or {}))
    inputs = dict(inputs_for_lap(lap), client_valid=client_valid)
    with SessionLocal() as db:
        return check_lap(db, Path(lap.telemetry_path), meta, client_valid=client_valid, exclude_lap_id=lap.id,
                         inputs=inputs)


def test_a_crash_while_reading_the_file_makes_the_lap_invalid_not_a_500(c, monkeypatch):
    from server import lap_check

    check = _recheck(c, monkeypatch, lap_check, "validate_and_read")
    assert check.validation.is_valid is False and check.validation.reasons[0].startswith("validation error: boom")
    assert check.verdict.decision["status"] == tl.INVALID and check.stats == {}


@pytest.mark.parametrize("client_valid, status", [(True, tl.REVIEW), (False, tl.INVALID)])
def test_a_crash_in_the_track_limits_check_is_review_or_the_clients_own_rejection(c, monkeypatch, client_valid, status):
    check = _recheck(c, monkeypatch, tl, "assess", client_valid=client_valid)
    codes = [f.code for f in check.verdict.findings]
    assert "check_failed" in codes and check.verdict.status == status
    assert ("client_invalid" in codes) is (not client_valid)
    failed = next(f for f in check.verdict.findings if f.code == "check_failed")
    assert failed.detail == {"error": "RuntimeError"}


def test_a_crash_in_the_decision_layer_is_review(c, monkeypatch):
    from server import lap_decision

    check = _recheck(c, monkeypatch, lap_decision, "assess")
    failed = [f for f in check.verdict.findings if f.code == "check_failed"]
    assert failed and failed[0].detail == {"error": "RuntimeError", "step": "decision"}
    assert check.verdict.status == tl.REVIEW and check.verdict.decision is None


def test_a_crash_in_the_lap_figures_keeps_the_verdict_and_drops_only_the_figures(c, monkeypatch):
    from server import lap_insights

    check = _recheck(c, monkeypatch, lap_insights, "compute",
                     metadata_extra={"track_limits_steps_per_point": None, "track_limits_steps_per_penalty": None})
    assert check.verdict.status == tl.STATUS_VALID and "driven_m" not in check.stats


def test_the_session_falls_back_to_the_steps_the_client_reported():
    from types import SimpleNamespace

    from server.lap_check import inputs_from_metadata, is_automatic_reason, sectors_ok

    meta = SimpleNamespace(is_valid=True, lap_report=None, game_lap_state=None, tail_samples=None,
                           sector_times=[30.0, 30.0, 30.0], track_limits_steps_per_point=3,
                           track_limits_steps_per_penalty=12)
    inputs = inputs_from_metadata(meta, client_valid=True)
    assert inputs.get("steps_per_point") == 3 and inputs.get("steps_per_penalty") == 12
    assert is_automatic_reason(None) is False and is_automatic_reason(42) is False
    assert sectors_ok(["x", 1]) is None and sectors_ok([1.0]) is None and sectors_ok([1.0, 0]) is False
