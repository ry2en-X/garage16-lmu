"""teams.name case-insensitive uniqueness

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-27

Replaces the case-SENSITIVE unique index on teams.name (ix_teams_name,
from migration 0001) with a case-INSENSITIVE one on lower(name). Without
this, "Garage16", "garage16", and "GARAGE16" were three distinct teams —
the DB-level constraint allowed it even though nothing in the product
ever intended that (server/routers/teams.py's create_team() only ever
checked exact-string equality). A functional unique index on lower(name)
works identically on SQLite and PostgreSQL, so no ORM-level-only check is
relied on to enforce this (the spec is explicit: "Nicht nur im
Python-Code").
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    # Data-safety check: the OLD case-sensitive constraint allowed
    # case-variant duplicates to exist already (however unlikely on a
    # small NAS deployment with a handful of teams). Creating the new
    # unique index would just fail with an opaque DB error in that case —
    # fail loudly and specifically here instead, so whoever runs this
    # migration knows exactly which team names to rename by hand first.
    duplicates = conn.execute(
        sa.text(
            "SELECT lower(name) AS lname, COUNT(*) AS c, GROUP_CONCAT(name) AS names "
            "FROM teams GROUP BY lower(name) HAVING COUNT(*) > 1"
        )
        if conn.dialect.name == "sqlite"
        else sa.text(
            "SELECT lower(name) AS lname, COUNT(*) AS c, STRING_AGG(name, ', ') AS names "
            "FROM teams GROUP BY lower(name) HAVING COUNT(*) > 1"
        )
    ).fetchall()
    if duplicates:
        details = "; ".join(f"{row.lname!r}: {row.names}" for row in duplicates)
        raise RuntimeError(
            "Cannot make teams.name case-insensitively unique — these teams "
            f"already collide once case is ignored: {details}. Rename one of "
            "each colliding pair (e.g. via UPDATE teams SET name = ... WHERE "
            "id = ...) before re-running this migration."
        )

    op.drop_index("ix_teams_name", table_name="teams")
    op.create_index("ix_teams_name_lower", "teams", [sa.text("lower(name)")], unique=True)


def downgrade() -> None:
    op.drop_index("ix_teams_name_lower", table_name="teams")
    op.create_index("ix_teams_name", "teams", ["name"], unique=True)
