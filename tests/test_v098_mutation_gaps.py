"""V0.9.8 — the cases scripts/mutation_check.py showed nobody tested: changing the decision did not
make any test fail. Each test here kills such a mutant.

  records:    a lap EQUAL to the current best is no new record (PR, WR, team best); a team best
              counts the driver's own teams only, and only that team's laps
  client_ip:  no or unparseable peer address; malformed IPv6 brackets
  rate limit: exactly at the end of the window a new window starts; a new window allows the full
              number again
  write lock: the default does not wait; a missing storage folder is created; a waiting writer
              gets in promptly once the lock is free
"""

from __future__ import annotations

import os
import threading
import time

import pytest

from server.database import SessionLocal
from server.models import RecordEvent
from tests.helpers.synthetic_lap import make_lap
from tests.helpers.v093 import headers, register, upload_df


def _up(client, who, lap_time, n):
    return upload_df(client, who, make_lap(seed=300 + n, n=1900, lap_time=lap_time), lap_time=lap_time, lap_number=n,
                     recorded_at=1_700_200_000 + n, track="Imola", car_model="GAP_MODEL", car_name="Gap Car")["lap_id"]


def _types(lap_id):
    with SessionLocal() as db:
        return sorted((e.record_type, e.team_id) for e in db.query(RecordEvent).filter(RecordEvent.lap_id == lap_id))


# ---------------------------------------------------------------- records

def test_a_lap_equal_to_the_best_is_no_new_record(client):
    a, b = register(client, "Gap Equal A"), register(client, "Gap Equal B")
    team = client.post("/teams", json={"name": "Gap Equal Team"}, headers=headers(a)).json()
    client.post("/teams/join", json={"invite_code": team["invite_code"]}, headers=headers(b))
    first = _up(client, a, 90.0, 1)
    assert {t for t, _ in _types(first)} == {"PR", "WR", "TEAM_BEST"}
    assert _types(_up(client, a, 90.0, 2)) == [], "the same time again: no PR, no WR, no team best"
    assert _types(_up(client, b, 90.0, 3)) == [("PR", None)], "B's first lap is his PR — but ties neither WR nor team best"


def test_a_team_best_counts_only_the_drivers_own_teams_and_their_laps(client):
    a, b, c = register(client, "Gap Team A"), register(client, "Gap Team B"), register(client, "Gap Team C")
    team_a = client.post("/teams", json={"name": "Gap Team One"}, headers=headers(a)).json()
    team_c = client.post("/teams", json={"name": "Gap Team Two"}, headers=headers(c)).json()
    _up(client, c, 85.0, 1)                                  # the fastest lap is in ANOTHER team
    lap_a = _up(client, a, 88.0, 2)
    assert ("TEAM_BEST", team_a["id"]) in _types(lap_a), "another team's faster lap does not block this team's best"
    assert all(team_id != team_c["id"] for _t, team_id in _types(lap_a)), "no event for a team the driver is not in"
    lap_b = _up(client, b, 80.0, 3)                          # B is in no team at all
    assert [t for t, _ in _types(lap_b) if t == "TEAM_BEST"] == [], "no team, no team best"


# ---------------------------------------------------------------- client address

def test_without_a_usable_peer_address_the_raw_value_or_unknown_is_returned():
    from server.client_ip import parse_trusted, resolve

    private = parse_trusted("private")
    assert resolve(None, "198.51.100.7", private) == "unknown"
    assert resolve("", None, private) == "unknown"
    assert resolve("testclient", "198.51.100.7", private) == "testclient"


@pytest.mark.parametrize("hop,expected", [
    ("[2001:db8::1", "172.18.0.5"),          # unclosed bracket: garbage, keep the proxy
    ("2001:db8::1", "2001:db8::1"),          # IPv6 without port or brackets
    ("[2001:db8::1]", "2001:db8::1"),
    ('"198.51.100.7"', "198.51.100.7"),
])
def test_ip_forms_in_the_chain(hop, expected):
    from server.client_ip import parse_trusted, resolve

    assert resolve("172.18.0.5", hop, parse_trusted("private")) == expected


# ---------------------------------------------------------------- rate limit windows

def test_the_window_ends_exactly_on_time_and_a_new_one_allows_the_full_number(monkeypatch):
    from server import rate_limit

    clock = [1000.0]
    monkeypatch.setattr(rate_limit.time, "monotonic", lambda: clock[0])
    rate_limit.reset_all()
    assert [rate_limit._check("b", "k", 3, 60.0) for _ in range(4)] == [True, True, True, False]
    clock[0] += 60.0                                          # exactly at the end: a fresh window
    assert [rate_limit._check("b", "k", 3, 60.0) for _ in range(4)] == [True, True, True, False]
    clock[0] += 59.999
    assert rate_limit._check("b", "k", 3, 60.0) is False      # still the same window


# ---------------------------------------------------------------- write lock

def _hold_exclusive(path, seconds, ready):
    import fcntl

    fd = os.open(path, os.O_RDWR | os.O_CREAT)
    fcntl.flock(fd, fcntl.LOCK_EX)
    ready.set()
    time.sleep(seconds)
    fcntl.flock(fd, fcntl.LOCK_UN)
    os.close(fd)


def test_by_default_a_writer_does_not_wait(tmp_path):
    from server import write_lock as wl

    ready = threading.Event()
    t = threading.Thread(target=_hold_exclusive, args=(tmp_path / wl.LOCK_NAME, 1.5, ready))
    t.start()
    ready.wait(5)
    start = time.monotonic()
    with pytest.raises(wl.MaintenanceActive):
        with wl.writing(tmp_path):
            pass
    assert time.monotonic() - start < 0.5, "refused at once — an upload never hangs on a backup"
    t.join()


def test_a_waiting_writer_gets_in_promptly_and_a_missing_folder_is_created(tmp_path):
    from server import write_lock as wl

    nested = tmp_path / "a" / "b"
    with wl.writing(nested):                                  # parents created
        pass
    ready = threading.Event()
    t = threading.Thread(target=_hold_exclusive, args=(nested / wl.LOCK_NAME, 0.3, ready))
    t.start()
    ready.wait(5)
    start = time.monotonic()
    with wl.writing(nested, wait_s=5):
        waited = time.monotonic() - start
    assert 0.2 <= waited < 0.6, waited
    t.join()
