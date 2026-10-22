"""
The labelled track-limit reference set (V0.9.9): every case with the decision Garage16 must make
and WHY — the reason comes from the geometry, LMU's documented behaviour or the physics of the
manipulation, never from the implementation. tests/test_v099_trackcheck_reference.py checks every
case; scripts/trackcheck_eval.py adds a larger generated grid and computes the error rates.

`expected` is what the CURRENT rules decide where that is a known limit (marked "LIMIT:"): such a
case stays in the set, counts as a false negative in the measurement, and is listed in
docs/QUALITY.md. A change that fixes it makes the test fail on purpose — then the expectation and
the documented rates are updated together.
"""

from __future__ import annotations

from typing import List

from tests.helpers.trackcheck import CARS, GT3, LMP, TRACKS, Excursion, Scenario

# sections with something beyond the kerb, per track: (section, kerb width)
GRASS_SPOTS = {"fast": (3, 1.2), "tight": (1, 0.8), "mixed": (5, 1.0)}


def cases() -> List[Scenario]:
    out: List[Scenario] = []
    add = out.append

    # ---------------------------------------------------------------- 2.1 positive (clean laps)
    for track in TRACKS:
        for car in CARS:
            add(Scenario(f"clean line / {track} / {car.name}", track=track, car=car, expected="valid",
                         why="every wheel on the asphalt, every signal present, nothing reported"))
            add(Scenario(f"kerbs at every apex / {track} / {car.name}", track=track, car=car, line="kerbs",
                         expected="valid",
                         why="inner wheels on the kerb, outer wheels on the track: part of the car stays on the track (FIA); a kerb (surface 5) is not 'off'"))
    for track in TRACKS:
        add(Scenario(f"measurement noise / {track}", track=track, noise=1.0, seed=3, expected="valid",
                     why="lateral noise 0.15 m sd, timestamp jitter +-1.5 ms, speed noise: inside every tolerance"))
    add(Scenario("fast sweeper, high lateral acceleration", track="fast", car=LMP, expected="valid",
                 why="3.2 G through the 200 m sweeper is within an LMP car's grip; no excursion"))
    add(Scenario("hairpin and quick direction changes", track="tight", car=GT3, line="kerbs", expected="valid",
                 why="15 m hairpin and S-bends on kerbs, all within the track"))
    add(Scenario("0.3 s of samples missing in a corner", track="fast", drop=(1400.0, 0.3), expected="valid",
                 why="a gap below the 1 s coverage limit; LMU's own flags still cover the stretch"))
    add(Scenario("30 Hz frames, 60 Hz polls", frame_hz=30.0, expected="valid", why="above the 10 Hz floor"))
    add(Scenario("50 Hz frames, no repeated polls", poll_hz=None, expected="valid", why="a different but complete sampling"))
    add(Scenario("lap distance every 5th frame", scoring_every=5, expected="valid", why="LMU's scoring at 10 Hz instead of 5"))
    add(Scenario("two wheels on the grass, two on the track", excursions=(Excursion(9, -0.3),), lmu="silent",
                 expected="valid", why="the inner wheels stay on the asphalt: the car never left the track"))
    add(Scenario("inner wheel 5 cm inside the line", excursions=(Excursion(9, -0.05),), lmu="silent", expected="valid",
                 why="boundary: the inner wheels are still on the track"))
    add(Scenario("inner wheel exactly on the line", excursions=(Excursion(9, 0.0),), lmu="silent", expected="valid",
                 why="boundary: touching the white line is on the track"))
    add(Scenario("every row twice", manipulations=("duplicate_rows",), expected="valid",
                 why="a repeated poll of the same frame carries no new information and changes nothing"))
    add(Scenario("client 0.9.0 (no 0.9.3 signals)", client_version="0.9.0", manipulations=("drop_093",), expected="valid",
                 why="every signal a 0.9.0 client records is present; the 0.9.3 ones cannot be expected"))

    # ---------------------------------------------------------------- 2.2 negative (violations)
    for car in CARS:
        add(Scenario(f"four wheels on the gravel, LMU invalidates / {car.name}", car=car,
                     excursions=(Excursion(3, 1.5),), lmu="strict", expected="invalid",
                     why="LMU's own verdict (mLapInvalidated) is decisive"))
    add(Scenario("four wheels on the gravel, LMU only adds steps", excursions=(Excursion(3, 1.5),), lmu="lenient",
                 expected="review", why="LMU judged no time gained; the geometry shows a clear excursion -> a human decides"))
    for track, (section, kerb) in GRASS_SPOTS.items():
        add(Scenario(f"four wheels off, LMU silent / {track}", track=track,
                     excursions=(Excursion(section, kerb + 0.3),), lmu="silent", expected="review",
                     why="all four wheels beyond the kerb for > 0.5 s: surface types and track edge show it"))
    add(Scenario("chicane cut across the inside", track="fast", excursions=(Excursion(5, 1.5, 120.0, 0.5, -1),), lmu="silent",
                 expected="review", why="all four wheels on the grass inside the chicane"))
    add(Scenario("excursion in the last metres before the line", track="fast",
                 excursions=(Excursion(10, 1.6, 120.0, 0.85),), lmu="strict", expected="review",
                 why="LMU scores it only after the line (outside this file); the geometry inside the lap shows it"))
    add(Scenario("all four on a wide kerb beyond the line, LMU invalidates", track="mixed",
                 excursions=(Excursion(1, 0.2),), lmu="strict", expected="invalid", why="LMU's own verdict"))
    add(Scenario("all four on a wide kerb beyond the line, LMU silent", track="mixed",
                 excursions=(Excursion(1, 0.2),), lmu="silent", expected="valid",
                 why="LIMIT: no wheel on grass/gravel and the car's centre < 2 m beyond LMU's approximate edge — "
                     "without LMU's verdict this is not distinguishable from legal kerb use"))
    add(Scenario("inner wheel 5 cm beyond the line, LMU silent", excursions=(Excursion(9, 0.05),), lmu="silent",
                 expected="valid", why="LIMIT: boundary case just outside — same reason as the wide kerb"))
    add(Scenario("inner wheel 5 cm beyond the line, LMU invalidates", excursions=(Excursion(9, 0.05),), lmu="strict",
                 expected="invalid", why="LMU's own verdict"))

    # ------------------------------------------------- manipulated or insufficient recordings
    add(Scenario("LMU's flags removed from the file", manipulations=("strip_flags",), expected="review",
                 why="a 0.9.4 client always records them: missing = cannot be checked"))
    add(Scenario("every track-limits signal removed", manipulations=("strip_all",), expected="review",
                 why="nothing left to check the lap with"))
    add(Scenario("LMU's flag with values LMU never writes", manipulations=("garbage_flags",), expected="review",
                 why="LMU's verdict unreadable"))
    add(Scenario("flag and surfaces forged to 'clean', position kept", excursions=(Excursion(3, 1.5),), lmu="strict",
                 manipulations=("forge_flags",), expected="review",
                 why="the position still shows the car 2.5 m beyond the edge"))
    add(Scenario("consistent forgery of flags, surfaces and position", excursions=(Excursion(3, 1.5),), lmu="strict",
                 manipulations=("forge_all",), expected="valid",
                 why="LIMIT: a file forged consistently in every signal cannot be told from a real clean lap"))
    add(Scenario("lap distance jumps 300 m ahead", manipulations=("teleport",), expected="review",
                 why="the distance advances far beyond what the speed allows"))
    add(Scenario("timestamps squeezed by 7 %", manipulations=("time_compress",), expected="review",
                 why="speed trace and lap distance disagree by 7 % over the lap"))
    add(Scenario("three samples with +40 m/s", manipulations=("speed_spike",), expected="review",
                 why="a speed change of tens of G in 0.1 s is not a car"))
    add(Scenario("two blocks of samples swapped", manipulations=("shuffle",), expected="invalid",
                 why="time goes backwards: not one continuous recording"))
    add(Scenario("file cut off before the line", manipulations=("truncate",), expected="invalid",
                 why="the recording is 20 % shorter than the lap time"))
    add(Scenario("a stretch pasted in again", manipulations=("replay",), expected="invalid",
                 why="the lap distance jumps back"))
    add(Scenario("excursion pasted over with a repeated frame", excursions=(Excursion(3, 1.5),), lmu="silent",
                 manipulations=("freeze_excursion",), expected="review",
                 why="1.5 s of identical frames at speed while the time advances"))
    add(Scenario("8 frames per second", frame_hz=8.0, poll_hz=None, scoring_every=2, expected="review",
                 why="too coarse to see a short excursion"))
    add(Scenario("1.5 s of samples missing", track="fast", drop=(1400.0, 1.5), expected="review",
                 why="an excursion in that stretch would be invisible"))
    return out


def by_name():
    return {c.name: c for c in cases()}
