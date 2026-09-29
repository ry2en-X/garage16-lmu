"""
client/lmu/structs.py — ctypes mirror of Le Mans Ultimate's native shared
memory interface.

CRITICAL CORRECTION vs. earlier versions of this project: LMU does NOT use
the classic third-party rFactor2 plugin's two named files
($rFactor2SMMP_Telemetry$ / $rFactor2SMMP_Scoring$, from
TheIronWolfModding/rF2SharedMemoryMapPlugin). It ships its own, *built-in*
shared memory interface: a single named mapping "LMU_Data", one unified
struct (SharedMemoryObjectOut), and a companion named Windows Event
("LMU_Data_Event") that the game signals on update — there is no
mVersionUpdateBegin/mVersionUpdateEnd counter pair on these buffers.
Treating LMU like classic rF2 (wrong name, wrong struct shape, wrong
tearing-detection primitive) meant the reader could never receive real
data from the game.

Provenance and confidence, field by field:
  - VehicleScoringInfoV01 (sector times, lap times, position/place, etc.):
    every field name, type, and byte offset below is cross-checked against
    stephenhoran/goLMUSharedMemory (MIT license), whose offsets were in
    turn verified against ctypes.sizeof()/offsetof() from s-victor's
    pyLMUSharedMemory. HIGH confidence — this is the struct the leaderboard
    and record logic actually depend on.
  - TelemInfoV01 / TelemWheelV01: field names, types, and *order* are taken
    from the same source, but that project only publishes fully-verified
    byte offsets for VehicleScoringInfoV01, not this struct. Offsets here
    are derived mechanically from that order via ctypes' own `_pack_ = 4`
    layout engine (Python's implementation of the same "cap every field's
    alignment at 4 bytes" rule as the game's C++ `#pragma pack(4)`) — which
    the ABI test below cross-validates by reproducing VehicleScoringInfoV01's
    independently-known offsets byte-for-byte. MEDIUM-HIGH confidence: the
    algorithm is proven, the field list is not independently offset-verified.
  - ScoringInfoV01 (track name, session, weather): even the reference
    implementation is NOT fully certain of one region's padding (it runs a
    runtime heuristic search for mRaining rather than trusting a fixed
    offset — see its lmu_types_helper.go). This module lays the struct out
    the same mechanical way and adds a runtime plausibility check
    (validate_scoring_info) that logs a warning if the weather fields look
    implausible, rather than silently trusting an offset nobody has fully
    confirmed. LOWER confidence on that specific region only.

If LMU updates its shared memory layout, or if the ABI test in
tests/test_lmu_structs.py starts failing against a real running game,
these struct definitions — not the game — are what's wrong. Re-derive from
the same references (see README) before assuming anything else.
"""

from __future__ import annotations

import ctypes

SHARED_MEMORY_NAME = "LMU_Data"
SHARED_MEMORY_EVENT_NAME = "LMU_Data_Event"
MAX_MAPPED_VEHICLES = 104


class TelemVect3(ctypes.Structure):
    _pack_ = 4
    _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double), ("z", ctypes.c_double)]


class VehicleScoringInfoV01(ctypes.Structure):
    """Per-vehicle race/scoring data — this is where sector/lap times and
    track position live. Field offsets here are independently verified
    (see module docstring); the ABI test asserts every one of them."""

    _pack_ = 4
    _fields_ = [
        ("id", ctypes.c_int32),
        ("driver_name", ctypes.c_char * 32),
        ("vehicle_name", ctypes.c_char * 64),
        ("total_laps", ctypes.c_int16),
        ("sector", ctypes.c_int8),
        ("finish_status", ctypes.c_int8),
        ("lap_dist", ctypes.c_double),
        ("path_lateral", ctypes.c_double),
        ("track_edge", ctypes.c_double),
        ("best_sector1", ctypes.c_double),
        ("best_sector2", ctypes.c_double),
        ("best_lap_time", ctypes.c_double),
        ("last_sector1", ctypes.c_double),
        ("last_sector2", ctypes.c_double),
        ("last_lap_time", ctypes.c_double),
        ("cur_sector1", ctypes.c_double),
        ("cur_sector2", ctypes.c_double),
        ("num_pitstops", ctypes.c_int16),
        ("num_penalties", ctypes.c_int16),
        ("is_player", ctypes.c_bool),
        ("control", ctypes.c_int8),
        ("in_pits", ctypes.c_bool),
        ("place", ctypes.c_uint8),
        ("vehicle_class", ctypes.c_char * 32),
        ("time_behind_next", ctypes.c_double),
        ("laps_behind_next", ctypes.c_int32),
        ("time_behind_leader", ctypes.c_double),
        ("laps_behind_leader", ctypes.c_int32),
        ("lap_start_et", ctypes.c_double),
        ("pos", TelemVect3),
        ("local_vel", TelemVect3),
        ("local_accel", TelemVect3),
        ("ori", TelemVect3 * 3),
        ("local_rot", TelemVect3),
        ("local_rot_accel", TelemVect3),
        ("headlights", ctypes.c_uint8),
        ("pit_state", ctypes.c_uint8),
        ("server_scored", ctypes.c_uint8),
        ("individual_phase", ctypes.c_uint8),
        ("qualification", ctypes.c_int32),
        ("time_into_lap", ctypes.c_double),
        ("estimated_lap_time", ctypes.c_double),
        ("pit_group", ctypes.c_char * 24),
        ("flag", ctypes.c_uint8),
        ("under_yellow", ctypes.c_bool),
        # mCountLapFlag: 0 = don't count this lap (e.g. formation/out-lap
        # under some conditions), 1 = count normally, 2 = count as lap but
        # flag as not eligible for fastest-lap type comparisons. This is
        # the real, game-reported signal for lap validity — see
        # parser.py's _derive_is_valid, which uses this instead of a
        # fabricated field.
        ("count_lap_flag", ctypes.c_uint8),
        ("in_garage_stall", ctypes.c_bool),
        ("upgrade_pack", ctypes.c_uint8 * 16),
        ("pit_lap_dist", ctypes.c_float),
        ("best_lap_sector1", ctypes.c_float),
        ("best_lap_sector2", ctypes.c_float),
        ("steam_id", ctypes.c_uint64),
        ("veh_filename", ctypes.c_char * 32),
        ("attack_mode", ctypes.c_int16),
        ("fuel_fraction", ctypes.c_uint8),
        ("drs_state", ctypes.c_bool),
        ("expansion", ctypes.c_uint8 * 4),
    ]


# (offset, field_name) pairs taken directly from goLMUSharedMemory's
# ReadVehicleScoringInfoV01 (manually offset-verified against
# ctypes.sizeof() from pyLMUSharedMemory) — the ABI test asserts our
# ctypes layout reproduces every one of these exactly.
VEHICLE_SCORING_VERIFIED_OFFSETS = [
    (0, "id"), (4, "driver_name"), (36, "vehicle_name"), (100, "total_laps"),
    (102, "sector"), (103, "finish_status"), (104, "lap_dist"), (112, "path_lateral"),
    (120, "track_edge"), (128, "best_sector1"), (136, "best_sector2"), (144, "best_lap_time"),
    (152, "last_sector1"), (160, "last_sector2"), (168, "last_lap_time"), (176, "cur_sector1"),
    (184, "cur_sector2"), (192, "num_pitstops"), (194, "num_penalties"), (196, "is_player"),
    (197, "control"), (198, "in_pits"), (199, "place"), (200, "vehicle_class"),
    (232, "time_behind_next"), (240, "laps_behind_next"), (244, "time_behind_leader"),
    (252, "laps_behind_leader"), (256, "lap_start_et"), (264, "pos"), (288, "local_vel"),
    (312, "local_accel"), (336, "ori"), (408, "local_rot"), (432, "local_rot_accel"),
    (456, "headlights"), (457, "pit_state"), (458, "server_scored"), (459, "individual_phase"),
    (460, "qualification"), (464, "time_into_lap"), (472, "estimated_lap_time"), (480, "pit_group"),
    (504, "flag"), (505, "under_yellow"), (506, "count_lap_flag"), (507, "in_garage_stall"),
    (508, "upgrade_pack"), (524, "pit_lap_dist"), (528, "best_lap_sector1"), (532, "best_lap_sector2"),
    (536, "steam_id"), (544, "veh_filename"), (576, "attack_mode"), (578, "fuel_fraction"),
    (579, "drs_state"), (580, "expansion"),
]
VEHICLE_SCORING_VERIFIED_SIZE = 584


class TelemWheelV01(ctypes.Structure):
    _pack_ = 4
    _fields_ = [
        ("suspension_deflection", ctypes.c_double),
        ("ride_height", ctypes.c_double),
        ("susp_force", ctypes.c_double),
        ("brake_temp", ctypes.c_double),
        ("brake_pressure", ctypes.c_double),
        ("rotation", ctypes.c_double),
        ("lateral_patch_vel", ctypes.c_double),
        ("longitudinal_patch_vel", ctypes.c_double),
        ("lateral_ground_vel", ctypes.c_double),
        ("longitudinal_ground_vel", ctypes.c_double),
        ("camber", ctypes.c_double),
        ("lateral_force", ctypes.c_double),
        ("longitudinal_force", ctypes.c_double),
        ("tire_load", ctypes.c_double),
        ("grip_fract", ctypes.c_double),
        ("pressure", ctypes.c_double),
        ("temperature", ctypes.c_double * 3),
        ("wear", ctypes.c_double),
        ("terrain_name", ctypes.c_char * 16),
        ("surface_type", ctypes.c_uint8),
        ("flat", ctypes.c_bool),
        ("detached", ctypes.c_bool),
        ("static_undeflected_radius", ctypes.c_uint8),
        ("vertical_tire_deflection", ctypes.c_double),
        ("wheel_y_location", ctypes.c_double),
        ("toe", ctypes.c_double),
        ("tire_carcass_temperature", ctypes.c_double),
        ("tire_inner_layer_temperature", ctypes.c_double * 3),
        ("expansion", ctypes.c_uint8 * 24),
    ]


class TelemInfoV01(ctypes.Structure):
    """Per-vehicle physics telemetry. Field order verified against
    goLMUSharedMemory; byte offsets are derived (not independently
    confirmed) — see module docstring's confidence note."""

    _pack_ = 4
    _fields_ = [
        ("id", ctypes.c_int32),
        ("delta_time", ctypes.c_double),
        ("elapsed_time", ctypes.c_double),
        ("lap_number", ctypes.c_int32),
        ("lap_start_et", ctypes.c_double),
        ("vehicle_name", ctypes.c_char * 64),
        ("track_name", ctypes.c_char * 64),
        ("pos", TelemVect3),
        ("local_vel", TelemVect3),
        ("local_accel", TelemVect3),
        ("ori", TelemVect3 * 3),
        ("local_rot", TelemVect3),
        ("local_rot_accel", TelemVect3),
        ("gear", ctypes.c_int32),
        ("engine_rpm", ctypes.c_double),
        ("engine_water_temp", ctypes.c_double),
        ("engine_oil_temp", ctypes.c_double),
        ("clutch_rpm", ctypes.c_double),
        ("unfiltered_throttle", ctypes.c_double),
        ("unfiltered_brake", ctypes.c_double),
        ("unfiltered_steering", ctypes.c_double),
        ("unfiltered_clutch", ctypes.c_double),
        ("filtered_throttle", ctypes.c_double),
        ("filtered_brake", ctypes.c_double),
        ("filtered_steering", ctypes.c_double),
        ("filtered_clutch", ctypes.c_double),
        ("steering_shaft_torque", ctypes.c_double),
        ("front_3rd_deflection", ctypes.c_double),
        ("rear_3rd_deflection", ctypes.c_double),
        ("front_wing_height", ctypes.c_double),
        ("front_ride_height", ctypes.c_double),
        ("rear_ride_height", ctypes.c_double),
        ("drag", ctypes.c_double),
        ("front_downforce", ctypes.c_double),
        ("rear_downforce", ctypes.c_double),
        ("fuel", ctypes.c_double),
        ("engine_max_rpm", ctypes.c_double),
        ("scheduled_stops", ctypes.c_uint8),
        ("overheating", ctypes.c_bool),
        ("detached", ctypes.c_bool),
        ("headlights", ctypes.c_bool),
        ("dent_severity", ctypes.c_uint8 * 8),
        ("last_impact_et", ctypes.c_double),
        ("last_impact_magnitude", ctypes.c_double),
        ("last_impact_pos", TelemVect3),
        ("engine_torque", ctypes.c_double),
        ("current_sector", ctypes.c_int32),
        ("speed_limiter", ctypes.c_uint8),
        ("max_gears", ctypes.c_uint8),
        ("front_tire_compound_index", ctypes.c_uint8),
        ("rear_tire_compound_index", ctypes.c_uint8),
        ("fuel_capacity", ctypes.c_double),
        ("front_flap_activated", ctypes.c_uint8),
        ("rear_flap_activated", ctypes.c_uint8),
        ("rear_flap_legal_status", ctypes.c_uint8),
        ("ignition_starter", ctypes.c_uint8),
        ("front_tire_compound_name", ctypes.c_char * 18),
        ("rear_tire_compound_name", ctypes.c_char * 18),
        ("speed_limiter_available", ctypes.c_uint8),
        ("anti_stall_activated", ctypes.c_uint8),
        ("unused", ctypes.c_uint8 * 2),
        ("visual_steering_wheel_range", ctypes.c_float),
        ("rear_brake_bias", ctypes.c_double),
        ("turbo_boost_pressure", ctypes.c_double),
        ("physics_to_graphics_offset", ctypes.c_float * 3),
        ("physical_steering_wheel_range", ctypes.c_float),
        ("delta_best", ctypes.c_double),
        ("battery_charge_fraction", ctypes.c_double),
        ("electric_boost_motor_torque", ctypes.c_double),
        ("electric_boost_motor_rpm", ctypes.c_double),
        ("electric_boost_motor_temperature", ctypes.c_double),
        ("electric_boost_water_temperature", ctypes.c_double),
        ("electric_boost_motor_state", ctypes.c_uint8),
        ("expansion", ctypes.c_uint8 * 103),
        ("wheel", TelemWheelV01 * 4),
    ]


class ScoringInfoV01(ctypes.Structure):
    """Session-level info (track name, session type, weather, timing).
    See module docstring: the weather block's exact padding is the one
    region even the best public reference isn't fully certain of —
    validate_scoring_info() below sanity-checks it at runtime."""

    _pack_ = 4
    _fields_ = [
        ("track_name", ctypes.c_char * 64),
        ("session", ctypes.c_int32),
        ("current_et", ctypes.c_double),
        ("end_et", ctypes.c_double),
        ("max_laps", ctypes.c_int32),
        ("lap_dist", ctypes.c_double),
        ("results_stream", ctypes.c_void_p),
        ("num_vehicles", ctypes.c_int32),
        ("game_phase", ctypes.c_uint8),
        ("yellow_flag_state", ctypes.c_int8),
        ("sector_flag", ctypes.c_int8 * 3),
        ("start_light", ctypes.c_uint8),
        ("num_red_lights", ctypes.c_uint8),
        ("in_realtime", ctypes.c_bool),
        ("player_name", ctypes.c_char * 32),
        ("plr_file_name", ctypes.c_char * 64),
        ("dark_cloud", ctypes.c_double),
        ("raining", ctypes.c_double),
        ("ambient_temp", ctypes.c_double),
        ("track_temp", ctypes.c_double),
        ("wind", TelemVect3),
        ("min_path_wetness", ctypes.c_double),
        ("max_path_wetness", ctypes.c_double),
        ("game_mode", ctypes.c_uint8),
        ("is_password_protected", ctypes.c_bool),
        ("server_port", ctypes.c_uint16),
        ("server_public_ip", ctypes.c_uint32),
        ("max_players", ctypes.c_int32),
        ("server_name", ctypes.c_char * 32),
        ("start_et", ctypes.c_float),
        ("avg_path_wetness", ctypes.c_double),
        ("expansion", ctypes.c_uint8 * 200),
        ("vehicle", ctypes.c_void_p),
    ]


def validate_scoring_info(info: "ScoringInfoV01") -> bool:
    """Defense-in-depth for the one region of ScoringInfoV01 whose exact
    padding no public source has fully nailed down (see module docstring).
    Returns False (caller should log + distrust weather fields for this
    read, not the whole struct) if these plainly-bounded fields look like
    they landed on the wrong bytes."""
    return (
        0.0 <= info.raining <= 1.0
        and -60.0 <= info.ambient_temp <= 60.0
        and -60.0 <= info.track_temp <= 80.0
    )


class ApplicationStateV01(ctypes.Structure):
    _pack_ = 4
    _fields_ = [
        ("app_window", ctypes.c_void_p),
        ("width", ctypes.c_uint32),
        ("height", ctypes.c_uint32),
        ("refresh_rate", ctypes.c_uint32),
        ("windowed", ctypes.c_uint32),
        ("options_location", ctypes.c_uint8),
        ("options_page", ctypes.c_char * 31),
        ("expansion", ctypes.c_uint8 * 204),
    ]


SME_MAX = 16  # number of SharedMemoryEvent enum values (SME_ENTER .. SME_FFB)


class SharedMemoryGeneric(ctypes.Structure):
    _pack_ = 4
    _fields_ = [
        ("events", ctypes.c_uint32 * SME_MAX),
        ("game_version", ctypes.c_int32),
        ("ffb_torque", ctypes.c_float),
        ("app_info", ApplicationStateV01),
    ]


class SharedMemoryPathData(ctypes.Structure):
    _pack_ = 4
    _fields_ = [
        ("user_data", ctypes.c_char * 260),
        ("custom_variables", ctypes.c_char * 260),
        ("steward_results", ctypes.c_char * 260),
        ("player_profile", ctypes.c_char * 260),
        ("plugins_folder", ctypes.c_char * 260),
    ]


class SharedMemoryScoringData(ctypes.Structure):
    _pack_ = 4
    _fields_ = [
        ("scoring_info", ScoringInfoV01),
        ("scoring_stream_size", ctypes.c_uint8 * 12),
        ("veh_scoring_info", VehicleScoringInfoV01 * MAX_MAPPED_VEHICLES),
        ("scoring_stream", ctypes.c_uint8 * 65536),
    ]


class SharedMemoryTelemetryData(ctypes.Structure):
    _pack_ = 4
    _fields_ = [
        ("active_vehicles", ctypes.c_uint8),
        ("player_vehicle_idx", ctypes.c_uint8),
        ("player_has_vehicle", ctypes.c_bool),
        ("telem_info", TelemInfoV01 * MAX_MAPPED_VEHICLES),
    ]


class SharedMemoryObjectOut(ctypes.Structure):
    """The single top-level struct LMU writes into the "LMU_Data" mapping."""

    _pack_ = 4
    _fields_ = [
        ("generic", SharedMemoryGeneric),
        ("paths", SharedMemoryPathData),
        ("scoring", SharedMemoryScoringData),
        ("telemetry", SharedMemoryTelemetryData),
    ]
