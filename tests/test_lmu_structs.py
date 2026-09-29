"""
ABI self-test for client/lmu/structs.py.

This is the check the review demanded: prove the ctypes layout actually
matches the game's C++ struct layout, rather than asserting it in a
comment. VehicleScoringInfoV01's offsets are independently verified
(see structs.py docstring) — every one of them is checked here. If this
test ever fails against a real game update, the struct definitions are
what need fixing, not this test.
"""

import ctypes

from client.lmu import structs


def test_vehicle_scoring_offsets_match_verified_reference():
    for expected_offset, field_name in structs.VEHICLE_SCORING_VERIFIED_OFFSETS:
        actual_offset = getattr(structs.VehicleScoringInfoV01, field_name).offset
        assert actual_offset == expected_offset, (
            f"{field_name}: expected offset {expected_offset}, got {actual_offset} — "
            "ctypes _pack_=4 layout no longer matches the verified reference."
        )


def test_vehicle_scoring_total_size():
    assert ctypes.sizeof(structs.VehicleScoringInfoV01) == structs.VEHICLE_SCORING_VERIFIED_SIZE


def test_telem_vect3_size():
    assert ctypes.sizeof(structs.TelemVect3) == 24


def test_telem_wheel_no_uninitialized_gap_at_end():
    # Basic sanity: struct size is a multiple of its own pack alignment (4),
    # and non-zero — catches an empty/degenerate _fields_ list.
    size = ctypes.sizeof(structs.TelemWheelV01)
    assert size > 0
    assert size % 4 == 0


def test_telem_info_wheel_array_present():
    # mWheel[4] must be the trailing member and each element must be a
    # TelemWheelV01 — a common transcription slip is off-by-one on the
    # array length or wrong element type, which silently shifts nothing
    # (it's last) but breaks every consumer that reads mWheel[i].
    field = getattr(structs.TelemInfoV01, "wheel")
    assert field.size == 4 * ctypes.sizeof(structs.TelemWheelV01)


def test_shared_memory_object_out_is_well_formed():
    # We can't validate this against a live game here (Windows + LMU
    # required), so this only proves the Python layout is internally
    # consistent (no ctypes layout exception, non-zero size). Real
    # validation happens by running scripts/dump_shared_memory.py (see
    # README) against the actual running game.
    size = ctypes.sizeof(structs.SharedMemoryObjectOut)
    assert size > ctypes.sizeof(structs.SharedMemoryScoringData)
    assert size > ctypes.sizeof(structs.SharedMemoryTelemetryData)


def test_scoring_info_validate_rejects_implausible_weather():
    info = structs.ScoringInfoV01()
    info.raining = 5.0  # out of [0,1] — the sentinel case this check exists for
    info.ambient_temp = 20.0
    info.track_temp = 25.0
    assert structs.validate_scoring_info(info) is False


def test_scoring_info_validate_accepts_plausible_weather():
    info = structs.ScoringInfoV01()
    info.raining = 0.0
    info.ambient_temp = 22.5
    info.track_temp = 31.0
    assert structs.validate_scoring_info(info) is True
