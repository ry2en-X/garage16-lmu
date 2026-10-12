"""V0.9.3 — the server's own track-limits verdict (server/track_limits.py), the corrected
count_lap_flag meaning in the client, and the admin review of undecided laps.

Unit tests build the per-sample arrays directly (every rule, its position on the lap, the
edges of the lap, the defensive "implausible signal" path); the end-to-end tests upload real
lap files through the real client Uploader and check what the server stores and shows."""
from __future__ import annotations

import numpy as np
import pytest

from server import track_limits as tl
from tests.helpers.v093 import clean_lap, headers, lap_row, register, upload_df

N = 600  # 10 s at 60 Hz is enough for the unit cases


def _columns(n=N, **overrides):
    t = np.linspace(0.0, n / 60.0, n)
    cols = {
        "t": t,
        "lap_dist": np.linspace(0.0, 1000.0, n),
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


def codes(verdict, severity=None):
    return [f.code for f in verdict.findings if severity is None or f.severity == severity]


def test_a_clean_lap_is_valid_and_lists_the_signals_it_was_checked_with():
    v = tl.assess(_columns())
    assert v.status == tl.STATUS_VALID
    assert codes(v) == []
    assert {"lap_invalidated", "count_lap_flag", "track_limits_steps", "surface_type", "track_edge", "client"} <= set(v.signals)


def test_lmu_invalidating_the_lap_mid_lap_invalidates_it_with_the_place():
    flag = np.zeros(N)
    flag[300:] = 1.0
    v = tl.assess(_columns(lap_invalidated=flag))
    assert v.status == tl.STATUS_INVALID
    finding = v.findings[0]
    assert finding.code == "game_invalidated" and finding.source == "lap_invalidated"
    assert finding.at == pytest.approx(np.linspace(0, 1000, N)[300], abs=0.01)
    assert "track limits: LMU invalidated this lap at 501 m" in v.reasons()


def test_last_laps_flag_in_the_first_second_and_the_next_laps_tail_do_not_count():
    flag = np.zeros(N)
    flag[:40] = 1.0        # carried over from the previous lap for 0.66 s
    flag[-3:] = 1.0        # already the next lap (recorded after the lap number flipped)
    v = tl.assess(_columns(lap_invalidated=flag), tail_samples=3)
    assert v.status == tl.STATUS_VALID, v.as_dict()


def test_count_lap_flag_1_means_the_time_does_not_count():
    """The V0.9.3 correction: 1 = "count the lap but NOT its time" (rFactor 2 header)."""
    v = tl.assess(_columns(count_lap_flag=np.full(N, 1.0)))
    assert codes(v, tl.INVALID) == ["game_time_not_counted"]
    v = tl.assess(_columns(count_lap_flag=np.full(N, 0.0)))
    assert codes(v, tl.INVALID) == ["game_lap_not_counted"]
    # decided on the last sample of the lap BODY: the tail belongs to the next lap
    flags = np.full(N, 2.0)
    flags[-2:] = 0.0
    assert tl.assess(_columns(count_lap_flag=flags), tail_samples=2).status == tl.STATUS_VALID


def test_every_track_limits_step_rise_is_an_event_at_its_place():
    # V0.9.3.1: a rise is a REVIEW finding (steps are fractions of an LMU warning point),
    # and a rise in the first seconds without any excursion belongs to the previous lap
    steps = np.zeros(N)
    steps[200:] = 1.0      # 3.3 s into the lap, nothing off track before -> previous lap
    steps[450:] = 3.0      # 7.5 s into the lap -> this lap
    v = tl.assess(_columns(track_limits_steps=steps))
    events = sorted((f for f in v.findings if f.code in ("track_limits_points", "track_limits_points_previous_lap")),
                    key=lambda f: f.at)
    assert [f.detail["added"] for f in events] == [1, 2]
    assert [round(f.at) for f in events] == [round(1000 * 200 / (N - 1)), round(1000 * 450 / (N - 1))]
    assert [f.code for f in events] == ["track_limits_points_previous_lap", "track_limits_points"]
    assert [f.severity for f in events] == [tl.INFO, tl.REVIEW]
    assert v.status == tl.STATUS_REVIEW


def test_a_gap_in_the_points_column_is_not_a_jump():
    steps = np.full(N, 2.0)
    steps[100:110] = np.nan
    assert tl.assess(_columns(track_limits_steps=steps)).status == tl.STATUS_VALID


def test_four_wheels_on_grass_ask_for_review_two_wheels_are_only_noted():
    surf = {f"surface_type_{w}": np.zeros(N) for w in tl.WHEELS}
    for w in tl.WHEELS:
        surf[f"surface_type_{w}"][300:310] = 2.0           # all four on grass for 10 samples
    surf["surface_type_fl"][400:420] = 4.0                 # two wheels in the gravel
    surf["surface_type_rl"][400:420] = 4.0
    v = tl.assess(_columns(**surf))
    off = [f for f in v.findings if f.code == "four_wheels_off"]
    # V0.9.3.1: a heuristic — review, not invalid (LMU judges track width and time gained)
    assert len(off) == 1 and off[0].severity == tl.REVIEW and off[0].detail["surfaces"] == [2]
    assert off[0].tier == tl.TIER_HEURISTIC
    assert off[0].until > off[0].at
    noted = [f for f in v.findings if f.code == "wheels_off"]
    assert len(noted) == 1 and noted[0].severity == tl.INFO and noted[0].detail["wheels"] == 2


def test_a_single_frame_off_the_track_is_noise():
    surf = {f"surface_type_{w}": np.zeros(N) for w in tl.WHEELS}
    for w in tl.WHEELS:
        surf[f"surface_type_{w}"][300:302] = 3.0           # two samples < OFF_MIN_SAMPLES
    assert tl.assess(_columns(**surf)).status == tl.STATUS_VALID


def test_kerbs_are_on_the_track():
    surf = {f"surface_type_{w}": np.full(N, 5.0) for w in tl.WHEELS}
    assert tl.assess(_columns(**surf)).status == tl.STATUS_VALID


def test_an_implausible_surface_signal_is_ignored_not_trusted():
    """All four wheels 'on grass' for most of the lap cannot be a real lap — a struct-offset
    or game-update problem must not wipe out a clean lap."""
    surf = {f"surface_type_{w}": np.full(N, 2.0) for w in tl.WHEELS}
    v = tl.assess(_columns(**surf))
    assert v.status == tl.STATUS_VALID
    assert any(f.code == "signal_unreliable" and f.source == "surface_type" for f in v.findings)
    garbage = {f"surface_type_{w}": np.full(N, 17.0) for w in tl.WHEELS}
    assert "surface_type" not in tl.assess(_columns(**garbage)).signals


def test_out_of_range_game_flags_are_ignored():
    v = tl.assess(_columns(lap_invalidated=np.full(N, 42.0), count_lap_flag=np.full(N, 9.0)))
    assert v.status == tl.STATUS_VALID
    assert {f.source for f in v.findings if f.code == "signal_unreliable"} >= {"lap_invalidated", "count_lap_flag"}


def test_beyond_the_track_edge_only_asks_for_a_review():
    lateral = np.zeros(N)
    lateral[200:260] = 9.0           # 3 m beyond a 6 m edge for 1 s
    v = tl.assess(_columns(path_lateral=lateral))
    assert v.status == tl.STATUS_REVIEW
    edge = [f for f in v.findings if f.code == "edge_exceeded"][0]
    assert edge.severity == tl.REVIEW and edge.detail["beyond_m"] == pytest.approx(3.0)
    lateral[200:205] = 9.0           # a short brush is not enough
    lateral[205:260] = 0.0
    assert tl.assess(_columns(path_lateral=lateral)).status == tl.STATUS_VALID


def test_a_much_shorter_lap_than_usual_asks_for_a_review():
    # 600 samples at 60 m/s over 9.98 s = ~599 m driven
    v = tl.assess(_columns(), reference_distance=700.0)
    assert codes(v, tl.REVIEW) == ["short_distance"]
    assert tl.assess(_columns(), reference_distance=605.0).status == tl.STATUS_VALID   # within tolerance
    assert tl.assess(_columns(), reference_distance=None).status == tl.STATUS_VALID


def test_a_penalty_during_the_lap_asks_for_a_review():
    pens = np.zeros(N)
    pens[300:] = 1
    assert codes(tl.assess(_columns(num_penalties=pens)), tl.REVIEW) == ["penalty_added"]


def test_the_clients_own_rejection_still_counts():
    v = tl.assess(_columns(), client_valid=False, client_state={"count_lap_flag": 2, "invalidated": False})
    assert codes(v, tl.INVALID) == ["client_invalid"]


def test_a_lap_from_an_old_client_relies_on_the_client_and_says_so():
    cols = {"t": np.linspace(0, 10, N), "lap_dist": np.linspace(0, 1000, N), "speed": np.full(N, 60.0)}
    v = tl.assess(cols, client_valid=True)
    assert v.status == tl.STATUS_VALID and "not_verifiable" in codes(v, tl.INFO)
    assert tl.assess(cols, client_valid=False).status == tl.STATUS_INVALID


def test_the_stored_verdict_is_small_plain_json():
    flag = np.zeros(N)
    flag[300:] = 1
    out = tl.assess(_columns(lap_invalidated=flag)).as_dict()
    assert out["version"] == tl.TRACK_LIMITS_VERSION and out["status"] == "invalid"
    # sample 300 of 600: 1000 m * 300/599 and 10 s * 300/599, rounded for storage
    assert out["findings"][0] == {"code": "game_invalidated", "severity": "invalid", "source": "lap_invalidated",
                                  "at": 500.8, "t": 5.01, "tier": "lmu"}


# ---------------------------------------------------------------- client: corrected count_lap_flag

def test_the_client_rejects_count_lap_flag_1_and_reads_the_lap_body():
    from types import SimpleNamespace

    from client.telemetry.channels import EXTENDED_COLUMNS
    from client.telemetry.parser import LapParser, TelemetrySample

    def sample(t, **values):
        ext = [float("nan")] * len(EXTENDED_COLUMNS)
        for name, value in values.items():
            ext[EXTENDED_COLUMNS.index(name)] = value
        return TelemetrySample(t=t, lap_dist=0, speed=0, throttle=0, brake=0, clutch=0, steering=0, gear=1, rpm=0,
                               fuel=0, pos_x=0, pos_y=0, pos_z=0, tire_pressures=(0,) * 4, tire_temps_outer=(0,) * 4,
                               brake_temps=(0,) * 4, ext=ext)

    body = [sample(i / 10, count_lap_flag=2, lap_invalidated=0, track_limits_steps=0, num_penalties=0) for i in range(50)]
    tail = [sample(5.0 + i / 10, count_lap_flag=0, lap_invalidated=0, track_limits_steps=0, num_penalties=0) for i in range(3)]
    state = LapParser._game_lap_state(body + tail, tail=3)
    assert state == {"count_lap_flag": 2, "invalidated": False, "track_limits_steps": 0, "penalties_added": 0}
    scoring = SimpleNamespace(last_lap_time=90.0, in_pits=False, count_lap_flag=0, last_sector1=30.0, last_sector2=60.0)
    assert LapParser._derive_is_valid(scoring, pit_this_lap=False, game_state=state) is True

    one = [sample(i / 10, count_lap_flag=1, lap_invalidated=0, track_limits_steps=0, num_penalties=0) for i in range(50)]
    assert LapParser._derive_is_valid(scoring, False, LapParser._game_lap_state(one, 0)) is False
    carried = [sample(i / 10, count_lap_flag=2, lap_invalidated=1 if i < 5 else 0, track_limits_steps=0) for i in range(50)]
    assert LapParser._game_lap_state(carried, 0)["invalidated"] is False, "the first second is left out"
    stepped = [sample(i / 10, count_lap_flag=2, lap_invalidated=0, track_limits_steps=0 if i < 30 else 1) for i in range(50)]
    # V0.9.3.1: added steps are reported, but no longer reject the lap on the client
    assert LapParser._derive_is_valid(scoring, False, LapParser._game_lap_state(stepped, 0)) is True
    assert "track_limits_points" in LapParser._reasons(scoring, False, LapParser._game_lap_state(stepped, 0))
    # without per-sample data the frame's own flag decides, with the corrected meaning
    assert LapParser._derive_is_valid(SimpleNamespace(**{**scoring.__dict__, "count_lap_flag": 1}), False) is False
    assert LapParser._derive_is_valid(SimpleNamespace(**{**scoring.__dict__, "count_lap_flag": 2}), False) is True


# ---------------------------------------------------------------- end to end

def test_upload_lmu_invalidated_lap_is_invalid_with_reason_and_verdict(client):
    me = register(client, "Off Track")
    df = clean_lap(seed=3)
    flags = np.zeros(len(df), dtype=np.float32)
    flags[len(df) // 2:] = 1.0
    df["lap_invalidated"] = flags
    lap_id = upload_df(client, me, df)["lap_id"]
    lap = lap_row(lap_id)
    assert lap.is_valid is False
    assert any(r.startswith("track limits: LMU invalidated this lap at ") for r in lap.invalid_reason)
    assert lap.validation["status"] == "invalid"
    assert lap.validation["findings"][0]["code"] == "game_invalidated"
    assert lap.client_claimed_valid is True, "the client's claim is kept for the audit"
    # the driver sees why
    mine = client.get("/telemetry/laps", headers=headers(me)).json()
    assert mine[0]["validation_status"] == "invalid" and mine[0]["is_valid"] is False


def test_upload_with_four_wheels_off_goes_to_review(client):
    me = register(client, "Grass")
    df = clean_lap(seed=4)
    for w in tl.WHEELS:
        col = np.zeros(len(df), dtype=np.float32)
        col[1000:1010] = 2.0
        df[f"surface_type_{w}"] = col
    lap = lap_row(upload_df(client, me, df)["lap_id"])
    # V0.9.3.1: counts, and an admin looks at it
    assert lap.is_valid is True and lap.review_status == "pending"
    assert [f["code"] for f in lap.validation["findings"] if f["severity"] == "review"] == ["four_wheels_off"]


def test_review_laps_count_until_an_admin_decides(client):
    me = register(client, "Edge")
    df = clean_lap(seed=5)
    lateral = np.zeros(len(df), dtype=np.float32)
    lateral[1200:1300] = 10.0          # far beyond the 7 m edge for ~3 s
    df["path_lateral"] = lateral
    lap_id = upload_df(client, me, df)["lap_id"]
    lap = lap_row(lap_id)
    assert lap.is_valid is True and lap.review_status == "pending"
    assert lap.validation["status"] == "review"

    admin = {"X-Admin-Token": "test-admin-token"}
    queue = client.get("/admin/review", headers=admin).json()
    assert [q["id"] for q in queue] == [lap_id]
    assert queue[0]["validation"]["findings"][0]["code"] == "edge_exceeded"

    resp = client.post(f"/admin/laps/{lap_id}/review", json={"decision": "invalidate", "note": "cut T3"}, headers=admin)
    assert resp.status_code == 200 and resp.json()["review_status"] == "invalidated"
    lap = lap_row(lap_id)
    assert lap.is_valid is False and lap.invalid_reason == ["admin review: cut T3"]
    assert lap.validation["review"]["decision"] == "invalidate"
    assert client.get("/admin/review", headers=admin).json() == []
    assert len(client.get("/admin/review", params={"status": "invalidated"}, headers=admin).json()) == 1


def test_clearing_a_review_keeps_the_lap(client):
    me = register(client, "Penalty")
    df = clean_lap(seed=6)
    pens = np.zeros(len(df), dtype=np.float32)
    pens[1500:] = 1.0
    df["num_penalties"] = pens
    lap_id = upload_df(client, me, df)["lap_id"]
    admin = {"X-Admin-Token": "test-admin-token"}
    assert client.post(f"/admin/laps/{lap_id}/review", json={"decision": "clear"}, headers=admin).status_code == 200
    lap = lap_row(lap_id)
    assert lap.is_valid is True and lap.review_status == "cleared"
    assert client.post(f"/admin/laps/{lap_id}/review", json={"decision": "maybe"}, headers=admin).status_code == 422
    assert client.post(f"/admin/laps/{lap_id}/review", json={"decision": "clear"}).status_code == 403


def test_a_clean_upload_stores_a_valid_verdict_and_figures(client):
    me = register(client, "Clean")
    lap = lap_row(upload_df(client, me, clean_lap(seed=7))["lap_id"])
    assert lap.is_valid is True and lap.review_status is None
    assert lap.validation["status"] == "valid"
    assert lap.lap_stats["profile"]["step"] == 50.0
    assert lap.lap_stats["driven_m"] > 2000


def test_an_old_clients_lap_keeps_working_and_says_it_could_not_be_verified(client):
    me = register(client, "Old Client")
    df = clean_lap(seed=8, with_extended=False)
    lap = lap_row(upload_df(client, me, df, client_version=None)["lap_id"])
    assert lap.is_valid is True
    assert [f["code"] for f in lap.validation["findings"]] == ["not_verifiable"]


# ---------------------------------------------------------------- re-checking stored laps

def test_revalidating_old_laps_is_a_dry_run_and_never_flips_validity_unasked(client, capsys):
    from scripts import revalidate_laps
    from server.database import SessionLocal
    from server.models import Lap

    me = register(client, "Archive")
    df = clean_lap(seed=31)
    flags = np.zeros(len(df), dtype=np.float32)
    flags[2000:] = 1.0
    df["lap_invalidated"] = flags
    lap_id = upload_df(client, me, df)["lap_id"]
    with SessionLocal() as db:   # what a lap uploaded before V0.9.3 looks like: valid, no verdict, no figures
        db.query(Lap).filter(Lap.id == lap_id).update({Lap.is_valid: True, Lap.invalid_reason: None,
                                                       Lap.validation: None, Lap.lap_stats: None})
        db.commit()

    assert revalidate_laps.main([]) == 0
    out = capsys.readouterr().out
    assert f"lap {lap_id:>6}" in out and "LMU invalidated this lap" in out and "Dry run" in out
    assert lap_row(lap_id).validation is None

    assert revalidate_laps.main(["--apply"]) == 0
    lap = lap_row(lap_id)
    assert lap.validation["status"] == "invalid" and lap.lap_stats["profile"]
    assert lap.is_valid is True and lap.review_status == "pending", "flagged for an admin, still counting"

    assert revalidate_laps.main(["--apply", "--update-validity"]) == 0
    lap = lap_row(lap_id)
    assert lap.is_valid is False and lap.invalid_reason[0].startswith("track limits: LMU invalidated this lap")
    with pytest.raises(SystemExit):
        revalidate_laps.main(["--update-validity"])
