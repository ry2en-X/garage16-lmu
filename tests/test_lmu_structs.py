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


# ---------------------------------------------------------------------------
# V0.9.0 — LMU v1.3+ named fields (driver aids / electronics, wheel extras)
#
# The offsets below are what the field ORDER published by two independent
# projects (LMU Pitwall's bridge/src/shared_memory/types.rs and TinyPedal's
# pyLMUSharedMemory lmu_data.py — both state they follow LMU's own
# SharedMemoryInterface.hpp) produces under `#pragma pack(4)`. They are
# written out literally on purpose: if someone reorders or resizes a field,
# the numbers here no longer match and this test fails loudly instead of the
# client silently reading shifted (but still plausible-looking) values.
# ---------------------------------------------------------------------------

TELEM_INFO_SIZE = 1888          # also stated by an independent project (fpauker/lmu-rpm-leds)
TELEM_WHEEL_SIZE = 260
TELEM_WHEEL_ARRAY_OFFSET = 848  # 1888 - 4 * 260

TELEM_V13_EXPECTED_OFFSETS = {
    # offsets that existed before and must not move
    "gear": 352,
    "engine_rpm": 356,
    "unfiltered_throttle": 388,
    "fuel": 524,
    "engine_max_rpm": 532,
    "rear_brake_bias": 664,
    "delta_best": 696,
    "electric_boost_motor_state": 744,
    # the 1.3 block, carved out of the old 103-byte reserve
    "lap_invalidated": 745,
    "abs_active": 746,
    "tc_active": 747,
    "speed_limiter_active": 748,
    "wiper_state": 749,
    "tc_level": 750,
    "tc_max": 751,
    "tc_slip_level": 752,
    "tc_slip_max": 753,
    "tc_cut_level": 754,
    "tc_cut_max": 755,
    "abs_level": 756,
    "abs_max": 757,
    "motor_map": 758,
    "motor_map_max": 759,
    "brake_migration": 760,
    "brake_migration_max": 761,
    "front_anti_sway": 762,
    "front_anti_sway_max": 763,
    "rear_anti_sway": 764,
    "rear_anti_sway_max": 765,
    "lift_and_coast_progress": 766,
    "track_limits_steps": 767,
    "regen": 768,
    "state_of_charge": 772,
    "virtual_energy": 776,
    "time_gap_car_ahead": 780,
    "time_gap_car_behind": 784,
    "time_gap_place_ahead": 788,
    "time_gap_place_behind": 792,
    "vehicle_model": 796,
    "vehicle_class_enum": 826,
    "vehicle_championship_enum": 827,
    "expansion": 828,
    "wheel": TELEM_WHEEL_ARRAY_OFFSET,
}

TELEM_WHEEL_EXPECTED_OFFSETS = {
    "suspension_deflection": 0,
    "tire_load": 104,
    "grip_fract": 112,
    "temperature": 128,
    "wear": 152,
    "terrain_name": 160,
    "vertical_tire_deflection": 180,   # pack(4): right after the 4 one-byte fields at 176..179
    "toe": 196,
    "tire_carcass_temperature": 204,
    "tire_inner_layer_temperature": 212,
    "optimal_temp": 236,
    "compound_index": 240,
    "compound_type": 241,
    "expansion": 242,
}


def test_telem_info_size_is_unchanged_by_the_named_fields():
    # LMU spends reserve bytes instead of growing the struct; if this fails the
    # whole shared-memory telemetry array is misaligned.
    assert ctypes.sizeof(structs.TelemInfoV01) == TELEM_INFO_SIZE
    assert ctypes.sizeof(structs.TelemWheelV01) == TELEM_WHEEL_SIZE


def test_wheel_array_still_ends_the_struct():
    wheels = ctypes.sizeof(structs.TelemWheelV01) * 4
    assert structs.TelemInfoV01.wheel.offset == TELEM_WHEEL_ARRAY_OFFSET
    assert structs.TelemInfoV01.wheel.offset + wheels == ctypes.sizeof(structs.TelemInfoV01)


def test_telem_info_v13_offsets_match_the_published_layout():
    for name, expected in TELEM_V13_EXPECTED_OFFSETS.items():
        actual = getattr(structs.TelemInfoV01, name).offset
        assert actual == expected, f"TelemInfoV01.{name}: expected offset {expected}, got {actual}"


def test_telem_wheel_offsets_match_the_published_layout():
    # Hand-computed from the field list under pack(4); the fields the recorder reads must not move.
    for name, expected in TELEM_WHEEL_EXPECTED_OFFSETS.items():
        actual = getattr(structs.TelemWheelV01, name).offset
        assert actual == expected, f"TelemWheelV01.{name}: expected offset {expected}, got {actual}"


def test_telem_wheel_named_tail_adds_up_to_the_old_reserve():
    # 4 (float) + 1 + 1 + 18 reserve = the 24-byte reserve this block replaced.
    tail = (
        ctypes.sizeof(ctypes.c_float) + 2 * ctypes.sizeof(ctypes.c_uint8)
        + structs.TelemWheelV01.expansion.size
    )
    assert tail == 24
    assert structs.TelemWheelV01.optimal_temp.offset + tail == TELEM_WHEEL_SIZE
