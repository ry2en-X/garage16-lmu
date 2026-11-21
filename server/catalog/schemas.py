"""Response bodies of the catalog endpoints (venues, layouts, class options)."""
from __future__ import annotations

from typing import List

from pydantic import BaseModel


class CatalogLayoutOut(BaseModel):
    track_name: str  # exactly the string LMU sent — this is what leaderboard queries use
    lap_count: int


class CatalogVenueOut(BaseModel):
    """One track with all of its layouts/variants. `layouts` is empty
    for a known track nobody has driven yet — it is still listed, so every
    track exists in the UI before its first lap."""

    venue: str
    lap_count: int
    layouts: List[CatalogLayoutOut]


class CatalogClassOptionOut(BaseModel):
    name: str
    lap_count: int  # valid laps in this class (at the chosen track, if one was given)
