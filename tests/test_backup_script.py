"""scripts/backup.sh — the checks before anything happens (V0.8.8, rewritten for V0.9.8).

The backup itself (consistent with uploads running, interrupted, killed, failing steps) is
exercised for real in tests/test_v098_backup_restore.py — from this project's folder, whose name
has spaces in it. What stays here, with a stub `docker`, is what must happen BEFORE the write lock
is taken: the pinned volume name (V0.8.8: never derived from the folder name), a custom compose
project name, and the refusal to back up a volume that does not exist.
"""

from __future__ import annotations

import shutil
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="bash needed")

STUB = r'''#!/bin/bash
echo "docker $*" >> "$STUB_LOG"
case "$1" in
  volume) [[ " $STUB_VOLUMES " == *" $3 "* ]] && exit 0 || exit 1 ;;
  run) [[ "$*" == *" -d "* ]] && exit 1 ;;   # stop at the lock helper: nothing after it is tested here
esac
exit 0
'''


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "Garage16 - LMU"
    (root / "scripts").mkdir(parents=True)
    (root / "server").mkdir()
    shutil.copy(ROOT / "scripts" / "backup.sh", root / "scripts" / "backup.sh")
    (root / "server" / "main.py").write_text('VERSION = "9.9.9"\n')
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "docker"
    stub.write_text(STUB)
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    return root, bin_dir, tmp_path / "docker.log"


def _run(project, cwd=None, volumes="garage16_telemetry_data", env=None):
    root, bin_dir, log = project
    out = root.parent / "backups"
    r = subprocess.run(["bash", str(root / "scripts" / "backup.sh"), str(out)], cwd=cwd or root.parent,
                       env={"PATH": f"{bin_dir}:/usr/bin:/bin", "STUB_LOG": str(log), "STUB_VOLUMES": volumes, **(env or {})},
                       capture_output=True, text=True, timeout=30)
    return r, out, (log.read_text() if log.exists() else "")


def test_the_pinned_volume_name_is_used_whatever_the_folder_is_called(project):
    r, out, calls = _run(project)
    assert "docker volume inspect garage16_telemetry_data" in calls
    assert "Garage16 - LMU_telemetry_data" not in calls
    assert "-v garage16_telemetry_data:/data" in calls, "the lock helper gets the right volume"


def test_a_custom_compose_project_name_is_honoured(project):
    r, out, calls = _run(project, volumes="mystack_telemetry_data", env={"COMPOSE_PROJECT_NAME": "mystack"})
    assert "docker volume inspect mystack_telemetry_data" in calls and "-v mystack_telemetry_data:/data" in calls


def test_a_missing_volume_is_refused_before_anything_is_written(project):
    r, out, calls = _run(project, volumes="")
    assert r.returncode == 1 and "does not exist" in r.stderr
    assert not out.exists() or not any(out.iterdir()), "no folder, not even a .partial one"
    assert "docker run" not in calls


def test_a_failed_lock_publishes_nothing(project):
    r, out, calls = _run(project)
    assert r.returncode != 0 and "nothing was published" in r.stderr
    assert not [p for p in out.iterdir()] if out.exists() else True
