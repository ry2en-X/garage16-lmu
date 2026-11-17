"""V1.0.1 — a new driver's lap that would beat the Garage16 Best of others waits for an admin
(server/fair_play.py). The rest of the suite runs with the rule off (tests/conftest.py)."""

from __future__ import annotations

import pytest

from server.config import settings
from server.database import SessionLocal
from server.models import Lap, RecordEvent
from tests.test_moderation import _upload_lap as _upload_any


def _upload_lap(client, account, lap_time):
    """A valid lap of `lap_time` (its sectors add up to it)."""
    return _upload_any(client, account, lap_time=lap_time, sector_times=[lap_time / 3] * 3, speed=4500.0 / lap_time)

ADMIN = {"X-Admin-Token": "test-admin-token"}


@pytest.fixture(autouse=True)
def rule_on(monkeypatch):
    monkeypatch.setattr(settings, "new_driver_review_laps", 3)


def _register(client, name):
    return client.post("/accounts/register", params={"display_name": name}).json()


def _lap(lap_id):
    db = SessionLocal()
    try:
        lap = db.query(Lap).filter(Lap.id == lap_id).one()
        records = sorted(e.record_type for e in db.query(RecordEvent).filter(RecordEvent.lap_id == lap_id))
        return lap.review_status, (lap.validation or {}).get("fair_play"), records
    finally:
        db.close()


def test_a_new_driver_beating_the_garage16_best_is_held_until_an_admin_clears_it(client):
    veteran, newcomer = _register(client, "Veteran"), _register(client, "Newcomer")
    first = _upload_lap(client, veteran, lap_time=100.0)
    assert _lap(first)[0] is None                       # the first lap on a track and car is never held
    fast = _upload_lap(client, newcomer, lap_time=95.0)
    status, reason, records = _lap(fast)
    assert status == "held" and records == []
    assert reason["reason"] == "new_driver_record" and reason["official_laps_before"] == 0
    assert reason["garage16_best_before"] == pytest.approx(100.0)
    # in the admin's review queue, with the reason
    queue = client.get("/admin/review", headers=ADMIN).json()
    assert [q["id"] for q in queue] == [fast] and queue[0]["validation"]["fair_play"]["reason"] == "new_driver_record"
    # not on the leaderboard yet
    board = client.get("/leaderboard/car/Monza/Porsche 911 GT3 R").json()
    assert [row["lap_time"] for row in board][:1] == [pytest.approx(100.0)]
    # cleared: it counts, and its records are evaluated now
    resp = client.post(f"/admin/laps/{fast}/review", json={"decision": "clear"}, headers=ADMIN)
    assert resp.status_code == 200 and "WR" in resp.json()["records"]


def test_a_slower_lap_of_a_new_driver_is_not_held(client):
    veteran, newcomer = _register(client, "Veteran"), _register(client, "Newcomer")
    _upload_lap(client, veteran, lap_time=95.0)
    slow = _upload_lap(client, newcomer, lap_time=100.0)
    status, reason, records = _lap(slow)
    assert status is None and reason is None and records == ["PR"]


def test_an_established_driver_is_not_held(client):
    veteran, regular = _register(client, "Veteran"), _register(client, "Regular")
    _upload_lap(client, veteran, lap_time=100.0)
    for i in range(3):                                  # three official laps, slower than the best
        _upload_lap(client, regular, lap_time=101.0 + i)
    fast = _upload_lap(client, regular, lap_time=95.0)
    assert _lap(fast)[0] is None and "WR" in _lap(fast)[2]


def test_the_rule_can_be_switched_off(client, monkeypatch):
    monkeypatch.setattr(settings, "new_driver_review_laps", 0)
    veteran, newcomer = _register(client, "Veteran"), _register(client, "Newcomer")
    _upload_lap(client, veteran, lap_time=100.0)
    fast = _upload_lap(client, newcomer, lap_time=95.0)
    assert _lap(fast)[0] is None and "WR" in _lap(fast)[2]


def test_revalidation_keeps_the_fair_play_reason():
    from server.lap_check import stored_validation
    from server import track_limits

    verdict = track_limits.Verdict(status=track_limits.STATUS_VALID, findings=[], signals=[])
    kept = stored_validation(verdict, {"fair_play": {"reason": "new_driver_record"}})
    assert kept["fair_play"] == {"reason": "new_driver_record"}
