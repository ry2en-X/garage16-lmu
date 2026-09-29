"""add drivers.email, drivers.password_hash, password_reset_tokens

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-26
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("drivers") as batch_op:
        # Both nullable: existing drivers stay valid, token-only accounts
        # (the desktop client never needs one — see server/auth.py) remain
        # supported forever. Non-breaking by construction, same approach
        # as every other migration in this project (0005's car_class/
        # car_model, 0006's team roles).
        batch_op.add_column(sa.Column("email", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("password_hash", sa.String(), nullable=True))
        batch_op.create_index("ix_drivers_email", ["email"], unique=True)

    op.create_table(
        "password_reset_tokens",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("token_hash", sa.String(), nullable=False),
        sa.Column("driver_id", sa.Integer(), sa.ForeignKey("drivers.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("used_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "ix_password_reset_tokens_token_hash", "password_reset_tokens", ["token_hash"], unique=True
    )
    op.create_index(
        "ix_password_reset_tokens_driver_id", "password_reset_tokens", ["driver_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_password_reset_tokens_driver_id", table_name="password_reset_tokens")
    op.drop_index("ix_password_reset_tokens_token_hash", table_name="password_reset_tokens")
    op.drop_table("password_reset_tokens")
    with op.batch_alter_table("drivers") as batch_op:
        batch_op.drop_index("ix_drivers_email")
        batch_op.drop_column("password_hash")
        batch_op.drop_column("email")
