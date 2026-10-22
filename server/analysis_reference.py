"""
analysis_reference.py — which lap to compare against (V0.9.1). No HTTP: plain
functions over a database session, so every rule is unit-testable.

Kinds of reference
------------------
  "wr"         The fastest VALID lap by anyone on the same track layout and the
               same car, i.e. exactly the record that records.py calls WR (it is
               what produces the WR record events, so the two never disagree).
               Garage16 has no official world record — this is the fastest lap
               ON GARAGE16 — so the UI calls it "Garage16 Best", not "World
               Record" (see routers/analysis.py and the user docs).
  "team_best"  The fastest valid lap of anyone who shares a team with the viewer.
  "my_best"    The viewer's own fastest valid lap.

"Same car" is records._car_identity_filter's rule: same car_model, or — for laps
from clients older than V0.6.3 that have no car_model — same car_name. By default
that is also the scope of the comparison ("model"); scope="class" widens it to the
whole car class (Hypercar, LMGT3, ...), which is the scope of the main leaderboard.

Never a wrong lap
-----------------
When there is nothing suitable the result says WHY (a reason code) instead of
falling back to some other lap: no comparable lap, viewer in no team, team has no
lap here, and so on. The one deliberate substitution: if the best lap IS the lap
the viewer is already looking at, it is not offered a second time — the next
fastest one is returned instead and `already_best` says so.

Authorisation: every candidate is a valid lap (any signed-in driver may open
those) or the viewer's own — the same rule as routers/analysis.py. Laps whose
telemetry file is gone are skipped, so a reference can always be opened.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, List, Optional

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from .models import Driver, Lap, TeamMembership
from .official import official_filter, visible_filter

KINDS = ("wr", "team_best", "my_best")
SCOPES = ("model", "class")
CANDIDATES_TO_TRY = 8  # fastest N laps are tried until one has a readable file

# Reason codes (the client turns them into text)
NO_LAP = "no_lap"                  # nothing comparable exists
NO_TEAM = "no_team"                # viewer is in no team
NO_TEAM_LAP = "no_team_lap"        # the team has no valid lap here
NO_OWN_LAP = "no_own_lap"          # viewer has no valid lap here
ALREADY_BEST = "already_best"      # best lap is the one being viewed, nothing else to offer


@dataclass(frozen=True)
class LapContext:
    """What a comparison has to match."""

    track_name: str
    car_model: Optional[str]
    car_name: str
    car_class: Optional[str]

    @classmethod
    def of(cls, lap: Lap) -> "LapContext":
        return cls(lap.track_name, lap.car_model, lap.car_name, lap.car_class)


@dataclass
class ReferenceResult:
    kind: str
    lap: Optional[Lap]
    reason: Optional[str] = None
    already_best: bool = False


def _same_car(context: LapContext, scope: str):
    if scope == "class" and context.car_class:
        return Lap.car_class == context.car_class
    if context.car_model is not None:
        return Lap.car_model == context.car_model
    return and_(Lap.car_model.is_(None), Lap.car_name == context.car_name)


def _fastest(
    db: Session, context: LapContext, scope: str, *driver_filter, limit: int = CANDIDATES_TO_TRY
) -> List[Lap]:
    stmt = (
        select(Lap)
        .where(official_filter(), Lap.track_name == context.track_name, _same_car(context, scope), *driver_filter)
        .order_by(Lap.lap_time.asc(), Lap.id.asc())
        .limit(limit)
    )
    return list(db.execute(stmt).scalars().all())


def team_driver_ids(db: Session, driver_id: int) -> List[int]:
    """Everyone who shares at least one team with `driver_id` (including the driver)."""
    team_ids = select(TeamMembership.team_id).where(TeamMembership.driver_id == driver_id)
    rows = db.execute(select(TeamMembership.driver_id).where(TeamMembership.team_id.in_(team_ids)).distinct()).all()
    return sorted({row[0] for row in rows} | {driver_id})


def resolve_reference(
    db: Session,
    viewer: Driver,
    kind: str,
    context: LapContext,
    *,
    scope: str = "model",
    exclude_lap_id: Optional[int] = None,
    analysable: Callable[[Lap], bool] = lambda lap: True,
) -> ReferenceResult:
    if kind not in KINDS:
        raise ValueError(f"unknown reference kind: {kind}")
    if scope not in SCOPES:
        raise ValueError(f"unknown scope: {scope}")

    if kind == "wr":
        candidates = _fastest(db, context, scope)
        empty_reason = NO_LAP
    elif kind == "my_best":
        candidates = _fastest(db, context, scope, Lap.driver_id == viewer.id)
        empty_reason = NO_OWN_LAP
    else:  # team_best
        members = team_driver_ids(db, viewer.id)
        if members == [viewer.id]:
            # Only the viewer: is that "no team" or "a team of one"? Ask the table.
            in_team = db.execute(
                select(TeamMembership.id).where(TeamMembership.driver_id == viewer.id).limit(1)
            ).first()
            if in_team is None:
                return ReferenceResult(kind, None, NO_TEAM)
        candidates = _fastest(db, context, scope, Lap.driver_id.in_(members))
        empty_reason = NO_TEAM_LAP

    already_best = False
    for lap in candidates:
        if exclude_lap_id is not None and lap.id == exclude_lap_id:
            already_best = True
            continue
        if not analysable(lap):
            continue
        return ReferenceResult(kind, lap, None, already_best)

    if already_best:
        return ReferenceResult(kind, None, ALREADY_BEST, True)
    return ReferenceResult(kind, None, empty_reason)


@dataclass
class QuickPair:
    mode: str
    lap_a: Optional[Lap]
    reference: ReferenceResult

    @property
    def lap_b(self) -> Optional[Lap]:
        return self.reference.lap


QUICK_MODES = {"wr": "wr", "team_best": "team_best", "my_best": "my_best"}


def quick_pair(
    db: Session,
    viewer: Driver,
    mode: str,
    track_name: Optional[str] = None,
    *,
    car_model: Optional[str] = None,
    scope: str = "model",
    analysable: Callable[[Lap], bool] = lambda lap: True,
) -> QuickPair:
    """"You vs ..." in one step, so nobody has to look up two lap ids.

    Lap A is the viewer's best valid lap at the track (optionally for one car
    model) — except for "my_best", where A is the viewer's most RECENT valid lap
    (comparing your best with your best would be pointless) and B their best.
    Lap B is the reference of `mode` for lap A's track and car.
    Without `track_name`, the track (and car) of the viewer's most recent valid lap.
    """
    if mode not in QUICK_MODES:
        raise ValueError(f"unknown quick mode: {mode}")
    if not track_name:
        # No track given ("You vs ..." from the analysis start page): use the one the
        # viewer drove most recently, and that lap's car.
        recent = db.execute(
            select(Lap).where(Lap.driver_id == viewer.id, visible_filter())
            .order_by(Lap.uploaded_at.desc(), Lap.id.desc()).limit(CANDIDATES_TO_TRY)
        ).scalars().all()
        latest = next((lap for lap in recent if analysable(lap)), None)
        if latest is None:
            return QuickPair(mode, None, ReferenceResult(mode, None, NO_OWN_LAP))
        track_name = latest.track_name
        car_model = car_model or latest.car_model
    own = [Lap.driver_id == viewer.id, visible_filter(), Lap.track_name == track_name]
    if car_model:
        own.append(Lap.car_model == car_model)
    order = (Lap.uploaded_at.desc(), Lap.id.desc()) if mode == "my_best" else (Lap.lap_time.asc(), Lap.id.asc())
    own_laps = db.execute(select(Lap).where(*own).order_by(*order).limit(CANDIDATES_TO_TRY)).scalars().all()
    lap_a = next((lap for lap in own_laps if analysable(lap)), None)
    if lap_a is None:
        return QuickPair(mode, None, ReferenceResult(mode, None, NO_OWN_LAP))
    result = resolve_reference(
        db, viewer, QUICK_MODES[mode], LapContext.of(lap_a),
        scope=scope, exclude_lap_id=lap_a.id, analysable=analysable,
    )
    return QuickPair(mode, lap_a, result)
