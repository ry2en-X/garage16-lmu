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

import atexit
import os
import shutil
import tempfile

_tmp_db_dir = tempfile.mkdtemp(prefix="garage16_test_db_")
_tmp_storage_dir = tempfile.mkdtemp(prefix="garage16_test_storage_")
# V0.9.1: remove them again when the test run ends — every run used to leave a database and up
# to ~20 MB of uploaded test telemetry behind in the temp folder.
atexit.register(shutil.rmtree, _tmp_db_dir, ignore_errors=True)
atexit.register(shutil.rmtree, _tmp_storage_dir, ignore_errors=True)

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


@pytest.fixture(autouse=True)
def _reset_analysis_cache():
    """The analysis cache (server/analysis_cache.py) is process-global: clear it
    around every test so one test's cached result can never satisfy another's
    request (the cache only ever speeds things up, but tests must not depend on
    what ran before them)."""
    from server.analysis_cache import analysis_cache
    from server.telemetry_analysis import clear_lap_cache

    analysis_cache.clear()
    clear_lap_cache()
    yield
    analysis_cache.clear()
    clear_lap_cache()


# ---------------------------------------------------------------------------
# V0.9.7: release gate — a skipped test is NOT a passed test
# ---------------------------------------------------------------------------
#
# Many tests skip themselves when a tool is missing (node/jsdom for the frontend, bash/gzip for the
# backup scripts, xvfb for the client GUI, caddy for the reverse proxy). That keeps a quick local
# run usable, but a release must not be built from a run in which half the checks silently did not
# happen. With GARAGE16_STRICT_TESTS=1 (set by scripts/release_check.py and the CI pipeline):
#
#   * the session refuses to start when a dependency the suite needs is missing — reported as an
#     INFRASTRUCTURE problem with exit code 6, never as a test result;
#   * every skip counts as a failure, unless its reason matches GARAGE16_ALLOWED_SKIPS
#     (comma-separated substrings) or is one of the documented opt-in checks ("opt-in").

STRICT_EXIT_INFRASTRUCTURE = 6
_STRICT = os.environ.get("GARAGE16_STRICT_TESTS") == "1"
_ALWAYS_ALLOWED_SKIPS = ("opt-in",)


def _allowed_skips() -> tuple:
    extra = [s.strip() for s in os.environ.get("GARAGE16_ALLOWED_SKIPS", "").split(",") if s.strip()]
    return _ALWAYS_ALLOWED_SKIPS + tuple(extra)


def pytest_sessionstart(session):
    if not _STRICT:
        return
    from scripts.test_requirements import missing_test_dependencies

    # tools (caddy, xvfb-run, …) are not required up front: their tests skip, and a skip fails
    # in strict mode with the test's own explanation
    missing = missing_test_dependencies(include_tools=False)
    if missing:
        pytest.exit(
            "INFRASTRUCTURE: the test environment is incomplete, nothing was tested — missing: "
            + "; ".join(missing)
            + ". Set it up with scripts/setup_test_env.sh (or see README, 'Running the tests').",
            returncode=STRICT_EXIT_INFRASTRUCTURE,
        )


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if not _STRICT or not report.skipped or hasattr(report, "wasxfail"):
        return
    reason = report.longrepr[2] if isinstance(report.longrepr, tuple) else str(report.longrepr)
    if any(token in reason for token in _allowed_skips()):
        return
    report.outcome = "failed"
    report.longrepr = f"STRICT (GARAGE16_STRICT_TESTS=1): a skipped test counts as a failure — {reason}"


@pytest.hookimpl(hookwrapper=True)
def pytest_make_collect_report(collector):
    """A whole module skipped at import (pytest.importorskip) is the same: not tested."""
    outcome = yield
    report = outcome.get_result()
    if not _STRICT or not report.skipped:
        return
    reason = report.longrepr[2] if isinstance(report.longrepr, tuple) else str(report.longrepr)
    if any(token in reason for token in _allowed_skips()):
        return
    report.outcome = "failed"
    report.longrepr = f"STRICT (GARAGE16_STRICT_TESTS=1): a module skipped at import counts as a failure — {reason}"
