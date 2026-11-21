"""Migration 0020: slugs of drivers and teams, the redirects of renamed ones, and the two
leaderboard indexes. Additive, filled for existing rows, reversible."""

from __future__ import annotations

import pytest

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEXT = (ROOT / "alembic" / "versions" / "0020_slugs_and_leaderboard_indexes.py").read_text(encoding="utf-8")


def test_it_is_the_head_and_follows_0019():
    versions = sorted(p.name for p in (ROOT / "alembic" / "versions").glob("0*.py"))
    assert versions[-1] == "0020_slugs_and_leaderboard_indexes.py"
    assert 'revision: str = "0020"' in TEXT and 'down_revision: Union[str, None] = "0019"' in TEXT


def test_it_only_adds_and_its_way_back_removes_exactly_that():
    upgrade = TEXT.split("def upgrade", 1)[1].split("def downgrade", 1)[0]
    downgrade = TEXT.split("def downgrade", 1)[1]
    assert "drop" not in upgrade.lower() and "nullable=True" in upgrade
    for name in ("ix_laps_board_class", "ix_laps_board_model", "slug_redirects", "ix_drivers_slug", "ix_teams_slug"):
        assert name in upgrade and name in downgrade
    # no batch copy of `teams` on the way up: SQLite would lose ix_teams_name_lower (migration 0008)
    assert 'batch_alter_table("teams")' not in upgrade


def test_the_frozen_slug_function_matches_the_server():
    from server.core.slugs import slugify

    namespace: dict = {}
    exec(compile(TEXT.split("def _fill", 1)[0], "0020", "exec"), namespace)   # imports + _slug
    for name in ("Moritz Lachnit", "Müller · Ørsted", "Straße 1", "???", "HyperMotion Esports", "  a--b  "):
        assert namespace["_slug"](name) == slugify(name), name


def test_the_leaderboard_query_can_use_the_index(client):
    """SQLite's query plan for the class leaderboard names the new index (PostgreSQL decides by its
    statistics; tests/db/test_migrations.py runs the chain there)."""
    from sqlalchemy import text

    from server.core.database import engine

    if engine.dialect.name != "sqlite":
        pytest.skip("EXPLAIN QUERY PLAN is SQLite's; PostgreSQL picks its plan by statistics")
    with engine.connect() as conn:
        plan = " ".join(str(r) for r in conn.execute(text(
            "EXPLAIN QUERY PLAN SELECT id FROM laps WHERE track_name = 'x' AND car_class = 'y' AND is_valid = 1 ORDER BY lap_time")))
    assert "ix_laps_board_class" in plan, plan
