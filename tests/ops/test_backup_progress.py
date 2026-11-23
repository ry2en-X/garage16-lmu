"""Found on the NAS update (22.11.2026): the first incremental backup copied 3.5 GB / 1242 files and
printed nothing for minutes; the operator thought it hung and pressed Ctrl+C. store_add / store_stage
now report what they are about to do and how far they are (on stderr: stdout is the result line the
callers capture). The no-manifest note at the end of backup.sh is tested here as well."""

from __future__ import annotations

import os
import re
import shlex
import shutil
import stat
import subprocess
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
STORE_SH = ROOT / "docker" / "telemetry-store.sh"
pytestmark = pytest.mark.skipif(shutil.which("sh") is None or shutil.which("bash") is None, reason="sh/bash needed")


def _sh(script: str, **kw):
    return subprocess.run(["sh", "-c", f". {shlex.quote(str(STORE_SH))} && {script}"], capture_output=True, text=True, timeout=120, **kw)


def _make_volume(root: Path, n: int, size: int = 2000) -> Path:
    data = root / "data"
    for i in range(n):
        d = data / f"driver{i % 5}"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"lap{i}.bin").write_bytes(b"x" * size)
    (data / ".garage16-lock").write_text("not data")
    return data


def test_store_add_reports_plan_and_progress_for_more_than_100_files(tmp_path):
    data = _make_volume(tmp_path, 250)
    store, lst = tmp_path / "store", tmp_path / "list.txt"
    r = _sh(f"store_add {data} {store} {lst}")
    assert r.returncode == 0, r.stderr
    # stdout stays the machine-readable result line
    assert r.stdout.strip() == f"250 250 {250 * 2000}"
    lines = r.stderr.splitlines()
    assert all(line.startswith("[backup] ") for line in lines), lines
    assert any("250 files in the volume" in line and "250 of them new" in line for line in lines), lines
    assert any("do not interrupt" in line for line in lines), "hint for a big first copy"
    assert any(re.search(r"\b100 / 250 files", line) for line in lines), lines
    assert any(re.search(r"\b200 / 250 files", line) for line in lines), lines
    assert any("250 / 250 files" in line and "copied." in line for line in lines), lines
    assert len(list(store.rglob("lap*.bin"))) == 250 and len(lst.read_text().splitlines()) == 250


def test_second_backup_copies_nothing_and_gives_no_hint(tmp_path):
    data = _make_volume(tmp_path, 120)
    store, lst = tmp_path / "store", tmp_path / "list.txt"
    assert _sh(f"store_add {data} {store} {lst}").returncode == 0
    (data / "driver0" / "new.bin").write_bytes(b"y" * 10)
    r = _sh(f"store_add {data} {store} {lst}")
    assert r.stdout.strip() == f"121 1 {120 * 2000 + 10}"
    assert "1 of them new" in r.stderr and "do not interrupt" not in r.stderr


def test_a_small_backup_stays_quiet_apart_from_the_summary(tmp_path):
    data = _make_volume(tmp_path, 5)
    r = _sh(f"store_add {data} {tmp_path / 'store'} {tmp_path / 'l.txt'}")
    assert "do not interrupt" not in r.stderr and "5 files in the volume" in r.stderr


def test_a_progress_line_at_least_every_10_seconds(tmp_path):
    """Few but slow files: a fake `cp` that takes 1 s per file, `date` runs 6 s per call."""
    data = _make_volume(tmp_path, 6)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "date"
    fake.write_text(f"#!/bin/sh\nn=$(cat {tmp_path}/clock 2>/dev/null || echo 1000)\necho $((n + 6)) > {tmp_path}/clock\necho $((n + 6))\n")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    r = _sh(f"store_add {data} {tmp_path / 'store'} {tmp_path / 'l.txt'}", env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"})
    assert r.returncode == 0, r.stderr
    assert re.search(r"\[backup\] \d / 6 files .* copied \.\.\.", r.stderr), r.stderr


def test_store_stage_reports_progress(tmp_path):
    data = _make_volume(tmp_path, 230)
    store, lst = tmp_path / "store", tmp_path / "list.txt"
    assert _sh(f"store_add {data} {store} {lst}").returncode == 0
    target = tmp_path / "stage"
    target.mkdir()
    r = _sh(f"store_stage {store} {lst} {target}")
    assert r.returncode == 0, r.stderr
    lines = r.stderr.splitlines()
    assert all(line.startswith("[restore] ") for line in lines), lines
    assert any("Copying 230 files" in line for line in lines)
    assert any("do not interrupt" in line for line in lines)
    assert any("100 / 230 files" in line for line in lines) and any("230 / 230 files copied." in line for line in lines)
    assert len(list(target.rglob("lap*.bin"))) == 230


# --- backup.sh: old folders without a manifest are kept — and the operator is told -----------------

PRUNE_STUB = '#!/bin/bash\necho "docker $*" >> "$STUB_LOG"\nexit 0\n'


def _prune_section() -> str:
    src = (ROOT / "scripts" / "ops" / "backup.sh").read_text()
    start = src.index('log "Pruning complete backups')
    end = src.index('log "Remaining backups:"')
    return src[start:end]


def _run_prune(tmp_path, folders):
    backups = tmp_path / "backups"
    backups.mkdir()
    old = time.time() - 40 * 86400
    for name, has_manifest in folders:
        d = backups / name
        d.mkdir()
        if has_manifest:
            (d / "manifest.json").write_text("{}")
        os.utime(d, (old, old))
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "docker"
    stub.write_text(PRUNE_STUB)
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    script = 'log() { echo "[backup] $*"; }\nRETENTION_DAYS=14\nBACKUP_DIR="$1"\n' + _prune_section()
    r = subprocess.run(["bash", "-c", script, "x", str(backups)], cwd=ROOT, capture_output=True, text=True, timeout=30,
                       env={"PATH": f"{bin_dir}:/usr/bin:/bin", "STUB_LOG": str(tmp_path / "docker.log")})
    return r, backups


def test_old_folders_without_manifest_are_kept_and_reported(tmp_path):
    r, backups = _run_prune(tmp_path, [("garage16_2026-09-01_0100", False), ("garage16_2026-09-02_0100", False),
                                        ("garage16_2026-09-03_0100", True)])
    assert r.returncode == 0, r.stderr
    assert (backups / "garage16_2026-09-01_0100").exists() and (backups / "garage16_2026-09-02_0100").exists()
    assert not (backups / "garage16_2026-09-03_0100").exists(), "old WITH manifest is pruned as before"
    notes = [line for line in r.stdout.splitlines() if "no manifest.json" in line]
    assert len(notes) == 1 and "2 backup folder" in notes[0] and "by hand" in notes[0] and "sudo" in notes[0], r.stdout


def test_no_note_when_everything_old_has_a_manifest(tmp_path):
    r, backups = _run_prune(tmp_path, [("garage16_2026-09-03_0100", True)])
    assert r.returncode == 0 and "manifest" not in r.stdout
