"""V0.9.7 — a database FILLED at an older schema migrates to the current one, and the current code
can read every row afterwards.

test_migrations.py proves the chain on an EMPTY database. A real update (the NAS going from e.g.
V0.9.3 to V0.9.7) runs the same chain over existing rows — data migrations, new NOT NULL columns,
changed indexes can fail there and only there. So for every older revision: build the schema at
that revision, put rows into EVERY table (filled generically from the reflected schema at that
revision — no hand-written fixture that silently goes stale), upgrade to head, then:

  * no row was lost,
  * every ORM model of the current code loads every row (a wrong default or a type the code
    cannot read fails here).
"""

from __future__ import annotations

import datetime as dt
import os
import subprocess
import sys
from pathlib import Path

import pytest
import sqlalchemy as sa

pytestmark = pytest.mark.filterwarnings("ignore:Skipped unsupported reflection")

ROOT = Path(__file__).resolve().parent.parent
REVISIONS = sorted(p.name.split("_", 1)[0] for p in (ROOT / "alembic" / "versions").glob("[0-9][0-9][0-9][0-9]_*.py"))
HEAD = REVISIONS[-1]


def _alembic(*args: str, url: str) -> None:
    env = {**os.environ, "LMU_GARAGE_DB_URL": url, "LMU_GARAGE_ENV": "development"}
    result = subprocess.run([sys.executable, "-m", "alembic", *args], cwd=ROOT, env=env,
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr


def _value(column: sa.Column, n: int):
    """A plausible value for a NOT NULL column without a default, by type."""
    t = column.type
    name = column.name
    if isinstance(t, sa.Boolean):
        return False
    if isinstance(t, (sa.Integer, sa.BigInteger, sa.SmallInteger)):
        return 1
    if isinstance(t, (sa.Float, sa.Numeric)):
        return 90.5
    if isinstance(t, sa.DateTime):
        return dt.datetime(2026, 9, 1, 12, 0, 0) + dt.timedelta(days=30 * (1 if "expires" in name else 0))
    if isinstance(t, sa.JSON):
        return [30.1, 30.2, 30.2] if name == "sector_times" else {}
    if isinstance(t, (sa.String, sa.Text, sa.Unicode)):
        return f"{name}-{n}"
    return f"{name}-{n}"


def _fill(url: str) -> dict:
    """One row in every table (parents before children, FK columns -> id 1). Returns row counts."""
    engine = sa.create_engine(url)
    meta = sa.MetaData()
    meta.reflect(engine)
    counts = {}
    with engine.begin() as conn:
        for table in meta.sorted_tables:
            if table.name == "alembic_version":
                continue
            row = {}
            for column in table.columns:
                if column.primary_key and isinstance(column.type, sa.Integer):
                    row[column.name] = 1
                elif column.foreign_keys:
                    row[column.name] = 1
                elif not column.nullable and column.server_default is None:
                    row[column.name] = _value(column, 1)
            conn.execute(table.insert().values(**row))
            counts[table.name] = 1
    engine.dispose()
    return counts


@pytest.mark.parametrize("revision", REVISIONS[:-1])
def test_a_filled_database_from_an_older_schema_migrates_to_head_and_loads(tmp_path, revision):
    url = f"sqlite:///{tmp_path / 'old.db'}"
    _alembic("upgrade", revision, url=url)
    before = _fill(url)
    _alembic("upgrade", "head", url=url)

    engine = sa.create_engine(url)
    with engine.connect() as conn:
        version = conn.execute(sa.text("select version_num from alembic_version")).scalar()
        assert version.startswith(HEAD), (version, HEAD)
        for table, n in before.items():
            assert conn.execute(sa.text(f'select count(*) from "{table}"')).scalar() == n, f"rows lost in {table}"

    # every model of the CURRENT code reads every migrated row
    from sqlalchemy.orm import Session

    from server import models
    from server.database import Base

    with Session(engine) as session:
        for mapper in Base.registry.mappers:
            cls = mapper.class_
            if cls.__tablename__ in before:
                rows = session.query(cls).all()
                assert len(rows) == before[cls.__tablename__], cls.__name__
                for row in rows:
                    for attr in mapper.column_attrs:
                        getattr(row, attr.key)        # loads and converts every column
        lap = session.get(models.Lap, 1)
        if lap is not None:
            assert lap.sector_times == [30.1, 30.2, 30.2]
    engine.dispose()
