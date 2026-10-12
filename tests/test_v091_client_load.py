"""V0.9.1 (load review) — what the recorder costs the driver's PC while LMU is running.

  * reading the shared memory copies only the player's data (a few KB), not the whole struct
    with every vehicle (317 KiB) three times per frame — and returns exactly the same Frame
  * the torn-read guard still works for every region that is copied
  * a recorded sample keeps its ~180 extended values as float32 (720 bytes, not ~5.6 KB of
    Python float objects), and the lap file written from that is identical
"""

from __future__ import annotations

import ctypes
import random
from array import array

import numpy as np
import pytest

from client.lmu.structs import SharedMemoryObjectOut
from client.telemetry import reader as reader_module
from client.telemetry.channels import EXTENDED_COLUMNS, extract_extended
from client.telemetry.reader import LMUSharedMemoryReader

SIZE = ctypes.sizeof(SharedMemoryObjectOut)


def _shared_memory(seed=1, player_slot=5, scoring_slot=7, vehicles=12):
    """A buffer laid out like LMU's struct: random bytes everywhere (so a wrong offset shows),
    then the few fields a Frame depends on set to valid values."""
    rng = random.Random(seed)
    buf = ctypes.create_string_buffer(bytes(rng.getrandbits(8) for _ in range(SIZE)), SIZE)
    obj = SharedMemoryObjectOut.from_buffer(buf)
    info = obj.scoring.scoring_info
    info.raining, info.ambient_temp, info.track_temp, info.num_vehicles = 0.2, 21.0, 30.0, vehicles
    for i in range(obj.scoring.veh_scoring_info._length_):
        obj.scoring.veh_scoring_info[i].id = 1000 + i
    tel = obj.telemetry
    tel.player_has_vehicle, tel.player_vehicle_idx = 1, player_slot
    tel.telem_info[player_slot].id = 1000 + scoring_slot
    return buf


class _View:
    def __init__(self, buf):
        self._buf = buf  # keep the buffer alive: the view only holds its address (like a real mapping)
        self._address = ctypes.addressof(buf)
        self._size = SIZE

    def read_bytes(self):
        return ctypes.string_at(self._address, self._size)

    def close(self):
        pass


def _reader(buf):
    r = LMUSharedMemoryReader()
    r._view = _View(buf)
    return r


def _full_read_frame(buf):
    """The V0.9.0 way, kept as the reference: copy the whole struct, then select."""
    r = LMUSharedMemoryReader()

    class NoAddress:
        def read_bytes(self_inner):
            return ctypes.string_at(ctypes.addressof(buf), SIZE)

        def close(self_inner):
            pass

    r._view = NoAddress()
    return r.read()


@pytest.mark.parametrize("seed,player_slot,scoring_slot,vehicles", [(1, 5, 7, 12), (2, 0, 0, 1), (3, 103, 30, 31), (4, 17, 2, 40)])
def test_region_read_returns_exactly_the_frame_of_a_full_read(seed, player_slot, scoring_slot, vehicles):
    buf = _shared_memory(seed, player_slot, scoring_slot, vehicles)
    fast = _reader(buf).read()
    full = _full_read_frame(buf)
    assert fast is not None and full is not None
    assert bytes(fast.telemetry) == bytes(full.telemetry)
    assert bytes(fast.scoring) == bytes(full.scoring)
    assert bytes(fast.session) == bytes(full.session)


def test_region_read_declines_the_same_cases_as_a_full_read():
    cases = []
    buf = _shared_memory(); SharedMemoryObjectOut.from_buffer(buf).telemetry.player_has_vehicle = 0; cases.append(("no player", buf))
    buf = _shared_memory(); SharedMemoryObjectOut.from_buffer(buf).telemetry.player_vehicle_idx = 9999; cases.append(("bad index", buf))
    buf = _shared_memory(); SharedMemoryObjectOut.from_buffer(buf).scoring.scoring_info.ambient_temp = 500.0; cases.append(("misaligned scoring", buf))
    buf = _shared_memory(scoring_slot=20, vehicles=12); cases.append(("player not among the counted vehicles", buf))
    for name, b in cases:
        assert _reader(b).read() is None, name
        assert _full_read_frame(b) is None, name


def test_the_returned_frame_is_a_private_copy_not_a_live_view():
    buf = _shared_memory()
    frame = _reader(buf).read()
    before = bytes(frame.telemetry)
    ctypes.memset(ctypes.addressof(buf), 0, SIZE)        # the game overwrites the shared memory
    assert bytes(frame.telemetry) == before


def test_only_the_players_data_is_copied(monkeypatch):
    buf = _shared_memory()
    copied = []
    real = ctypes.string_at
    monkeypatch.setattr(reader_module.ctypes, "string_at", lambda addr, size: (copied.append(size), real(addr, size))[1])
    assert _reader(buf).read() is not None
    assert sum(copied) < 10_000, f"{sum(copied)} bytes copied per frame (the whole struct is {SIZE})"
    monkeypatch.setattr(reader_module.ctypes, "string_at", real)
    copied_full = []
    monkeypatch.setattr(reader_module.ctypes, "string_at", lambda addr, size: (copied_full.append(size), real(addr, size))[1])
    _reader(buf)._read_object()
    assert sum(copied_full) >= 2 * SIZE, "the old way, for comparison: the whole struct, twice"


def test_a_torn_read_of_any_region_skips_the_frame(monkeypatch):
    buf = _shared_memory()
    real = ctypes.string_at
    for victim in range(3):            # session info, player telemetry, player scoring
        calls = {"n": 0}

        def flaky(addr, size, victim=victim):
            data = real(addr, size)
            calls["n"] += 1
            # the second read of region `victim` sees a byte the game changed in between
            if calls["n"] == 2 * victim + 2:
                data = bytes([data[0] ^ 0xFF]) + data[1:]
            return data

        monkeypatch.setattr(reader_module.ctypes, "string_at", flaky)
        assert _reader(buf).read() is None, f"region {victim}"
    monkeypatch.setattr(reader_module.ctypes, "string_at", real)
    assert _reader(buf).read() is not None


def test_views_without_an_address_still_use_the_full_read():
    buf = _shared_memory()
    assert _full_read_frame(buf) is not None


# ---------------------------------------------------------------- memory per sample

def test_a_recorded_sample_keeps_its_extended_values_as_float32():
    from client.lmu.structs import TelemInfoV01
    from client.telemetry.parser import LapParser
    from client.telemetry.reader import Frame

    buf = _shared_memory()
    frame = _reader(buf).read()
    sample = LapParser()._build_sample(frame)
    assert isinstance(sample.ext, array) and sample.ext.typecode == "f"
    assert len(sample.ext) == len(EXTENDED_COLUMNS)
    assert sample.ext.itemsize * len(sample.ext) == 4 * len(EXTENDED_COLUMNS), "720 bytes, not ~5.6 KB of Python floats"
    expected = np.asarray(extract_extended(frame.telemetry, frame.scoring), dtype=np.float64).astype(np.float32)
    got = np.frombuffer(sample.ext.tobytes(), dtype=np.float32)
    assert np.array_equal(got, expected, equal_nan=True), "the same float32 values the file would have held"


def test_the_lap_file_is_identical_whether_samples_hold_tuples_or_float32_arrays(tmp_path):
    from client.telemetry.parser import Lap, TelemetrySample
    from client.telemetry.recorder import LapRecorder

    rng = np.random.default_rng(3)
    width = len(EXTENDED_COLUMNS)
    values = rng.normal(size=(200, width)) * 1000
    values[5, 7] = np.nan
    values[9, 3] = 1e40                                   # beyond float32: inf in both cases

    def samples(as_array):
        out = []
        for i in range(200):
            row = tuple(float(v) for v in values[i])
            out.append(TelemetrySample(
                t=i * 0.02, lap_dist=i * 1.0, speed=50.0, throttle=0.5, brake=0.0, clutch=0.0, steering=0.0, gear=3, rpm=6000.0,
                fuel=50.0, pos_x=0.0, pos_y=0.0, pos_z=float(i), tire_pressures=(1, 1, 1, 1), tire_temps_outer=(1, 1, 1, 1),
                brake_temps=(1, 1, 1, 1), ext=array("f", row) if as_array else row,
            ))
        return out

    def lap(as_array):
        return Lap(track_name="T", car_name="C", session_type=10, lap_number=1, lap_time=4.0, sector_times=[1.0, 1.0, 2.0],
                   is_valid=True, started_at=0.0, ambient_temp=20.0, track_temp=30.0, samples=samples(as_array))

    from_tuples = LapRecorder._build_dataframe(lap(False))
    from_arrays = LapRecorder._build_dataframe(lap(True))
    assert list(from_tuples.columns) == list(from_arrays.columns)
    for column in EXTENDED_COLUMNS:
        assert from_arrays[column].dtype == np.float32
        assert np.array_equal(from_tuples[column].to_numpy(), from_arrays[column].to_numpy(), equal_nan=True), column
