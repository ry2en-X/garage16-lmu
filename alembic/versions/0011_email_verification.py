"""email verification (V0.8-NAS §2)

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-27

Adds drivers.email_verified / email_verified_at, and a
email_verification_tokens table — same hashed-at-rest, single-use,
expiring pattern already used for password_reset_tokens (migration 0007)
and team_invitations (migration 0010).

Backward compatible by construction: every existing driver with an email
already set (from V0.6.9's set-password, before this feature existed)
gets email_verified=False by the column default below. This does NOT
retroactively lock anyone out — login still works unverified (see
routers/accounts.py's login() docstring for that decision) — it just
means an existing driver will see a "please verify" prompt in Account
Settings after this migration runs. No forced re-verification flow, no
account lockout as a side effect of shipping this feature late.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("drivers") as batch_op:
        batch_op.add_column(sa.Column("email_verified", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.add_column(sa.Column("email_verified_at", sa.DateTime(), nullable=True))

    op.create_table(
        "email_verification_tokens",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("token_hash", sa.String(), nullable=False),
        sa.Column("driver_id", sa.Integer(), sa.ForeignKey("drivers.id"), nullable=False),
        # The email address this token verifies — captured at request
        # time, not read from drivers.email at confirm time. Matters for
        # the "changed email again before confirming the first one"
        # case: confirming an old token must not verify whatever email
        # happens to be on the driver row NOW.
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("used_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_email_verification_tokens_token_hash", "email_verification_tokens", ["token_hash"], unique=True)
    op.create_index("ix_email_verification_tokens_driver_id", "email_verification_tokens", ["driver_id"])


def downgrade() -> None:
    op.drop_index("ix_email_verification_tokens_driver_id", table_name="email_verification_tokens")
    op.drop_index("ix_email_verification_tokens_token_hash", table_name="email_verification_tokens")
    op.drop_table("email_verification_tokens")
    with op.batch_alter_table("drivers") as batch_op:
        batch_op.drop_column("email_verified_at")
        batch_op.drop_column("email_verified")
