from __future__ import annotations

from typing import List, Optional

from sqlalchemy import and_, func
from sqlalchemy.orm import Session

from .models import Lap, RecordEvent, TeamMembership


def _car_identity_filter(lap: Lap):
    """What counts as "the same car" for PR/WR/TEAM_BEST purposes.

    Must match routers/leaderboard.py's car_leaderboard() and
    routers/teams.py's team-best/team-records queries — both compare by
    car_model (the exact vehicle model, ignoring team/livery/car number,
    which car_name still carries — see models.py). Using car_name here
    instead (the pre-V0.6.9 bug) meant a driver switching liveries or car
    numbers on the same model started a fresh PR/WR/TEAM_BEST chain, and
    a lap could be announced as a new TEAM_BEST while the team's own
    displayed record (car_model-grouped) didn't actually change — two
    parallel, disagreeing notions of "record" from one Lap table.

    car_model is NULL for laps from clients older than V0.6.3 (see
    leaderboard.py's module docstring) — those fall back to the old
    car_name comparison so their existing PR/WR history doesn't silently
    reset the day this ships.
    """
    if lap.car_model is not None:
        return Lap.car_model == lap.car_model
    return and_(Lap.car_model.is_(None), Lap.car_name == lap.car_name)


def _prior_best(db: Session, lap: Lap, **extra_filters) -> Optional[float]:
    query = db.query(func.min(Lap.lap_time)).filter(
        Lap.is_valid.is_(True),
        Lap.track_name == lap.track_name,
        _car_identity_filter(lap),
        Lap.id != lap.id,
    )
    for column, value in extra_filters.items():
        query = query.filter(getattr(Lap, column) == value)
    return query.scalar()


def evaluate_and_record(db: Session, lap: Lap) -> List[RecordEvent]:
    """Stage PR/WR/TEAM_BEST RecordEvents for `lap` in the current session.

    Deliberately does NOT commit or rollback: the caller (upload_lap) owns
    the transaction boundary so the lap row and its record events either
    land together or not at all. Callers must commit (and roll back on
    failure) themselves.

    Race-safety: two uploads racing to set the same record could each read
    the same "current best" before either commits, and both decide they've
    set a new record. This is prevented not here but at the transaction
    level — upload_lap (routers/telemetry.py) opens its transaction with
    `BEGIN IMMEDIATE` on SQLite, which serializes the whole read-compare-
    write sequence across concurrent uploads instead of just the final
    write. See that module for the reasoning.
    """
    if not lap.is_valid:
        return []

    # car_name here is purely the human-readable label stored on the
    # RecordEvent row (e.g. for the Discord announcement text) — it is
    # NOT used to decide whether this lap matches a prior one; that
    # decision is _car_identity_filter()'s job (car_model-based).
    common = {"track_name": lap.track_name, "car_name": lap.car_name}
    events: List[RecordEvent] = []
    prior_pb = _prior_best(db, lap, driver_id=lap.driver_id)
    if prior_pb is None or lap.lap_time < prior_pb:
        events.append(RecordEvent(
            lap_id=lap.id, driver_id=lap.driver_id, team_id=None,
            record_type="PR", lap_time=lap.lap_time, previous_best=prior_pb, **common,
        ))

    prior_wr = _prior_best(db, lap)
    if prior_wr is None or lap.lap_time < prior_wr:
        events.append(RecordEvent(
            lap_id=lap.id, driver_id=lap.driver_id, team_id=None,
            record_type="WR", lap_time=lap.lap_time, previous_best=prior_wr, **common,
        ))

    team_ids = db.query(TeamMembership.team_id).filter(
        TeamMembership.driver_id == lap.driver_id
    ).all()
    for (team_id,) in team_ids:
        prior_team_best = (
            db.query(func.min(Lap.lap_time))
            .join(TeamMembership, TeamMembership.driver_id == Lap.driver_id)
            .filter(
                TeamMembership.team_id == team_id,
                Lap.is_valid.is_(True),
                Lap.track_name == lap.track_name,
                _car_identity_filter(lap),
                Lap.id != lap.id,
            )
            .scalar()
        )
        if prior_team_best is None or lap.lap_time < prior_team_best:
            events.append(RecordEvent(
                lap_id=lap.id, driver_id=lap.driver_id, team_id=team_id,
                record_type="TEAM_BEST", lap_time=lap.lap_time,
                previous_best=prior_team_best, **common,
            ))

    if events:
        db.add_all(events)
        db.flush()
    return events
