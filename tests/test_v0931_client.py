"""V0.9.3.1 — the client side: same verdict as the server, the late window after the finish
line (client/telemetry/lap_verdict.py, parser.py) and the uploader's step-down for older
servers (0.9.4 -> 0.9.3.1 -> 0.9.3 -> 0.9.2 metadata)."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from client.telemetry import lap_verdict
from client.telemetry.channels import EXTENDED_COLUMNS
from client.telemetry.parser import LapParser


def _frame(lap_number, lap_dist, t, *, steps=0, last_lap_time=90.0, surface=0, flag=2, in_pits=False):
    wheel = SimpleNamespace(pressure=180.0, temperature=(350.0, 351.0, 352.0), brake_temp=600.0, surface_type=surface)
    tele = SimpleNamespace(
        lap_number=lap_number, elapsed_time=t, vehicle_name=b"499P Custom Team 2025 #397",
        local_vel=SimpleNamespace(x=0.0, y=0.0, z=-60.0), unfiltered_throttle=1.0, unfiltered_brake=0.0,
        unfiltered_clutch=0.0, unfiltered_steering=0.0, gear=6, engine_rpm=8000.0, fuel=50.0,
        pos=SimpleNamespace(x=0.0, y=0.0, z=0.0), wheel=[wheel] * 4, vehicle_model=b"Ferrari 499P\x00",
        lap_invalidated=0, track_limits_steps=steps,
    )
    scoring = SimpleNamespace(
        lap_dist=lap_dist, in_pits=in_pits, count_lap_flag=flag, last_lap_time=last_lap_time, last_sector1=30.0,
        last_sector2=60.0, vehicle_class=b"Hyper", veh_filename=b"397_25_499P", path_lateral=0.5, track_edge=6.0,
        num_penalties=0,
    )
    session = SimpleNamespace(track_name=b"Le Mans", session=10, ambient_temp=22.0, track_temp=30.0,
                              track_grip_level=3, is_fixed_setup=True, track_limits_steps_per_point=4,
                              track_limits_steps_per_penalty=12)
    return SimpleNamespace(telemetry=tele, scoring=scoring, session=session)


@pytest.fixture
def ext(monkeypatch):
    monkeypatch.setitem(LapParser._build_sample.__globals__, "extract_extended", lambda tele, scoring=None: tuple(
        {"lap_invalidated": float(tele.lap_invalidated), "track_limits_steps": float(tele.track_limits_steps),
         "count_lap_flag": float(scoring.count_lap_flag), "num_penalties": float(scoring.num_penalties),
         "path_lateral": scoring.path_lateral, "track_edge": scoring.track_edge}.get(name, 0.0)
        for name in EXTENDED_COLUMNS))


def _drive_to_line(parser, *, body_steps=0, last_lap_time=90.0):
    for i in range(200):                                       # lap 1: 0..3980 m over 20 s
        assert parser.feed(_frame(1, i * 20.0, i * 0.1, steps=body_steps if i >= 150 else 0)) is None
    # the line at 20.0 s; LMU resets the distance 2 frames later
    out = [parser.feed(_frame(2, 3990.0, 20.0, steps=body_steps)),
           parser.feed(_frame(2, 3995.0, 20.05, steps=body_steps)),
           parser.feed(_frame(2, 2.0, 20.1, steps=body_steps, last_lap_time=last_lap_time))]
    return out


def test_a_finished_lap_is_held_for_the_late_window_and_then_carries_it(ext):
    parser = LapParser()
    assert _drive_to_line(parser) == [None, None, None], "held after the line"
    lap = None
    t = 20.1
    i = 0
    while lap is None and t < 40:
        i += 1
        t = 20.1 + i * 0.1
        steps = 1 if t >= 22.5 else 0                         # LMU scores an offence 2.5 s after the line
        lap = parser.feed(_frame(2, 2.0 + i * 20, t, steps=steps))
    assert lap is not None and lap.lap_number == 1
    assert t == pytest.approx(26.0, abs=0.11), "released LATE_WINDOW_SECONDS after the line"
    late = lap.lap_report["late"]
    assert late["complete"] is True and late["steps"] == [[0.0, 0], [2.5, 1]]
    assert late["lap_time_at_line"] == 90.0 and late["last_lap_time"] == 90.0
    assert late["can_see_excursions"] is True and late["excursion_at"] is None
    assert lap.lap_report["reasons"] == [] and lap.is_valid is True


def test_steps_during_the_lap_are_reported_but_do_not_reject_it(ext):
    parser = LapParser(late_window_seconds=0)
    lap = [x for x in _drive_to_line(parser, body_steps=1) if x is not None][0]
    assert lap.is_valid is True
    assert lap.lap_report["reasons"] == ["track_limits_points"]


def test_the_late_window_records_a_withdrawn_lap_time_and_the_first_excursion(ext):
    parser = LapParser()
    _drive_to_line(parser)
    lap = None
    for i in range(1, 80):
        t = 20.1 + i * 0.1
        lap = parser.feed(_frame(2, 2.0 + i * 20, t, last_lap_time=-1.0 if t >= 23.0 else 90.0,
                                 surface=2 if 21.0 <= t < 21.5 else 0))
        if lap:
            break
    late = lap.lap_report["late"]
    assert late["last_lap_time"] == -1.0 and late["lap_time_at_line"] == 90.0
    assert late["excursion_at"] == pytest.approx(1.0, abs=0.06)


def test_a_held_lap_is_released_incomplete_when_the_session_ends(ext):
    parser = LapParser()
    _drive_to_line(parser)
    lap = parser.flush_pending()
    assert lap is not None and lap.lap_report["late"]["complete"] is False
    assert parser.flush_pending() is None


def test_a_held_lap_is_released_after_the_wall_clock_limit_when_no_frames_come(ext, monkeypatch):
    parser = LapParser()
    _drive_to_line(parser)
    start = parser._late.wall_start
    assert parser.idle(now=start + 1.0) is None
    lap = parser.idle(now=start + lap_verdict.LATE_WALL_LIMIT_SECONDS + 0.1)
    assert lap is not None and lap.lap_report["late"]["complete"] is False


def test_a_lap_shorter_than_the_window_releases_the_held_one_at_the_next_line(ext):
    parser = LapParser()
    _drive_to_line(parser)
    for i in range(1, 30):                                    # 3 s of lap 2 ...
        assert parser.feed(_frame(2, 2.0 + i * 100, 20.1 + i * 0.1)) is None
    released = parser.feed(_frame(3, 3990.0, 23.1))           # ... and the line again
    assert released is not None and released.lap_number == 1 and released.lap_report["late"]["complete"] is False


def test_the_reasons_follow_the_shared_rules():
    assert lap_verdict.lap_reasons(lap_time=90, in_pits=False, sector1=30, sector2=60,
                                   game_state={"count_lap_flag": 2, "track_limits_steps": 2, "penalties_added": 1}) == \
        ["track_limits_points", "penalty_added"]
    reasons = lap_verdict.lap_reasons(lap_time=0, in_pits=True, sector1=0, sector2=0,
                                      game_state={"count_lap_flag": 1, "invalidated": True})
    assert reasons == ["no_lap_time", "pit_lane", "sector_times_implausible", "game_time_not_counted", "game_invalidated"]
    assert lap_verdict.is_valid(["track_limits_points", "late_track_limits"]) is True
    assert lap_verdict.is_valid(["pit_lane"]) is False


def test_excursion_reads_surfaces_and_the_track_edge():
    on = SimpleNamespace(surface_type=0)
    grass = SimpleNamespace(surface_type=2)
    centre = SimpleNamespace(path_lateral=1.0, track_edge=6.0)
    assert lap_verdict.excursion(SimpleNamespace(wheel=[on] * 4), centre) is False
    assert lap_verdict.excursion(SimpleNamespace(wheel=[grass, grass, on, on]), centre) is True
    assert lap_verdict.excursion(SimpleNamespace(wheel=[grass, on, on, on]), centre) is False
    assert lap_verdict.excursion(SimpleNamespace(), SimpleNamespace(path_lateral=9.0, track_edge=6.0)) is True
    assert lap_verdict.excursion(SimpleNamespace(), SimpleNamespace()) is None


# ---------------------------------------------------------------- recorder and uploader

def _lap_with_report():
    from client.telemetry.parser import Lap, TelemetrySample

    sample = TelemetrySample(t=0, lap_dist=0, speed=0, throttle=0, brake=0, clutch=0, steering=0, gear=1, rpm=0, fuel=0,
                             pos_x=0, pos_y=0, pos_z=0, tire_pressures=(0,) * 4, tire_temps_outer=(0,) * 4, brake_temps=(0,) * 4)
    return Lap(track_name="T", car_name="C", session_type=10, lap_number=1, lap_time=90.0, sector_times=[30, 30, 30],
               is_valid=True, started_at=0.0, ambient_temp=20.0, track_temp=30.0, samples=[sample] * 3,
               game_lap_state={"count_lap_flag": 2},
               lap_report={"reasons": ["track_limits_points"], "late": {"seconds": 6.0, "complete": True}},
               lap_context={"pitstops": 0, "place": 2})


def test_the_recorder_stores_the_lap_report(tmp_path):
    from client.telemetry.recorder import LapRecorder

    meta = json.loads(LapRecorder(data_dir=str(tmp_path)).save(_lap_with_report()).read_text())
    assert meta["lap_report"]["reasons"] == ["track_limits_points"]


class _Resp:
    def __init__(self, status, text="", payload=None):
        self.status_code, self.text, self._payload = status, text, payload or {}

    def json(self):
        return self._payload


@pytest.mark.parametrize("server_knows,expected_level", [("0.9.4", 0), ("0.9.3.1", 1), ("0.9.3", 2), ("0.9.2", 3)])
def test_the_uploader_steps_down_one_metadata_level_at_a_time(tmp_path, monkeypatch, server_knows, expected_level):
    from client.telemetry.recorder import LapRecorder
    from client.uploader import uploader as up

    recorder = LapRecorder(data_dir=str(tmp_path))
    meta_path = recorder.save(_lap_with_report())
    accepted = {"0.9.4": up._LEVELS[0], "0.9.3.1": up._LEVELS[1], "0.9.3": up._LEVELS[2], "0.9.2": up._LEVELS[3]}[server_knows]
    sent = []

    class Body:
        def __init__(self, fields, *_a, **_k):
            self.fields = fields
            self.content_type = "multipart/form-data"

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_post(url, data=None, timeout=None, headers=None):
        metadata = json.loads(data.fields["envelope"])["metadata"]
        sent.append(set(metadata))
        if set(metadata) - accepted:
            return _Resp(400, '{"detail":"envelope.metadata is invalid: Extra inputs are not permitted"}')
        return _Resp(200, payload={"lap_id": 7})

    monkeypatch.setattr(up, "StreamingMultipart", Body)
    monkeypatch.setattr(up.requests, "post", fake_post)
    monkeypatch.setattr(up.time, "sleep", lambda s: None)
    uploader = up.Uploader(recorder=recorder, auth_token="x", client_secret="y")
    assert uploader.upload_one(meta_path) is True
    assert uploader.metadata_level == expected_level
    assert len(sent) == expected_level + 1
    if expected_level == 1:
        assert "lap_report" in sent[-1] and "lap_context" not in sent[-1], "a 0.9.3.1 server still gets its fields"
    if expected_level == 2:
        assert "game_lap_state" in sent[-1] and "lap_report" not in sent[-1], "a 0.9.3 server still gets the 0.9.3 fields"
    assert uploader.legacy_metadata is (expected_level == 3)


def test_the_track_limits_probe_writes_every_frame_and_prints_every_change(tmp_path, monkeypatch, capsys):
    from scripts import track_limits_probe as probe

    frames = [_frame(1, 3900.0, 10.0), _frame(2, 2.0, 10.1), _frame(2, 40.0, 12.4, steps=1),
              _frame(2, 60.0, 12.6, steps=1, last_lap_time=-1.0, surface=2)]

    class FakeReader:
        def open(self):
            pass

        def close(self):
            pass

        def stream(self, poll_hz=60.0):
            yield from frames

    monkeypatch.setattr(probe, "LMUSharedMemoryReader", FakeReader)
    out_file = tmp_path / "probe.csv"
    assert probe.main(["--csv", str(out_file)]) == 0
    printed = capsys.readouterr().out
    assert "lap 2   +2.30 s after the line" in printed and "track_limits_steps 0 -> 1" in printed
    assert "last_lap_time 90.0 -> -1.0" in printed and "surfaces 0/0/0/0 -> 2/2/2/2" in printed
    rows = out_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(rows) == 1 + len(frames) and rows[0].startswith("et,lap_number,lap_dist,last_lap_time")
