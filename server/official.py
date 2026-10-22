"""
official.py — which laps count for leaderboards and records (V0.9.9).

One rule, used by every query that ranks laps or decides a record:

    official = is_valid AND review_status NOT IN ("held", "quarantined")

  held         the lap check said REVIEW (server/lap_decision.py): stored and analysable, but not
               official until an admin clears it — then its record events are evaluated
               (routers/admin.py review_lap).
  quarantined  its file failed the integrity check (server/integrity.py).

Unchanged on purpose: "pending" (laps flagged before V0.9.9) keeps counting provisionally, exactly
as before — existing leaderboards and records do not change on the day of the update. An admin can
hold such a lap explicitly (scripts/revalidate_laps.py --apply --hold).
"""

from __future__ import annotations

from sqlalchemy import or_

from .models import Lap

HELD = "held"
QUARANTINED = "quarantined"
NOT_OFFICIAL = (HELD, QUARANTINED)


def official_filter():
    """SQL condition for `official` (NULL review_status counts)."""
    return Lap.is_valid.is_(True) & or_(Lap.review_status.is_(None), Lap.review_status.not_in(NOT_OFFICIAL))


def is_official(lap) -> bool:
    return bool(lap.is_valid) and lap.review_status not in NOT_OFFICIAL


def visible_filter():
    """Laps other drivers may see and analyse: valid and not quarantined (a held lap is visible —
    it is a normal lap waiting for a decision — a quarantined one only to its owner and admins)."""
    return Lap.is_valid.is_(True) & or_(Lap.review_status.is_(None), Lap.review_status != QUARANTINED)


def is_visible(lap) -> bool:
    return bool(lap.is_valid) and lap.review_status != QUARANTINED
