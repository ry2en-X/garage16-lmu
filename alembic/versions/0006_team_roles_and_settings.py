"""add team_memberships.role, teams.description, teams.discord_announcements_enabled

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-27
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("team_memberships") as batch_op:
        # "owner" | "admin" | "member" — see server/routers/teams.py's
        # permission helpers. Existing rows (pre-migration memberships)
        # default to "member"; a follow-up data migration below promotes
        # each team's earliest member to "owner" so no team is left
        # without one.
        batch_op.add_column(sa.Column("role", sa.String(), nullable=False, server_default="member"))

    with op.batch_alter_table("teams") as batch_op:
        batch_op.add_column(sa.Column("description", sa.String(), nullable=True))
        # Per-team opt-out for TEAM_BEST Discord announcements — see
        # discord_bot/bot.py's _post_pending_events(). Defaults to True
        # (existing behavior unchanged) so this is non-breaking.
        batch_op.add_column(
            sa.Column("discord_announcements_enabled", sa.Boolean(), nullable=False, server_default=sa.true())
        )

    # Data migration: every existing team needs exactly one owner. Promote
    # whichever membership row has the lowest id per team (i.e. whoever
    # joined first — the closest available proxy for "created the team",
    # since team creation didn't record that explicitly before this
    # migration).
    conn = op.get_bind()
    team_ids = [row[0] for row in conn.execute(sa.text("SELECT id FROM teams"))]
    for team_id in team_ids:
        first_membership_id = conn.execute(
            sa.text(
                "SELECT id FROM team_memberships WHERE team_id = :team_id ORDER BY id ASC LIMIT 1"
            ),
            {"team_id": team_id},
        ).scalar()
        if first_membership_id is not None:
            conn.execute(
                sa.text("UPDATE team_memberships SET role = 'owner' WHERE id = :id"),
                {"id": first_membership_id},
            )


def downgrade() -> None:
    with op.batch_alter_table("teams") as batch_op:
        batch_op.drop_column("discord_announcements_enabled")
        batch_op.drop_column("description")
    with op.batch_alter_table("team_memberships") as batch_op:
        batch_op.drop_column("role")
