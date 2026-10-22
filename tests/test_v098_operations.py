"""V0.9.8 — operations: the upload log keeps its 30 days whatever the upload frequency (at start,
then hourly, in bounded batches); liveness and readiness are separate and readiness says what is
missing; every error answer carries the request id and nothing internal; the operations view can
drill down by request id and filter by client version."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone


from server.database import SessionLocal
from server.models import UploadEvent
from tests.helpers.synthetic_lap import make_lap
from tests.helpers.v093 import headers, register, upload_df

ADMIN = {"X-Admin-Token": "test-admin-token"}


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def test_old_upload_log_rows_go_in_bounded_batches_recent_ones_stay(client):
    from server.upload_log import prune

    with SessionLocal() as db:
        for days in [40] * 7 + [31, 29, 1]:
            db.add(UploadEvent(outcome="accepted", status_code=200, created_at=_now() - timedelta(days=days)))
        db.commit()
        assert prune(db, batch=3, max_batches=2) == 6, "two batches of three, no more in one call"
        assert prune(db, batch=3, max_batches=5) == 2
        assert prune(db) == 0
        left = sorted(round((_now() - e.created_at).days) for e in db.query(UploadEvent).all())
    assert left == [1, 29]


def test_pruning_runs_at_start_and_then_at_most_hourly(client, monkeypatch):
    import server.upload_log as ul

    with SessionLocal() as db:
        db.add(UploadEvent(outcome="accepted", status_code=200, created_at=_now() - timedelta(days=90)))
        db.commit()
    assert ul.maybe_prune(force=True) == 1                  # what the server does at start (lifespan)
    with SessionLocal() as db:
        db.add(UploadEvent(outcome="accepted", status_code=200, created_at=_now() - timedelta(days=90)))
        db.commit()
    assert ul.maybe_prune() == 0, "not again within the hour"
    monkeypatch.setattr(ul, "_last_prune", ul._last_prune - ul.PRUNE_INTERVAL_S - 1)
    assert ul.maybe_prune() == 1


def test_the_lifespan_prunes_at_start():
    from fastapi.testclient import TestClient

    from server.main import app

    with SessionLocal() as db:
        db.add(UploadEvent(outcome="accepted", status_code=200, created_at=_now() - timedelta(days=90)))
        db.commit()
    with TestClient(app):
        with SessionLocal() as db:
            assert db.query(UploadEvent).filter(UploadEvent.created_at < _now() - timedelta(days=31)).count() == 0


def test_liveness_and_readiness(client, monkeypatch, tmp_path):
    from server import main
    from server.config import settings

    assert client.get("/health/live").json()["status"] == "ok"
    ready = client.get("/health/ready")
    assert ready.status_code == 200 and ready.json()["checks"] == {"database": "ok", "storage": "ok"}

    # a backup running: still ready (reading works, uploads wait a moment) — reported
    marker = settings.telemetry_storage_dir / ".garage16-maintenance"
    marker.write_text("x")
    try:
        body = client.get("/health/ready").json()
        assert body["status"] == "ready" and {"code": "MAINTENANCE"} in body["warnings"]
    finally:
        marker.unlink()

    # storage gone (a file where the folder should be): not ready, and it says why
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x")
    monkeypatch.setattr(settings, "telemetry_storage_dir", blocker / "telemetry")
    r = client.get("/health/ready")
    assert r.status_code == 503 and r.json()["checks"]["storage"] != "ok"
    monkeypatch.undo()

    # database down: not ready; /health says so without the driver's message (host, user …)
    class Broken:
        def __init__(self, *a, **k):
            raise RuntimeError("connection to server at 10.1.2.3 failed for user secretuser")
    monkeypatch.setattr(main, "SessionLocal", Broken)
    r = client.get("/health/ready")
    assert r.status_code == 503 and r.json()["checks"]["database"] == "unreachable"
    h = client.get("/health").json()
    assert h["db_ok"] is False and "secretuser" not in json.dumps(h) and "10.1.2.3" not in json.dumps(h)
    assert client.get("/health/live").status_code == 200, "alive although not ready"


def test_every_error_answer_carries_the_request_id_and_nothing_internal(client, monkeypatch):
    from fastapi.testclient import TestClient

    from server import upload_log
    from server.main import app

    r = client.get("/telemetry/analysis/laps/999999", headers={"X-Request-ID": "trace-404"})
    assert r.status_code in (401, 404) and r.json()["request_id"] == "trace-404" and r.headers["X-Request-ID"] == "trace-404"
    me = register(client, "Ops Errors")
    r = client.get("/telemetry/analysis/laps/notanumber", headers={**headers(me), "X-Request-ID": "trace-422"})
    assert r.status_code == 422 and r.json()["request_id"] == "trace-422" and isinstance(r.json()["detail"], list)
    # an unexpected crash: 500 with the id, no message, no trace
    monkeypatch.setattr(upload_log, "summary", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("/srv/secret/path boom")))
    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.get("/admin/operations", headers={**ADMIN, "X-Request-ID": "trace-500"})
    assert r.status_code == 500
    assert r.json() == {"detail": "Internal server error", "request_id": "trace-500"}
    assert "boom" not in r.text and "Traceback" not in r.text


def test_operations_drill_down_filters_and_timeline(client):
    a = register(client, "Ops Drill")
    upload_df(client, a, make_lap(seed=41))
    client.post("/telemetry/upload", headers={**headers(a), "X-Request-ID": "find-me-1"},
                data={"envelope": json.dumps({"client_version": "0.0.1"}), "signature": "ab"}, files={"telemetry": ("l.parquet", b"x")})
    body = client.get("/admin/operations?request_id=find-me-1", headers=ADMIN).json()
    assert [e["status_code"] for e in body["request"]] == [426] and body["request"][0]["reason"] == "UPDATE_REQUIRED"
    assert body["request"][0]["client_version"] == "0.0.1"
    only_old = client.get("/admin/operations?client_version=0.0.1", headers=ADMIN).json()["uploads"]
    assert only_old["total"] == 1 and only_old["counts"]["rejected"] == 1
    everything = client.get("/admin/operations", headers=ADMIN).json()["uploads"]
    assert everything["total"] == 2 and sum(x["uploads"] for x in everything["timeline"]) == 2
    assert sum(x["failed"] for x in everything["timeline"]) == 1 and everything["truncated"] is False
    assert client.get("/admin/operations?outcome=nonsense", headers=ADMIN).status_code == 422


def test_every_rate_limit_bucket_the_routes_use_is_configured_and_answers_429_the_same_way(client):
    import re
    from pathlib import Path

    from server.config import settings
    from server.rate_limit import _check, reset_all

    used = set()
    for path in (Path(__file__).resolve().parent.parent / "server" / "routers").glob("*.py"):
        used |= set(re.findall(r'rate_limit\("([a-z_]+)"', path.read_text(encoding="utf-8")))
    assert used and used <= set(settings.rate_limits), f"no configured limit (falls back to a default): {used - set(settings.rate_limits)}"
    # a limit reached: 429 with the bucket named and the request id, like every error
    reset_all()
    max_requests, window = settings.rate_limits["register"]
    for _ in range(max_requests):
        assert _check("register", "ip:testclient", max_requests, window)
    r = client.post("/accounts/register", params={"display_name": "Too Many"}, headers={"X-Request-ID": "trace-429"})
    assert r.status_code == 429 and "register" in r.json()["detail"] and r.json()["request_id"] == "trace-429"


def test_the_data_export_holds_everything_the_database_has_about_the_driver(client):
    me = register(client, "Export Me")
    team = client.post("/teams", json={"name": "Export Team"}, headers=headers(me)).json()
    lap_id = upload_df(client, me, make_lap(seed=51))["lap_id"]
    data = client.get("/accounts/me/export", headers=headers(me)).json()
    assert [l["id"] for l in data["laps"]] == [lap_id]
    assert data["teams"] == [{"team": "Export Team", "role": "owner", "joined_at": data["teams"][0]["joined_at"]}]
    assert data["records"] and all(r["lap_id"] == lap_id for r in data["records"])
    assert [u["outcome"] for u in data["upload_log"]] == ["accepted"] and data["upload_log"][0]["lap_id"] == lap_id
    assert "email" in data and "sessions" in data
    assert team["id"]
