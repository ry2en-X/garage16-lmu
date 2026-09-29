"""scripts/backup.sh, executed for real with a fake `docker` on PATH (V0.8.8).

The script shipped broken for anyone whose project folder isn't literally
named "garage16": it derived the telemetry volume name from the folder
name, so in "Garage16 - LMU" it asked Docker for the invalid volume
"Garage16 - LMU_telemetry_data" and aborted after writing the DB dump —
an incomplete backup every single time. These tests run the actual bash
script; the fake `docker` enforces Docker's real volume-name rule.
"""

from __future__ import annotations

import shutil
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
pytestmark = pytest.mark.skipif(shutil.which("bash") is None or shutil.which("gzip") is None, reason="bash/gzip needed")

FAKE_DOCKER = r'''#!/bin/bash
echo "cwd=$(pwd) docker $*" >> "$FAKE_DOCKER_LOG"
name_ok() { [[ "$1" =~ ^[a-zA-Z0-9][a-zA-Z0-9_.-]*$ ]]; }
case "$1" in
  volume)   # docker volume inspect <name>
    [[ "$FAKE_VOLUMES" == *"$3"* ]] && exit 0 || exit 1 ;;
  compose)  # docker compose exec -T db pg_dump ...
    if [[ "$*" == *pg_dump* ]]; then
      [ "$FAKE_EMPTY_DUMP" = "1" ] || echo "-- PostgreSQL database dump (fake) $(printf 'x%.0s' {1..200})"
    fi
    exit 0 ;;
  run)      # docker run ... -v NAME:/data:ro ... tar czf /backup/telemetry.tar.gz
    for a in "$@"; do
      if [[ "$a" == *":/data:ro" ]]; then name_ok "${a%%:*}" || { echo "docker: invalid volume name: ${a%%:*}" >&2; exit 125; }; fi
    done
    for a in "$@"; do [[ "$a" == *":/backup" ]] && touch "${a%%:*}/telemetry.tar.gz"; done
    exit 0 ;;
esac
exit 0
'''


@pytest.fixture
def project(tmp_path):
    """A copy of the script inside a folder named EXACTLY like the real
    deployment folder — spaces and all."""
    root = tmp_path / "Garage16 - LMU"
    (root / "scripts").mkdir(parents=True)
    shutil.copy(ROOT / "scripts" / "backup.sh", root / "scripts" / "backup.sh")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake = fake_bin / "docker"
    fake.write_text(FAKE_DOCKER, encoding="utf-8")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    return root, fake_bin, tmp_path / "docker.log"


def _run(project, cwd, volumes="garage16_telemetry_data", empty_dump="0", env_extra=None):
    root, fake_bin, log = project
    out = root.parent / "backups"
    env = {
        "PATH": f"{fake_bin}:/usr/bin:/bin", "FAKE_DOCKER_LOG": str(log),
        "FAKE_VOLUMES": volumes, "FAKE_EMPTY_DUMP": empty_dump, **(env_extra or {}),
    }
    result = subprocess.run(["bash", str(root / "scripts" / "backup.sh"), str(out)], cwd=cwd, env=env, capture_output=True, text=True, timeout=30)
    return result, out, log


def test_backup_works_in_a_folder_named_garage16_lmu_and_uses_the_pinned_volume_name(project):
    root = project[0]
    result, out, log = _run(project, cwd=root)
    assert result.returncode == 0, result.stdout + result.stderr
    calls = log.read_text(encoding="utf-8")
    assert "garage16_telemetry_data:/data:ro" in calls
    assert "Garage16 - LMU_telemetry_data" not in calls
    (run_dir,) = list(out.glob("garage16_*"))
    assert (run_dir / "db.sql.gz").stat().st_size > 0
    assert (run_dir / "telemetry.tar.gz").exists()


def test_backup_works_no_matter_which_directory_it_is_started_from(project, tmp_path):
    """Synology's Task Scheduler / cron start scripts in / or /root."""
    root = project[0]
    elsewhere = tmp_path / "somewhere_else"
    elsewhere.mkdir()
    result, out, log = _run(project, cwd=elsewhere)
    assert result.returncode == 0, result.stdout + result.stderr
    # compose commands ran from the project root, not from the caller's directory
    compose_lines = [l for l in log.read_text(encoding="utf-8").splitlines() if " docker compose " in l]
    assert compose_lines and all(f"cwd={root}" in l for l in compose_lines)


def test_backup_refuses_to_run_when_the_telemetry_volume_does_not_exist(project):
    """Never silently archive a brand-new empty volume."""
    result, out, log = _run(project, cwd=project[0], volumes="some_other_volume")
    assert result.returncode != 0
    assert "does not exist" in result.stderr
    assert not any(" docker run " in l for l in log.read_text(encoding="utf-8").splitlines())  # never got as far as archiving
    assert not list(out.glob("garage16_*"))  # and left no half-made backup folder behind


def test_backup_fails_loudly_on_an_empty_database_dump(project):
    result, _, _ = _run(project, cwd=project[0], empty_dump="1")
    assert result.returncode != 0
    assert "dump is empty" in result.stderr


def test_backup_honours_a_custom_compose_project_name(project):
    result, _, log = _run(project, cwd=project[0], volumes="myproj_telemetry_data", env_extra={"COMPOSE_PROJECT_NAME": "myproj"})
    assert result.returncode == 0, result.stdout + result.stderr
    assert "myproj_telemetry_data:/data:ro" in log.read_text(encoding="utf-8")
