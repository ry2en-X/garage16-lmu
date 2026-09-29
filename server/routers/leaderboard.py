"""
routers/leaderboard.py — Two leaderboard views, per the requested
architecture (V0.6.3):

  - Main leaderboard: track + class (e.g. "Hypercar", "LMGT3") — every
    car within that class competes together. GET /leaderboard/class/...
  - Sub-leaderboard: track + exact car model, ignoring team/livery/car
    number (car_name still carries those; car_model deliberately doesn't
    — see server/models.py). GET /leaderboard/car/...

Both share the same ranking query (each driver's single best *valid* lap
for the given filters, fastest first) — see _ranked_leaderboard.

car_class/car_model come from LMU's own VehicleScoringInfoV01 struct
(vehicle_class / veh_filename — see client/lmu/structs.py), populated by
clients V0.6.3+. Laps from older clients have these as NULL and won't
appear in either of these views — they still show up in a driver's own
/telemetry/laps.
"""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..leaderboard_ranking import ranked_leaderboard
from ..catalog import (
    canonical_class,
    class_sort_key,
    known_class_names,
    known_venue_names,
    venue_for,
    venue_matches_query,
)
from ..models import Driver, Lap
from ..schemas import CatalogClassOptionOut, CatalogLayoutOut, CatalogVenueOut, LeaderboardEntry, RecentActivityEntry

router = APIRouter(prefix="/leaderboard", tags=["leaderboard"])


@router.get("/recent", response_model=List[RecentActivityEntry])
def recent_activity(limit: int = 15, db: Session = Depends(get_db)) -> List[RecentActivityEntry]:
    """Most recent valid laps platform-wide, newest first — the landing
    page's "recent activity" section (V0.7.2). Deliberately simple: no
    per-driver dedup, no filtering by class/car — just what's actually
    been happening lately, same spirit as a team's activity console but
    across the whole platform instead of one team."""
    stmt = (
        select(Lap.driver_id, Driver.display_name, Lap.track_name, Lap.car_name, Lap.lap_time, Lap.uploaded_at)
        .join(Driver, Driver.id == Lap.driver_id)
        .where(Lap.is_valid.is_(True))
        .order_by(Lap.uploaded_at.desc())
        .limit(min(limit, 50))
    )
    rows = db.execute(stmt).all()
    return [
        RecentActivityEntry(
            driver_id=driver_id, driver_name=name, track_name=track, car_name=car,
            lap_time=lap_time, uploaded_at=uploaded_at.isoformat(),
        )
        for driver_id, name, track, car, lap_time, uploaded_at in rows
    ]


@router.get("/class/{track_name}/{car_class}", response_model=List[LeaderboardEntry])
def class_leaderboard(
    track_name: str, car_class: str, limit: int = 20, db: Session = Depends(get_db)
) -> List[LeaderboardEntry]:
    """Main leaderboard: best valid lap per driver for this track, across
    every car model within `car_class` (e.g. all Hypercars together,
    regardless of manufacturer)."""
    return ranked_leaderboard(
        db, [Lap.track_name == track_name, Lap.car_class == canonical_class(car_class)], limit
    )


@router.get("/car/{track_name}/{car_model}", response_model=List[LeaderboardEntry])
def car_leaderboard(
    track_name: str, car_model: str, limit: int = 20, db: Session = Depends(get_db)
) -> List[LeaderboardEntry]:
    """Sub-leaderboard: best valid lap per driver for this exact track +
    car model, ignoring team/livery/car number (those live in car_name,
    not car_model — see server/models.py)."""
    return ranked_leaderboard(
        db, [Lap.track_name == track_name, Lap.car_model == car_model], limit
    )


# --- Catalog: real track/class/car names, discovered from actual uploads.
#
# Deliberately NOT a hand-maintained list of "every car/track LMU has" —
# LMU adds content over time, and keeping a static list in sync with that
# would be exactly the maintenance burden this avoids. Instead: whatever
# values real drivers have actually uploaded ARE the catalog. A brand-new
# car/track shows up here the moment someone's first lap on it lands,
# with zero code changes. The tradeoff: something nobody's driven yet
# obviously won't appear — that's fine, there's no leaderboard for it to
# join anyway until someone does.


@router.get("/catalog/tracks", response_model=List[str])
def catalog_tracks(db: Session = Depends(get_db)) -> List[str]:
    """Every track_name with at least one uploaded lap, alphabetical."""
    rows = db.execute(select(Lap.track_name).distinct().order_by(Lap.track_name)).all()
    return [r[0] for r in rows]


@router.get("/catalog/classes", response_model=List[str])
def catalog_classes(track_name: Optional[str] = None, db: Session = Depends(get_db)) -> List[str]:
    """Every car_class with at least one uploaded lap — optionally scoped
    to one track, which is what the leaderboard UI actually wants (no
    point offering a class nobody's driven at the selected track).
    car_class is NULL for laps from clients older than V0.6.3; those are
    excluded here the same way they're already excluded from the
    leaderboard itself."""
    stmt = select(Lap.car_class).distinct().where(Lap.car_class.is_not(None))
    if track_name:
        stmt = stmt.where(Lap.track_name == track_name)
    rows = db.execute(stmt.order_by(Lap.car_class)).all()
    return [r[0] for r in rows]


@router.get("/catalog/cars", response_model=List[str])
def catalog_cars(
    track_name: Optional[str] = None, car_class: Optional[str] = None, db: Session = Depends(get_db)
) -> List[str]:
    """Every car_model with at least one uploaded lap — optionally scoped
    to one track and/or one class. The car_class filter (V0.7.2 §9.5) is
    what makes a real three-level cascade possible (Track → Class →
    Model, each narrowed by everything picked before it) instead of
    Model only ever being narrowed by Track — without it, picking
    "Hypercar" at Le Mans still offered every GT3 model driven there too.
    See catalog_classes for the same NULL-exclusion note."""
    stmt = select(Lap.car_model).distinct().where(Lap.car_model.is_not(None))
    if track_name:
        stmt = stmt.where(Lap.track_name == track_name)
    if car_class:
        stmt = stmt.where(Lap.car_class == canonical_class(car_class))
    rows = db.execute(stmt.order_by(Lap.car_model)).all()
    return [r[0] for r in rows]


@router.get("/catalog/venues", response_model=List[CatalogVenueOut])
def catalog_venues(q: Optional[str] = None, db: Session = Depends(get_db)) -> List[CatalogVenueOut]:
    """Every track — grouped by venue, with ALL layouts/variants LMU has sent
    (V0.8.9). No manual list to maintain:

      - venues come from a seeded list (server/catalog.py), so a known track
        is listed even before its first lap (`layouts` empty, `lap_count` 0);
      - layouts are exactly the raw track strings found in uploaded laps, so
        every variant appears by itself the first time someone drives it;
      - a track we've never heard of is its own venue — it shows up too.

    `q` searches the venue name, its keywords and its layout names, ignoring
    case, accents and punctuation: q=fuji returns Fuji Speedway with every
    Fuji layout. Only valid laps count (the leaderboards only show those)."""
    rows = db.execute(
        select(Lap.track_name, func.count()).where(Lap.is_valid.is_(True)).group_by(Lap.track_name)
    ).all()
    by_venue: dict = {}
    for track_name, count in rows:
        by_venue.setdefault(venue_for(track_name), []).append(CatalogLayoutOut(track_name=track_name, lap_count=count))
    for known in known_venue_names():
        by_venue.setdefault(known, [])

    out = []
    for venue in sorted(by_venue, key=str.lower):
        layouts = sorted(by_venue[venue], key=lambda item: item.track_name.lower())
        if q and not venue_matches_query(venue, [layout.track_name for layout in layouts], q):
            continue
        out.append(CatalogVenueOut(venue=venue, lap_count=sum(item.lap_count for item in layouts), layouts=layouts))
    return out


@router.get("/catalog/class-options", response_model=List[CatalogClassOptionOut])
def catalog_class_options(track_name: Optional[str] = None, db: Session = Depends(get_db)) -> List[CatalogClassOptionOut]:
    """Every class, always (V0.8.9): the six known LMU classes exist before any
    lap is driven, plus any other class LMU has sent, each with its number of
    valid laps (at `track_name` when given). The UI can then offer the whole
    list and just say "no laps yet" instead of hiding classes nobody drove."""
    stmt = select(Lap.car_class, func.count()).where(Lap.is_valid.is_(True), Lap.car_class.is_not(None))
    if track_name:
        stmt = stmt.where(Lap.track_name == track_name)
    counts = {name: count for name, count in db.execute(stmt.group_by(Lap.car_class)).all()}
    names = set(known_class_names()) | set(counts)
    return [CatalogClassOptionOut(name=name, lap_count=counts.get(name, 0)) for name in sorted(names, key=class_sort_key)]
