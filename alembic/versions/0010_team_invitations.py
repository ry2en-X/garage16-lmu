"""team_invitations — real invitation lifecycle (V0.7.2 §9.2)

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-27

Addressed by invited_driver_id only (not invited_email — see this
migration's accompanying model docstring in server/models.py for why
that's a deliberate scope decision, not an oversight). The existing
Team.invite_code system is untouched and keeps working exactly as
before — this is an addition, not a replacement, per the spec's own
"das bestehende Join-Code-System kann weiterhin existieren".
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "team_invitations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("team_id", sa.Integer(), sa.ForeignKey("teams.id"), nullable=False),
        sa.Column("invited_driver_id", sa.Integer(), sa.ForeignKey("drivers.id"), nullable=False),
        sa.Column("created_by_driver_id", sa.Integer(), sa.ForeignKey("drivers.id"), nullable=False),
        # pending | accepted | declined | expired | revoked — see
        # server/models.py's TeamInvitation docstring for the state
        # machine. Plain String, not a DB enum: consistent with how this
        # project already does RecordEvent.record_type and
        # TeamMembership.role (no DB-level enum type anywhere in this
        # schema), and keeps a future status value a plain migration
        # rather than an enum-alteration one (Postgres enum changes are
        # notoriously annoying).
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("accepted_at", sa.DateTime(), nullable=True),
        sa.Column("declined_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_team_invitations_team_id", "team_invitations", ["team_id"])
    op.create_index("ix_team_invitations_invited_driver_id", "team_invitations", ["invited_driver_id"])
    # Not a DB-level uniqueness constraint on (team_id, invited_driver_id,
    # status='pending') — SQLite/PostgreSQL partial-index syntax differs
    # enough that a single portable Alembic op isn't clean, and the
    # actual invariant ("don't create a second pending invite for someone
    # already pending") is cheap to enforce at the application level (one
    # extra SELECT before the INSERT, in routers/teams.py) without that
    # complexity. Same tradeoff this project already made for
    # Driver.email's case-insensitive uniqueness before migration 0008
    # existed for teams.name.


def downgrade() -> None:
    op.drop_index("ix_team_invitations_invited_driver_id", table_name="team_invitations")
    op.drop_index("ix_team_invitations_team_id", table_name="team_invitations")
    op.drop_table("team_invitations")
