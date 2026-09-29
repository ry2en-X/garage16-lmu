"""readable car classes + keep LMU's raw class string (V0.8.9)

Revision ID: 0012
Revises: 0011
Create Date: 2026-09-28

LMU reports its top class as "Hyper"; the web UI should say "Hypercar" (and
name the other classes consistently). laps.car_class now holds the readable
name; the string LMU actually sent moves to the new laps.car_class_raw so the
mapping can always be re-derived or corrected from the source.

Data migration: every existing lap gets car_class_raw = its old car_class,
then car_class is rewritten through the same alias table the server uses
(server/catalog.py). Unknown classes are left exactly as they were. The alias
table is copied here on purpose — a migration must keep working even after
the application code it was written against changes; tests/
test_readable_classes.py pins this copy to server/catalog.py so they can't
drift apart unnoticed.

Downgrade restores car_class from car_class_raw and drops the column, so the
data round-trips.
"""
from __future__ import annotations

import re
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: Union[str, None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# alias (uppercase letters+digits only) -> readable name. KEEP IN SYNC with
# server/catalog.py CLASSES (enforced by a test).
_ALIASES = {
    "HYPER": "Hypercar", "HYPERCAR": "Hypercar",
    "LMP2WEC": "LMP2 WEC",
    "LMP2ELMS": "LMP2 ELMS",
    "LMP3": "LMP3",
    "GTE": "GTE", "LMGTE": "GTE",
    "GT3": "GT3", "LMGT3": "GT3",
}


def _canonical(raw: str) -> str:
    cleaned = raw.strip()
    return _ALIASES.get(re.sub(r"[^A-Z0-9]", "", cleaned.upper()), cleaned)


def upgrade() -> None:
    with op.batch_alter_table("laps") as batch_op:
        batch_op.add_column(sa.Column("car_class_raw", sa.String(), nullable=True))

    conn = op.get_bind()
    conn.execute(sa.text("UPDATE laps SET car_class_raw = car_class WHERE car_class IS NOT NULL"))
    for (raw,) in conn.execute(sa.text("SELECT DISTINCT car_class FROM laps WHERE car_class IS NOT NULL")).fetchall():
        readable = _canonical(raw)
        if readable != raw:
            conn.execute(
                sa.text("UPDATE laps SET car_class = :readable WHERE car_class = :raw"),
                {"readable": readable, "raw": raw},
            )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("UPDATE laps SET car_class = car_class_raw WHERE car_class_raw IS NOT NULL"))
    with op.batch_alter_table("laps") as batch_op:
        batch_op.drop_column("car_class_raw")
