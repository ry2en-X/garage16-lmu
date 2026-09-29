"""initial baseline — mirrors server/models.py as of the current schema

This migration is a *baseline*: it creates all six tables exactly as
server/models.py currently defines them (including drivers.discord_user_id,
teams, team_memberships, discord_channels, link_codes, record_events).

- Brand-new/empty dev DB: `alembic upgrade head` runs this and creates
  everything from scratch (in addition to, or instead of, the existing
  Base.metadata.create_all() call in server/main.py — running both is
  harmless since create_all only fills in missing tables).
- An existing dev DB that already has all of these tables (e.g. one that
  was bootstrapped via create_all before Alembic existed): don't run
  `upgrade`, run `alembic stamp head` instead. That just records "this DB
  is at revision 0001" in the alembic_version table without touching any
  tables — which is exactly what you want when the tables already match.
- An existing DB that is missing newer columns (the original problem this
  migration exists to solve): create/adjust future revisions with
  `alembic revision --autogenerate -m "..."` on top of this one.

Revision ID: 0001
Revises:
Create Date: 2026-09-16
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "drivers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("display_name", sa.String(), nullable=False),
        sa.Column("token_hash", sa.String(), nullable=False),
        sa.Column("client_secret", sa.String(), nullable=False),
        sa.Column("discord_user_id", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_drivers_token_hash", "drivers", ["token_hash"], unique=True)
    op.create_index("ix_drivers_discord_user_id", "drivers", ["discord_user_id"], unique=True)

    op.create_table(
        "teams",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("invite_code", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_teams_name", "teams", ["name"], unique=True)
    op.create_index("ix_teams_invite_code", "teams", ["invite_code"], unique=True)

    op.create_table(
        "laps",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("driver_id", sa.Integer(), sa.ForeignKey("drivers.id"), nullable=False),
        sa.Column("track_name", sa.String(), nullable=False),
        sa.Column("car_name", sa.String(), nullable=False),
        sa.Column("session_type", sa.Integer(), nullable=False),
        sa.Column("lap_number", sa.Integer(), nullable=False),
        sa.Column("lap_time", sa.Float(), nullable=False),
        sa.Column("sector_times", sa.JSON(), nullable=False),
        sa.Column("is_valid", sa.Boolean(), nullable=False),
        sa.Column("started_at", sa.Float(), nullable=False),
        sa.Column("recorded_at", sa.Integer(), nullable=False),
        sa.Column("ambient_temp", sa.Float(), nullable=True),
        sa.Column("track_temp", sa.Float(), nullable=True),
        sa.Column("sample_count", sa.Integer(), nullable=True),
        sa.Column("telemetry_hash", sa.String(), nullable=False),
        sa.Column("telemetry_path", sa.String(), nullable=False),
        sa.Column("client_version", sa.String(), nullable=True),
        sa.Column("sent_at", sa.Float(), nullable=True),
        sa.Column("uploaded_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("driver_id", "telemetry_hash", name="uq_driver_telemetry_hash"),
    )
    op.create_index("ix_laps_driver_id", "laps", ["driver_id"])
    op.create_index("ix_laps_track_name", "laps", ["track_name"])
    op.create_index("ix_laps_car_name", "laps", ["car_name"])
    op.create_index("ix_laps_lap_time", "laps", ["lap_time"])
    op.create_index("ix_laps_telemetry_hash", "laps", ["telemetry_hash"])

    op.create_table(
        "team_memberships",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("team_id", sa.Integer(), sa.ForeignKey("teams.id"), nullable=False),
        sa.Column("driver_id", sa.Integer(), sa.ForeignKey("drivers.id"), nullable=False),
        sa.Column("joined_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("team_id", "driver_id", name="uq_team_driver"),
    )
    op.create_index("ix_team_memberships_team_id", "team_memberships", ["team_id"])
    op.create_index("ix_team_memberships_driver_id", "team_memberships", ["driver_id"])

    op.create_table(
        "discord_channels",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("team_id", sa.Integer(), sa.ForeignKey("teams.id"), nullable=True),
        sa.Column("guild_id", sa.String(), nullable=False),
        sa.Column("channel_id", sa.String(), nullable=False),
        sa.Column("registered_by", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_discord_channels_team_id", "discord_channels", ["team_id"])
    op.create_index("ix_discord_channels_guild_id", "discord_channels", ["guild_id"])
    op.create_index("ix_discord_channels_channel_id", "discord_channels", ["channel_id"], unique=True)

    op.create_table(
        "link_codes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("driver_id", sa.Integer(), sa.ForeignKey("drivers.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("used_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_link_codes_code", "link_codes", ["code"], unique=True)

    op.create_table(
        "record_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("lap_id", sa.Integer(), sa.ForeignKey("laps.id"), nullable=False),
        sa.Column("driver_id", sa.Integer(), sa.ForeignKey("drivers.id"), nullable=False),
        sa.Column("team_id", sa.Integer(), sa.ForeignKey("teams.id"), nullable=True),
        sa.Column("record_type", sa.String(), nullable=False),
        sa.Column("track_name", sa.String(), nullable=False),
        sa.Column("car_name", sa.String(), nullable=False),
        sa.Column("lap_time", sa.Float(), nullable=False),
        sa.Column("previous_best", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("announced_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_record_events_lap_id", "record_events", ["lap_id"])
    op.create_index("ix_record_events_driver_id", "record_events", ["driver_id"])
    op.create_index("ix_record_events_team_id", "record_events", ["team_id"])
    op.create_index("ix_record_events_created_at", "record_events", ["created_at"])


def downgrade() -> None:
    op.drop_table("record_events")
    op.drop_table("link_codes")
    op.drop_table("discord_channels")
    op.drop_table("team_memberships")
    op.drop_table("laps")
    op.drop_table("teams")
    op.drop_table("drivers")
