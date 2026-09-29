"""
conftest.py — Test-session-wide setup.

Sets environment variables BEFORE any test module (or the production code
it imports) can import server.config — pytest always loads conftest.py
first, which is what makes this reliable. server.config.Settings reads
these once, at import time, into a module-level singleton (`settings`),
so this is the only place that can safely control which DB/storage the
whole test session uses.

Without this, integration tests that exercise the real FastAPI app would
either hit whatever LMU_GARAGE_DB_URL happens to be set in the shell (or
the default `sqlite:///./lmu_garage_server.db` in the repo root — a real
file, shared across test runs and easy to accidentally commit or leave
dirty).
"""

from __future__ import annotations

import os
import tempfile

_tmp_db_dir = tempfile.mkdtemp(prefix="garage16_test_db_")
_tmp_storage_dir = tempfile.mkdtemp(prefix="garage16_test_storage_")

os.environ.setdefault("LMU_GARAGE_ENV", "development")
os.environ.setdefault("LMU_GARAGE_DB_URL", f"sqlite:///{_tmp_db_dir}/test.db")
os.environ.setdefault("LMU_GARAGE_STORAGE_DIR", _tmp_storage_dir)
os.environ.setdefault("LMU_GARAGE_ADMIN_TOKEN", "test-admin-token")
# Registration stays open (no LMU_GARAGE_REGISTRATION_SECRET) so tests can
# freely register drivers without needing the header.

import pytest  # noqa: E402


@pytest.fixture()
def client():
    """A TestClient against the real app, with the DB schema created
    fresh (Base.metadata.create_all runs automatically at import time in
    development — see server/main.py) and tables cleared between tests
    that use this fixture, so tests don't leak drivers/laps into each
    other."""
    from fastapi.testclient import TestClient

    from server.database import Base, engine
    from server.main import app

    with TestClient(app) as c:
        yield c

    # Clean slate for the next test — drop and recreate all tables rather
    # than trying to delete rows in FK-safe order by hand.
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


@pytest.fixture(autouse=True)
def _reset_rate_limits():
    """Rate-limit state (server/rate_limit.py) is process-global, so
    without this, a rate-limit test running early would poison the
    buckets for every test after it in the same session."""
    from server.rate_limit import reset_all

    reset_all()
    yield
    reset_all()
