"""scripts/restore.sh, executed for real with a fake `docker` on PATH
(V0.8.8). The SQL sequence it performs was verified separately against a
real PostgreSQL 16 (a plain restore into the populated database fails with
'relation "alembic_version" already exists'; drop + create + load works and
keeps the data and the migration version). What these tests pin down is the
SCRIPT: the ORDER of operations (writers stopped before the database is
dropped, dropped before it's loaded, server started last), the safeguards
(confirmation, invalid input, missing volume) and that it works from the
real deployment folder name and from any working directory.

NOT covered here (no Docker daemon in the dev sandbox): the real
`docker compose` / `docker run` plumbing on the NAS — see the admin manual's
restore drill."""

from __future__ import annotations

import gzip
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
pytestmark = pytest.mark.skipif(shutil.which("bash") is None or shutil.which("gzip") is None, reason="bash/gzip needed")

FAKE_DOCKER = r'''#!/bin/bash
echo "cwd=$(pwd) docker $*" >> "$FAKE_DOCKER_LOG"
case "$1" in
  volume) [[ "$FAKE_VOLUMES" == *"$3"* ]] && exit 0 || exit 1 ;;
  compose)
    if [[ "$*" == *pg_isready* ]]; then exit 0; fi
    if [[ "$*" == *pg_dump* ]]; then [ "$FAKE_DUMP_FAILS" = "1" ] && exit 1; echo "-- current db dump $(printf 'y%.0s' {1..200})"; exit 0; fi
    if [[ "$*" == *psql* && "$*" != *"-c "* ]]; then n=$(wc -c); echo "   (stdin to psql: $n bytes)" >> "$FAKE_DOCKER_LOG"; exit 0; fi
    exit 0 ;;
esac
exit 0
'''


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "Garage16 - LMU"
    (root / "scripts").mkdir(parents=True)
    shutil.copy(ROOT / "scripts" / "restore.sh", root / "scripts" / "restore.sh")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake = fake_bin / "docker"
    fake.write_text(FAKE_DOCKER, encoding="utf-8")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    backup = tmp_path / "backup"
    backup.mkdir()
    with gzip.open(backup / "db.sql.gz", "wb") as f:
        f.write(b"-- pg_dump output " + b"z" * 300)
    with gzip.open(backup / "telemetry.tar.gz", "wb") as f:
        f.write(b"not really a tar, but valid gzip " * 5)
    return root, fake_bin, tmp_path / "docker.log", backup


def _run(project, *args, stdin="", cwd=None, volumes="garage16_telemetry_data", dump_fails="0"):
    root, fake_bin, log, _ = project
    env = {"PATH": f"{fake_bin}:/usr/bin:/bin", "FAKE_DOCKER_LOG": str(log), "FAKE_VOLUMES": volumes, "FAKE_DUMP_FAILS": dump_fails}
    return subprocess.run(["bash", str(root / "scripts" / "restore.sh"), *args], cwd=cwd or root, env=env,
                          input=stdin, capture_output=True, text=True, timeout=60)


def _calls(project):
    log = project[2]
    return log.read_text(encoding="utf-8").splitlines() if log.exists() else []


def _index(calls, needle, start=0):
    for i in range(start, len(calls)):
        if needle in calls[i]:
            return i
    raise AssertionError(f"{needle!r} not found in:\n" + "\n".join(calls))


def test_full_restore_runs_the_steps_in_the_safe_order(project):
    backup = project[3]
    result = _run(project, "--yes", str(backup / "db.sql.gz"), str(backup / "telemetry.tar.gz"))
    assert result.returncode == 0, result.stdout + result.stderr
    calls = _calls(project)

    stop = _index(calls, "docker compose stop server discord_bot")
    db_up = _index(calls, "docker compose up -d db")
    safety = _index(calls, "pg_dump")
    drop = _index(calls, "DROP DATABASE IF EXISTS")
    create = _index(calls, "CREATE DATABASE")
    load = _index(calls, "stdin to psql")
    telemetry = _index(calls, "docker run --rm")
    start = _index(calls, "docker compose up -d server discord_bot")

    assert stop < db_up < safety < drop < load < telemetry < start   # writers stopped first, server started last
    assert drop == create or drop < create                          # same or following -c in one psql call
    assert "WITH (FORCE)" in calls[drop]                            # connections to the old DB can't block the drop
    assert "-d postgres" in calls[drop]                             # you can't drop the database you're connected to
    assert "find /data -mindepth 1 -delete" in calls[telemetry]     # old telemetry files are replaced, not mixed in
    assert "garage16_telemetry_data:/data" in calls[telemetry]
    assert "(stdin to psql: " in calls[load + 0]
    # the dump really was streamed into psql (uncompressed size, not zero)
    assert int(calls[load].split("stdin to psql: ")[1].split(" bytes")[0]) > 100


def test_a_safety_dump_of_the_current_database_is_kept_next_to_the_backup(project):
    backup = project[3]
    _run(project, "--yes", str(backup / "db.sql.gz"), str(backup / "telemetry.tar.gz"))
    safety = list(backup.glob("pre_restore_*.sql.gz"))
    assert len(safety) == 1 and safety[0].stat().st_size > 0


def test_a_failing_safety_dump_warns_but_does_not_block_a_restore_of_a_broken_database(project):
    backup = project[3]
    result = _run(project, "--yes", str(backup / "db.sql.gz"), str(backup / "telemetry.tar.gz"), dump_fails="1")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "could not make a safety dump" in result.stderr
    assert not list(backup.glob("pre_restore_*"))  # no empty/garbage safety file left behind
    assert any("DROP DATABASE" in c for c in _calls(project))


def test_without_confirmation_nothing_is_changed(project):
    backup = project[3]
    result = _run(project, str(backup / "db.sql.gz"), str(backup / "telemetry.tar.gz"), stdin="no\n")
    assert result.returncode != 0 and "Aborted" in result.stdout
    calls = _calls(project)
    assert not any("stop" in c or "DROP" in c or "docker run" in c for c in calls)


def test_typing_restore_confirms(project):
    backup = project[3]
    result = _run(project, str(backup / "db.sql.gz"), str(backup / "telemetry.tar.gz"), stdin="RESTORE\n")
    assert result.returncode == 0, result.stdout + result.stderr
    assert any("DROP DATABASE" in c for c in _calls(project))


def test_bad_input_is_rejected_before_anything_is_touched(project):
    backup = project[3]
    (backup / "broken.sql.gz").write_bytes(b"this is not gzip")
    (backup / "empty.sql.gz").write_bytes(b"")
    for bad in ("broken.sql.gz", "empty.sql.gz", "missing.sql.gz"):
        result = _run(project, "--yes", str(backup / bad), str(backup / "telemetry.tar.gz"))
        assert result.returncode != 0, bad
    assert not any("stop" in c or "DROP" in c for c in _calls(project))


def test_wrong_number_of_arguments_prints_usage(project):
    result = _run(project, "--yes", "only-one-file.gz")
    assert result.returncode == 2 and "Usage" in result.stderr


def test_a_missing_telemetry_volume_stops_the_restore_before_dropping_anything(project):
    backup = project[3]
    result = _run(project, "--yes", str(backup / "db.sql.gz"), str(backup / "telemetry.tar.gz"), volumes="other")
    assert result.returncode != 0 and "does not exist" in result.stderr
    assert not any("DROP" in c for c in _calls(project))


def test_works_from_any_working_directory_and_runs_compose_in_the_project_root(project, tmp_path):
    root, backup = project[0], project[3]
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    result = _run(project, "--yes", str(backup / "db.sql.gz"), str(backup / "telemetry.tar.gz"), cwd=elsewhere)
    assert result.returncode == 0, result.stdout + result.stderr
    compose_calls = [c for c in _calls(project) if " docker compose " in c]
    assert compose_calls and all(f"cwd={root}" in c for c in compose_calls)


def test_restore_start_services_can_leave_the_bot_off_for_a_server_move(project):
    """Restoring onto a NEW server while the old one still runs: only the
    API may start, or two bots with one token double-post every record."""
    root, fake_bin, log, backup = project
    import os
    env = {"PATH": f"{fake_bin}:/usr/bin:/bin", "FAKE_DOCKER_LOG": str(log), "FAKE_VOLUMES": "garage16_telemetry_data",
           "FAKE_DUMP_FAILS": "0", "RESTORE_START_SERVICES": "server"}
    result = subprocess.run(["bash", str(root / "scripts" / "restore.sh"), "--yes", str(backup / "db.sql.gz"), str(backup / "telemetry.tar.gz")],
                            cwd=root, env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    up_calls = [c for c in _calls(project) if "docker compose up -d" in c and "up -d db" not in c]
    assert len(up_calls) == 1 and up_calls[0].rstrip().endswith("up -d server")
