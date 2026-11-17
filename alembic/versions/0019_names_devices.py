"""Unique driver names and one token per PC (V1.0.1)

Revision ID: 0019
Revises: 0018
Create Date: 2026-10-26

* drivers.display_name_key: the name in its compared form (NFKC, case folded, invisible characters
  removed, whitespace collapsed — server/names.py), unique. Filled here for every driver in the
  order of their id: when two existing drivers already share a name, the OLDER one gets the key;
  the younger keeps its name but no key (NULL) and has to pick a free name at the next rename.
  Nothing is renamed and no row is lost.
* device_tokens: one auth token per connected PC (POST /accounts/desktop-login), so a driver can
  connect the rig and a laptop at the same time. The existing per-driver token keeps working.

Purely additive. Downgrade drops the table and the column.
"""
from __future__ import annotations

import unicodedata
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: Union[str, None] = "0018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _key(name: str) -> str:
    # frozen copy of server/names.py name_key() as of V1.0.1 — a migration must not change later
    text = unicodedata.normalize("NFKC", str(name or ""))
    text = "".join(ch for ch in text if not unicodedata.category(ch).startswith("C") or ch.isspace())
    text = text.casefold()
    return " ".join(text.split())[:120]


def upgrade() -> None:
    with op.batch_alter_table("drivers") as batch:
        batch.add_column(sa.Column("display_name_key", sa.String(length=120), nullable=True))

    bind = op.get_bind()
    drivers = sa.table("drivers", sa.column("id", sa.Integer), sa.column("display_name", sa.String),
                       sa.column("display_name_key", sa.String))
    seen = set()
    for driver_id, name in bind.execute(sa.select(drivers.c.id, drivers.c.display_name).order_by(drivers.c.id)).fetchall():
        key = _key(name)
        if not key or key in seen:
            continue
        seen.add(key)
        bind.execute(drivers.update().where(drivers.c.id == driver_id).values(display_name_key=key))

    op.create_index("ix_drivers_display_name_key", "drivers", ["display_name_key"], unique=True)

    op.create_table(
        "device_tokens",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("driver_id", sa.Integer(), sa.ForeignKey("drivers.id"), nullable=False),
        sa.Column("token_hash", sa.String(), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_device_tokens_driver_id", "device_tokens", ["driver_id"])
    op.create_index("ix_device_tokens_token_hash", "device_tokens", ["token_hash"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_device_tokens_token_hash", table_name="device_tokens")
    op.drop_index("ix_device_tokens_driver_id", table_name="device_tokens")
    op.drop_table("device_tokens")
    op.drop_index("ix_drivers_display_name_key", table_name="drivers")
    with op.batch_alter_table("drivers") as batch:
        batch.drop_column("display_name_key")
