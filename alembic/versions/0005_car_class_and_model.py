"""add laps.car_class and laps.car_model — supports the class-based main
leaderboard (track + class, e.g. Hypercar/GT3) and the model-based
sub-leaderboard (track + exact car model, ignoring team/number/livery)

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-25
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("laps") as batch_op:
        # From VehicleScoringInfoV01.vehicle_class (ABI-verified offset,
        # see client/lmu/structs.py) — e.g. "Hypercar", "LMGT3". Nullable:
        # laps uploaded by clients older than V0.6.3 won't have this.
        batch_op.add_column(sa.Column("car_class", sa.String(), nullable=True))
        # From VehicleScoringInfoV01.veh_filename — the car's internal
        # model identifier, distinct from car_name/vehicle_name (which
        # includes team, livery and car number). Nullable for the same
        # pre-V0.6.3 reason.
        batch_op.add_column(sa.Column("car_model", sa.String(), nullable=True))

    # Both are query filters for the leaderboard endpoints (see
    # server/routers/leaderboard.py) — index them like track_name/car_name.
    op.create_index("ix_laps_car_class", "laps", ["car_class"])
    op.create_index("ix_laps_car_model", "laps", ["car_model"])


def downgrade() -> None:
    op.drop_index("ix_laps_car_model", table_name="laps")
    op.drop_index("ix_laps_car_class", table_name="laps")
    with op.batch_alter_table("laps") as batch_op:
        batch_op.drop_column("car_model")
        batch_op.drop_column("car_class")
