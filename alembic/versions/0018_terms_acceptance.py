"""Terms acceptance at sign-up (V1.0)

Revision ID: 0018
Revises: 0017
Create Date: 2026-10-24

* drivers.terms_accepted_at / drivers.terms_version: when and which version of the terms of use
  and privacy policy the driver accepted on the website's sign-up form (POST /accounts/signup).
  Null for every account created before V1.0.

Purely additive. Downgrade drops the two columns.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: Union[str, None] = "0017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("drivers") as batch:
        batch.add_column(sa.Column("terms_accepted_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("terms_version", sa.String(length=32), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("drivers") as batch:
        batch.drop_column("terms_version")
        batch.drop_column("terms_accepted_at")
