"""add moderation support: laps.invalid_reason, drivers.is_locked, lap_reports table

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-24
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("laps") as batch_op:
        # JSON list of human-readable reasons validate_dataframe() failed on
        # (or a moderator's note for an admin-invalidated lap). Nullable —
        # a valid lap has nothing to explain.
        batch_op.add_column(sa.Column("invalid_reason", sa.JSON(), nullable=True))

    with op.batch_alter_table("drivers") as batch_op:
        # Distinct from token_revoked_at: revoking a token is something a
        # driver can do to themselves (lost device); locking is a
        # moderation action. A locked driver's existing token still hashes
        # correctly but get_current_driver() rejects it — see server/auth.py.
        batch_op.add_column(
            sa.Column("is_locked", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch_op.add_column(sa.Column("locked_reason", sa.String(), nullable=True))

    op.create_table(
        "lap_reports",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("lap_id", sa.Integer(), sa.ForeignKey("laps.id"), nullable=False, index=True),
        sa.Column("reported_by_driver_id", sa.Integer(), sa.ForeignKey("drivers.id"), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.Column("resolution", sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("lap_reports")
    with op.batch_alter_table("drivers") as batch_op:
        batch_op.drop_column("locked_reason")
        batch_op.drop_column("is_locked")
    with op.batch_alter_table("laps") as batch_op:
        batch_op.drop_column("invalid_reason")
