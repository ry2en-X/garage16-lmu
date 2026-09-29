"""
discord_bot/models.py — Compatibility re-export.

The bot talks to the same database as the FastAPI server, so it must use
the *same* SQLAlchemy models (and therefore the same declarative Base) as
server/models.py. This module used to contain a second, independent copy
of every model class bound to a separate Base — that worked only by
accident, because discord_bot/bot.py happens to import directly from
server.models instead of from here. Having two Base objects for the same
tables is a real footgun (relationship() lookups, metadata.create_all, and
Alembic autogenerate would only ever see one of the two copies), so this
file now just re-exports the real models. Anything importing
`discord_bot.models` keeps working; there's exactly one source of truth.
"""

from __future__ import annotations

from server.models import (
    DiscordChannel,
    Driver,
    Lap,
    LinkCode,
    RecordEvent,
    Team,
    TeamMembership,
)

__all__ = [
    "DiscordChannel",
    "Driver",
    "Lap",
    "LinkCode",
    "RecordEvent",
    "Team",
    "TeamMembership",
]
