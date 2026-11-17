"""fair_play.py — a second look at records set by new drivers (V1.0.1).

With open registration anybody can create an account, and the desktop client holds the account's
signing key: the signature proves WHICH account sent a lap, not that the lap was really driven.
What protects the leaderboards is the server's own check of the telemetry (server/lap_check.py).
This adds one cheap rule on top, for the case that matters most:

    a driver with fewer than LMU_GARAGE_NEW_DRIVER_REVIEW_LAPS official laps
    whose lap would beat the current Garage16 Best (track + car model) of OTHER drivers
    → the lap is held for an admin (review_status "held", Admin → Review),
      exactly like a lap the track-limits check could not decide.

The lap stays visible and analysable; only its records (and Discord announcements) wait until an
admin clears it (routers/admin.py review_lap). The first lap ever on a track and car is not held —
there is no one to overtake. 0 turns the rule off.
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from .models import Lap
from .official import HELD, is_official, official_filter
from .records import _car_identity_filter

REASON = "new_driver_record"


def hold_reason(db: Session, lap: Lap, review_laps: int) -> Optional[dict]:
    """What to record when `lap` (flushed, not yet committed) must be held, else None."""
    if review_laps <= 0 or not is_official(lap):
        return None
    earlier = (db.query(func.count(Lap.id))
               .filter(official_filter(), Lap.driver_id == lap.driver_id, Lap.id != lap.id)
               .scalar() or 0)
    if earlier >= review_laps:
        return None
    best_of_others = (db.query(func.min(Lap.lap_time))
                      .filter(official_filter(), Lap.track_name == lap.track_name, _car_identity_filter(lap),
                              Lap.driver_id != lap.driver_id, Lap.id != lap.id)
                      .scalar())
    if best_of_others is None or lap.lap_time >= best_of_others:
        return None
    return {"reason": REASON, "official_laps_before": int(earlier), "review_below": int(review_laps),
            "garage16_best_before": float(best_of_others)}


def apply(db: Session, lap: Lap, review_laps: int) -> bool:
    """Holds `lap` if hold_reason() says so (sets review_status and validation["fair_play"])."""
    reason = hold_reason(db, lap, review_laps)
    if reason is None:
        return False
    lap.review_status = HELD
    validation = dict(lap.validation or {})
    validation["fair_play"] = reason
    lap.validation = validation
    return True
