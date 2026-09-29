"""add drivers.token_revoked_at (token revocation)

Supports POST /accounts/revoke and the rotate-token/rotate-secret flow in
server/routers/accounts.py: get_current_driver() now rejects any request
authenticated with a token whose driver has token_revoked_at set.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-16
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("drivers", sa.Column("token_revoked_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("drivers", "token_revoked_at")
