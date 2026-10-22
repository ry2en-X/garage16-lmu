"""V0.9.8 — records under concurrent uploads: whatever order the uploads commit in, every record
type stays a strictly improving sequence (no event for a lap slower than the record it would beat,
no two events for the same time), and the last event is the fastest lap. Six uploads of ONE driver
at once (personal best), and two team members racing for the team best."""

from __future__ import annotations

import threading

from server.database import SessionLocal
from server.models import RecordEvent
from tests.helpers.synthetic_lap import make_lap
from tests.helpers.v093 import headers, register, upload_df


def _race(client, jobs):
    errors = []

    def run(account, lap_time, n):
        try:
            upload_df(client, account, make_lap(seed=200 + n, n=1900, lap_time=lap_time), lap_time=lap_time, lap_number=n,
                      recorded_at=1_700_100_000 + n, track="Spa", car_model="RACE_MODEL", car_name="Race Car #1")
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=run, args=job) for job in jobs]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors


def _events(record_type, **filters):
    with SessionLocal() as db:
        q = db.query(RecordEvent).filter(RecordEvent.record_type == record_type, RecordEvent.track_name == "Spa")
        for k, v in filters.items():
            q = q.filter(getattr(RecordEvent, k) == v)
        return [(e.id, e.lap_time, e.previous_best) for e in q.order_by(RecordEvent.id).all()]


def _strictly_improving(events):
    times = [t for _id, t, _prev in events]
    assert times == sorted(times, reverse=True) and len(set(times)) == len(times), events
    for (_i, t, prev), (_j, before, _p) in zip(events[1:], events):
        assert prev == before and t < before, events


def test_six_uploads_of_one_driver_at_once_keep_the_personal_best_consistent(client):
    me = register(client, "Race PB")
    times = [92.4, 91.8, 93.0, 91.1, 92.0, 91.5]
    _race(client, [(me, t, i + 1) for i, t in enumerate(times)])
    prs = _events("PR", driver_id=me["driver_id"])
    assert prs and prs[-1][1] == min(times)
    _strictly_improving(prs)
    wrs = _events("WR")
    assert wrs and wrs[-1][1] == min(times)
    _strictly_improving(wrs)


def test_two_team_members_racing_for_the_team_best(client):
    a, b = register(client, "Race Team A"), register(client, "Race Team B")
    team = client.post("/teams", json={"name": "Race Team"}, headers=headers(a)).json()
    assert client.post("/teams/join", json={"invite_code": team["invite_code"]}, headers=headers(b)).status_code == 200
    jobs = [(a, 95.0, 1), (b, 94.0, 2), (a, 93.5, 3), (b, 93.9, 4)]
    _race(client, jobs)
    team_bests = _events("TEAM_BEST")
    assert team_bests and team_bests[-1][1] == 93.5
    _strictly_improving([e for e in team_bests])
