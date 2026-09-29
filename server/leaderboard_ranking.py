"""
leaderboard_ranking.py — the ranked-leaderboard query, shared between
routers/leaderboard.py (global leaderboards) and routers/teams.py (team
leaderboards, V0.6.8). Extracted here because it's now genuinely used
from two places with identical logic — not a speculative abstraction.
"""

from __future__ import annotations

from typing import List, Sequence

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.orm import Session

from .models import Driver, Lap
from .schemas import LeaderboardEntry


def ranked_leaderboard(db: Session, filters: Sequence[ColumnElement], limit: int) -> List[LeaderboardEntry]:
    """Each driver's single best *valid* lap matching `filters`, fastest
    first. Uses ROW_NUMBER() OVER (PARTITION BY driver_id ORDER BY
    lap_time, id) rather than a "GROUP BY driver_id, then join back on
    matching lap_time" — the latter can return more than one row for the
    same driver whenever two of their laps have the *exact* same
    lap_time (not rare, given rounded lap times), since the join only
    matches on (driver_id, lap_time), and neither condition alone is
    unique. Ranking by row number and keeping only rank 1 guarantees
    exactly one row per driver regardless of ties; the `Lap.id`
    tiebreaker just makes which of two identical times shows up
    deterministic rather than dependent on row storage order.
    """
    ranked = (
        select(
            Lap.id,
            Lap.driver_id,
            Lap.lap_time,
            Lap.sector_times,
            Lap.is_valid,
            Lap.uploaded_at,
            func.row_number()
            .over(partition_by=Lap.driver_id, order_by=(Lap.lap_time.asc(), Lap.id.asc()))
            .label("rank"),
        )
        .where(Lap.is_valid.is_(True), *filters)
        .subquery()
    )

    stmt = (
        select(
            ranked.c.driver_id, ranked.c.lap_time, ranked.c.sector_times, ranked.c.is_valid,
            ranked.c.uploaded_at, Driver.display_name,
        )
        .join(Driver, Driver.id == ranked.c.driver_id)
        .where(ranked.c.rank == 1)
        .order_by(ranked.c.lap_time.asc())
        .limit(min(limit, 100))
    )
    rows = db.execute(stmt).all()
    return [
        LeaderboardEntry(
            driver_id=driver_id,
            driver_name=display_name,
            lap_time=lap_time,
            sector_times=sector_times,
            is_valid=is_valid,
            uploaded_at=uploaded_at.isoformat(),
        )
        for driver_id, lap_time, sector_times, is_valid, uploaded_at, display_name in rows
    ]
