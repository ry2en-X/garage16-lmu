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

from array import array

import os
from dataclasses import dataclass, field, replace
from math import sqrt
from typing import List, Optional, Sequence
import logging

from . import lap_verdict
from .channels import EXTENDED_COLUMNS, extract_extended
from .reader import Frame, _text, model_text

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
    # V0.9.0: every extended channel (suspension, tire forces/loads/slip
    # velocities, accelerations, driver aids, ...) as one flat tuple in
    # channels.EXTENDED_COLUMNS order. A single tuple instead of ~160 fields
    # keeps per-frame cost and memory low and the column order defined in
    # exactly one place. None for samples built without it (older tests,
    # tools) — the recorder then simply writes the core columns only.
    ext: Optional[Sequence[float]] = None  # float32 array (V0.9.1) in EXTENDED_COLUMNS order


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
    # V0.9.3 — car identity: LMU's own "brand & model" name (TelemInfoV01.mVehicleModel,
    # LMU 1.3+). car_model above stays the raw veh_filename (which carries car number and
    # season, e.g. "397_25_499P"); the server derives the model from both.
    vehicle_model: str = ""
    # V0.9.3 — how many samples at the END of `samples` were recorded after LMU had
    # already switched to the next lap number (the variable lap_dist lag, see P0-7a).
    # The game's per-lap flags (lap_invalidated, count_lap_flag, ...) already belong to the
    # next lap there, so the track-limits check leaves exactly these samples out.
    tail_samples: int = 0
    # V0.9.3 — session context (ScoringInfoV01, recorded for comparability only)
    track_grip_level: Optional[int] = None
    fixed_setup: Optional[bool] = None
    track_limits_steps_per_point: Optional[int] = None
    track_limits_steps_per_penalty: Optional[int] = None
    # V0.9.3 — what the client itself read from the game for this lap (summary of the
    # per-sample columns; the server recomputes it from the file and decides)
    game_lap_state: dict = field(default_factory=dict)
    # V0.9.3 — every car seen in the session: [{"veh_file", "model", "class"}]. Lets the
    # server learn which veh_filename belongs to which model for the whole grid at once.
    grid_vehicles: List[dict] = field(default_factory=list)
    # V0.9.3.1 — why the client judged the lap as it did, and what LMU reported in the
    # first seconds after the finish line: {"reasons": [...], "late": {...}}
    # (client/telemetry/lap_verdict.py). Sent to servers from 0.9.3.1 on.
    lap_report: dict = field(default_factory=dict)
    # V0.9.4 — the car and the session at the end of the lap, for the Stint Analyzer: pit
    # stops so far, tyre compound, damage, tank size, position, time left (see lap_context()).
    lap_context: dict = field(default_factory=dict)


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

    def __init__(self, late_window_seconds: Optional[float] = None):
        # V0.9.3.1: how long a finished lap is held after the line (lap_verdict.LateWindow);
        # 0 returns it at once (the boundary-logic tests use that)
        self._late_window_seconds = (lap_verdict.LATE_WINDOW_SECONDS if late_window_seconds is None
                                     else max(float(late_window_seconds), 0.0))
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
        # V0.9.3: index in _buffer of the first sample recorded after the lap_number flip
        self._tail_start: Optional[int] = None
        # V0.9.3: cars seen in this session, keyed by veh_filename (see observe_vehicles)
        self._grid: dict = {}
        # V0.9.3.1: a finished lap is held for the late window after the finish line
        # (lap_verdict.LateWindow) before it is returned — see feed() and flush_pending()
        self._late: Optional[lap_verdict.LateWindow] = None
        self._pending: Optional[Lap] = None
        self._pending_fix: Optional[dict] = None   # V0.9.4: lap time to confirm in the late window
        # V0.9.4: samples of the NEXT lap found at the end of the buffer when the lap number
        # changed (LMU reset the distance first) — they start the next lap
        self._carry: Optional[list] = None
        self._force_reset = False
        self._previous_lmu_time: Optional[float] = None

    MAX_GRID_VEHICLES = 128

    def observe_vehicles(self, vehicles) -> None:
        """V0.9.3: remembers the cars of the session (called every few seconds by the
        telemetry loop with reader.read_vehicle_catalog()). Bounded; blank names ignored."""
        for entry in vehicles or ():
            veh_file = (entry.get("veh_file") or "").strip()
            if not veh_file or veh_file in self._grid:
                continue
            if len(self._grid) >= self.MAX_GRID_VEHICLES:
                break
            self._grid[veh_file] = {
                "veh_file": veh_file[:64],
                "model": (entry.get("model") or "").strip()[:64],
                "class": (entry.get("class") or "").strip()[:64],
            }

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
        self._tail_start = None
        self._grid = {}  # a new session may have a different grid
        # A held lap is NOT silently dropped here: callers take it with flush_pending()
        # first (client/main.py does). Whatever is still held now is discarded.
        self._late = None
        self._pending = None
        self._pending_fix = None
        self._previous_lmu_time = None
        self._carry = None
        self._force_reset = False

    # ------------------------------------------------------------------ late window

    def flush_pending(self) -> Optional[Lap]:
        """The finished lap still held for its late window, returned now (the window is
        marked incomplete) — e.g. when the session ends. None if nothing is held."""
        if self._pending is None:
            self._late = None
            return None
        return self._release(complete=False)

    def idle(self, now: Optional[float] = None) -> Optional[Lap]:
        """Called when no frame arrives (menu, garage). Releases a held lap once the late
        window's wall-clock limit is over."""
        if self._pending is not None and self._late is not None and self._late.timed_out(now):
            return self._release(complete=False)
        return None

    def _release(self, complete: bool) -> Lap:
        lap, late = self._pending, self._late
        self._pending, self._late = None, None
        if self._pending_fix is not None and self._pending_fix.get("time", True):
            # LMU never reported a matching time: the lap goes out with what LMU said at the
            # line, and the report says so (the server's plausibility check then decides)
            report = dict(lap.lap_report or {})
            measured = self._pending_fix["measured"]
            report["lap_time_unconfirmed"] = {"lmu": round(self._pending_fix["stale"], 4),
                                              "measured": round(measured, 3) if measured is not None else -1.0}
            lap.lap_report = report
        self._pending_fix = None
        report = dict(lap.lap_report or {})
        if late is not None:
            report["late"] = late.report(complete)
        lap.lap_report = report
        return lap

    def _start_late_window(self, frame: Frame) -> Optional[Lap]:
        """At the finish line (telemetry lap number flipped). Returns a lap that was still
        held from before (a lap shorter than the late window) — released incomplete."""
        released = self._release(complete=False) if self._pending is not None else None
        previous = self._buffer[-1].ext if self._buffer else None

        def ext(name):
            if previous is None:
                return None
            index = self._EXT_INDEX[name]
            return previous[index] if len(previous) > index else None

        self._late = lap_verdict.LateWindow(
            frame.telemetry.elapsed_time, steps_at_line=ext("track_limits_steps"),
            penalties_at_line=ext("num_penalties"), window=self._late_window_seconds,
        )
        return released

    # V0.9.4: LMU's lap time at the moment the lap is finalised is sometimes still the PREVIOUS
    # lap's (or not set yet) — the scoring struct lags the telemetry. Such a lap then failed the
    # server's plausibility check ("telemetry duration does not match claimed lap_time") although
    # LMU counted it. The measured duration (line time − lap start, from the telemetry clock) is
    # only used to RECOGNISE a lap time that does not belong to this lap; the time that is sent
    # is always LMU's own: the first value LMU reports during the late window that matches.
    LAP_TIME_MATCH_FRACTION = 0.02
    LAP_TIME_MATCH_MIN_S = 0.5

    @classmethod
    def _time_matches(cls, lap_time: float, measured: Optional[float]) -> bool:
        if measured is None or measured <= 0:
            return True   # nothing to compare with: trust LMU
        return lap_time > 0 and abs(lap_time - measured) <= max(cls.LAP_TIME_MATCH_MIN_S, cls.LAP_TIME_MATCH_FRACTION * measured)

    def _maybe_correct_lap_time(self, frame: Frame) -> None:
        fix = self._pending_fix
        if fix is None or self._pending is None:
            return
        scoring = frame.scoring
        value = float(scoring.last_lap_time)
        lap = self._pending
        after = round(float(frame.telemetry.elapsed_time) - fix["line_et"], 2)
        if fix.get("time", True):
            if value == fix["stale"] or not self._time_matches(value, fix["measured"]):
                return
        elif value != fix["stale"]:
            return                     # LMU moved on to another time: leave the lap as it is
        s1, s2 = float(scoring.last_sector1), float(scoring.last_sector2)
        sectors_ok = 0 < s1 < s2 < value
        if not fix.get("time", True) and not sectors_ok:
            return                     # still waiting for the sector times
        lap.sector_times = self._derive_sector_splits(scoring)
        reasons = self._reasons(scoring, fix["pit"], fix["game_state"])
        report = dict(lap.lap_report or {})
        report["reasons"] = reasons
        if fix.get("time", True):
            report["lap_time_corrected"] = {"from": round(fix["stale"], 4), "to": round(value, 4), "after_s": after}
            logger.info("Lap %d: LMU reported the lap time late — %.3f s instead of %.3f s.", lap.lap_number, value, fix["stale"])
        else:
            report["sectors_late_s"] = after
            logger.info("Lap %d: LMU reported the sector times %.2f s late.", lap.lap_number, after)
        lap.lap_report = report
        lap.lap_time = value
        lap.is_valid = lap_verdict.is_valid(reasons)
        self._previous_lmu_time = value
        if self._late is not None:
            self._late.set_lap_time(value)
        if fix.get("time", True) and not sectors_ok:
            # the time is right now; the sector times may still follow
            self._pending_fix = {**fix, "time": False, "stale": value}
            return
        self._pending_fix = None

    def _observe_late(self, frame: Frame) -> None:
        tele, scoring = frame.telemetry, frame.scoring
        self._maybe_correct_lap_time(frame)
        self._late.observe(
            tele.elapsed_time, last_lap_time=scoring.last_lap_time,
            steps=getattr(tele, "track_limits_steps", None), penalties=getattr(scoring, "num_penalties", None),
            off_track=lap_verdict.excursion(tele, scoring),
        )

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
        # V0.9.3.1: a lap released from its late window in this frame (see below)
        released: Optional[Lap] = None
        if self._late is not None:
            self._observe_late(frame)

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
                self._tail_start = None
                self._pit_this_lap = False
                released = self.flush_pending()
                # Falls through to the common tail.
            else:
                self._reset_wait_frames += 1
                prev_lap_dist = self._buffer[-1].lap_dist if self._buffer else None
                reset_detected = self._force_reset or (
                    prev_lap_dist is not None
                    and scoring.lap_dist < prev_lap_dist - self._RESET_DROP_THRESHOLD_METERS
                )
                self._force_reset = False
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
                tail = len(self._buffer) - self._tail_start if self._tail_start is not None else 0
                game_state = self._game_lap_state(self._buffer, tail)
                reasons = self._reasons(scoring, self._pit_this_lap, game_state)
                completed = Lap(
                    track_name=session.track_name.decode(errors="replace").strip("\x00"),
                    car_name=tele.vehicle_name.decode(errors="replace").strip("\x00"),
                    session_type=session.session,
                    lap_number=self._current_lap_number,
                    lap_time=scoring.last_lap_time,
                    sector_times=self._derive_sector_splits(scoring),
                    is_valid=lap_verdict.is_valid(reasons),
                    started_at=self._lap_start_time if self._lap_start_time is not None else 0.0,
                    ambient_temp=session.ambient_temp,
                    track_temp=session.track_temp,
                    car_class=scoring.vehicle_class.decode(errors="replace").strip("\x00"),
                    car_model=scoring.veh_filename.decode(errors="replace").strip("\x00"),
                    samples=self._buffer,
                    vehicle_model=model_text(getattr(tele, "vehicle_model", b"")),
                    tail_samples=tail,
                    track_grip_level=_small_int(getattr(session, "track_grip_level", None), 0, 4),
                    fixed_setup=_bool_or_none(getattr(session, "is_fixed_setup", None)),
                    track_limits_steps_per_point=_small_int(getattr(session, "track_limits_steps_per_point", None), 0, 255),
                    track_limits_steps_per_penalty=_small_int(getattr(session, "track_limits_steps_per_penalty", None), 0, 255),
                    game_lap_state=game_state,
                    grid_vehicles=list(self._grid.values()),
                    lap_report={"reasons": reasons},
                    lap_context=lap_context(tele, scoring, session),
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
                # V0.9.3.1: held for the late window that started at the line; returned
                # by a later frame (or flush_pending / idle)
                # V0.9.4: does LMU's lap time belong to THIS lap? (see _maybe_correct_lap_time)
                measured = None
                if self._late is not None and self._lap_start_time is not None:
                    measured = self._late.line_et - self._lap_start_time
                # stale: does not fit the measured duration, or is exactly the previous lap's time
                # (LMU had not updated it yet — two laps never match to 0.1 ms)
                previous_time = self._previous_lmu_time
                stale = (not self._time_matches(float(scoring.last_lap_time), measured)
                         or (previous_time is not None and float(scoring.last_lap_time) > 0
                             and abs(float(scoring.last_lap_time) - previous_time) < 1e-4))
                self._previous_lmu_time = float(scoring.last_lap_time) if scoring.last_lap_time > 0 else previous_time
                if self._late is not None:
                    self._late.set_lap_time(scoring.last_lap_time)
                    self._pending = completed
                    # V0.9.4: also when only the sector times are not there yet (LMU writes them late)
                    sectors_missing = "sector_times_implausible" in reasons and float(scoring.last_lap_time) > 0
                    self._pending_fix = {"stale": float(scoring.last_lap_time), "measured": measured,
                                         "pit": self._pit_this_lap, "game_state": game_state,
                                         "line_et": self._late.line_et,
                                         "time": stale} if (stale or sectors_missing) else None
                    completed = None
                self._current_lap_number = self._pending_lap_number
                self._awaiting_lap_dist_reset = False
                self._pending_lap_number = None
                self._tail_start = None
                # None: the common tail (below) sets it lazily from this
                # very frame — the one where lap_dist actually reset,
                # which is the true first sample of the new lap.
                completing_start = self._lap_start_time
                self._lap_start_time = None
                self._buffer = []
                self._pit_this_lap = False
                if self._carry:
                    # V0.9.4: the next lap had already begun (LMU reset the distance before
                    # the lap number changed) — its first samples move over, re-timed
                    carry, self._carry = self._carry, None
                    base = carry[0].t
                    self._buffer = [replace(sample, t=sample.t - base) for sample in carry]
                    self._lap_start_time = (completing_start or 0.0) + base
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
                # V0.9.4: LMU sometimes resets the lap distance a few frames BEFORE the lap
                # number changes (seen on Fuji, Spa, Silverstone). The reset this branch waits
                # for has then already happened: the samples after it belong to the new lap.
                early = self._early_reset_index()
                if early is not None:
                    self._carry = self._buffer[early:]
                    self._buffer = self._buffer[:early]
                    released = self._start_late_window(frame)
                    self._observe_late(frame)
                    self._awaiting_lap_dist_reset = True
                    self._pending_lap_number = tele.lap_number
                    self._reset_wait_frames = 0
                    self._tail_start = len(self._buffer)
                    self._force_reset = True
                    again = self.feed(frame)   # finalised at once: this frame is past the reset
                    return released or again
                # V0.9.3.1: the finish line — the late window starts here
                released = self._start_late_window(frame)
                self._observe_late(frame)
                self._awaiting_lap_dist_reset = True
                self._pending_lap_number = tele.lap_number
                self._reset_wait_frames = 0
                self._tail_start = len(self._buffer)  # this frame is the first of the tail
                self._buffer.append(self._build_sample(frame))
                if scoring.in_pits:
                    self._pit_this_lap = True
                return released
            else:
                logger.info(
                    "Lap number jumped %d → %d (not +1) — discarding buffer (%d samples)",
                    self._current_lap_number, tele.lap_number, len(self._buffer),
                )
                self._current_lap_number = tele.lap_number
                self._lap_start_time = None
                self._buffer = []
                self._tail_start = None
                self._pit_this_lap = False
                released = self.flush_pending()
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
        if completed is None and self._pending is not None and self._late is not None \
                and self._late.done(tele.elapsed_time):
            completed = self._release(complete=True)
        return released or completed

    _EARLY_RESET_LOOKBACK = 300   # samples (5 s at 60 Hz)

    def _early_reset_index(self) -> Optional[int]:
        """Index of the first sample after a lap-distance reset near the end of the buffer
        (the next lap already began), or None. Only a clear reset counts: a drop of more than
        _RESET_DROP_THRESHOLD_METERS, after which the distance stays near the start."""
        buf = self._buffer
        n = len(buf)
        if n < 20:
            return None
        for i in range(n - 1, max(n - self._EARLY_RESET_LOOKBACK, 1) - 1, -1):
            if buf[i].lap_dist < buf[i - 1].lap_dist - self._RESET_DROP_THRESHOLD_METERS:
                if i < 10 or max(s.lap_dist for s in buf[i:]) > self._RESET_DROP_THRESHOLD_METERS:
                    return None
                return i
        return None

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
            # V0.9.1: float32 array, not a tuple of Python floats — 180 values take 720 bytes
            # instead of ~5.6 KB (each Python float is its own 24-byte object). For a 15,000-sample
            # lap that is ~10 MiB of RAM on the driver's PC instead of ~83 MiB. The file stores
            # these channels as float32 anyway, so the saved values are identical.
            ext=array("f", extract_extended(tele, scoring)),
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
        if s1 <= 0 or s2_cumulative <= 0 or total <= 0 or s2_cumulative <= s1 or s2_cumulative >= total:
            # Missing/invalid sector data (e.g. first lap out of the pits,
            # or a session/track without three timed sectors) — report
            # what we can and leave the rest at 0 rather than emitting a
            # negative or nonsensical split.
            return [max(s1, 0.0), 0.0, 0.0]
        s2 = s2_cumulative - s1
        s3 = total - s2_cumulative
        return [s1, s2, max(s3, 0.0)]

    # V0.9.3: positions of the per-sample game flags inside TelemetrySample.ext
    _EXT_INDEX = {name: EXTENDED_COLUMNS.index(name) for name in (
        "lap_invalidated", "track_limits_steps", "count_lap_flag", "num_penalties",
    )}
    # The first second of a lap is left out of the flag checks: if LMU clears last lap's
    # flags a few frames late, they must not count against this lap.
    _SETTLE_SECONDS = 1.0

    @classmethod
    def _game_lap_state(cls, samples: Sequence[TelemetrySample], tail: int) -> dict:
        """What the game itself said about this lap, read from the samples of the lap's
        body (without the first second and without the tail recorded after LMU switched to
        the next lap number). Keys are present only when the client recorded them:
          count_lap_flag     the value on the lap's last body sample (2 = time counts)
          invalidated        LMU's mLapInvalidated was set during the lap
          track_limits_steps how many track-limits steps were added during the lap
          penalties_added    how many penalties were added during the lap
        """
        body = samples[: max(len(samples) - max(tail, 0), 0)]
        body = [s for s in body if s.ext is not None and s.t >= cls._SETTLE_SECONDS] or [
            s for s in samples[: max(len(samples) - max(tail, 0), 0)] if s.ext is not None
        ]
        if not body:
            return {}

        def column(name):
            index = cls._EXT_INDEX[name]
            values = [s.ext[index] for s in body if len(s.ext) > index]
            return [v for v in values if v == v]  # drop NaN (not recorded)

        state: dict = {}
        flags = column("count_lap_flag")
        if flags:
            state["count_lap_flag"] = int(flags[-1])
        invalidated = column("lap_invalidated")
        if invalidated:
            state["invalidated"] = any(v >= 0.5 for v in invalidated)
        for name, key in (("track_limits_steps", "track_limits_steps"), ("num_penalties", "penalties_added")):
            values = column(name)
            if values:
                state[key] = int(sum(max(b - a, 0.0) for a, b in zip(values, values[1:])))
        return state

    @staticmethod
    def _derive_is_valid(scoring, pit_this_lap: bool, game_state: Optional[dict] = None) -> bool:
        """Validity from real, game-reported signals — not a fabricated
        `lap_valid` field (VehicleScoringInfoV01 has no such member; see
        client/lmu/structs.py). We reject laps with no positive lap time,
        non-monotonic sector cumulative times, or a pit-lane visit during the
        lap — a lap through the pits is a real, completed lap but not a fair
        comparison for records/leaderboards.

        V0.9.3 — the game's own verdict, read from the lap's samples (game_state,
        see _game_lap_state) when the client recorded them:
          * mCountLapFlag must be 2 ("count lap AND time"). 1 means LMU counts the
            lap but not its time — earlier versions wrongly accepted it.
          * mLapInvalidated must not have been set during the lap.
          * no track-limits steps may have been added during the lap.
        Without game_state (older code paths) the flag of the given scoring frame is
        used as before, now with the corrected meaning.

        V0.9.3.1 — the same rules as the server (client/telemetry/lap_verdict.py): added
        track-limits steps and penalties no longer reject a lap here. They are reported
        (reason codes) and the server sends the lap to review; only LMU's own verdict and
        the decided rules (no time, pit lane, sector times) reject it.

        This is the CLIENT's opinion only. The server repeats every check on the
        uploaded file and decides (server/track_limits.py)."""
        return lap_verdict.is_valid(LapParser._reasons(scoring, pit_this_lap, game_state))

    @staticmethod
    def _reasons(scoring, pit_this_lap: bool, game_state: Optional[dict] = None) -> List[str]:
        state = dict(game_state or {})
        state.setdefault("count_lap_flag", scoring.count_lap_flag)
        return lap_verdict.lap_reasons(
            lap_time=scoring.last_lap_time, in_pits=bool(pit_this_lap or scoring.in_pits),
            sector1=scoring.last_sector1, sector2=scoring.last_sector2, game_state=state,
        )


def _num(obj, name, lo, hi, digits=None):
    try:
        value = float(getattr(obj, name))
    except (AttributeError, TypeError, ValueError):
        return None
    if not (lo <= value <= hi) or value != value:
        return None
    return round(value, digits) if digits is not None else int(value)


def lap_context(tele, scoring, session) -> dict:
    """V0.9.4: what the Stint Analyzer needs to know about the car and the session at the end of
    a lap. Read once per lap (a few fields), only what LMU reports — no value is guessed; a
    field LMU does not offer (or that is out of range) is simply left out.

      pitstops         stops so far in this session (VehicleScoringInfoV01.mNumPitstops)
      place            position
      compound_front / compound_rear   LMU's tyre compound index and name (to be confirmed with
                       real data: docs/LMU_VERIFICATION_PROTOCOL.md (w))
      fuel_capacity_l  tank size
      damage           highest dent severity of the 8 body zones (0 none .. 2 heavy), engine
                       overheating, a detached part
      session_left_s   time left in the session, max_laps (0 = time-limited session)"""
    out: dict = {}
    for key, obj, name, lo, hi, digits in (
        ("pitstops", scoring, "num_pitstops", 0, 500, None),
        ("place", scoring, "place", 1, 255, None),
        ("compound_front", tele, "front_tire_compound_index", 0, 255, None),
        ("compound_rear", tele, "rear_tire_compound_index", 0, 255, None),
        ("fuel_capacity_l", tele, "fuel_capacity", 0.5, 500.0, 1),
        ("session_left_s", session, "session_time_remaining", 0.0, 10 * 86400.0, 0),
        ("max_laps", session, "max_laps", 0, 100000, None),
    ):
        value = _num(obj, name, lo, hi, digits)
        if value is not None:
            out[key] = value
    for key, name in (("compound_front_name", "front_tire_compound_name"), ("compound_rear_name", "rear_tire_compound_name")):
        text = _decode(getattr(tele, name, b""))
        if text:
            out[key] = text[:18]
    try:
        dents = [int(v) for v in tele.dent_severity]
        if dents and all(0 <= v <= 255 for v in dents):
            out["damage"] = {"dent_max": max(dents), "overheating": bool(getattr(tele, "overheating", False)),
                             "detached": bool(getattr(tele, "detached", False))}
    except (AttributeError, TypeError, ValueError):
        pass
    return out


def _decode(raw) -> str:
    if isinstance(raw, (bytes, bytearray)):
        return _text(bytes(raw))
    return str(raw or "").strip()


def _small_int(value, lo: int, hi: int) -> Optional[int]:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if lo <= number <= hi else None


def _bool_or_none(value) -> Optional[bool]:
    if value is None:
        return None
    try:
        return bool(value)
    except Exception:  # noqa: BLE001
        return None
