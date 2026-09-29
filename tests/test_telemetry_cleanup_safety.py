"""scripts/telemetry_cleanup.py --execute must never run against the wrong
database (V0.8.8). The README told operators to run it "inside the server
container" — but the script wasn't in the image, and `docker compose exec`
doesn't inherit the database URL the container's entrypoint builds for the
server, so the tool would have looked at the SQLite DEFAULT (empty) instead
of PostgreSQL, making every telemetry file look orphaned."""

from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path

import pytest
from sqlalchemy.engine import make_url

from server.config import settings
from tests.test_moderation import _upload_lap

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _run_cli(*argv) -> int:
    from scripts import telemetry_cleanup

    old_argv = sys.argv
    sys.argv = ["telemetry_cleanup", *argv]
    try:
        return telemetry_cleanup.main()
    finally:
        sys.argv = old_argv


def _stray_file(name: str) -> Path:
    d = settings.telemetry_storage_dir / "driver_424242" / "cleanup_safety" / "car"
    d.mkdir(parents=True, exist_ok=True)
    f = d / name
    f.write_bytes(b"x" * 32)
    old = time.time() - 5 * 3600
    os.utime(f, (old, old))
    return f


def test_it_reports_which_database_it_is_using_without_leaking_the_password(client, capsys):
    _run_cli()
    out = capsys.readouterr().out
    url = make_url(settings.database_url)
    assert f"Database : {url.get_backend_name()}" in out and "Storage  :" in out
    if url.password:  # PostgreSQL runs: the password must never be printed
        assert url.password not in out


def test_execute_deletes_a_real_orphan_when_pointed_at_a_populated_database(client, capsys):
    _upload_lap(client, _register(client))  # the DB now has a lap
    stray = _stray_file("real_orphan.parquet")
    assert _run_cli("--execute") == 0
    assert not stray.exists()


def test_execute_refuses_when_the_database_has_no_laps_but_files_exist(client, capsys):
    """The tell-tale sign of pointing the tool at the wrong (empty) database."""
    stray = _stray_file("would_be_lost.parquet")
    assert _run_cli("--execute") == 2
    assert stray.exists()  # nothing deleted
    assert "0 laps" in capsys.readouterr().err


def test_dry_run_still_reports_but_never_deletes_even_on_a_suspicious_database(client, capsys):
    stray = _stray_file("dry_run_only.parquet")
    assert _run_cli() == 0
    assert stray.exists()


@pytest.mark.skipif(make_url(settings.database_url).get_backend_name() != "sqlite", reason="CLI-level variant needs the SQLite test database; the logic itself is covered below for both dialects")
def test_execute_refuses_production_settings_with_the_sqlite_default_database(client, monkeypatch, capsys):
    _upload_lap(client, _register(client))
    stray = _stray_file("prod_sqlite.parquet")
    monkeypatch.setattr(settings, "environment", "production")
    assert _run_cli("--execute") == 2
    assert stray.exists()
    assert "SQLite default database" in capsys.readouterr().err


def test_wrong_database_detection_logic_for_both_dialects(monkeypatch):
    """Database-independent: the decision function itself."""
    from scripts.telemetry_cleanup import _wrong_database_suspicion as suspicion

    sqlite_url = "sqlite:///./lmu_garage_server.db"
    pg_url = "postgresql+psycopg://garage16:pw@db:5432/garage16"

    monkeypatch.setattr(settings, "environment", "production")
    assert "SQLite default database" in suspicion(sqlite_url, lap_count=50, orphan_count=1)   # production + sqlite: always refused
    assert suspicion(pg_url, lap_count=50, orphan_count=1) is None                              # the normal, healthy case
    assert "0 laps" in suspicion(pg_url, lap_count=0, orphan_count=3)                           # empty DB + files: wrong DB

    monkeypatch.setattr(settings, "environment", "development")
    assert suspicion(sqlite_url, lap_count=50, orphan_count=1) is None                          # dev on sqlite is normal
    assert "0 laps" in suspicion(sqlite_url, lap_count=0, orphan_count=1)
    assert suspicion(sqlite_url, lap_count=0, orphan_count=0) is None                           # nothing to delete anyway


def _register(client):
    resp = client.post("/accounts/register", params={"display_name": "CleanupSafety"})
    assert resp.status_code == 200
    return resp.json()


# --------------------------- the documented docker command really works

def test_the_readme_command_only_uses_variables_the_server_container_defines():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    command = re.search(r"docker compose exec -T server sh -c '([^\n]+)'", readme)
    assert command, "README must contain the exact `docker compose exec -T server sh -c '...'` cleanup command"
    server_env_block = compose[compose.index("  server:"):compose.index("  reverse_proxy:")]
    for var in set(re.findall(r"\$\{(POSTGRES_[A-Z]+)\}", command.group(1))):
        assert f"      {var}:" in server_env_block, f"{var} is not set in the server container's environment"
    assert "scripts.telemetry_cleanup" in command.group(1)


def test_the_server_image_actually_contains_the_cleanup_script():
    dockerfile = (ROOT / "docker" / "Dockerfile.server").read_text(encoding="utf-8")
    assert "COPY scripts/telemetry_cleanup.py ./scripts/telemetry_cleanup.py" in dockerfile
