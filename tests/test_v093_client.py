"""V0.9.3 — what the desktop client records and sends in addition: the session struct's new
named fields, LMU's model name, every car of the session (veh_filename -> model), the tail
of samples recorded after the lap-number flip, and the four scoring values per sample."""
from __future__ import annotations

import ctypes
import json
from types import SimpleNamespace

import pytest

from client.lmu import structs
from client.lmu.structs import SharedMemoryObjectOut
from client.telemetry.channels import EXTENDED_COLUMNS
from client.telemetry.parser import Lap, LapParser
from client.telemetry.reader import LMUSharedMemoryReader, _text
from client.telemetry.recorder import LapRecorder

SIZE = ctypes.sizeof(SharedMemoryObjectOut)


def test_naming_the_scoring_reserve_moves_nothing():
    """13 named bytes + 187 reserve = the former 200-byte reserve: same struct size, and the
    vehicle pointer after it stays where it was."""
    info = structs.ScoringInfoV01
    assert info.expansion.size == 187
    assert info.session_time_remaining.offset == info.avg_path_wetness.offset + 8
    assert info.track_limits_steps_per_point.offset + 1 == info.expansion.offset
    assert info.vehicle.offset == info.expansion.offset + 187
    assert info.expansion.offset - info.session_time_remaining.offset == 13


class _View:
    def __init__(self, buf):
        self._buf = buf
        self._address = ctypes.addressof(buf)


def _buffer_with_cars(cars, player_has_vehicle=True):
    buf = ctypes.create_string_buffer(SIZE)
    obj = SharedMemoryObjectOut.from_buffer(buf)
    obj.scoring.scoring_info.num_vehicles = len(cars)
    obj.telemetry.active_vehicles = len(cars)
    obj.telemetry.player_has_vehicle = player_has_vehicle
    for i, (veh_file, model, cls, name) in enumerate(cars):
        v = obj.scoring.veh_scoring_info[i]
        v.id = 500 + i
        v.veh_filename = veh_file.encode()
        v.vehicle_class = cls.encode()
        v.vehicle_name = name.encode()
        tele = obj.telemetry.telem_info[len(cars) - 1 - i]      # telemetry slots in another order
        tele.id = 500 + i
        tele.vehicle_model = model.encode()
    return buf


def test_the_vehicle_catalog_lists_every_car_with_its_model():
    buf = _buffer_with_cars([
        ("6_25_963", "Porsche 963", "Hyper", "Porsche Penske Motorsport 2025 #6"),
        ("3_25_963", "Porsche 963", "Hyper", "Porsche Penske Motorsport 2025 #3"),
        ("50_25_499P", "Ferrari 499P", "Hyper", "Ferrari AF Corse 2025 #50"),
    ])
    reader = LMUSharedMemoryReader()
    reader._view = _View(buf)
    cars = reader.read_vehicle_catalog()
    assert [(c["veh_file"], c["model"]) for c in cars] == [
        ("6_25_963", "Porsche 963"), ("3_25_963", "Porsche 963"), ("50_25_499P", "Ferrari 499P")]
    assert cars[0]["class"] == "Hyper" and cars[2]["name"].endswith("#50")


def test_the_vehicle_catalog_never_breaks_recording():
    reader = LMUSharedMemoryReader()
    assert reader.read_vehicle_catalog() == [], "no view: nothing, no exception"
    reader._view = SimpleNamespace(_address=None)
    assert reader.read_vehicle_catalog() == []


def test_text_from_shared_memory():
    assert _text(b"Porsche 963\x00garbage") == "Porsche 963"
    assert _text("Citroën".encode("cp1252") + b"\x00") == "Citroën", "Windows-1252 bytes are read, not dropped"
    assert _text(b"\x01\x02\x03") == "", "control characters are not a name"
    assert _text(b"") == ""


def test_the_parser_remembers_the_grid_bounded_and_without_duplicates():
    parser = LapParser()
    parser.observe_vehicles([{"veh_file": "6_25_963", "model": "Porsche 963", "class": "Hyper"}] * 3)
    parser.observe_vehicles([{"veh_file": "", "model": "x"}, {"veh_file": "50_25_499P", "model": "Ferrari 499P", "class": "Hyper"}])
    assert [g["veh_file"] for g in parser._grid.values()] == ["6_25_963", "50_25_499P"]
    parser.observe_vehicles([{"veh_file": f"{i}_25_X", "model": "X"} for i in range(500)])
    assert len(parser._grid) == LapParser.MAX_GRID_VEHICLES
    parser.reset()
    assert parser._grid == {}, "a new session may have a different grid"


# ---------------------------------------------------------------- a whole lap through the parser

def _frame(lap_number, lap_dist, t, *, flag=2, invalidated=0, steps=0):
    wheel = SimpleNamespace(pressure=180.0, temperature=(350.0, 351.0, 352.0), brake_temp=600.0)
    tele = SimpleNamespace(
        lap_number=lap_number, elapsed_time=t, vehicle_name=b"499P Custom Team 2025 #397",
        local_vel=SimpleNamespace(x=0.0, y=0.0, z=-60.0), unfiltered_throttle=1.0, unfiltered_brake=0.0,
        unfiltered_clutch=0.0, unfiltered_steering=0.0, gear=6, engine_rpm=8000.0, fuel=50.0,
        pos=SimpleNamespace(x=0.0, y=0.0, z=0.0), wheel=[wheel] * 4, vehicle_model=b"Ferrari 499P\x00",
        lap_invalidated=invalidated, track_limits_steps=steps,
    )
    scoring = SimpleNamespace(
        lap_dist=lap_dist, in_pits=False, count_lap_flag=flag, last_lap_time=90.0, last_sector1=30.0,
        last_sector2=60.0, vehicle_class=b"Hyper", veh_filename=b"397_25_499P", path_lateral=0.5, track_edge=6.0,
        num_penalties=0,
    )
    session = SimpleNamespace(track_name=b"Le Mans", session=10, ambient_temp=22.0, track_temp=30.0,
                              track_grip_level=3, is_fixed_setup=True, track_limits_steps_per_point=4,
                              track_limits_steps_per_penalty=12)
    return SimpleNamespace(telemetry=tele, scoring=scoring, session=session)


def test_a_completed_lap_carries_identity_tail_session_and_game_state(monkeypatch):
    # extended channels from SimpleNamespaces: only the scoring part is real here
    # patched in the globals LapParser actually uses (another test may have reloaded the module)
    monkeypatch.setitem(LapParser._build_sample.__globals__, "extract_extended", lambda tele, scoring=None: tuple(
        {"lap_invalidated": float(tele.lap_invalidated), "track_limits_steps": float(tele.track_limits_steps),
         "count_lap_flag": float(scoring.count_lap_flag), "num_penalties": float(scoring.num_penalties),
         "path_lateral": scoring.path_lateral, "track_edge": scoring.track_edge}.get(name, 0.0)
        for name in EXTENDED_COLUMNS))

    parser = LapParser(late_window_seconds=0)
    parser.observe_vehicles([{"veh_file": "397_25_499P", "model": "Ferrari 499P", "class": "Hyper"}])
    lap = None
    for i in range(200):                                    # lap 1 body: 0 .. 3980 m
        assert parser.feed(_frame(1, i * 20.0, i * 0.1)) is None
    # lap number flips; lap_dist resets 3 frames later; the tail frames carry the next lap's flags
    assert parser.feed(_frame(2, 3990.0, 20.0, flag=0)) is None
    assert parser.feed(_frame(2, 3995.0, 20.1, flag=0)) is None
    lap = parser.feed(_frame(2, 2.0, 20.2, flag=0))
    assert lap is not None and type(lap).__name__ == "Lap"   # (not isinstance: another test may reload the module)
    assert lap.tail_samples == 2
    assert lap.vehicle_model == "Ferrari 499P" and lap.car_model == "397_25_499P"
    assert (lap.track_grip_level, lap.fixed_setup, lap.track_limits_steps_per_point) == (3, True, 4)
    assert lap.game_lap_state == {"count_lap_flag": 2, "invalidated": False, "track_limits_steps": 0, "penalties_added": 0}
    assert lap.is_valid is True, "the tail's flag 0 belongs to the next lap"
    assert lap.grid_vehicles == [{"veh_file": "397_25_499P", "model": "Ferrari 499P", "class": "Hyper"}]


def test_the_recorder_stores_only_what_the_lap_has(tmp_path):
    from client.telemetry.parser import TelemetrySample

    sample = TelemetrySample(t=0, lap_dist=0, speed=0, throttle=0, brake=0, clutch=0, steering=0, gear=1, rpm=0, fuel=0,
                             pos_x=0, pos_y=0, pos_z=0, tire_pressures=(0,) * 4, tire_temps_outer=(0,) * 4, brake_temps=(0,) * 4)
    base = dict(track_name="T", car_name="C", session_type=10, lap_number=1, lap_time=90.0, sector_times=[30, 30, 30],
                is_valid=True, started_at=0.0, ambient_temp=20.0, track_temp=25.0, samples=[sample])
    rec = LapRecorder(data_dir=str(tmp_path))
    old_style = json.loads(rec.save(Lap(**base)).read_text())
    assert not set(old_style) & {"vehicle_model", "tail_samples", "grid_vehicles", "game_lap_state"}, "nothing new when empty"
    new = json.loads(rec.save(Lap(**base, vehicle_model="Ferrari 499P", tail_samples=3, track_grip_level=0, fixed_setup=False,
                                  game_lap_state={"count_lap_flag": 2}, grid_vehicles=[{"veh_file": "a", "model": "b", "class": "c"}])).read_text())
    assert new["vehicle_model"] == "Ferrari 499P" and new["tail_samples"] == 3
    assert new["track_grip_level"] == 0 and new["fixed_setup"] is False, "0 and False are values, not 'missing'"
    assert new["grid_vehicles"] == [{"veh_file": "a", "model": "b", "class": "c"}]


def test_the_uploader_sends_the_new_fields_to_a_093_server(tmp_path):
    from client.uploader.uploader import Uploader

    (tmp_path / "lap.parquet").write_bytes(b"x")
    meta = {"track_name": "T", "car_name": "C", "session_type": 10, "lap_number": 1, "lap_time": 90.0, "sector_times": [1, 1, 1],
            "is_valid": True, "started_at": 0, "recorded_at": 0, "telemetry_file": "lap.parquet", "uploaded": False,
            "vehicle_model": "M", "tail_samples": 2, "grid_vehicles": [], "rejected_reason": "local only"}
    (tmp_path / "lap.json").write_text(json.dumps(meta))
    payload = Uploader(recorder=LapRecorder(data_dir=str(tmp_path)))._build_payload(tmp_path / "lap.json")
    sent = payload["envelope"]["metadata"]
    assert sent["vehicle_model"] == "M" and sent["tail_samples"] == 2
    assert "uploaded" not in sent and "rejected_reason" not in sent
    legacy = Uploader(recorder=LapRecorder(data_dir=str(tmp_path)))._build_payload(tmp_path / "lap.json", legacy=True)
    assert "vehicle_model" not in legacy["envelope"]["metadata"]


@pytest.mark.parametrize("module", ["scripts.car_identity_probe", "scripts.revalidate_laps", "scripts.car_identity_report"])
def test_the_new_scripts_import_and_have_help(module, capsys):
    import importlib

    mod = importlib.import_module(module)
    with pytest.raises(SystemExit) as exit_info:
        mod.main(["--help"])
    assert exit_info.value.code == 0
    assert "usage" in capsys.readouterr().out.lower()
