"""web sessions (cookie-based), separate from drivers.token_hash

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-27

Fixes a real bug from V0.7.0: POST /accounts/login reused
_issue_fresh_token(), which overwrites drivers.token_hash — the SAME
column the desktop client's auth_token lives in. Logging into the web
app with email+password silently invalidated a driver's running LMU
client (their next upload would 401). This directly violates the
"desktop client auth must never be mixed with web login" requirement.

This table gives the web login flow its own, fully independent credential
domain. drivers.token_hash / client_secret are untouched by anything in
this migration and remain exclusively the desktop client's.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("session_token_hash", sa.String(), nullable=False),
        sa.Column("driver_id", sa.Integer(), sa.ForeignKey("drivers.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        # Best-effort, display-only (account "Sessions" list — spec §3.9);
        # never used for any security decision.
        sa.Column("user_agent", sa.String(), nullable=True),
    )
    op.create_index("ix_sessions_session_token_hash", "sessions", ["session_token_hash"], unique=True)
    op.create_index("ix_sessions_driver_id", "sessions", ["driver_id"])


def downgrade() -> None:
    op.drop_index("ix_sessions_driver_id", table_name="sessions")
    op.drop_index("ix_sessions_session_token_hash", table_name="sessions")
    op.drop_table("sessions")
