"""scripts/restore.sh — what is refused before the live system is touched (V0.8.8, rewritten for
V0.9.8).

The restore itself — staged, validated, switched, a fault injected into every step, rollback and
cleanup, damaged / foreign / pre-V0.9.8 backups — runs for real in tests/test_v098_backup_restore.py.
Here, with a stub `docker` that records every call: wrong arguments, a backup without manifest,
an unconfirmed restore and a missing volume change nothing (no `compose stop`, no database created).
"""

from __future__ import annotations

import gzip
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
pytestmark = pytest.mark.skipif(shutil.which("bash") is None or shutil.which("gzip") is None, reason="bash/gzip needed")

STUB = r'''#!/bin/bash
echo "docker $*" >> "$STUB_LOG"
case "$1" in
  volume) [[ " $STUB_VOLUMES " == *" $3 "* ]] && exit 0 || exit 1 ;;
esac
exit 0
'''


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "Garage16 - LMU"
    (root / "scripts").mkdir(parents=True)
    (root / "alembic" / "versions").mkdir(parents=True)
    (root / "alembic" / "versions" / "0016_x.py").write_text("")
    shutil.copy(ROOT / "scripts" / "restore.sh", root / "scripts" / "restore.sh")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "docker"
    stub.write_text(STUB)
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    backup = tmp_path / "garage16_20261019_030000"
    backup.mkdir()
    with gzip.open(backup / "db.sql.gz", "wb") as f:
        f.write(b"-- dump\n-- PostgreSQL database dump complete\n")
    with gzip.open(backup / "telemetry.tar.gz", "wb") as f:
        f.write(b"x" * 100)
    return root, bin_dir, tmp_path / "docker.log", backup


def _run(project, *args, stdin="", volumes="garage16_telemetry_data", cwd=None):
    root, bin_dir, log, _ = project
    r = subprocess.run(["bash", str(root / "scripts" / "restore.sh"), *args], cwd=cwd or root.parent, input=stdin,
                       env={"PATH": f"{bin_dir}:/usr/bin:/bin", "STUB_LOG": str(log), "STUB_VOLUMES": volumes},
                       capture_output=True, text=True, timeout=30)
    return r, (log.read_text() if log.exists() else "")


def _untouched(calls):
    assert "compose stop" not in calls and "CREATE DATABASE" not in calls and "volume create" not in calls, calls


def test_wrong_arguments_print_the_usage(project):
    for args in ((), ("a", "b", "c"), ("--rollback",), ("--rollback", "yesterday")):
        r, calls = _run(project, *args)
        assert r.returncode == 2, args
        _untouched(calls)


def test_a_backup_without_manifest_needs_allow_unverified(project):
    r, calls = _run(project, "--yes", str(project[3]))
    assert r.returncode == 1 and "no manifest.json" in r.stderr
    _untouched(calls)


def test_without_typing_restore_nothing_is_changed(project):
    r, calls = _run(project, "--allow-unverified", str(project[3] / "db.sql.gz"), str(project[3] / "telemetry.tar.gz"), stdin="no\n")
    assert r.returncode == 1 and "nothing was changed" in r.stdout, r.stdout + r.stderr
    _untouched(calls)


def test_a_missing_telemetry_volume_stops_before_anything(project):
    r, calls = _run(project, "--yes", "--allow-unverified", str(project[3] / "db.sql.gz"), str(project[3] / "telemetry.tar.gz"), volumes="")
    assert r.returncode == 1 and "does not exist" in r.stderr
    _untouched(calls)


def test_missing_files_are_reported(project):
    r, calls = _run(project, "--yes", "/nope/db.sql.gz", "/nope/telemetry.tar.gz")
    assert r.returncode == 1 and "does not exist" in r.stderr
    _untouched(calls)
