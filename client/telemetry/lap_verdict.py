"""
client/telemetry/lap_verdict.py — the client's verdict on a finished lap, with its reasons
(V0.9.3.1), and the "late window" observed right after the finish line.

Same rules as the server
------------------------
The server decides (server/track_limits.py). The client only says what it saw — but its
yes/no must follow the SAME rules, or a lap the server would let count (and review) arrives
as "client rejected it" and stays invalid. That was the V0.9.3 bug: the client rejected
every lap whose track-limits step counter rose, although a rise is only a fraction of an
LMU warning point and LMU itself had not invalidated the lap.

INVALIDATING and REVIEW_ONLY below must therefore match server/track_limits.py RULES
(tests/test_v0931_verdict.py compares them). Since 0.9.3.1 the client also sends the
reason codes, so the server never has to guess why a lap was rejected.

The late window
---------------
LMU scores a track-limits offence 2–5 s after the car rejoins (LMU's guide "Understanding
track limits"). An offence in the last corner can be scored after the finish line, while
the next lap is already running. The parser therefore keeps a finished lap for
LATE_WINDOW_SECONDS after the line and records, change by change, what LMU reports in that
time: the step counter, the finished lap's time (LMU's mLastLapTime — other LMU tools read
a value <= 0 as "last lap invalid"; unconfirmed, so the server only asks for a review), the
penalties, and when the new lap first left the track (to tell a late report of this lap
from an early offence of the next one). See server/track_limits.py attribute_rise.
"""

from __future__ import annotations

import time
from typing import List, Optional

# --- the rules (must match server/track_limits.py RULES; see the module docstring)
INVALIDATING = frozenset({
    "no_lap_time", "pit_lane",
    "game_invalidated", "game_time_not_counted", "game_lap_not_counted",
})
# V0.9.4: sector_times_implausible moved here — LMU writes the sector times late; a lap LMU
# counts is not rejected for an unknown sector split (the client also waits for them, parser.py)
REVIEW_ONLY = frozenset({
    "track_limits_points", "penalty_added", "late_track_limits", "game_last_lap_time_withdrawn",
    "sector_times_implausible",
})

LATE_WINDOW_SECONDS = 6.0           # LMU: 2-5 s after rejoining, plus a margin
# A finished lap is never held longer than this in wall-clock time (pause menu, back to
# the garage, the game stops sending the player's car): it is then sent as it is, with an
# incomplete late window (the server knows it was incomplete).
LATE_WALL_LIMIT_SECONDS = 30.0
MAX_LATE_EVENTS = 32
OFF_SURFACES = (2, 3, 4)            # grass, dirt, gravel
EDGE_MARGIN_M = 2.0
EDGE_MIN_TRACK_EDGE_M = 2.0


def is_valid(reasons) -> bool:
    return not any(code in INVALIDATING for code in reasons or ())


def lap_reasons(*, lap_time: float, in_pits: bool, sector1: float, sector2: float,
                game_state: Optional[dict]) -> List[str]:
    """The reason codes for one finished lap (empty = nothing to report)."""
    reasons: List[str] = []
    if lap_time <= 0:
        reasons.append("no_lap_time")
    if in_pits:
        reasons.append("pit_lane")
    # V0.9.4: a split at or beyond the lap time cannot belong to this lap (the previous lap's
    # values, LMU had not updated them yet)
    if sector1 <= 0 or sector2 <= sector1 or (lap_time > 0 and sector2 >= lap_time):
        reasons.append("sector_times_implausible")
    state = game_state or {}
    flag = state.get("count_lap_flag")
    if flag == 0:
        reasons.append("game_lap_not_counted")
    elif flag == 1:
        reasons.append("game_time_not_counted")
    if state.get("invalidated"):
        reasons.append("game_invalidated")
    if (state.get("track_limits_steps") or 0) > 0:
        reasons.append("track_limits_points")
    if (state.get("penalties_added") or 0) > 0:
        reasons.append("penalty_added")
    return reasons


def excursion(tele, scoring) -> Optional[bool]:
    """Is the car off the track right now (two wheels on grass/dirt/gravel, or clearly beyond
    LMU's approximate track edge)? None when the frame carries neither signal."""
    seen = False
    try:
        surfaces = [int(w.surface_type) for w in tele.wheel]
        seen = True
        if sum(1 for s in surfaces if s in OFF_SURFACES) >= 2:
            return True
    except (AttributeError, TypeError, ValueError):
        pass
    try:
        lateral, edge = abs(float(scoring.path_lateral)), abs(float(scoring.track_edge))
        if edge >= EDGE_MIN_TRACK_EDGE_M:
            seen = True
            if lateral - edge >= EDGE_MARGIN_M:
                return True
    except (AttributeError, TypeError, ValueError):
        pass
    return False if seen else None


class LateWindow:
    """What LMU reports in the first LATE_WINDOW_SECONDS after the finish line.

    Call observe() once per frame (cheap: a few comparisons, an entry only on a change),
    report() when done()."""

    __slots__ = ("line_et", "lap_time_at_line", "last_et", "steps", "penalties", "wall_start", "window",
                 "last_lap_time", "excursion_at", "can_see_excursions", "_last_steps", "_last_pen")

    def __init__(self, line_et: float, steps_at_line: Optional[float] = None,
                 penalties_at_line: Optional[float] = None, lap_time_at_line: float = 0.0,
                 wall_start: Optional[float] = None, window: float = LATE_WINDOW_SECONDS):
        self.window = float(window)
        self.line_et = float(line_et)
        self.lap_time_at_line = float(lap_time_at_line)
        self.last_et = self.line_et
        self.last_lap_time = float(lap_time_at_line)
        self.wall_start = time.monotonic() if wall_start is None else float(wall_start)
        self.steps: List[list] = []
        self.penalties: List[list] = []
        self.excursion_at: Optional[float] = None
        self.can_see_excursions = False
        self._last_steps = None
        self._last_pen = None
        self._record_steps(0.0, steps_at_line)
        self._record_pen(0.0, penalties_at_line)

    def _record_steps(self, d: float, value) -> None:
        if value is None or value != value:
            return
        value = int(value)
        if value != self._last_steps and len(self.steps) < MAX_LATE_EVENTS:
            self.steps.append([round(d, 2), value])
            self._last_steps = value

    def _record_pen(self, d: float, value) -> None:
        if value is None or value != value:
            return
        value = int(value)
        if value != self._last_pen and len(self.penalties) < MAX_LATE_EVENTS:
            self.penalties.append([round(d, 2), value])
            self._last_pen = value

    def observe(self, et: float, *, last_lap_time: float, steps, penalties, off_track: Optional[bool]) -> None:
        d = float(et) - self.line_et
        if d < 0:
            return
        self.last_et = max(self.last_et, float(et))
        self.last_lap_time = float(last_lap_time)
        self._record_steps(d, steps)
        self._record_pen(d, penalties)
        if off_track is not None:
            self.can_see_excursions = True
            if off_track and self.excursion_at is None:
                self.excursion_at = round(d, 2)

    def set_lap_time(self, lap_time: float) -> None:
        """The finished lap's time as the parser emitted it (read when LMU reset the lap
        distance, a few frames after the line)."""
        self.lap_time_at_line = float(lap_time)
        self.last_lap_time = float(lap_time)

    def done(self, et: Optional[float] = None, now: Optional[float] = None) -> bool:
        if et is not None and float(et) - self.line_et >= self.window:
            return True
        return self.timed_out(now)

    def timed_out(self, now: Optional[float] = None) -> bool:
        now = time.monotonic() if now is None else float(now)
        return now - self.wall_start >= LATE_WALL_LIMIT_SECONDS

    def report(self, complete: bool) -> dict:
        return {
            "seconds": round(max(self.last_et - self.line_et, 0.0), 2),
            "complete": bool(complete),
            "lap_time_at_line": round(self.lap_time_at_line, 4),
            "last_lap_time": round(self.last_lap_time, 4),
            "steps": self.steps,
            "penalties": self.penalties,
            "excursion_at": self.excursion_at,
            "can_see_excursions": self.can_see_excursions,
        }
