"""car model identity, track-limits verdicts, per-lap figures (V0.9.3)

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-10

1. Car identity (server/car_identity.py). Up to V0.9.2 laps.car_model held LMU's raw
   veh_filename, which is per livery ("397_25_499P" = car #397, season 25, model 499P),
   so every car number counted as a different car. Now:
     laps.car_model_raw        what car_model held until now (kept, never changed)
     laps.car_vehicle_model    LMU's model name (only clients 0.9.3+ send it)
     laps.car_identity_source  how car_model was decided
     laps.car_model            the MODEL identity — for existing laps the veh_filename
                               without the car number, season kept: "499P (2025)"
     car_aliases               veh_filename group -> model name, learned from uploads
   Laps keep their exact grouping data: car_model_raw holds the original, so the
   identity can always be re-derived (scripts/car_identity_report.py) and the downgrade
   restores car_model byte for byte.

2. Track limits and review (server/track_limits.py): laps.validation (JSON verdict with
   every finding), laps.review_status. Existing laps keep their is_valid unchanged; they
   simply have no verdict until scripts/revalidate_laps.py is run (dry run by default).

3. Per-lap figures (server/lap_insights.py): laps.lap_stats (JSON). Filled for new laps
   at upload; scripts/revalidate_laps.py --stats fills it for existing ones.

The fallback rule is COPIED here on purpose (like 0012's class table): a migration must
keep producing the same result after the application code changes.
tests/test_v093_car_identity.py pins this copy to server/car_identity.py.
"""
from __future__ import annotations

import re
from typing import Optional, Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: Union[str, None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# KEEP IN SYNC with server/car_identity.py (enforced by a test).
_PATTERN = re.compile(r"^(?P<number>\d{1,4})_(?P<season>\d{2})_(?P<model>.+)$")
_SPACES = re.compile(r"\s+")


def _clean(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    text = _SPACES.sub(" ", str(value)).strip()
    if not text or not text.isprintable():
        return None
    return text[:100]


def fallback_identity(veh_file: Optional[str]) -> Optional[str]:
    text = _clean(veh_file)
    if text is None:
        return None
    match = _PATTERN.match(text)
    if match:
        model = _clean(match.group("model").replace("_", " "))
        if model:
            return _clean(f"{model} ({2000 + int(match.group('season'))})")
    return text


def upgrade() -> None:
    with op.batch_alter_table("laps") as batch_op:
        batch_op.add_column(sa.Column("car_model_raw", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("car_vehicle_model", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("car_identity_source", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("validation", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("review_status", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("lap_stats", sa.JSON(), nullable=True))
        batch_op.create_index("ix_laps_car_identity_source", ["car_identity_source"])
        batch_op.create_index("ix_laps_review_status", ["review_status"])

    op.create_table(
        "car_aliases",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("alias_key", sa.String(), nullable=False),
        sa.Column("canonical", sa.String(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("conflict", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("conflict_with", sa.String(), nullable=True),
        sa.Column("seen_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("rules_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("first_seen", sa.DateTime(), nullable=True),
        sa.Column("last_seen", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_car_aliases_alias_key", "car_aliases", ["alias_key"], unique=True)

    conn = op.get_bind()
    conn.execute(sa.text("UPDATE laps SET car_model_raw = car_model WHERE car_model IS NOT NULL"))
    for (raw,) in conn.execute(sa.text("SELECT DISTINCT car_model_raw FROM laps WHERE car_model_raw IS NOT NULL")).fetchall():
        identity = fallback_identity(raw)
        if identity is None:
            continue
        conn.execute(
            sa.text("UPDATE laps SET car_model = :identity, car_identity_source = 'veh_file' WHERE car_model_raw = :raw"),
            {"identity": identity, "raw": raw},
        )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("UPDATE laps SET car_model = car_model_raw WHERE car_model_raw IS NOT NULL"))
    # Laps uploaded after the upgrade by clients that sent only a model name (no
    # veh_filename) keep that name — the 0.9.2 schema has nowhere else to put it.
    op.drop_index("ix_car_aliases_alias_key", table_name="car_aliases")
    op.drop_table("car_aliases")
    with op.batch_alter_table("laps") as batch_op:
        batch_op.drop_index("ix_laps_review_status")
        batch_op.drop_index("ix_laps_car_identity_source")
        batch_op.drop_column("lap_stats")
        batch_op.drop_column("review_status")
        batch_op.drop_column("validation")
        batch_op.drop_column("car_identity_source")
        batch_op.drop_column("car_vehicle_model")
        batch_op.drop_column("car_model_raw")
