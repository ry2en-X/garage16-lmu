"""The commands written in the guides must work (V1.0.2, found on the NAS update 22.11.2026: the
update guide told the operator to probe http://localhost:8000/api/v1/health/ready inside the server
container; the HTTPS middleware answered 400 and nothing in the tests had ever run what the guide says).

Checked here, for docs/ADMIN_GUIDE.md, docs/DEPLOYMENT.md, docs/RUNBOOKS.md (and the URLs also in
docker-compose.yml):
  * every http://localhost:8000/<path> answers 200 or 503 over plain HTTP with GARAGE16_REQUIRE_HTTPS on
  * every `python -m scripts.<...>` module exists and starts (`-h`)
  * every ./scripts/...sh path in a fenced code block exists
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

from server.core.config import settings

ROOT = Path(__file__).resolve().parents[2]
GUIDES = [ROOT / "docs" / "ADMIN_GUIDE.md", ROOT / "docs" / "DEPLOYMENT.md", ROOT / "docs" / "RUNBOOKS.md"]
URL_SOURCES = GUIDES + [ROOT / "docker-compose.yml"]


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _urls() -> list[str]:
    found = set()
    for p in URL_SOURCES:
        for m in re.finditer(r"http://localhost:8000(/[^\s'\"`)\\]*)", _read(p)):
            found.add(m.group(1).rstrip(".,;"))
    return sorted(found)


def _modules() -> list[str]:
    found = set()
    for p in GUIDES + [ROOT / "docker-compose.yml"]:
        found.update(re.findall(r"python -m (scripts(?:\.\w+)+)", _read(p)))
    return sorted(found)


def _fenced_blocks(text: str) -> list[str]:
    return re.findall(r"^[ \t]*```[^\n]*\n(.*?)^[ \t]*```", text, flags=re.S | re.M)


# Explicit allow-list: the chapter "Update der NAS von V1.0.0 auf V1.0.1" (ADMIN_GUIDE.md) describes
# what the operator ran at V1.0.1, where the script still sat directly in scripts/ (step 7, "Backup
# mit dem neuen Ordner" — new relative to V1.0.0, i.e. V1.0.1). Since V1.0.2 it is scripts/ops/.
HISTORIC_SCRIPT_PATHS = {("ADMIN_GUIDE.md", "scripts/backup.sh")}


def _script_paths() -> list[tuple[str, str]]:
    """(guide, path) for every scripts/...sh in a fenced block — except blocks that are explicitly about
    the OLD folder of an earlier version: the update chapters run the old folder's backup script
    (V1.0.1 and before: ./scripts/backup.sh, no scripts/ops/ yet). Those blocks set `ALT=` or say
    "alten Ordner"."""
    out = []
    for p in GUIDES:
        for block in _fenced_blocks(_read(p)):
            if "ALT=" in block or "alten Ordner" in block:
                continue
            for m in re.finditer(r"(?<![\w/.-])(?:\./)?(scripts/[\w./-]+\.sh)", block):
                out.append((p.name, m.group(1)))
    return sorted(set(out) - HISTORIC_SCRIPT_PATHS)


def test_the_guides_were_found_and_contain_commands():
    # a test that silently checks nothing is worse than none
    assert "/health/ready" in _urls() or "/api/v1/health/ready" in _urls()
    assert len(_modules()) >= 4
    assert any(path.endswith("scripts/ops/backup.sh") for _, path in _script_paths())


@pytest.mark.parametrize("path", _urls())
def test_guide_urls_answer_over_plain_http_when_https_is_required(client, monkeypatch, path):
    monkeypatch.setattr(settings, "require_https", True)  # like production (docker-compose.yml)
    r = client.get(path)
    assert r.status_code in (200, 503), (path, r.status_code, r.text)


# Every module of the guides uses argparse, and argparse handles -h before anything touches the
# database or the volume, so `-h` is safe to run for real (own process, like the operator does).
# A module that would reach the DB before parsing would hang/fail here — then import it instead.
@pytest.mark.parametrize("module", _modules())
def test_guide_modules_exist_and_start(module):
    rel = ROOT / (module.replace(".", "/") + ".py")
    assert rel.is_file(), f"{module}: {rel} does not exist"
    r = subprocess.run([sys.executable, "-m", module, "-h"], cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, (module, r.stdout, r.stderr)
    assert "usage" in r.stdout.lower(), (module, r.stdout)


@pytest.mark.parametrize("guide,path", _script_paths())
def test_guide_script_paths_exist(guide, path):
    assert (ROOT / path).is_file(), f"{guide}: {path} does not exist"
