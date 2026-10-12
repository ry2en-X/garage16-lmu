"""Stint Analyzer: laps.lap_context and race_rule_sets (V0.9.4)

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-12

* laps.lap_context (JSON, nullable): the car and the session at the end of the lap as the
  0.9.4 client reports it — pit stops so far, tyre compound, damage, tank size, position, time
  left in the session. Laps of older clients keep NULL; nothing is back-filled or guessed (the
  Stint Analyzer then falls back to what lap_stats measured: pit lane, fuel, tyre wear).
* race_rule_sets: an admin's race rules for the strategy calculator.

Purely additive — no existing value changes. Downgrade drops both.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: Union[str, None] = "0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("laps") as batch_op:
        batch_op.add_column(sa.Column("lap_context", sa.JSON(), nullable=True))
    op.create_table(
        "race_rule_sets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False, unique=True),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("track_name", sa.String(), nullable=True),
        sa.Column("car_class", sa.String(), nullable=True),
        sa.Column("rules", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_race_rule_sets_track_name", "race_rule_sets", ["track_name"])


def downgrade() -> None:
    op.drop_index("ix_race_rule_sets_track_name", table_name="race_rule_sets")
    op.drop_table("race_rule_sets")
    with op.batch_alter_table("laps") as batch_op:
        batch_op.drop_column("lap_context")
