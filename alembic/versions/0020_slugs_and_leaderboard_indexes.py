"""Readable links and faster leaderboards (V1.0.2)

Revision ID: 0020
Revises: 0019
Create Date: 2026-11-20

* drivers.slug, teams.slug: the name as it appears in links (/driver/moritz-lachnit,
  /team/hypermotion-esports — server/core/slugs.py), filled here for every row. Indexed, not unique:
  two names from before V1.0.2 may share a slug (the older one gets the link).
* slug_redirects: an earlier slug of a renamed driver or team, so an old link keeps working.
* ix_laps_board_class (track_name, car_class, is_valid, lap_time) and ix_laps_board_model
  (track_name, car_model, is_valid, lap_time): the leaderboards read only the rows they show.

Purely additive. Downgrade drops the indexes, the table and the two columns.
"""
from __future__ import annotations

import unicodedata
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0020"
down_revision: Union[str, None] = "0019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _slug(text: str) -> str:
    # frozen copy of server/core/slugs.py slugify() as of V1.0.2 — a migration must not change later
    folded = unicodedata.normalize("NFKD", str(text or "").casefold())
    out, dash = [], False
    for ch in folded:
        if unicodedata.category(ch) == "Mn":
            continue
        if ch.isalnum():
            out.append(ch)
            dash = False
        elif not dash and out:
            out.append("-")
            dash = True
    return "".join(out).strip("-")[:80].rstrip("-")


def _fill(table_name: str, name_column: str) -> None:
    bind = op.get_bind()
    table = sa.table(table_name, sa.column("id", sa.Integer), sa.column(name_column, sa.String),
                     sa.column("slug", sa.String))
    for row_id, name in bind.execute(sa.select(table.c.id, table.c[name_column])).fetchall():
        bind.execute(table.update().where(table.c.id == row_id).values(slug=_slug(name) or None))


def upgrade() -> None:
    # plain ADD COLUMN (no batch copy): on SQLite a batch would rebuild teams and lose the
    # expression index ix_teams_name_lower (migration 0008)
    op.add_column("drivers", sa.Column("slug", sa.String(length=80), nullable=True))
    op.add_column("teams", sa.Column("slug", sa.String(length=80), nullable=True))
    _fill("drivers", "display_name")
    _fill("teams", "name")
    op.create_index("ix_drivers_slug", "drivers", ["slug"])
    op.create_index("ix_teams_slug", "teams", ["slug"])
    op.create_table(
        "slug_redirects",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.Column("slug", sa.String(length=80), nullable=False),
        sa.Column("target_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("kind", "slug", name="uq_slug_redirect"),
    )
    op.create_index("ix_laps_board_class", "laps", ["track_name", "car_class", "is_valid", "lap_time"])
    op.create_index("ix_laps_board_model", "laps", ["track_name", "car_model", "is_valid", "lap_time"])


def downgrade() -> None:
    op.drop_index("ix_laps_board_model", table_name="laps")
    op.drop_index("ix_laps_board_class", table_name="laps")
    op.drop_table("slug_redirects")
    op.drop_index("ix_teams_slug", table_name="teams")
    op.drop_index("ix_drivers_slug", table_name="drivers")
    with op.batch_alter_table("teams") as batch:
        batch.drop_column("slug")
    if op.get_bind().dialect.name == "sqlite":
        # the batch copy above dropped the expression index (SQLite cannot reflect it) — put it back
        op.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_teams_name_lower ON teams (lower(name))")
    with op.batch_alter_table("drivers") as batch:
        batch.drop_column("slug")
