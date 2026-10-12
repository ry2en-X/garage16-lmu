"""V0.9.3.1 — the track-limits verdict in tiers (server/track_limits.py).

The regression cases the external review and the LMU research asked for:
  * the step counter rises, but the lap stays valid (a step is a fraction of a point)
  * a trip into the gravel without LMU's own flag is a review, not an invalid lap
  * reports LMU sends after the finish line are attributed — and flagged when unclear
  * the client's verdict follows the SAME rules (no "client rejected it" for a heuristic)
  * a 0.9.3 client's rejection is explained from its game summary where possible
"""
from __future__ import annotations

import numpy as np
import pytest

from server import track_limits as tl

N = 900  # 15 s at 60 Hz


def _columns(n=N, **overrides):
    t = np.linspace(0.0, n / 60.0, n)
    cols = {
        "t": t,
        "lap_dist": np.linspace(0.0, 1500.0, n),
        "speed": np.full(n, 60.0),
        "lap_invalidated": np.zeros(n),
        "track_limits_steps": np.zeros(n),
        "count_lap_flag": np.full(n, 2.0),
        "num_penalties": np.zeros(n),
        "path_lateral": np.zeros(n),
        "track_edge": np.full(n, 6.0),
        **{f"surface_type_{w}": np.zeros(n) for w in tl.WHEELS},
    }
    cols.update(overrides)
    return cols


def at_second(seconds):
    return int(round(seconds * 60))


def steps_rising_at(*seconds, n=N):
    steps = np.zeros(n)
    for s in seconds:
        steps[at_second(s):] += 1.0
    return steps


def codes(verdict, severity=None):
    return [f.code for f in verdict.findings if severity is None or f.severity == severity]


def find(verdict, code):
    return [f for f in verdict.findings if f.code == code]


# ---------------------------------------------------------------- the rule catalogue

def test_every_rule_has_a_tier_and_only_heuristics_and_unconfirmed_signals_can_be_promoted():
    for code, rule in tl.RULES.items():
        assert rule.tier in (tl.TIER_LMU, tl.TIER_RULE, tl.TIER_HEURISTIC, tl.TIER_SIGNAL), code
        assert rule.note, f"{code}: every rule says why it exists"
    assert {c for c, r in tl.RULES.items() if r.severity == tl.INVALID} == {
        "game_invalidated", "game_time_not_counted", "game_lap_not_counted",
        "no_lap_time", "pit_lane", "client_invalid",
    }, "only LMU's verdict and the decided Garage16 rules invalidate (V0.9.4: unknown sector times only review)"
    heuristics = {c for c, r in tl.RULES.items() if r.tier == tl.TIER_HEURISTIC}
    assert {c for c in heuristics if tl.RULES[c].severity == tl.REVIEW} <= tl.PROMOTABLE
    assert all(tl.RULES[c].severity == tl.REVIEW for c in tl.PROMOTABLE)


def test_promotion_only_affects_promotable_codes():
    assert tl.parse_promote("four_wheels_off, track_limits_points") == {"four_wheels_off", "track_limits_points"}
    assert tl.parse_promote("game_invalidated;pit_lane;nonsense;") == frozenset(), "LMU's verdict cannot be changed this way"
    assert tl.parse_promote("") == frozenset() and tl.parse_promote(None) == frozenset()


# ---------------------------------------------------------------- steps / points

def test_a_rising_step_counter_alone_keeps_the_lap_valid_and_asks_for_review():
    v = tl.assess(_columns(track_limits_steps=steps_rising_at(9.0)), steps_per_point=4, steps_per_penalty=12)
    assert v.status == tl.STATUS_REVIEW
    [f] = find(v, "track_limits_points")
    assert f.severity == tl.REVIEW and f.tier == tl.TIER_HEURISTIC
    assert f.detail == {"added": 1, "total": 1, "attribution": "this_lap", "points": 0.25, "total_points": 0.25}
    assert not v.invalidating


def test_lmu_invalidating_the_same_lap_is_what_invalidates_it():
    flag = np.zeros(N)
    flag[at_second(9.2):] = 1.0
    v = tl.assess(_columns(track_limits_steps=steps_rising_at(9.0), lap_invalidated=flag))
    assert v.status == tl.STATUS_INVALID
    assert codes(v, tl.INVALID) == ["game_invalidated"] and find(v, "game_invalidated")[0].tier == tl.TIER_LMU


def test_a_penalty_level_step_rise_is_marked():
    steps = np.zeros(N)
    steps[at_second(10):] = 12.0
    [f] = find(tl.assess(_columns(track_limits_steps=steps), steps_per_point=4, steps_per_penalty=12), "track_limits_points")
    assert f.detail["points"] == 3.0 and f.detail["penalty_level"] is True


def test_without_lmus_ratio_there_are_no_points_in_the_finding():
    [f] = find(tl.assess(_columns(track_limits_steps=steps_rising_at(9.0))), "track_limits_points")
    assert "points" not in f.detail


def test_the_operator_can_let_a_confirmed_heuristic_invalidate():
    v = tl.assess(_columns(track_limits_steps=steps_rising_at(9.0)), promote=tl.parse_promote("track_limits_points"))
    assert v.status == tl.STATUS_INVALID and codes(v, tl.INVALID) == ["track_limits_points"]


# ---------------------------------------------------------------- surface

def test_a_trip_into_the_gravel_without_lmus_flag_is_only_a_review():
    surf = {f"surface_type_{w}": np.zeros(N) for w in tl.WHEELS}
    for w in tl.WHEELS:
        surf[f"surface_type_{w}"][at_second(8):at_second(10)] = 4.0
    v = tl.assess(_columns(**surf))
    assert v.status == tl.STATUS_REVIEW and codes(v, tl.REVIEW) == ["four_wheels_off"]
    assert tl.assess(_columns(**surf), promote={"four_wheels_off"}).status == tl.STATUS_INVALID


# ---------------------------------------------------------------- around the finish line

def test_a_rise_in_the_first_second_belongs_to_the_previous_lap():
    v = tl.assess(_columns(track_limits_steps=steps_rising_at(0.5)))
    [f] = find(v, "track_limits_points_previous_lap")
    assert f.severity == tl.INFO and f.detail["attribution"] == "previous_lap"
    assert v.status == tl.STATUS_VALID


def test_an_early_rise_without_an_excursion_before_it_belongs_to_the_previous_lap():
    v = tl.assess(_columns(track_limits_steps=steps_rising_at(3.0)))
    assert codes(v) .count("track_limits_points_previous_lap") == 1 and v.status == tl.STATUS_VALID


def test_an_early_rise_after_an_excursion_in_this_lap_is_ambiguous_and_reviewed():
    surf = {f"surface_type_{w}": np.zeros(N) for w in tl.WHEELS}
    surf["surface_type_fl"][at_second(1.5):at_second(2.0)] = 2.0   # two wheels on the grass at 1.5 s
    surf["surface_type_rl"][at_second(1.5):at_second(2.0)] = 2.0
    v = tl.assess(_columns(track_limits_steps=steps_rising_at(4.0), **surf))
    [f] = find(v, "track_limits_points")
    assert f.detail["attribution"] == "ambiguous" and f.severity == tl.REVIEW and v.status == tl.STATUS_REVIEW


def test_an_early_rise_is_ambiguous_when_no_signal_could_show_an_excursion():
    cols = _columns(track_limits_steps=steps_rising_at(3.0))
    for w in tl.WHEELS:
        del cols[f"surface_type_{w}"]
    del cols["path_lateral"], cols["track_edge"]
    [f] = find(tl.assess(cols), "track_limits_points")
    assert f.detail["attribution"] == "ambiguous"


def test_a_rise_in_the_tail_after_the_line_is_a_late_report_of_this_lap():
    steps = np.zeros(N)
    steps[N - 2:] = 1.0                      # in the last 3 samples (the tail)
    v = tl.assess(_columns(track_limits_steps=steps), tail_samples=3)
    [f] = find(v, "late_track_limits")
    assert f.severity == tl.REVIEW and f.detail["from"] == "tail" and f.detail["attribution"] == "this_lap"
    assert not find(v, "track_limits_points")


def _late(**overrides):
    late = {"seconds": 6.0, "complete": True, "lap_time_at_line": 90.0, "last_lap_time": 90.0,
            "steps": [[0.0, 3]], "penalties": [[0.0, 0]], "excursion_at": None, "can_see_excursions": True}
    late.update(overrides)
    return late


def test_the_clients_late_window_attributes_a_late_report_to_this_lap():
    v = tl.assess(_columns(), late=_late(steps=[[0.0, 3], [2.4, 4]]), steps_per_point=4)
    [f] = find(v, "late_track_limits")
    assert f.detail == {"added": 1, "seconds_after_line": 2.4, "attribution": "this_lap", "from": "client", "points": 0.25}
    assert v.status == tl.STATUS_REVIEW


def test_a_late_report_after_the_new_laps_first_excursion_is_ambiguous():
    [f] = find(tl.assess(_columns(), late=_late(steps=[[0.0, 3], [3.0, 4]], excursion_at=1.2)), "late_track_limits")
    assert f.detail["attribution"] == "ambiguous"


def test_a_report_late_in_the_window_belongs_to_the_next_lap_and_is_left_to_it():
    v = tl.assess(_columns(), late=_late(steps=[[0.0, 3], [6.5, 4]]))
    assert not find(v, "late_track_limits") and v.status == tl.STATUS_VALID


def test_with_a_late_window_the_tail_is_not_counted_twice():
    steps = np.zeros(N)
    steps[N - 2:] = 1.0
    v = tl.assess(_columns(track_limits_steps=steps), tail_samples=3, late=_late(steps=[[0.0, 0], [0.02, 1]]))
    assert [f.detail["from"] for f in find(v, "late_track_limits")] == ["client"]


def test_a_withdrawn_lap_time_is_reviewed_a_changed_one_is_shown():
    v = tl.assess(_columns(), late=_late(last_lap_time=-1.0))
    [f] = find(v, "game_last_lap_time_withdrawn")
    assert f.severity == tl.REVIEW and f.tier == tl.TIER_LMU and v.status == tl.STATUS_REVIEW
    v = tl.assess(_columns(), late=_late(last_lap_time=90.25))
    assert find(v, "game_lap_time_changed")[0].severity == tl.INFO and v.status == tl.STATUS_VALID


# ---------------------------------------------------------------- the client's verdict

def test_a_0931_client_reporting_steps_does_not_reject_and_the_server_checks_them_itself():
    v = tl.assess(_columns(track_limits_steps=steps_rising_at(9.0)), client_valid=True, client_reasons=["track_limits_points"])
    assert v.status == tl.STATUS_REVIEW and codes(v).count("track_limits_points") == 1


def test_a_0931_clients_rule_reasons_are_taken_over_with_the_servers_severity():
    v = tl.assess(_columns(), client_valid=False, client_reasons=["pit_lane"])
    [f] = find(v, "pit_lane")
    assert f.severity == tl.INVALID and f.tier == tl.TIER_RULE and f.source == "client"
    assert "client_invalid" not in codes(v)


def test_the_server_decides_on_a_signal_it_checked_itself():
    # the client says LMU invalidated the lap, the file shows no flag: the file wins
    v = tl.assess(_columns(), client_valid=False, client_reasons=["game_invalidated"])
    assert v.status == tl.STATUS_VALID
    # ... unless the server could not read that signal
    cols = _columns()
    del cols["lap_invalidated"]
    v = tl.assess(cols, client_valid=False, client_reasons=["game_invalidated"])
    assert codes(v, tl.INVALID) == ["game_invalidated"] and find(v, "game_invalidated")[0].source == "client"


def test_a_rejection_without_a_known_reason_still_counts():
    v = tl.assess(_columns(), client_valid=False, client_reasons=["some_future_reason"])
    assert codes(v, tl.INVALID) == ["client_invalid"]


def test_a_093_clients_steps_only_rejection_becomes_a_review():
    state = {"count_lap_flag": 2, "invalidated": False, "track_limits_steps": 1, "penalties_added": 0}
    v = tl.assess(_columns(track_limits_steps=steps_rising_at(9.0)), client_valid=False, client_state=state, sectors_ok=True)
    assert v.status == tl.STATUS_REVIEW
    [f] = find(v, "client_invalid_unexplained")
    assert f.detail["possible"] == ["pit_lane", "track_limits_points"]


def test_a_093_clients_rejection_is_explained_from_its_summary_where_it_can_be():
    v = tl.assess(_columns(), client_valid=False, client_state={"count_lap_flag": 2}, sectors_ok=False)
    # V0.9.4: unknown sector times explain the rejection, but only send the lap to review
    assert codes(v, tl.INVALID) == [] and codes(v, tl.REVIEW) == ["sector_times_implausible"]
    cols = _columns()
    del cols["count_lap_flag"]
    v = tl.assess(cols, client_valid=False, client_state={"count_lap_flag": 1})
    assert codes(v, tl.INVALID) == ["game_time_not_counted"] and find(v, "game_time_not_counted")[0].detail == {"inferred": True}
    # nothing in the summary explains it: it was the pit lane -> stays invalid
    v = tl.assess(_columns(), client_valid=False, client_state={"count_lap_flag": 2, "track_limits_steps": 0})
    assert codes(v, tl.INVALID) == ["client_invalid"]
    # an older client says nothing at all: its no still counts
    assert codes(tl.assess(_columns(), client_valid=False), tl.INVALID) == ["client_invalid"]


def test_client_and_server_use_the_same_rules():
    from client.telemetry import lap_verdict

    reportable = set(tl.CLIENT_REASON_SIGNAL)
    assert lap_verdict.INVALIDATING | lap_verdict.REVIEW_ONLY == reportable
    assert lap_verdict.INVALIDATING == {c for c in reportable if tl.RULES[c].severity == tl.INVALID}
    assert lap_verdict.REVIEW_ONLY == {c for c in reportable if tl.RULES[c].severity == tl.REVIEW}
    assert lap_verdict.LATE_WINDOW_SECONDS == tl.LATE_WINDOW_SECONDS
    assert lap_verdict.EDGE_MARGIN_M == tl.EDGE_MARGIN_M and set(lap_verdict.OFF_SURFACES) == {int(s) for s in tl.OFF_SURFACES}


@pytest.mark.parametrize("seconds,excursion,can_see,expected", [
    (0.4, None, False, "previous_lap"),
    (2.0, None, True, "previous_lap"),
    (2.0, 1.0, True, "ambiguous"),
    (2.0, 3.0, True, "previous_lap"),
    (2.0, None, False, "ambiguous"),
    (6.0, None, True, "this_lap"),
])
def test_attribution_rule(seconds, excursion, can_see, expected):
    assert tl.attribute_rise(seconds, excursion, can_see) == expected
