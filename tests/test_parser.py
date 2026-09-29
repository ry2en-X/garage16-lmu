"""
Tests for LapParser: sector-split math, lap-boundary detection, and
is_valid derivation. Pure-Python logic — no game, no mmap, no third-party
deps needed; builds fake Frame/telemetry/scoring objects with SimpleNamespace
instead of the real ctypes structs, since only field access matters here.
"""

from types import SimpleNamespace

from client.telemetry.parser import LapParser
from client.telemetry.reader import Frame


def _vec3(x=0.0, y=0.0, z=0.0):
    return SimpleNamespace(x=x, y=y, z=z)


def _wheel(pressure=1.0, temperature=(90.0, 95.0, 90.0), brake_temp=300.0):
    return SimpleNamespace(pressure=pressure, temperature=temperature, brake_temp=brake_temp)


def _telemetry(lap_number, elapsed_time, vehicle_name=b"Car", track_name=b"Track"):
    return SimpleNamespace(
        lap_number=lap_number,
        elapsed_time=elapsed_time,
        vehicle_name=vehicle_name,
        local_vel=_vec3(10.0, 0.0, 0.0),
        unfiltered_throttle=1.0,
        unfiltered_brake=0.0,
        unfiltered_clutch=0.0,
        unfiltered_steering=0.0,
        gear=3,
        engine_rpm=6000.0,
        fuel=50.0,
        pos=_vec3(1.0, 2.0, 3.0),
        wheel=[_wheel(), _wheel(), _wheel(), _wheel()],
    )


def _scoring(last_lap_time=90.0, last_sector1=30.0, last_sector2=60.0, in_pits=False, count_lap_flag=1,
             lap_dist=0.0, vehicle_class=b"Hypercar", veh_filename=b"TestCar"):
    return SimpleNamespace(
        last_lap_time=last_lap_time,
        last_sector1=last_sector1,
        last_sector2=last_sector2,
        in_pits=in_pits,
        count_lap_flag=count_lap_flag,
        lap_dist=lap_dist,
        vehicle_class=vehicle_class,
        veh_filename=veh_filename,
    )


def _session(track_name=b"Le Mans", session=10, ambient_temp=22.0, track_temp=30.0):
    return SimpleNamespace(track_name=track_name, session=session, ambient_temp=ambient_temp, track_temp=track_temp)


def test_sector_splits_basic_case():
    # S1=30, cumulative S2=60 (i.e. sector 2 itself took 30s), lap=90
    # -> S1=30, S2=60-30=30, S3=90-60=30
    scoring = _scoring(last_lap_time=90.0, last_sector1=30.0, last_sector2=60.0)
    splits = LapParser._derive_sector_splits(scoring)
    assert splits == [30.0, 30.0, 30.0]


def test_sector_splits_do_not_double_subtract_s1():
    # Regression test for the exact bug found in review: an uneven split
    # where the old `total - s1 - s2_cumulative` formula would have
    # produced a NEGATIVE or understated S3.
    # S1=20, cumulative S2=50 (sector2 itself = 30s), lap=100
    # -> correct S3 = 100 - 50 = 50 (NOT 100-20-50=30, the old bug's answer)
    scoring = _scoring(last_lap_time=100.0, last_sector1=20.0, last_sector2=50.0)
    s1, s2, s3 = LapParser._derive_sector_splits(scoring)
    assert (s1, s2, s3) == (20.0, 30.0, 50.0)
    assert s1 + s2 + s3 == scoring.last_lap_time


def test_sector_splits_invalid_data_returns_zeroed_fallback():
    scoring = _scoring(last_lap_time=0.0, last_sector1=0.0, last_sector2=0.0)
    assert LapParser._derive_sector_splits(scoring) == [0.0, 0.0, 0.0]


def test_is_valid_true_for_clean_lap():
    scoring = _scoring()
    assert LapParser._derive_is_valid(scoring, pit_this_lap=False) is True


def test_is_valid_false_when_pit_visited():
    scoring = _scoring()
    assert LapParser._derive_is_valid(scoring, pit_this_lap=True) is False


def test_is_valid_false_when_game_flags_dont_count():
    scoring = _scoring(count_lap_flag=0)
    assert LapParser._derive_is_valid(scoring, pit_this_lap=False) is False


def test_is_valid_false_for_nonpositive_lap_time():
    scoring = _scoring(last_lap_time=0.0)
    assert LapParser._derive_is_valid(scoring, pit_this_lap=False) is False


def test_feed_returns_none_until_lap_boundary_crossed():
    parser = LapParser()
    frame1 = Frame(telemetry=_telemetry(lap_number=1, elapsed_time=0.0), scoring=_scoring(), session=_session())
    assert parser.feed(frame1) is None
    frame2 = Frame(telemetry=_telemetry(lap_number=1, elapsed_time=10.0), scoring=_scoring(), session=_session())
    assert parser.feed(frame2) is None


def test_feed_emits_completed_lap_on_boundary():
    parser = LapParser()
    parser.feed(Frame(telemetry=_telemetry(lap_number=1, elapsed_time=0.0), scoring=_scoring(lap_dist=100.0), session=_session()))
    parser.feed(Frame(telemetry=_telemetry(lap_number=1, elapsed_time=45.0), scoring=_scoring(lap_dist=2000.0), session=_session()))
    # Boundary frame: lap_number already 2, but lap_dist hasn't reset yet
    # (realistic — see P0-7a) — must NOT emit yet.
    pending = parser.feed(
        Frame(telemetry=_telemetry(lap_number=2, elapsed_time=90.0), scoring=_scoring(lap_dist=4000.0, last_lap_time=90.0), session=_session())
    )
    assert pending is None
    # The actual reset frame — lap_dist drops back near 0 — THIS is what
    # finalizes lap 1.
    completed = parser.feed(
        Frame(telemetry=_telemetry(lap_number=2, elapsed_time=90.05), scoring=_scoring(lap_dist=5.0, last_lap_time=90.0), session=_session())
    )
    assert completed is not None
    assert completed.lap_number == 1
    assert completed.lap_time == 90.0
    # 4: the two ordinary frames, plus the boundary frame — all three
    # correctly attributed to the COMPLETING lap (P0-7a, V0.6.6) — the
    # reset frame itself becomes the new lap's first sample instead.
    assert len(completed.samples) == 3
    assert completed.samples[-1].lap_dist == 4000.0


def test_feed_marks_lap_invalid_if_pit_visited_mid_lap():
    parser = LapParser()
    parser.feed(Frame(telemetry=_telemetry(lap_number=1, elapsed_time=0.0), scoring=_scoring(in_pits=False, lap_dist=100.0), session=_session()))
    parser.feed(Frame(telemetry=_telemetry(lap_number=1, elapsed_time=10.0), scoring=_scoring(in_pits=True, lap_dist=2000.0), session=_session()))
    parser.feed(
        Frame(telemetry=_telemetry(lap_number=2, elapsed_time=90.0), scoring=_scoring(in_pits=False, lap_dist=4000.0), session=_session())
    )
    completed = parser.feed(
        Frame(telemetry=_telemetry(lap_number=2, elapsed_time=90.05), scoring=_scoring(in_pits=False, lap_dist=5.0), session=_session())
    )
    assert completed.is_valid is False


def test_feed_resets_pit_flag_for_next_lap():
    parser = LapParser()
    parser.feed(Frame(telemetry=_telemetry(lap_number=1, elapsed_time=0.0), scoring=_scoring(in_pits=True, lap_dist=100.0), session=_session()))
    parser.feed(Frame(telemetry=_telemetry(lap_number=2, elapsed_time=90.0), scoring=_scoring(in_pits=False, lap_dist=4000.0), session=_session()))
    lap1 = parser.feed(Frame(telemetry=_telemetry(lap_number=2, elapsed_time=90.05), scoring=_scoring(in_pits=False, lap_dist=5.0), session=_session()))
    assert lap1.is_valid is False

    parser.feed(Frame(telemetry=_telemetry(lap_number=2, elapsed_time=135.0), scoring=_scoring(in_pits=False, lap_dist=2000.0), session=_session()))
    parser.feed(Frame(telemetry=_telemetry(lap_number=3, elapsed_time=180.0), scoring=_scoring(in_pits=False, lap_dist=4000.0), session=_session()))
    lap2 = parser.feed(Frame(telemetry=_telemetry(lap_number=3, elapsed_time=180.05), scoring=_scoring(in_pits=False, lap_dist=5.0), session=_session()))
    assert lap2.is_valid is True  # lap 2 itself had no pit visit


# P0-7(b): Lap number jump (not +1) must NOT emit a completed lap.
def test_feed_discards_buffer_on_lap_number_jump():
    """Session restart (5→1) or similar jumps must discard the buffer
    instead of emitting a bogus lap with stale/invalid data."""
    parser = LapParser()
    # Feed some frames for "lap 5"
    parser.feed(Frame(telemetry=_telemetry(lap_number=5, elapsed_time=0.0), scoring=_scoring(), session=_session()))
    parser.feed(Frame(telemetry=_telemetry(lap_number=5, elapsed_time=45.0), scoring=_scoring(), session=_session()))
    # Jump to lap 1 — NOT a valid lap completion
    result = parser.feed(
        Frame(telemetry=_telemetry(lap_number=1, elapsed_time=0.0), scoring=_scoring(last_lap_time=-1.0), session=_session())
    )
    assert result is None  # must NOT emit a completed lap


def test_feed_discards_buffer_on_backward_jump():
    """Going from lap 3 to lap 1 (session reset) should not emit."""
    parser = LapParser()
    parser.feed(Frame(telemetry=_telemetry(lap_number=3, elapsed_time=200.0), scoring=_scoring(), session=_session()))
    result = parser.feed(
        Frame(telemetry=_telemetry(lap_number=1, elapsed_time=0.0), scoring=_scoring(last_lap_time=0.0), session=_session())
    )
    assert result is None


def test_feed_emits_on_normal_increment_after_jump():
    """After a jump (which discards), a normal n→n+1 should still work."""
    parser = LapParser()
    # Start at lap 5
    parser.feed(Frame(telemetry=_telemetry(lap_number=5, elapsed_time=0.0), scoring=_scoring(), session=_session()))
    # Jump to lap 1 (discards)
    parser.feed(Frame(telemetry=_telemetry(lap_number=1, elapsed_time=0.0), scoring=_scoring(lap_dist=100.0), session=_session()))
    # Feed lap 1 frames
    parser.feed(Frame(telemetry=_telemetry(lap_number=1, elapsed_time=45.0), scoring=_scoring(lap_dist=2000.0), session=_session()))
    # Normal increment 1→2: boundary seen, but must wait for lap_dist reset.
    pending = parser.feed(
        Frame(telemetry=_telemetry(lap_number=2, elapsed_time=90.0), scoring=_scoring(lap_dist=4000.0, last_lap_time=90.0), session=_session())
    )
    assert pending is None
    result = parser.feed(
        Frame(telemetry=_telemetry(lap_number=2, elapsed_time=90.05), scoring=_scoring(lap_dist=5.0, last_lap_time=90.0), session=_session())
    )
    assert result is not None
    assert result.lap_number == 1
    assert result.lap_time == 90.0


# P0-7a: real-data regression test (Michelin Raceway Road Atlanta, 2026-09-26).
# V0.6.6 update: an earlier fix assumed the lag between telemetry.lap_number
# incrementing and scoring.lap_dist actually resetting was always exactly 1
# frame. Further real testing (same day) showed it varies — 2, 2, 3, 3, and
# 4 frames across five real transitions in one session. This test now
# reflects the wait-for-actual-reset mechanism that replaced the fixed
# 1-frame shift, using real observed values (a 2-frame lag, matching the
# first transition measured that day).
def test_feed_attributes_boundary_lap_dist_to_completing_lap_not_new_one():
    parser = LapParser()
    parser.feed(Frame(
        telemetry=_telemetry(lap_number=27, elapsed_time=2170.15),
        scoring=_scoring(lap_dist=4075.04), session=_session(),
    ))
    # Boundary frame: lap_number already 28, lap_dist STILL the old lap's
    # value (real observed data — unchanged from the prior frame).
    pending = parser.feed(Frame(
        telemetry=_telemetry(lap_number=28, elapsed_time=2170.30),
        scoring=_scoring(lap_dist=4075.04, last_lap_time=-1.0), session=_session(),
    ))
    assert pending is None  # must NOT finalize yet — lap_dist hasn't reset
    # One more frame still waiting (real data: this transition had a
    # 2-frame lag) — lap_dist is STILL the old value here too.
    still_pending = parser.feed(Frame(
        telemetry=_telemetry(lap_number=28, elapsed_time=2170.35),
        scoring=_scoring(lap_dist=4075.04, last_lap_time=-1.0), session=_session(),
    ))
    assert still_pending is None
    # The actual reset frame (real observed value: 5.99).
    completed = parser.feed(Frame(
        telemetry=_telemetry(lap_number=28, elapsed_time=2170.40),
        scoring=_scoring(lap_dist=5.99, last_lap_time=88.5), session=_session(),
    ))
    assert completed is not None
    assert completed.lap_number == 27
    # The last sample of the completed lap must be the boundary frame's
    # (stale) distance — NOT the reset frame's near-zero value.
    assert completed.samples[-1].lap_dist == 4075.04

    # The reset frame itself is now the new lap's first buffered sample.
    assert len(parser._buffer) == 1
    assert parser._buffer[0].lap_dist == 5.99


# P0-7a additional real-data coverage: the largest observed lag that day
# (4 frames, transition 31->32) — proves the wait mechanism isn't
# accidentally limited to small lags.
def test_feed_waits_multiple_frames_for_larger_real_world_lag():
    parser = LapParser()
    parser.feed(Frame(telemetry=_telemetry(lap_number=31, elapsed_time=2466.07), scoring=_scoring(lap_dist=4065.92), session=_session()))
    # Boundary, then three more stale frames (real observed sequence for
    # this transition: lap_dist stays ~4080 for 4 frames after the flip).
    for elapsed in (2466.22, 2466.27, 2466.32, 2466.37):
        result = parser.feed(Frame(
            telemetry=_telemetry(lap_number=32, elapsed_time=elapsed),
            scoring=_scoring(lap_dist=4080.17, last_lap_time=72.14), session=_session(),
        ))
        assert result is None, f"finalized too early at elapsed_time={elapsed}"
    # The actual reset frame.
    completed = parser.feed(Frame(
        telemetry=_telemetry(lap_number=32, elapsed_time=2466.42),
        scoring=_scoring(lap_dist=11.06, last_lap_time=80.997), session=_session(),
    ))
    assert completed is not None
    assert completed.lap_number == 31
    assert completed.samples[-1].lap_dist == 4080.17


def test_feed_finalizes_via_safety_cap_if_lap_dist_never_resets():
    """If lap_dist never drops (an anomaly beyond anything observed so
    far), the parser must still eventually finalize the lap rather than
    buffering forever — via _MAX_RESET_WAIT_FRAMES."""
    parser = LapParser()
    parser.feed(Frame(telemetry=_telemetry(lap_number=1, elapsed_time=0.0), scoring=_scoring(lap_dist=100.0), session=_session()))
    result = None
    elapsed = 1.0
    for _ in range(LapParser._MAX_RESET_WAIT_FRAMES + 2):
        result = parser.feed(Frame(
            telemetry=_telemetry(lap_number=2, elapsed_time=elapsed),
            scoring=_scoring(lap_dist=4000.0, last_lap_time=90.0), session=_session(),
        ))
        if result is not None:
            break
        elapsed += 0.05
    assert result is not None  # must have finalized via the safety cap, not hung forever
    assert result.lap_number == 1
