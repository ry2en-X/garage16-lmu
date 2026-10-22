"""V0.9.3.1 — stored laps can be judged again (requirement A of the V0.9.3.1 review) and
track-limits reports after the finish line reach the right lap (requirement B).

  * a lap a 0.9.3 client/server rejected only for a rising step counter is "restorable":
    the dry run lists it, --apply alone changes no validity, --apply --restore makes it count
    again and puts it in the review queue — without a new upload
  * a lap an ADMIN invalidated, and a lap an admin cleared, are never changed
  * the check inputs are stored at upload; for old laps they are recovered (alembic 0014
    and server/lap_check.py legacy_inputs give the same result)
  * a step rise in the first seconds of lap N+1 gives lap N a late finding, in whichever
    order the two laps arrive
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from server.lap_check import CLIENT_INVALID_TEXT, legacy_inputs
from tests.helpers.v093 import clean_lap, lap_row, register, upload_df

ROOT = Path(__file__).resolve().parent.parent
STATE_093 = {"count_lap_flag": 2, "invalidated": False, "track_limits_steps": 1, "penalties_added": 0}


def _steps_lap(seed, rise_at_index=2000):
    df = clean_lap(seed=seed)
    steps = np.zeros(len(df), dtype=np.float32)
    steps[rise_at_index:] = 1.0
    df["track_limits_steps"] = steps
    return df


def _as_093_left_it(lap_id, *, invalid_reason=None):
    """What V0.9.3 stored for a lap its client rejected for a step rise."""
    from server.database import SessionLocal
    from server.models import Lap

    with SessionLocal() as db:
        db.query(Lap).filter(Lap.id == lap_id).update({
            Lap.is_valid: False,
            Lap.client_claimed_valid: False,
            Lap.invalid_reason: invalid_reason or ["track limits: LMU added track-limits points at 1234 m", CLIENT_INVALID_TEXT],
            Lap.validation: {"version": 1, "status": "invalid", "signals": ["track_limits_steps", "client"], "findings": [
                {"code": "track_limits_points", "severity": "invalid", "source": "track_limits_steps", "at": 1234.0},
                {"code": "client_invalid", "severity": "invalid", "source": "client", "detail": dict(STATE_093)},
            ]},
            Lap.check_inputs: None,
            Lap.review_status: None,
        }, synchronize_session=False)
        db.commit()


def test_new_uploads_store_their_check_inputs(client):
    me = register(client, "Inputs")
    lap_id = upload_df(client, me, _steps_lap(41), lap_report={"reasons": ["track_limits_points"]},
                       game_lap_state=dict(STATE_093), track_limits_steps_per_point=4, tail_samples=2)["lap_id"]
    lap = lap_row(lap_id)
    assert lap.is_valid is True and lap.review_status == "held"      # V0.9.9: REVIEW holds a new lap
    assert lap.check_inputs["client_reasons"] == ["track_limits_points"]
    assert lap.check_inputs["client_state"] == STATE_093 and lap.check_inputs["tail_samples"] == 2
    [f] = [f for f in lap.validation["findings"] if f["code"] == "track_limits_points"]
    assert f["severity"] == "review" and f["detail"]["points"] == 0.25 and f["tier"] == "heuristic"


def test_a_lap_rejected_only_for_a_step_rise_can_be_restored(client, capsys):
    from scripts import revalidate_laps

    me = register(client, "Restore")
    lap_id = upload_df(client, me, _steps_lap(42), is_valid=False, game_lap_state=dict(STATE_093))["lap_id"]
    _as_093_left_it(lap_id)

    assert revalidate_laps.main(["--driver", str(me["driver_id"])]) == 0
    out = capsys.readouterr().out
    assert f"[invalid -> valid (restorable)] lap {lap_id:>6}" in out and "Dry run" in out
    assert lap_row(lap_id).check_inputs is None, "a dry run changes nothing"

    assert revalidate_laps.main(["--apply", "--driver", str(me["driver_id"])]) == 0
    lap = lap_row(lap_id)
    assert lap.is_valid is False, "--apply alone never changes validity"
    assert lap.check_inputs["recovered"] is True and lap.check_inputs["client_state"] == STATE_093
    assert lap.validation["status"] == "review"
    assert {f["code"] for f in lap.validation["findings"]} >= {"track_limits_points", "client_invalid_unexplained"}

    assert revalidate_laps.main(["--apply", "--restore", "--driver", str(me["driver_id"])]) == 0
    lap = lap_row(lap_id)
    assert lap.is_valid is True and lap.invalid_reason is None and lap.review_status == "pending"
    assert lap.validation["restored"]["previous_reason"][0].startswith("track limits:")
    # nothing left to do: a second (dry) run finds nothing to restore
    capsys.readouterr()
    assert revalidate_laps.main(["--driver", str(me["driver_id"])]) == 0
    assert "[invalid -> valid (restorable)]" not in capsys.readouterr().out
    assert lap_row(lap_id).is_valid is True


def test_admin_decisions_are_never_changed(client, capsys):
    from scripts import revalidate_laps
    from server.database import SessionLocal
    from server.models import Lap

    me = register(client, "Decided")
    by_admin = upload_df(client, me, _steps_lap(43), is_valid=False, game_lap_state=dict(STATE_093), lap_number=1)["lap_id"]
    _as_093_left_it(by_admin, invalid_reason=["admin: cut the chicane"])
    flagged = clean_lap(seed=44)
    flags = np.zeros(len(flagged), dtype=np.float32)
    flags[2000:] = 1.0
    flagged["lap_invalidated"] = flags
    cleared = upload_df(client, me, flagged, lap_number=2, recorded_at=1_700_000_500)["lap_id"]
    with SessionLocal() as db:   # an admin decided: this lap counts
        db.query(Lap).filter(Lap.id == cleared).update({Lap.is_valid: True, Lap.review_status: "cleared",
                                                        Lap.invalid_reason: None})
        db.commit()

    assert revalidate_laps.main(["--apply", "--restore", "--update-validity", "--driver", str(me["driver_id"])]) == 0
    out = capsys.readouterr().out
    assert f"[invalid, would be valid, kept (not an automatic rejection)] lap {by_admin:>6}" in out
    assert f"[admin decided, validity kept] lap {cleared:>6}" in out
    assert lap_row(by_admin).is_valid is False and lap_row(by_admin).invalid_reason == ["admin: cut the chicane"]
    assert lap_row(cleared).is_valid is True and lap_row(cleared).review_status == "cleared"


def test_migration_0014_and_the_server_recover_the_same_inputs():
    spec = importlib.util.spec_from_file_location("m0014", ROOT / "alembic/versions/0014_lap_check_inputs.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["m0014"] = module
    spec.loader.exec_module(module)
    cases = [
        dict(client_claimed_valid=False, sector_times=[30.0, 0.0, 0.0], lap_stats={"session": {"track_limits_steps_per_point": 4}},
             validation={"findings": [{"code": "client_invalid", "detail": dict(STATE_093)}]}),
        dict(client_claimed_valid=True, sector_times=[30.0, 30.0, 30.0], lap_stats=None, validation=None),
        dict(client_claimed_valid=False, sector_times=[], lap_stats={}, validation={"findings": [{"code": "client_invalid"}]}),
    ]
    for case in cases:
        lap = SimpleNamespace(**case)
        assert module.inputs_for(case["client_claimed_valid"], case["validation"], case["lap_stats"], case["sector_times"]) \
            == legacy_inputs(lap)
    assert legacy_inputs(SimpleNamespace(**cases[0]))["sectors_ok"] is False
    assert legacy_inputs(SimpleNamespace(**cases[0]))["steps_per_point"] == 4


# ---------------------------------------------------------------- reports after the finish line

def _pair(client, name, order):
    me = register(client, name)
    first = clean_lap(seed=51)
    second = _steps_lap(52, rise_at_index=60)      # ~1.8 s into lap 2, nothing off track before
    uploads = {
        1: lambda: upload_df(client, me, first, lap_number=1, lap_time=90.0, started_at=100.0, recorded_at=1_700_000_000),
        2: lambda: upload_df(client, me, second, lap_number=2, lap_time=90.0, started_at=190.05, recorded_at=1_700_000_090),
    }
    ids = {n: uploads[n]()["lap_id"] for n in order}
    return ids[1], ids[2]


@pytest.mark.parametrize("order", [(1, 2), (2, 1)])
def test_a_step_rise_right_after_the_line_goes_to_the_lap_before(client, order):
    lap1, lap2 = _pair(client, f"Late{order[0]}", order)
    first, second = lap_row(lap1), lap_row(lap2)
    [own] = [f for f in second.validation["findings"] if f["code"].startswith("track_limits_points")]
    assert own["code"] == "track_limits_points_previous_lap" and own["severity"] == "info"
    assert second.is_valid is True and second.validation["status"] == "valid"
    [linked] = first.validation["linked"]
    assert linked["code"] == "late_track_limits" and linked["severity"] == "review"
    assert linked["detail"]["next_lap_id"] == lap2 and linked["detail"]["attribution"] == "this_lap"
    # V0.9.9: a lap that was already stored when the next lap's report arrived keeps counting
    # provisionally ("pending" — a record it holds is never taken away by a later upload); a lap
    # that arrives AFTER its next lap is held like any new REVIEW lap
    expected = "pending" if order == (1, 2) else "held"
    assert first.validation["status"] == "review" and first.review_status == expected and first.is_valid is True


def test_linked_findings_survive_revalidation_and_are_not_duplicated(client, capsys):
    from scripts import revalidate_laps
    from server.database import SessionLocal
    from server.lap_check import link_neighbours
    from server.models import Lap

    lap1, lap2 = _pair(client, "Twice", (1, 2))
    with SessionLocal() as db:
        assert link_neighbours(db, db.get(Lap, lap2)) == 0, "already linked"
    assert revalidate_laps.main(["--apply", "--driver", str(lap_row(lap1).driver_id)]) == 0
    assert len(lap_row(lap1).validation["linked"]) == 1 and lap_row(lap1).validation["status"] == "review"


def test_no_link_when_the_client_watched_the_late_window_itself(client):
    me = register(client, "Watched")
    late = {"seconds": 6.0, "complete": True, "lap_time_at_line": 90.0, "last_lap_time": 90.0,
            "steps": [[0.0, 0], [1.8, 1]], "penalties": [], "excursion_at": None, "can_see_excursions": True}
    lap1 = upload_df(client, me, clean_lap(seed=53), lap_number=1, started_at=100.0, lap_report={"reasons": [], "late": late})["lap_id"]
    lap2 = upload_df(client, me, _steps_lap(54, rise_at_index=60), lap_number=2, started_at=190.05,
                     recorded_at=1_700_000_090)["lap_id"]
    first = lap_row(lap1)
    assert [f["detail"]["from"] for f in first.validation["findings"] if f["code"] == "late_track_limits"] == ["client"]
    assert "linked" not in first.validation
    assert lap_row(lap2).is_valid is True


def test_laps_of_another_session_are_not_neighbours(client):
    me = register(client, "Apart")
    lap1 = upload_df(client, me, clean_lap(seed=55), lap_number=1, started_at=100.0)["lap_id"]
    upload_df(client, me, _steps_lap(56, rise_at_index=60), lap_number=2, started_at=500.0, recorded_at=1_700_000_090)
    assert "linked" not in (lap_row(lap1).validation or {})
