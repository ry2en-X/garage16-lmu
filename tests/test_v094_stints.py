"""V0.9.4 — Stint Analyzer, server side.

  * stints are split at pit stops (pit lane + standing still), and — for laps without pit lane
    data — at MEASURED new tyres / refuelling; the reasons are listed
  * lap flags come from measured values (pit lane, traffic gap, blue flag, wet, damage)
  * the pit loss is an estimate and needs at least 3 clean laps
  * tyre sets and their age in laps
  * laps.lap_context is stored at upload; the endpoints (sessions, session, team comparison,
    rule sets) keep the access rules: other drivers' laps only when valid, team mates only
"""
from __future__ import annotations

import pytest

from server.stints import analyse_session
from tests.helpers.v093 import clean_lap, headers, lap_row, register, upload_df

ADMIN = {"X-Admin-Token": "test-admin-token"}


def _lap(i, lap_time=90.0, *, fuel_end=None, fuel_used=2.0, wear_left=None, pit=None, ahead=None, valid=True,
         context=None, fuel_added=None, tyres_changed=None, wet=None):
    stats = {"version": 3, "fuel_used_l": fuel_used}
    if fuel_end is not None:
        stats["fuel_end_l"] = fuel_end
    if fuel_added:
        stats["fuel_added_l"] = fuel_added
    if tyres_changed:
        stats["tyres_changed"] = tyres_changed
    if wear_left is not None:
        stats["wheels"] = {w: {"wear_left_pct": wear_left, "wear_used_pct": 0.5} for w in ("fl", "fr", "rl", "rr")}
    if pit is not None:
        stats["pit"] = pit
    if ahead is not None:
        stats["traffic"] = {"ahead_close_share": ahead}
    if wet is not None:
        stats["conditions"] = {"wet_share": wet}
    return {"id": 100 + i, "lap_number": i, "lap_time": lap_time, "is_valid": valid, "recorded_at": 1000 + 90 * i,
            "sector_times": [30.0, 30.0, lap_time - 60.0], "stats": stats, "context": context or {}}


NO_PIT = {"lane_s": 0.0}


def _race():
    """10 laps: stop at the end of lap 5 (in-lap ends in the pits, out-lap 6 starts there),
    new tyres and 30 l of fuel; lap 3 in traffic."""
    laps = []
    fuel, wear = 60.0, 100.0
    for i in range(1, 11):
        fuel -= 2.0
        wear -= 1.0
        pit = dict(NO_PIT)
        lap_time = 90.0 + 0.05 * i
        added = None
        changed = None
        if i == 5:
            pit = {"lane_s": 12.0, "stationary_s": 0.0, "starts_in_pits": False, "ends_in_pits": True}
            lap_time += 8.0
        if i == 6:
            pit = {"lane_s": 30.0, "stationary_s": 20.0, "starts_in_pits": True, "ends_in_pits": False}
            fuel += 30.0
            wear = 99.0
            added, changed = 30.0, ["fl", "fr", "rl", "rr"]
            lap_time += 25.0
        laps.append(_lap(i, lap_time, fuel_end=fuel, wear_left=wear, pit=pit, ahead=0.3 if i == 3 else 0.0,
                         fuel_added=added, tyres_changed=changed,
                         context={"pitstops": 0 if i < 6 else 1, "compound_front_name": "Medium",
                                  "compound_rear_name": "Medium", "fuel_capacity_l": 90.0}))
    return laps


def test_stints_split_at_the_pit_stop_with_measured_reasons():
    result = analyse_session(_race(), session_type=10)
    assert [s["first_lap"] for s in result["stints"]] == [1, 6]
    assert "pit" in result["stints"][1]["reasons"]
    [stop] = result["pit_stops"]
    assert stop["kind"] == "stop" and stop["lap_ids"] == [105, 106]
    assert stop["stationary_s"] == 20.0 and stop["lane_s"] == 42.0
    assert stop["fuel_added_l"] == 30.0 and stop["tyres_changed"] is True
    assert stop["loss_estimated"] is True and stop["loss_s"] == pytest.approx(33.0, abs=0.6)


def test_flags_come_from_measured_values():
    rows = {r["lap_number"]: r for r in analyse_session(_race(), session_type=10)["laps"]}
    assert "race_start" in rows[1]["flags"]
    assert "traffic" in rows[3]["flags"] and not rows[3]["clean"]
    assert "inlap" in rows[5]["flags"] and "outlap" in rows[6]["flags"] and "pit_stop" in rows[6]["flags"]
    assert rows[4]["clean"] and rows[7]["clean"]
    assert rows[4]["compound"] == "Medium"


def test_tyre_sets_and_their_age():
    result = analyse_session(_race(), session_type=10)
    assert [t["number"] for t in result["tyre_sets"]] == [1, 2]
    rows = {r["lap_number"]: r for r in result["laps"]}
    assert rows[5]["tyre_age"] == 5 and rows[6]["tyre_age"] == 1 and rows[10]["tyre_age"] == 5
    assert result["tyre_sets"][1]["new_at_start"] is True


def test_no_pit_loss_without_three_clean_laps():
    laps = _race()[3:7]   # laps 4..7: only 2 clean ones
    result = analyse_session(laps, session_type=10)
    assert result["reference_lap_time"] is None
    assert all(stop["loss_s"] is None for stop in result["pit_stops"])


def test_without_pit_lane_data_new_tyres_and_fuel_still_start_a_stint():
    laps = []
    for i in range(1, 9):
        fresh = i >= 5
        laps.append(_lap(i, fuel_end=(40.0 - 2 * i) if not fresh else (70.0 - 2 * i),
                         wear_left=(100.0 - i) if not fresh else (104.0 - i)))
    result = analyse_session(laps)
    assert [s["first_lap"] for s in result["stints"]] == [1, 5]
    assert set(result["stints"][1]["reasons"]) >= {"tyres", "refuel"}
    assert result["pit_stops"] == []
    assert result["summary"]["has_pit_data"] is False


def test_a_lap_without_any_flag_source_is_not_called_traffic_or_wet():
    result = analyse_session([_lap(i) for i in range(1, 6)])
    assert all(r["flags"] == [] and r["clean"] for r in result["laps"])
    assert result["summary"]["has_traffic"] is False


def test_outliers_are_slower_than_107_percent_of_the_clean_median():
    laps = [_lap(i, 90.0) for i in range(1, 6)] + [_lap(6, 99.0)]
    rows = analyse_session(laps)["laps"]
    assert "outlier" in rows[-1]["flags"] and not rows[-1]["clean"]


# ---------------------------------------------------------------------------
# Upload + endpoints
# ---------------------------------------------------------------------------

CONTEXT = {"pitstops": 1, "place": 3, "compound_front": 1, "compound_rear": 1, "compound_front_name": "Medium",
           "compound_rear_name": "Medium", "fuel_capacity_l": 90.0, "session_left_s": 3600.0, "max_laps": 0,
           "damage": {"dent_max": 1, "overheating": False, "detached": False}}


def test_lap_context_is_stored_at_upload(client):
    me = register(client, "Context")
    lap_id = upload_df(client, me, clean_lap(seed=1), lap_context=CONTEXT)["lap_id"]
    assert lap_row(lap_id).lap_context == CONTEXT


def test_a_malformed_lap_context_is_refused(client):
    me = register(client, "BadContext")
    upload_df(client, me, clean_lap(seed=1), lap_context={**CONTEXT, "place": 0}, expect=400)
    upload_df(client, me, clean_lap(seed=1), lap_context={"secret": 1}, expect=400)


def _session(client, me, n=4, start=1_700_000_000, valid=lambda i: True, car_model="397_25_499P"):
    return [upload_df(client, me, clean_lap(seed=50 + i), lap_number=i + 1, recorded_at=start + 95 * i,
                      is_valid=valid(i), car_model=car_model, lap_context=CONTEXT)["lap_id"] for i in range(n)]


def test_session_endpoint_returns_stints_for_the_owner(client):
    me = register(client, "Owner")
    ids = _session(client, me)
    body = client.get("/telemetry/analysis/stints/session", params={"lap_id": ids[1]}, headers=headers(me)).json()
    assert [lap["id"] for lap in body["laps"]] == ids
    assert body["stints"] and body["driver"]["own"] is True and body["partial"] is False
    assert body["laps"][0]["compound"] == "Medium"
    sessions = client.get("/telemetry/analysis/stints/sessions", headers=headers(me)).json()
    assert sessions["sessions"][0]["key_lap_id"] == ids[-1] and sessions["own"] is True


def test_other_drivers_sessions_show_valid_laps_only(client):
    owner = register(client, "Owner2")
    other = register(client, "Other2")
    ids = _session(client, owner, valid=lambda i: i != 2)
    resp = client.get("/telemetry/analysis/stints/session", params={"lap_id": ids[0]}, headers=headers(other))
    assert resp.status_code == 200
    body = resp.json()
    assert ids[2] not in [lap["id"] for lap in body["laps"]] and body["partial"] is True
    hidden = client.get("/telemetry/analysis/stints/session", params={"lap_id": ids[2]}, headers=headers(other))
    assert hidden.status_code == 404
    # session lists of a driver who is not a team mate: not available
    assert client.get("/telemetry/analysis/stints/sessions", params={"driver_id": owner["driver_id"]},
                      headers=headers(other)).status_code == 404


def _team(client, owner, member):
    team = client.post("/teams", json={"name": "Stint Team"}, headers=headers(owner))
    assert team.status_code in (200, 201), team.text
    code = team.json()["invite_code"]
    joined = client.post("/teams/join", json={"invite_code": code}, headers=headers(member))
    assert joined.status_code in (200, 201), joined.text


def test_team_comparison_lists_me_and_my_team_mates_only(client):
    me = register(client, "Captain")
    mate = register(client, "Mate")
    stranger = register(client, "Stranger")
    _team(client, me, mate)
    _session(client, me, n=3)
    _session(client, mate, n=3, start=1_700_100_000)
    _session(client, stranger, n=3, start=1_700_200_000)
    body = client.get("/telemetry/analysis/stints/drivers", params={"track_name": "Le Mans"}, headers=headers(me)).json()
    names = sorted(d["name"] for d in body["drivers"])
    assert names == ["Captain", "Mate"] and body["valid_laps_only"] is True
    assert all(d["consistency"]["laps"] == 3 for d in body["drivers"])
    mates = client.get("/telemetry/analysis/stints/sessions", params={"driver_id": mate["driver_id"]},
                       headers=headers(me))
    assert mates.status_code == 200 and mates.json()["own"] is False


RULES = {"name": "Sprint 6h", "track_name": "Le Mans",
         "rules": {"race_minutes": 360, "min_pit_stops": 5, "max_stint_minutes": 65, "mandatory_tyre_change": False,
                   "refuel_l_per_s": 2.5, "fuel_margin_laps": 1}}


def test_rule_sets_are_kept_by_an_admin_and_readable_by_drivers(client):
    me = register(client, "Strategist")
    assert client.post("/admin/rule-sets", json=RULES).status_code in (401, 403, 422)
    created = client.post("/admin/rule-sets", json=RULES, headers=ADMIN)
    assert created.status_code == 201, created.text
    rule_id = created.json()["id"]
    assert client.post("/admin/rule-sets", json=RULES, headers=ADMIN).status_code == 409
    bad = {**RULES, "name": "No length", "rules": {"min_pit_stops": 1}}
    assert client.post("/admin/rule-sets", json=bad, headers=ADMIN).status_code == 422
    listed = client.get("/telemetry/analysis/stints/rule-sets", params={"track_name": "Le Mans"}, headers=headers(me)).json()
    assert [r["name"] for r in listed["rule_sets"]] == ["Sprint 6h"]
    assert listed["rule_sets"][0]["rules"]["min_pit_stops"] == 5
    other_track = client.get("/telemetry/analysis/stints/rule-sets", params={"track_name": "Spa"}, headers=headers(me))
    assert other_track.json()["rule_sets"] == []
    updated = client.put(f"/admin/rule-sets/{rule_id}", json={**RULES, "name": "Sprint 6h v2"}, headers=ADMIN)
    assert updated.status_code == 200 and updated.json()["name"] == "Sprint 6h v2"
    assert client.delete(f"/admin/rule-sets/{rule_id}", headers=ADMIN).status_code == 200
    assert client.get("/telemetry/analysis/stints/rule-sets", headers=headers(me)).json()["rule_sets"] == []


# ---------------------------------------------------------------------------
# Client: what LMU reports at the end of the lap
# ---------------------------------------------------------------------------

def test_the_client_reads_the_lap_context_and_leaves_out_what_lmu_did_not_report():
    from types import SimpleNamespace

    from client.telemetry.parser import lap_context

    tele = SimpleNamespace(front_tire_compound_index=1, rear_tire_compound_index=1,
                           front_tire_compound_name=b"Medium\x00\x00", rear_tire_compound_name=b"Medium\x00",
                           fuel_capacity=90.0, dent_severity=[0, 1, 0, 2, 0, 0, 0, 0], overheating=False, detached=True)
    scoring = SimpleNamespace(num_pitstops=2, place=4)
    session = SimpleNamespace(session_time_remaining=1234.4, max_laps=0)
    ctx = lap_context(tele, scoring, session)
    assert ctx == {"pitstops": 2, "place": 4, "compound_front": 1, "compound_rear": 1, "fuel_capacity_l": 90.0,
                   "session_left_s": 1234.0, "max_laps": 0, "compound_front_name": "Medium",
                   "compound_rear_name": "Medium", "damage": {"dent_max": 2, "overheating": False, "detached": True}}
    # an old structure without these fields, or nonsense values: nothing is guessed
    assert lap_context(SimpleNamespace(), SimpleNamespace(place=0), SimpleNamespace(session_time_remaining=-1.0)) == {}
    # what the client sends, the server accepts
    from server.schemas import LapContext
    assert LapContext(**ctx).model_dump(exclude_none=True) == ctx


# ---------------------------------------------------------------------------
# Client: LMU's lap time arriving after the lap was finalised (V0.9.4 fix)
# ---------------------------------------------------------------------------

def _late_lap(times_after_line):
    from tests.test_v0931_client import _drive_to_line, _frame
    from client.telemetry.parser import LapParser

    parser = LapParser()
    # the lap took 20 s on the telemetry clock; at the moment it is finalised LMU still shows
    # 19.0 s (the previous lap's time)
    assert _drive_to_line(parser, last_lap_time=19.0) == [None, None, None]
    lap, i = None, 0
    while lap is None and i < 200:
        i += 1
        t = 20.1 + i * 0.1
        lap = parser.feed(_frame(2, 2.0 + i * 20, t, last_lap_time=times_after_line(t)))
    return lap


def test_a_lap_time_lmu_reports_late_replaces_the_stale_one(ext_v094):
    lap = _late_lap(lambda t: 20.04 if t >= 20.6 else 19.0)
    assert lap.lap_time == pytest.approx(20.04)
    assert lap.lap_report["lap_time_corrected"]["from"] == 19.0
    assert lap.lap_report["lap_time_corrected"]["to"] == pytest.approx(20.04)
    assert lap.is_valid is True
    from server.schemas import LapReport
    LapReport(**lap.lap_report)   # the server accepts the report


def test_a_time_that_never_matches_is_kept_and_marked_unconfirmed(ext_v094):
    lap = _late_lap(lambda t: 19.0)
    assert lap.lap_time == 19.0, "never replaced by a measured value — only LMU's own time is sent"
    assert lap.lap_report["lap_time_unconfirmed"]["lmu"] == 19.0
    assert "lap_time_corrected" not in lap.lap_report


@pytest.fixture
def ext_v094(monkeypatch):
    from tests.test_v0931_client import ext as _ext  # noqa: F401  (same stand-in as the 0.9.3.1 tests)
    from client.telemetry.channels import EXTENDED_COLUMNS
    from client.telemetry.parser import LapParser

    monkeypatch.setitem(LapParser._build_sample.__globals__, "extract_extended", lambda tele, scoring=None: tuple(
        {"count_lap_flag": float(scoring.count_lap_flag), "track_limits_steps": float(tele.track_limits_steps),
         "lap_invalidated": float(tele.lap_invalidated), "num_penalties": float(scoring.num_penalties),
         "path_lateral": scoring.path_lateral, "track_edge": scoring.track_edge}.get(name, 0.0)
        for name in EXTENDED_COLUMNS))
