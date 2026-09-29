"""add laps.client_claimed_valid — track the client's own validity claim
separately from the server's authoritative is_valid verdict

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-17
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("laps") as batch_op:
        batch_op.add_column(
            sa.Column("client_claimed_valid", sa.Boolean(), nullable=False, server_default=sa.true())
        )


def downgrade() -> None:
    with op.batch_alter_table("laps") as batch_op:
        batch_op.drop_column("client_claimed_valid")
