"""
parser.py — Turns a stream of raw Frame objects (from reader.py) into
structured Lap objects: metadata (track, car, lap time, sector times,
validity) plus the buffered telemetry samples for that lap.

Usage:
    reader = LMUSharedMemoryReader()
    parser = LapParser()
    for frame in reader.stream():
        completed_lap = parser.feed(frame)
        if completed_lap is not None:
            recorder.save(completed_lap)
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from math import sqrt
from typing import List, Optional
import logging

from .reader import Frame

logger = logging.getLogger("lmu_garage.parser")

# V0.6.0: gate verbose per-frame/per-lap diagnostic logging behind an env
# var rather than always-on debug logging, which would flood the log at
# 60Hz. Set LMU_GARAGE_DEBUG=1 when driving a verification session per
# docs/LMU_VERIFICATION_PROTOCOL.md — this is what that protocol's "what
# to look for in the log" sections actually reference.
_DEBUG = os.environ.get("LMU_GARAGE_DEBUG", "").strip() in ("1", "true", "yes")


@dataclass
class TelemetrySample:
    """One recorded instant of telemetry, in-lap-relative terms."""

    t: float                # seconds since lap start
    lap_dist: float         # meters since start/finish line
    speed: float            # m/s
    throttle: float
    brake: float
    clutch: float
    steering: float
    gear: int
    rpm: float
    fuel: float
    pos_x: float
    pos_y: float
    pos_z: float
    tire_pressures: tuple   # (FL, FR, RL, RR)
    tire_temps_outer: tuple
    brake_temps: tuple


@dataclass
class Lap:
    track_name: str
    car_name: str
    session_type: int
    lap_number: int
    lap_time: float
    sector_times: List[float]
    is_valid: bool
    started_at: float
    ambient_temp: float
    track_temp: float
    # From VehicleScoringInfoV01.vehicle_class / .veh_filename — distinct
    # from car_name (which includes team/livery/number). Powers the
    # class-based main leaderboard and model-based sub-leaderboard — see
    # server/routers/leaderboard.py. Empty string if LMU reports nothing
    # for this car (some mods may not populate these fields).
    car_class: str = ""
    car_model: str = ""
    samples: List[TelemetrySample] = field(default_factory=list)


class LapParser:
    """
    Stateful parser: call feed(frame) once per telemetry tick. Returns a
    completed Lap when a lap boundary is crossed, else None.

    Lap boundary detection: LMU reports `lap_number` per vehicle and
    resets lap distance at the start/finish line. We treat a lap_number
    increment as "lap completed", and pull the finished lap's time and
    sector splits from the scoring struct's `last_lap_time` /
    `last_sector1` / `last_sector2` fields (these are populated by the
    game the instant the lap is closed out, which is more reliable than
    trying to derive them from raw telemetry timestamps ourselves).
    """

    def __init__(self):
        self._current_lap_number: Optional[int] = None
        self._lap_start_time: Optional[float] = None
        self._buffer: List[TelemetrySample] = []
        self._pit_this_lap = False
        # Staleness detection (P0-7g): if elapsed_time hasn't moved since
        # the last frame, the game is likely paused/menu/replay — the
        # shared memory buffer isn't being updated, but reader.py still
        # successfully reads *something* (the last values the game wrote).
        # Purely diagnostic for now (see docs/LMU_VERIFICATION_PROTOCOL.md
        # item (g)) — logged under LMU_GARAGE_DEBUG, not acted on, since
        # acting on it (e.g. dropping frames) needs confirmation from a
        # real session first.
        self._last_elapsed_time: Optional[float] = None
        self._stale_frame_count = 0
        # P0-7a (V0.6.6): once telemetry.lap_number increments, we don't
        # know YET whether this frame or a later one is the true start of
        # the new lap — scoring.lap_dist lags behind by a variable number
        # of frames (confirmed via real data, 2026-09-26: 2, 2, 3, 3, and
        # 4 frames across five transitions in one session — never a fixed
        # count). These three fields track "we've seen the lap_number
        # flip, now waiting for lap_dist to actually reset" — see feed().
        self._awaiting_lap_dist_reset = False
        self._pending_lap_number: Optional[int] = None
        self._reset_wait_frames = 0

    def reset(self) -> None:
        self._current_lap_number = None
        self._lap_start_time = None
        self._buffer = []
        self._pit_this_lap = False
        self._last_elapsed_time = None
        self._stale_frame_count = 0
        self._awaiting_lap_dist_reset = False
        self._pending_lap_number = None
        self._reset_wait_frames = 0

    # P0-7a (V0.6.6) tuning constants — see _awaiting_lap_dist_reset's
    # docstring in __init__ for the real-data background.
    #
    # A genuine lap_dist reset is a large, sudden backward drop (from
    # near-track-length down to near-zero) — nothing about normal forward
    # driving produces anything close to this per-frame, even at high
    # speed and low poll rate (at 70 m/s and 20Hz, one frame is ~3.5m of
    # travel; at 60Hz, ~1.2m). 500m comfortably separates "genuine reset"
    # from any plausible jitter.
    _RESET_DROP_THRESHOLD_METERS = 500.0
    # Real data (Michelin Raceway Road Atlanta, 2026-09-26) showed lags of
    # 2-4 frames at the diagnostic tool's 20Hz poll rate. The real client
    # polls at 60Hz (3x), and if the underlying delay is closer to a fixed
    # wall-clock duration than a fixed frame count, the same delay could
    # show up as ~3x more frames there. 30 frames (0.5s at 60Hz) is a
    # generous margin over that, while still short enough that hitting the
    # cap without a reset ever appearing is a genuine anomaly worth a
    # warning, not routine.
    _MAX_RESET_WAIT_FRAMES = 30

    def feed(self, frame: Frame) -> Optional[Lap]:
        tele = frame.telemetry
        scoring = frame.scoring
        session = frame.session

        completed: Optional[Lap] = None

        if _DEBUG:
            if self._last_elapsed_time is not None and tele.elapsed_time == self._last_elapsed_time:
                self._stale_frame_count += 1
                if self._stale_frame_count in (1, 60, 300):  # log once, then every ~1s/~5s at 60Hz
                    logger.debug(
                        "STALE: elapsed_time unchanged (%.3f) for %d consecutive frame(s) — "
                        "game paused/menu/replay? (P0-7g)",
                        tele.elapsed_time, self._stale_frame_count,
                    )
            else:
                self._stale_frame_count = 0
            self._last_elapsed_time = tele.elapsed_time

        if self._current_lap_number is None:
            # First frame we've seen — just start tracking. _lap_start_time
            # stays None; the common tail below sets it lazily from this
            # very frame (same mechanism a post-boundary lap start uses).
            self._current_lap_number = tele.lap_number
            if _DEBUG:
                logger.debug(
                    "INIT: lap_number=%d elapsed_time=%.3f count_lap_flag=%d in_pits=%s",
                    tele.lap_number, tele.elapsed_time, scoring.count_lap_flag, scoring.in_pits,
                )

        elif self._awaiting_lap_dist_reset:
            # We've already seen telemetry.lap_number flip to
            # self._pending_lap_number; scoring.lap_dist hasn't actually
            # reset yet (confirmed: this can take 2-4+ frames, not a fixed
            # count — see the class-level BUGFIX comment). Every frame
            # while we wait still belongs, distance-wise, to the
            # COMPLETING lap.
            if tele.lap_number not in (self._current_lap_number, self._pending_lap_number):
                # A further jump arrived before lap_dist ever reset — an
                # already-unusual situation stacked on another one. Bail
                # out to the simpler, safer discard path rather than
                # guessing which of two jumps the buffered samples belong to.
                logger.info(
                    "Lap number jumped again (%d -> %d) while still waiting for lap_dist "
                    "to reset after %d -> %d — discarding buffer (%d samples) (P0-7a)",
                    self._current_lap_number, tele.lap_number, self._current_lap_number,
                    self._pending_lap_number, len(self._buffer),
                )
                self._current_lap_number = tele.lap_number
                self._awaiting_lap_dist_reset = False
                self._pending_lap_number = None
                self._lap_start_time = None
                self._buffer = []
                self._pit_this_lap = False
                # Falls through to the common tail.
            else:
                self._reset_wait_frames += 1
                prev_lap_dist = self._buffer[-1].lap_dist if self._buffer else None
                reset_detected = (
                    prev_lap_dist is not None
                    and scoring.lap_dist < prev_lap_dist - self._RESET_DROP_THRESHOLD_METERS
                )
                if not reset_detected and self._reset_wait_frames <= self._MAX_RESET_WAIT_FRAMES:
                    # Still the completing lap's tail — keep it there.
                    self._buffer.append(self._build_sample(frame))
                    if scoring.in_pits:
                        self._pit_this_lap = True
                    return None
                if not reset_detected:
                    logger.warning(
                        "lap_dist never dropped within %d frames after lap_number %d -> %d "
                        "— finalizing the lap anyway (P0-7a)",
                        self._MAX_RESET_WAIT_FRAMES, self._current_lap_number, self._pending_lap_number,
                    )
                completed = Lap(
                    track_name=session.track_name.decode(errors="replace").strip("\x00"),
                    car_name=tele.vehicle_name.decode(errors="replace").strip("\x00"),
                    session_type=session.session,
                    lap_number=self._current_lap_number,
                    lap_time=scoring.last_lap_time,
                    sector_times=self._derive_sector_splits(scoring),
                    is_valid=self._derive_is_valid(scoring, pit_this_lap=self._pit_this_lap),
                    started_at=self._lap_start_time if self._lap_start_time is not None else 0.0,
                    ambient_temp=session.ambient_temp,
                    track_temp=session.track_temp,
                    car_class=scoring.vehicle_class.decode(errors="replace").strip("\x00"),
                    car_model=scoring.veh_filename.decode(errors="replace").strip("\x00"),
                    samples=self._buffer,
                )
                if _DEBUG:
                    actual_duration = tele.elapsed_time - (self._lap_start_time or 0.0)
                    logger.debug(
                        "EMIT: lap %d lap_time=%.3f (scoring) vs actual buffer duration=%.3f "
                        "(telemetry) — diff=%.3f | waited %d frame(s) for lap_dist to reset "
                        "(P0-7a: should be ~0 if scoring/telemetry are in sync)",
                        completed.lap_number, completed.lap_time, actual_duration,
                        completed.lap_time - actual_duration, self._reset_wait_frames,
                    )
                self._current_lap_number = self._pending_lap_number
                self._awaiting_lap_dist_reset = False
                self._pending_lap_number = None
                # None: the common tail (below) sets it lazily from this
                # very frame — the one where lap_dist actually reset,
                # which is the true first sample of the new lap.
                self._lap_start_time = None
                self._buffer = []
                self._pit_this_lap = False
                if scoring.in_pits:
                    self._pit_this_lap = True
                # Falls through to the common tail: this frame (the
                # actual reset frame) becomes the new lap's first sample.

        elif tele.lap_number != self._current_lap_number:
            if _DEBUG:
                logger.debug(
                    "BOUNDARY: telemetry.lap_number %d -> %d | scoring.last_lap_time=%.3f "
                    "last_sector1=%.3f last_sector2=%.3f count_lap_flag=%d in_pits=%s "
                    "lap_dist=%.1f (waiting for lap_dist reset before finalizing — P0-7a) "
                    "| buffer had %d samples before this frame (P0-7a/c)",
                    self._current_lap_number, tele.lap_number, scoring.last_lap_time,
                    scoring.last_sector1, scoring.last_sector2, scoring.count_lap_flag,
                    scoring.in_pits, scoring.lap_dist, len(self._buffer),
                )
            # Lap number changed. Only enter the reset-wait state for a
            # normal n → n+1 increment. Any other jump (session restart
            # 5→1, replay, crash-restart) discards the buffer immediately
            # — those frames are from a different context and don't
            # represent a valid lap regardless of lap_dist.
            if tele.lap_number == self._current_lap_number + 1:
                # BUGFIX (V0.6.6, confirmed with real LMU data,
                # 2026-09-26): don't finalize immediately. scoring.lap_dist
                # lags telemetry.lap_number by a VARIABLE number of frames
                # (observed: 2, 2, 3, 3, and 4 across five real transitions
                # in one session — an earlier fix assuming a fixed 1-frame
                # lag was confirmed insufficient by further real testing).
                # Enter a wait state instead: keep attributing frames to
                # the completing lap until lap_dist actually drops.
                self._awaiting_lap_dist_reset = True
                self._pending_lap_number = tele.lap_number
                self._reset_wait_frames = 0
                self._buffer.append(self._build_sample(frame))
                if scoring.in_pits:
                    self._pit_this_lap = True
                return None
            else:
                logger.info(
                    "Lap number jumped %d → %d (not +1) — discarding buffer (%d samples)",
                    self._current_lap_number, tele.lap_number, len(self._buffer),
                )
                self._current_lap_number = tele.lap_number
                self._lap_start_time = None
                self._buffer = []
                self._pit_this_lap = False
                # Falls through to the common tail: this frame becomes the
                # first sample of whatever context we're in now (same as
                # the discard branch has always done).

        if scoring.in_pits:
            self._pit_this_lap = True

        # Falsy-zero fix: use `is not None` instead of `or` — elapsed_time
        # can legitimately be 0.0 at session start. Lazy init: covers both
        # the very first frame ever (INIT branch left this None) and the
        # first frame of a new lap after a boundary (reset to None above).
        if self._lap_start_time is None:
            self._lap_start_time = tele.elapsed_time

        self._buffer.append(self._build_sample(frame))
        return completed

    def _build_sample(self, frame: Frame) -> TelemetrySample:
        tele = frame.telemetry
        scoring = frame.scoring
        # Falsy-zero-safe fallback: elapsed_time can legitimately be 0.0.
        # _lap_start_time is normally already set by the time this is
        # called (lazily, in feed()'s common tail) — this fallback only
        # matters for the degenerate case of two lap-boundary frames back
        # to back with zero ordinary frames between them (a "lap" with no
        # samples), where that lazy init never gets a turn to run.
        start = self._lap_start_time if self._lap_start_time is not None else tele.elapsed_time
        rel_t = tele.elapsed_time - start
        speed = sqrt(tele.local_vel.x ** 2 + tele.local_vel.y ** 2 + tele.local_vel.z ** 2)
        wheels = tele.wheel
        return TelemetrySample(
            t=rel_t,
            lap_dist=scoring.lap_dist,
            speed=speed,
            throttle=tele.unfiltered_throttle,
            brake=tele.unfiltered_brake,
            clutch=tele.unfiltered_clutch,
            steering=tele.unfiltered_steering,
            gear=tele.gear,
            rpm=tele.engine_rpm,
            fuel=tele.fuel,
            pos_x=tele.pos.x,
            pos_y=tele.pos.y,
            pos_z=tele.pos.z,
            tire_pressures=tuple(w.pressure for w in wheels),
            tire_temps_outer=tuple(sum(w.temperature) / 3.0 for w in wheels),
            brake_temps=tuple(w.brake_temp for w in wheels),
        )

    @staticmethod
    def _derive_sector_splits(scoring) -> List[float]:
        """S1/S2/S3 from the game's cumulative-from-lap-start sector
        timestamps:
            S1 = last_sector1
            S2 = last_sector2 - last_sector1
            S3 = lap_time - last_sector2

        An earlier version of this function computed
        `s3 = lap_time - s1 - s2` using the *raw cumulative* s2 — which
        double-subtracts S1 (once directly, once because s2 already
        includes it), understating S3 by exactly S1 on every lap. This is
        the fix.
        """
        s1 = scoring.last_sector1
        s2_cumulative = scoring.last_sector2
        total = scoring.last_lap_time
        if s1 <= 0 or s2_cumulative <= 0 or total <= 0 or s2_cumulative <= s1:
            # Missing/invalid sector data (e.g. first lap out of the pits,
            # or a session/track without three timed sectors) — report
            # what we can and leave the rest at 0 rather than emitting a
            # negative or nonsensical split.
            return [max(s1, 0.0), 0.0, 0.0]
        s2 = s2_cumulative - s1
        s3 = total - s2_cumulative
        return [s1, s2, max(s3, 0.0)]

    @staticmethod
    def _derive_is_valid(scoring, pit_this_lap: bool) -> bool:
        """Validity from real, game-reported signals — not a fabricated
        `lap_valid` field (VehicleScoringInfoV01 has no such member; see
        client/lmu/structs.py). `mCountLapFlag` is the game's own
        "should this lap count" signal: 0 means don't count it (e.g. an
        out-lap or a lap the game itself invalidated). We additionally
        reject laps with no positive lap time, non-monotonic sector
        cumulative times, or a pit-lane visit during the lap — a lap
        through the pits is a real, completed lap but not a fair
        comparison for records/leaderboards.

        This mirrors `mCountLapFlag`'s meaning as documented for
        VehicleScoringInfoV01; if LMU's actual semantics differ (e.g. a
        value other than 0 also meaning "don't count"), tighten this once
        confirmed against real session data rather than the reader
        guessing further.
        """
        if scoring.last_lap_time <= 0:
            return False
        if pit_this_lap or scoring.in_pits:
            return False
        if scoring.count_lap_flag == 0:
            return False
        if scoring.last_sector1 <= 0 or scoring.last_sector2 <= scoring.last_sector1:
            return False
        return True
