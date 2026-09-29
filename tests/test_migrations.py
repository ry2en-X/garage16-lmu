"""Tests for the Alembic migration chain itself (V0.8-NAS §10 —
"Migration-Tests ergänzen"). Previously this was checked manually with ad
hoc shell commands after every schema change — real, but not repeatable
or enforced by `pytest`. This makes it permanent: every future migration
gets this same upgrade -> downgrade -> upgrade round-trip for free.

Runs against a fresh, throwaway SQLite database (always available, no
external service needed) — this is about the migration *chain's own
internal consistency* (every revision's upgrade() and downgrade() must
work, in order, without manual intervention), not a substitute for
testing against real PostgreSQL. See scripts/test_against_postgres.sh
for that — SQLite and PostgreSQL both have Alembic run the same Python
migration files, so a chain that's internally consistent on SQLite is
strong evidence (not by itself proof) that the same is true on Postgres;
scripts/test_against_postgres.sh is what actually confirms it.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _run_alembic(*args: str, db_url: str) -> subprocess.CompletedProcess:
    # V0.8.9 fix: this used to REPLACE the whole environment with a hardcoded
    # Linux-style {"PATH": "/usr/bin:/bin", ...}. On Linux that's harmless (any
    # PATH containing python/alembic works), but on Windows it wipes out the
    # normal PATH entirely — and Python's own asyncio (pulled in by SQLAlchemy)
    # needs Windows' system DLL search path to load its `_overlapped` socket
    # extension at import time. Without it the subprocess dies with
    # "WinError 10106: the requested service provider could not be loaded"
    # before alembic ever runs. Fix: start from the REAL environment
    # (os.environ, whatever OS this is) and only override the two variables
    # this test actually needs, so PATH and everything else stays intact.
    env = {**os.environ, "LMU_GARAGE_DB_URL": db_url, "LMU_GARAGE_ENV": "development"}
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


@pytest.fixture
def fresh_sqlite_url(tmp_path):
    db_path = tmp_path / "migration_test.db"
    return f"sqlite:///{db_path}"


def test_full_upgrade_chain_succeeds_on_a_fresh_database(fresh_sqlite_url):
    result = _run_alembic("upgrade", "head", db_url=fresh_sqlite_url)
    assert result.returncode == 0, result.stderr
    # Every revision in the chain must have actually run, not silently
    # no-op'd — spot-check a few, not just the final "head" state.
    assert "Running upgrade  -> 0001" in result.stderr
    assert "Running upgrade 0010 -> 0011" in result.stderr


def test_full_downgrade_chain_succeeds_back_to_base(fresh_sqlite_url):
    up = _run_alembic("upgrade", "head", db_url=fresh_sqlite_url)
    assert up.returncode == 0, up.stderr

    down = _run_alembic("downgrade", "base", db_url=fresh_sqlite_url)
    assert down.returncode == 0, down.stderr
    assert "Running downgrade 0001 -> " in down.stderr


def test_upgrade_downgrade_upgrade_round_trip_leaves_a_working_schema(fresh_sqlite_url):
    """The exact sequence every future migration should be checked
    against by hand before this test existed — now automatic."""
    assert _run_alembic("upgrade", "head", db_url=fresh_sqlite_url).returncode == 0
    assert _run_alembic("downgrade", "-1", db_url=fresh_sqlite_url).returncode == 0
    result = _run_alembic("upgrade", "head", db_url=fresh_sqlite_url)
    assert result.returncode == 0, result.stderr

    # Schema must actually be usable afterwards, not just "alembic didn't
    # error" — a real app boot + a real write against the round-tripped DB.
    import os
    env = os.environ.copy()
    env["LMU_GARAGE_DB_URL"] = fresh_sqlite_url
    env["LMU_GARAGE_ENV"] = "development"
    check = subprocess.run(
        [sys.executable, "-c", (
            "from fastapi.testclient import TestClient\n"
            "from server.main import app\n"
            "with TestClient(app) as c:\n"
            "    r = c.post('/accounts/register', params={'display_name': 'RoundTrip'})\n"
            "    assert r.status_code == 200, r.text\n"
            "    print('OK')\n"
        )],
        cwd=PROJECT_ROOT, env=env, capture_output=True, text=True, timeout=30,
    )
    assert check.returncode == 0, check.stdout + check.stderr
    assert "OK" in check.stdout


def test_migration_chain_has_no_branch_points(fresh_sqlite_url):
    """`alembic heads` must report exactly one head — more than one means
    two migrations were both written against the same down_revision (a
    branch), which upgrade/downgrade head commands would otherwise handle
    ambiguously or silently pick one of."""
    result = _run_alembic("heads", db_url=fresh_sqlite_url)
    assert result.returncode == 0, result.stderr
    head_lines = [line for line in result.stdout.splitlines() if line.strip()]
    assert len(head_lines) == 1, f"expected exactly one head, got: {result.stdout}"
