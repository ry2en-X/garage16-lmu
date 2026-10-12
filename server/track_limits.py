"""
server/track_limits.py — does a lap respect the track limits? (V0.9.3, tiers since V0.9.3.1)

The server decides from the uploaded telemetry itself, combining several independent
signals, and keeps every finding with WHERE on the lap it happened, WHICH signal produced
it and WHICH KIND of rule it is — a verdict that can be checked, not a black box.

Three kinds of rules (V0.9.3.1)
-------------------------------
The V0.9.3 check treated everything it could measure as equally certain. Research into
LMU's documented behaviour (docs/LMU_VERIFICATION_PROTOCOL.md, section V0.9.3.1) showed
that this is wrong: LMU's own guide ("Understanding track limits") says a track-limits
offence is scored by the TIME GAINED, 2–5 s after the car rejoins, and the score is kept
in "steps" that are only a fraction of a warning point (points = steps / steps per point).
Outside races, LMU invalidates a lap when an offence earns warning points — LMU then sets
its own invalidation flag. A rising step counter alone is therefore NOT "LMU invalidated
this lap", and four wheels on the grass without any gain is not an offence for LMU either.

So every rule now belongs to exactly one tier (RULES below):

  lmu        The game's own verdict, read from LMU's flags. Can invalidate.
  rule       Garage16 rules that are decided and do not depend on interpretation
             (pit lane, no lap time, implausible sector times). Can invalidate.
  heuristic  Our own measurements (track-limits steps, wheels on grass, beyond the track
             edge, shorter than usual, penalties, late track-limits reports). They never
             invalidate on their own: the lap counts and goes to the admin review queue.
             Once real LMU recordings confirm one of them, the operator can let it
             invalidate (LMU_GARAGE_TRACK_LIMITS_INVALIDATING, see `promote` in assess()).

Signals (what the lap file holds; older clients have fewer — see `signals` in the result)
-------------------------------------------------------------------------------------
  lap_invalidated      LMU's own mLapInvalidated, per sample            (clients 0.9.0+)
  track_limits_steps   LMU's track-limits steps (mTrackLimitsSteps)     (clients 0.9.0+)
  surface_type_*       what each wheel is on: 0 dry, 1 wet, 2 grass,    (clients 0.9.0+)
                       3 dirt, 4 gravel, 5 kerb, 6 special
  count_lap_flag       2 = LMU counts lap AND time                      (clients 0.9.3+)
  path_lateral /       lateral position and the track edge relative to  (clients 0.9.3+)
  track_edge           LMU's approximate centre path, metres
  num_penalties        penalties the car carries                        (clients 0.9.3+)
  driven distance      ∫ speed dt, compared with the layout's usual distance
  client report        what the client concluded, with its reasons      (reasons 0.9.3.1+)
  late window          what LMU reported in the first seconds AFTER the (clients 0.9.3.1+)
                       finish line (client/telemetry/lap_verdict.py)

Which lap does a late report belong to?
---------------------------------------
LMU scores an offence 2–5 s after the car rejoins. An offence in the last corner can
therefore be scored after the finish line, i.e. while the NEXT lap is already running.
A rise of the step counter shortly after the line is attributed like this
(`attribute_rise`):

  < LATE_CERTAIN_SECONDS after the line   previous lap (nothing in the new lap can have
                                           been scored that fast)
  < LATE_WINDOW_SECONDS after the line    previous lap if the new lap shows NO excursion
                                           (two wheels off or beyond the track edge) before
                                           the rise and the signals to see one exist;
                                           otherwise AMBIGUOUS — both laps are flagged
  later                                   this lap

Ambiguous cases are never decided silently: they go to review (both laps). The previous lap
gets its finding either from the client's late window (clients 0.9.3.1+) or, for older
clients, from the next lap's file (server/lap_check.py link_neighbours).

Edges of the lap: LMU's own flags (lap_invalidated) are not read in the first
SETTLE_SECONDS (LMU may clear last lap's flag a few frames late) and the last
`tail_samples` samples (recorded after LMU had switched to the next lap number) belong to
the late window, not to the lap body.

Defensive by design: a signal that does not look like what LMU documents (values out of
range, "off track" for most of the lap) is reported as unreliable and IGNORED for that lap
rather than invalidating a clean lap — a struct-offset or game-update problem must not wipe
out leaderboards.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence

import numpy as np

TRACK_LIMITS_VERSION = 2

INVALID, REVIEW, INFO = "invalid", "review", "info"
STATUS_VALID, STATUS_REVIEW, STATUS_INVALID = "valid", "review", "invalid"
SEVERITY_ORDER = {INVALID: 0, REVIEW: 1, INFO: 2}

TIER_LMU, TIER_RULE, TIER_HEURISTIC, TIER_SIGNAL = "lmu", "rule", "heuristic", "signal"

WHEELS = ("fl", "fr", "rl", "rr")
OFF_SURFACES = (2.0, 3.0, 4.0)       # grass, dirt, gravel (LMU's mSurfaceType)
KNOWN_SURFACES = (0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0)

SETTLE_SECONDS = 1.0
OFF_MIN_SAMPLES = 3
OFF_MIN_SECONDS = 0.05
OFF_MERGE_GAP_SECONDS = 0.25       # two excursions closer than this are one
EDGE_MARGIN_M = 2.0
EDGE_MIN_SECONDS = 0.3
EDGE_MIN_TRACK_EDGE_M = 2.0        # a "track edge" closer than this to the path is not plausible
SHORT_DISTANCE_FRACTION = 0.015    # 1.5 % shorter than usual ...
SHORT_DISTANCE_MIN_M = 40.0        # ... and at least 40 m
MAX_OFF_SHARE = 0.30               # "all four off" on > 30 % of a lap = the signal is broken
MAX_EVENTS_PER_CODE = 20

# Attribution of reports that arrive around the finish line (see the module docstring).
# LMU documents "2-5 s after rejoining"; the window adds a margin to the upper end.
LATE_CERTAIN_SECONDS = 1.0
LATE_WINDOW_SECONDS = 6.0
ATTR_THIS, ATTR_PREVIOUS, ATTR_AMBIGUOUS = "this_lap", "previous_lap", "ambiguous"


@dataclass(frozen=True)
class Rule:
    tier: str
    severity: str          # default severity
    confirmed: bool        # is the MEANING confirmed (real LMU data or LMU documentation)?
    note: str


RULES: Dict[str, Rule] = {
    # --- LMU's own verdict
    "game_invalidated": Rule(TIER_LMU, INVALID, False,
                             "LMU's mLapInvalidated. LMU's own flag; its exact timing around the line is still to be confirmed (protocol r)."),
    "game_time_not_counted": Rule(TIER_LMU, INVALID, True,
                                  "LMU's mCountLapFlag = 1 (count the lap, not its time). Meaning confirmed in LMU verification (c)."),
    "game_lap_not_counted": Rule(TIER_LMU, INVALID, True,
                                 "LMU's mCountLapFlag = 0. Meaning confirmed in LMU verification (c)."),
    "game_last_lap_time_withdrawn": Rule(TIER_LMU, REVIEW, False,
                                         "LMU replaced the finished lap's time by 0 or less after the line (the convention other LMU tools use for an invalid last lap). Unconfirmed — review until protocol (s) confirms it."),
    # --- decided Garage16 rules
    "no_lap_time": Rule(TIER_RULE, INVALID, True, "LMU reported no lap time for the lap."),
    "pit_lane": Rule(TIER_RULE, INVALID, True, "The car was in the pit lane during the lap (Garage16 rule: no pit laps in comparisons)."),
    "sector_times_implausible": Rule(TIER_RULE, INVALID, True, "LMU's sector times are missing or not increasing."),
    "client_invalid": Rule(TIER_RULE, INVALID, True,
                           "An older client rejected the lap without saying why (before 0.9.3: pit lane, no time or LMU's count flag)."),
    # --- heuristics: review only, unless promoted by the operator
    "track_limits_points": Rule(TIER_HEURISTIC, REVIEW, False,
                                "LMU's track-limits step counter rose. Steps are fractions of a warning point; LMU invalidates only offences that earn points."),
    "late_track_limits": Rule(TIER_HEURISTIC, REVIEW, False,
                              "LMU's step counter rose shortly after the finish line — probably an offence at the end of this lap, scored late."),
    "four_wheels_off": Rule(TIER_HEURISTIC, REVIEW, False,
                            "All four wheels on grass, dirt or gravel. LMU judges by track width and time gained, not by surface."),
    "edge_exceeded": Rule(TIER_HEURISTIC, REVIEW, False, "The car's centre was clearly beyond LMU's (approximate) track edge."),
    "short_distance": Rule(TIER_HEURISTIC, REVIEW, False, "The lap was clearly shorter than the usual lap on this layout."),
    "penalty_added": Rule(TIER_HEURISTIC, REVIEW, False, "LMU gave the car a penalty during the lap."),
    "client_invalid_unexplained": Rule(TIER_HEURISTIC, REVIEW, False,
                                       "A 0.9.3 client rejected the lap; its report only shows track-limits steps, which no longer invalidate — but a pit-lane visit cannot be ruled out."),
    # --- shown, no effect
    "track_limits_points_previous_lap": Rule(TIER_HEURISTIC, INFO, False,
                                             "Steps added right after the line, before any excursion in this lap — counted for the previous lap."),
    "game_lap_time_changed": Rule(TIER_LMU, INFO, False, "LMU changed the finished lap's time after the line."),
    "wheels_off": Rule(TIER_HEURISTIC, INFO, False, "Two or three wheels off the track at the same time."),
    "signal_unreliable": Rule(TIER_SIGNAL, INFO, True, "A signal looked implausible and was ignored for this lap."),
    "not_verifiable": Rule(TIER_SIGNAL, INFO, True, "The lap has no track-limits signals at all (old client)."),
}

# What the operator may let invalidate once real data confirmed it (and nothing else).
PROMOTABLE = frozenset(code for code, rule in RULES.items() if rule.severity == REVIEW)

# Codes the client may report (client/telemetry/lap_verdict.py) and which file signal the
# server checks itself for each (None: only the client can know it).
CLIENT_REASON_SIGNAL = {
    "no_lap_time": None,
    "pit_lane": None,
    "sector_times_implausible": None,
    "game_invalidated": "lap_invalidated",
    "game_time_not_counted": "count_lap_flag",
    "game_lap_not_counted": "count_lap_flag",
    "track_limits_points": "track_limits_steps",
    "penalty_added": "num_penalties",
    "late_track_limits": None,
    "game_last_lap_time_withdrawn": None,
}

# Columns this module reads from a lap file (the upload reads them in one go).
COLUMNS: tuple = (
    "t", "lap_dist", "speed",
    "lap_invalidated", "track_limits_steps", "count_lap_flag", "num_penalties",
    "path_lateral", "track_edge",
    *(f"surface_type_{w}" for w in WHEELS),
)

MESSAGES = {
    "game_invalidated": "LMU invalidated this lap",
    "game_time_not_counted": "LMU does not count this lap time",
    "game_lap_not_counted": "LMU does not count this lap",
    "game_last_lap_time_withdrawn": "LMU withdrew the lap time after the line",
    "game_lap_time_changed": "LMU changed the lap time after the line",
    "no_lap_time": "LMU reported no lap time",
    "pit_lane": "pit lane during the lap",
    "sector_times_implausible": "implausible sector times",
    "track_limits_points": "LMU added track-limits steps",
    "late_track_limits": "LMU added track-limits steps right after the line",
    "track_limits_points_previous_lap": "track-limits steps for the previous lap",
    "four_wheels_off": "all four wheels off the track",
    "client_invalid": "the client rejected this lap (pit lane / game flags)",
    "client_invalid_unexplained": "the client rejected this lap; only track-limits steps explain it",
    "edge_exceeded": "beyond the track edge",
    "short_distance": "shorter than the usual lap on this layout",
    "penalty_added": "LMU gave a penalty during this lap",
    "wheels_off": "wheels off the track",
    "signal_unreliable": "a track-limits signal looked implausible and was ignored",
    "not_verifiable": "this lap has no track-limits data (recorded by an older client)",
}


def severity_of(code: str, promote: Iterable[str] = ()) -> str:
    rule = RULES.get(code)
    if rule is None:
        return INFO
    if code in PROMOTABLE and code in set(promote or ()):
        return INVALID
    return rule.severity


def tier_of(code: str) -> str:
    rule = RULES.get(code)
    return rule.tier if rule else TIER_SIGNAL


def parse_promote(text: Optional[str]) -> frozenset:
    """'four_wheels_off, track_limits_points' -> the promotable codes among them (unknown or
    non-promotable names are ignored — an LMU verdict cannot be weakened this way)."""
    names = {part.strip() for part in (text or "").replace(";", ",").split(",") if part.strip()}
    return frozenset(names & PROMOTABLE)


@dataclass
class Finding:
    code: str
    severity: str
    source: str
    at: Optional[float] = None       # lap distance, metres
    t: Optional[float] = None        # seconds into the lap (negative: before the lap; late: after the line)
    until: Optional[float] = None    # end of a stretch, metres
    detail: dict = field(default_factory=dict)
    tier: Optional[str] = None

    def __post_init__(self):
        if self.tier is None:
            self.tier = tier_of(self.code)

    def as_dict(self) -> dict:
        out = {k: v for k, v in asdict(self).items() if v is not None and v != {}}
        for key in ("at", "until"):
            if key in out:
                out[key] = round(float(out[key]), 1)
        if "t" in out:
            out["t"] = round(float(out["t"]), 2)
        return out


@dataclass
class Verdict:
    status: str
    findings: List[Finding]
    signals: List[str]

    def as_dict(self) -> dict:
        return {
            "version": TRACK_LIMITS_VERSION,
            "status": self.status,
            "findings": [f.as_dict() for f in self.findings],
            "signals": list(self.signals),
        }

    @property
    def invalidating(self) -> List[Finding]:
        return [f for f in self.findings if f.severity == INVALID]

    def reasons(self) -> List[str]:
        """Readable reasons for laps.invalid_reason (the lists the driver already sees)."""
        return [reason_text(f.code, f.at) for f in self.invalidating]


def reason_text(code: str, at: Optional[float] = None) -> str:
    where = f" at {at:.0f} m" if at is not None else ""
    return f"track limits: {MESSAGES.get(code, code)}{where}"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _runs(mask: np.ndarray) -> List[tuple]:
    """(start, end_exclusive) of every run of True in a boolean array."""
    if not mask.size:
        return []
    padded = np.concatenate(([False], mask, [False]))
    edges = np.flatnonzero(padded[1:] != padded[:-1])
    return list(zip(edges[::2].tolist(), edges[1::2].tolist()))


def _merge(runs: List[tuple], t: np.ndarray, gap: float) -> List[tuple]:
    merged: List[list] = []
    for start, end in runs:
        if merged and t[start] - t[merged[-1][1] - 1] <= gap:
            merged[-1][1] = end
        else:
            merged.append([start, end])
    return [tuple(r) for r in merged]


def _finite(values: Optional[np.ndarray]) -> Optional[np.ndarray]:
    if values is None:
        return None
    arr = np.asarray(values, dtype=float)
    return arr if np.isfinite(arr).any() else None


def _carry(values: np.ndarray) -> Optional[np.ndarray]:
    """Carries the last known value over NaN gaps (a gap never looks like a jump)."""
    finite = np.isfinite(values)
    if not finite.any():
        return None
    positions = np.where(finite, np.arange(len(values)), 0)
    np.maximum.accumulate(positions, out=positions)
    clean = values[positions].astype(float)
    clean[~np.isfinite(clean)] = values[finite][0]   # leading gap: the first known value
    return clean


def driven_distance(t: np.ndarray, speed: np.ndarray) -> Optional[float]:
    if t is None or speed is None or len(t) < 2:
        return None
    mask = np.isfinite(t) & np.isfinite(speed)
    if mask.sum() < 2:
        return None
    trapz = getattr(np, "trapezoid", None) or np.trapz
    return float(trapz(np.clip(speed[mask], 0, None), t[mask]))


def points_of(steps: Optional[float], steps_per_point: Optional[int]) -> Optional[float]:
    """LMU's warning points for a number of steps (TinyPedal's reading of the same fields:
    points = steps / steps per point). None when the session did not report the ratio."""
    if steps is None or not steps_per_point or steps_per_point <= 0:
        return None
    return round(float(steps) / float(steps_per_point), 3)


def attribute_rise(seconds_after_line: float, excursion_at: Optional[float], can_see_excursions: bool) -> str:
    """Which lap a step rise `seconds_after_line` after the finish line belongs to.
    excursion_at: seconds after the line of the new lap's first excursion (None: none seen)."""
    if seconds_after_line < LATE_CERTAIN_SECONDS:
        return ATTR_PREVIOUS
    if seconds_after_line >= LATE_WINDOW_SECONDS:
        return ATTR_THIS
    if can_see_excursions and (excursion_at is None or excursion_at > seconds_after_line):
        return ATTR_PREVIOUS
    return ATTR_AMBIGUOUS


# ---------------------------------------------------------------------------
# the check
# ---------------------------------------------------------------------------

def assess(columns: Dict[str, np.ndarray], *, tail_samples: int = 0, client_valid: bool = True,
           client_state: Optional[dict] = None, reference_distance: Optional[float] = None,
           client_reasons: Optional[Sequence[str]] = None, late: Optional[dict] = None,
           steps_per_point: Optional[int] = None, steps_per_penalty: Optional[int] = None,
           sectors_ok: Optional[bool] = None, promote: Iterable[str] = ()) -> Verdict:
    """The verdict for one lap.

    columns            arrays from the lap file (any of COLUMNS may be missing)
    reference_distance the usual driven distance of this layout, or None
    client_valid       the client's yes/no
    client_reasons     the client's reason codes (clients 0.9.3.1+); None = it did not say
    client_state       the client's game summary (clients 0.9.3) — used to explain a
                       rejection without reasons
    late               the client's late window (clients 0.9.3.1+), see
                       client/telemetry/lap_verdict.py
    steps_per_point /  LMU's ratios of the session (for points in the findings)
    steps_per_penalty
    sectors_ok         are the reported sector times plausible (None = unknown)
    promote            heuristic codes the operator lets invalidate
    """
    promote = frozenset(promote or ())
    findings: List[Finding] = []
    signals: List[str] = []

    def add(code: str, source: str, **kw) -> Finding:
        finding = Finding(code, severity_of(code, promote), source, **kw)
        findings.append(finding)
        return finding

    t = columns.get("t")
    lap_dist = columns.get("lap_dist")
    if t is None or lap_dist is None or len(t) < 3:
        _client_findings(add, signals, client_valid, client_reasons, client_state, sectors_ok, findings)
        _late_findings(add, late, steps_per_point)
        add("not_verifiable", "file")
        return _verdict(findings, signals)

    t = np.asarray(t, dtype=float)
    lap_dist = np.asarray(lap_dist, dtype=float)
    n = len(t)
    end = max(n - max(int(tail_samples or 0), 0), 0)
    body = np.zeros(n, dtype=bool)
    body[:end] = True
    settled = body & (t >= SETTLE_SECONDS)
    if settled.sum() < 3:          # very short laps: do not throw the whole lap away
        settled = body
    idx = np.flatnonzero(settled)
    body_idx = np.flatnonzero(body)

    def pos(i: int) -> dict:
        return {"at": float(lap_dist[i]), "t": float(t[i])}

    # --- 1. LMU's own invalidation flag
    flag = _finite(columns.get("lap_invalidated"))
    if flag is not None:
        values = flag[idx]
        finite = values[np.isfinite(values)]
        if finite.size and np.all(np.isin(finite, (0.0, 1.0))):
            signals.append("lap_invalidated")
            hit = np.flatnonzero(np.isfinite(values) & (values >= 0.5))
            if hit.size:
                add("game_invalidated", "lap_invalidated", **pos(int(idx[hit[0]])))
        else:
            add("signal_unreliable", "lap_invalidated")

    # --- 2. LMU's count_lap_flag on the last sample of the lap body
    count_flag = _finite(columns.get("count_lap_flag"))
    if count_flag is not None:
        values = count_flag[:end]
        finite = np.flatnonzero(np.isfinite(values))
        if finite.size and np.all(np.isin(values[finite], (0.0, 1.0, 2.0))):
            signals.append("count_lap_flag")
            last = int(values[finite[-1]])
            if last == 0:
                add("game_lap_not_counted", "count_lap_flag", detail={"value": 0})
            elif last == 1:
                add("game_time_not_counted", "count_lap_flag", detail={"value": 1})
        elif finite.size:
            add("signal_unreliable", "count_lap_flag")

    # --- 3. wheels off the track (surface type of all four wheels) — also the evidence
    #        whether the car left the track early in the lap (step attribution, below)
    excursion = np.zeros(n, dtype=bool)          # >= 2 wheels off or beyond the edge
    can_see_excursions = False
    surfaces = [_finite(columns.get(f"surface_type_{w}")) for w in WHEELS]
    if all(s is not None for s in surfaces):
        full = np.vstack(surfaces)                               # 4 x all samples
        stack = full[:, idx]
        finite_all = np.isfinite(stack).all(axis=0)
        known = np.isin(stack, KNOWN_SURFACES).all(axis=0)
        off = np.isin(stack, OFF_SURFACES)
        all_off = off.all(axis=0) & finite_all
        if finite_all.any() and known[finite_all].all() and all_off.mean() <= MAX_OFF_SHARE:
            signals.append("surface_type")
            can_see_excursions = True
            excursion |= (np.isin(full, OFF_SURFACES).sum(axis=0) >= 2)
            tt = t[idx]
            for start, stop in _merge(_runs(all_off), tt, OFF_MERGE_GAP_SECONDS)[:MAX_EVENTS_PER_CODE]:
                duration = float(tt[stop - 1] - tt[start])
                if stop - start >= OFF_MIN_SAMPLES and duration >= OFF_MIN_SECONDS:
                    i0, i1 = int(idx[start]), int(idx[stop - 1])
                    kinds = sorted({int(v) for v in stack[:, start:stop].ravel() if v in OFF_SURFACES})
                    add("four_wheels_off", "surface_type", at=float(lap_dist[i0]), t=float(t[i0]),
                        until=float(lap_dist[i1]), detail={"seconds": round(duration, 2), "surfaces": kinds})
            some_off = (off.sum(axis=0) >= 2) & ~all_off & finite_all
            for start, stop in _merge(_runs(some_off), tt, OFF_MERGE_GAP_SECONDS)[:MAX_EVENTS_PER_CODE]:
                if stop - start >= OFF_MIN_SAMPLES:
                    i0, i1 = int(idx[start]), int(idx[stop - 1])
                    add("wheels_off", "surface_type", at=float(lap_dist[i0]), t=float(t[i0]),
                        until=float(lap_dist[i1]), detail={"wheels": int(off[:, start:stop].sum(axis=0).max())})
        else:
            add("signal_unreliable", "surface_type")

    # --- 4. beyond LMU's (approximate) track edge
    lateral = _finite(columns.get("path_lateral"))
    edge = _finite(columns.get("track_edge"))
    if lateral is not None and edge is not None:
        lat, edg = lateral[idx], np.abs(edge[idx])
        finite = np.isfinite(lat) & np.isfinite(edg)
        if finite.any() and np.median(edg[finite]) >= EDGE_MIN_TRACK_EDGE_M:
            signals.append("track_edge")
            can_see_excursions = True
            all_lat, all_edg = np.abs(lateral), np.abs(edge)
            with np.errstate(invalid="ignore"):
                excursion |= np.isfinite(all_lat) & np.isfinite(all_edg) & (all_edg >= EDGE_MIN_TRACK_EDGE_M) & \
                    (all_lat - all_edg >= EDGE_MARGIN_M)
            beyond = finite & (np.abs(lat) - edg >= EDGE_MARGIN_M) & (edg >= EDGE_MIN_TRACK_EDGE_M)
            tt = t[idx]
            for start, stop in _merge(_runs(beyond), tt, OFF_MERGE_GAP_SECONDS)[:MAX_EVENTS_PER_CODE]:
                duration = float(tt[stop - 1] - tt[start])
                if duration >= EDGE_MIN_SECONDS:
                    i0, i1 = int(idx[start]), int(idx[stop - 1])
                    worst = float(np.max(np.abs(lat[start:stop]) - edg[start:stop]))
                    add("edge_exceeded", "track_edge", at=float(lap_dist[i0]), t=float(t[i0]),
                        until=float(lap_dist[i1]), detail={"seconds": round(duration, 2), "beyond_m": round(worst, 1)})
        elif finite.any():
            add("signal_unreliable", "track_edge")

    # --- 5. track-limits steps added during the lap (from the very first sample: a rise in
    #        the first seconds may belong to the previous lap — see attribute_rise)
    steps = _finite(columns.get("track_limits_steps"))
    if steps is not None and body_idx.size >= 2:
        values = steps[body_idx]
        ok = values[np.isfinite(values)]
        if ok.size and np.all((ok >= 0) & (ok <= 255) & (ok == np.round(ok))):
            signals.append("track_limits_steps")
            clean = _carry(values)
            rises = np.flatnonzero(np.diff(clean) > 0)
            first_excursion = np.flatnonzero(excursion[body_idx])
            for j in rises[:MAX_EVENTS_PER_CODE]:
                i = int(body_idx[j + 1])
                added = int(clean[j + 1] - clean[j])
                ex_t = float(t[body_idx[first_excursion[0]]]) if first_excursion.size else None
                attribution = attribute_rise(float(t[i]), ex_t, can_see_excursions)
                detail = {"added": added, "total": int(clean[j + 1]), "attribution": attribution}
                pts = points_of(added, steps_per_point)
                if pts is not None:
                    detail["points"] = pts
                    total_pts = points_of(clean[j + 1], steps_per_point)
                    detail["total_points"] = total_pts
                    if steps_per_penalty and added >= steps_per_penalty:
                        detail["penalty_level"] = True
                code = "track_limits_points_previous_lap" if attribution == ATTR_PREVIOUS else "track_limits_points"
                add(code, "track_limits_steps", **pos(i), detail=detail)
        elif ok.size:
            add("signal_unreliable", "track_limits_steps")

        # the tail (after the line, before LMU reset the distance) — only when the client
        # did not observe the late window itself (it covers the tail)
        if not late and end < n:
            tail_values = _carry(steps[max(end - 1, 0):])
            if tail_values is not None:
                tail_rises = np.flatnonzero(np.diff(tail_values) > 0)
                if tail_rises.size:
                    added = int(tail_values[-1] - tail_values[0])
                    detail = {"added": added, "seconds_after_line": 0.0, "attribution": ATTR_THIS, "from": "tail"}
                    pts = points_of(added, steps_per_point)
                    if pts is not None:
                        detail["points"] = pts
                    add("late_track_limits", "track_limits_steps", at=float(lap_dist[end - 1]) if end else None,
                        detail=detail)

    # --- 6. penalties added during the lap
    penalties = _finite(columns.get("num_penalties"))
    if penalties is not None:
        values = penalties[idx]
        finite = values[np.isfinite(values)]
        if finite.size and np.all((finite >= 0) & (finite < 1000)):
            signals.append("num_penalties")
            rises = np.flatnonzero(np.diff(finite) > 0)
            if rises.size:
                add("penalty_added", "num_penalties", detail={"added": int(np.sum(np.clip(np.diff(finite), 0, None)))})

    # --- 7. driven distance against the layout's usual distance
    speed = columns.get("speed")
    if speed is not None and reference_distance and reference_distance > 0:
        distance = driven_distance(t[body], np.asarray(speed, dtype=float)[body])
        if distance is not None:
            signals.append("driven_distance")
            shortfall = reference_distance - distance
            if shortfall >= max(SHORT_DISTANCE_MIN_M, SHORT_DISTANCE_FRACTION * reference_distance):
                add("short_distance", "driven_distance", detail={
                    "metres": round(distance, 0), "usual": round(reference_distance, 0), "short_by": round(shortfall, 0)})

    # --- 8. the late window the client observed after the line (clients 0.9.3.1+)
    _late_findings(add, late, steps_per_point, steps_per_penalty)

    # --- 9. what the client concluded
    _client_findings(add, signals, client_valid, client_reasons, client_state, sectors_ok, findings)

    track_signals = {"lap_invalidated", "track_limits_steps", "surface_type", "count_lap_flag", "track_edge"}
    if not track_signals.intersection(signals):
        add("not_verifiable", "file")
    return _verdict(findings, signals)


def _late_findings(add, late: Optional[dict], steps_per_point: Optional[int] = None,
                   steps_per_penalty: Optional[int] = None) -> None:
    """Findings from the client's late window: what LMU reported in the first seconds after
    the finish line (rises of the step counter, a withdrawn or changed lap time)."""
    if not isinstance(late, dict):
        return
    can_see = bool(late.get("can_see_excursions"))
    excursion_at = late.get("excursion_at")
    steps = [row for row in (late.get("steps") or []) if isinstance(row, (list, tuple)) and len(row) == 2]
    for (d0, v0), (d1, v1) in zip(steps, steps[1:]):
        try:
            d1, added = float(d1), int(v1) - int(v0)
        except (TypeError, ValueError):
            continue
        if added <= 0:
            continue
        attribution = attribute_rise(d1, excursion_at, can_see)
        if attribution == ATTR_THIS:
            continue          # belongs to the next lap; its own file reports it
        # seen from THIS (the finished) lap: "previous lap" of the line is this lap
        attribution = ATTR_THIS if attribution == ATTR_PREVIOUS else ATTR_AMBIGUOUS
        detail = {"added": added, "seconds_after_line": round(d1, 2), "attribution": attribution, "from": "client"}
        pts = points_of(added, steps_per_point)
        if pts is not None:
            detail["points"] = pts
            if steps_per_penalty and added >= steps_per_penalty:
                detail["penalty_level"] = True
        add("late_track_limits", "late_window", detail=detail)
    at_line, final = late.get("lap_time_at_line"), late.get("last_lap_time")
    if isinstance(at_line, (int, float)) and isinstance(final, (int, float)) and at_line > 0:
        if final <= 0:
            add("game_last_lap_time_withdrawn", "late_window",
                detail={"lap_time": round(float(at_line), 3), "became": round(float(final), 3)})
        elif abs(final - at_line) > 0.0005:
            add("game_lap_time_changed", "late_window",
                detail={"lap_time": round(float(at_line), 3), "became": round(float(final), 3)})


def _client_findings(add, signals: List[str], client_valid: bool, client_reasons: Optional[Sequence[str]],
                     client_state: Optional[dict], sectors_ok: Optional[bool], findings: List[Finding]) -> None:
    """The client's verdict, as findings of the same tiers as everything else.

    Clients 0.9.3.1+ say WHY (client_reasons). A reason the server checks itself from the file
    (LMU's flags, the step counter) is left to that check; the others are taken over with
    the server's own severity — so the same reason has the same effect on both sides.
    Older clients only say yes or no; a 0.9.3 client's game summary explains most of its
    rejections (see the module docstring of server/lap_check.py)."""
    signals.append("client")
    have = {f.code for f in findings}
    if client_reasons is not None:
        explained = False
        for code in dict.fromkeys(client_reasons):
            if code not in CLIENT_REASON_SIGNAL:
                continue          # a reason of a newer client this server does not know
            checked_by = CLIENT_REASON_SIGNAL[code]
            if checked_by is not None and checked_by in signals:
                explained = True  # the server looked at the same signal itself
                continue
            if code in have:
                explained = True
                continue
            add(code, "client")
            explained = True
        if not client_valid and not explained:
            add("client_invalid", "client", detail={"reasons": list(client_reasons)})
        return

    if client_valid:
        return
    state = dict(client_state or {})
    if not state:
        add("client_invalid", "client")
        return
    # A 0.9.3 client: it rejected for its game summary, for the sector times, or for the pit
    # lane (the only cause it did not report).
    hard = []
    flag = state.get("count_lap_flag")
    if flag == 0:
        hard.append("game_lap_not_counted")
    elif flag == 1:
        hard.append("game_time_not_counted")
    if state.get("invalidated"):
        hard.append("game_invalidated")
    if sectors_ok is False:
        hard.append("sector_times_implausible")
    hard_known = [code for code in hard if code in have]
    hard_new = [code for code in hard if code not in have and CLIENT_REASON_SIGNAL.get(code) not in signals]
    for code in hard_new:
        add(code, "client", detail={"inferred": True})
    if hard_known or hard_new:
        return
    if (state.get("track_limits_steps") or 0) > 0 or (state.get("penalties_added") or 0) > 0:
        add("client_invalid_unexplained", "client",
            detail={"possible": ["pit_lane", "track_limits_points"], "state": state})
        return
    add("client_invalid", "client", detail={"state": state})


def _verdict(findings: List[Finding], signals: Sequence[str]) -> Verdict:
    status = status_of(f.severity for f in findings)
    findings.sort(key=lambda f: (SEVERITY_ORDER[f.severity], f.at if f.at is not None else -1))
    return Verdict(status=status, findings=findings, signals=list(dict.fromkeys(signals)))


def status_of(severities: Iterable[str]) -> str:
    severities = set(severities)
    if INVALID in severities:
        return STATUS_INVALID
    if REVIEW in severities:
        return STATUS_REVIEW
    return STATUS_VALID


def rules_table() -> List[dict]:
    """The rule catalogue (for the admin page and the docs)."""
    return [{"code": code, "tier": r.tier, "severity": r.severity, "confirmed": r.confirmed,
             "promotable": code in PROMOTABLE, "note": r.note} for code, r in RULES.items()]
