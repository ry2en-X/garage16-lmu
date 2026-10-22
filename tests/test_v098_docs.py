"""V0.9.8 — versions, changelog, the update guide, the new documentation and the gate's pieces agree."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _section():
    guide = (ROOT / "docs/ADMIN_GUIDE.md").read_text(encoding="utf-8")
    return guide.split("**Neu in V0.9.8", 1)[1].split("**Neu in V0.9.7", 1)[0]


def test_versions_and_changelog():
    from client.uploader.uploader import CLIENT_VERSION
    from server.main import VERSION

    # V0.9.9 is the current server (tests/test_v099_docs.py); the V0.9.8 entry stays
    assert VERSION == "0.9.9"
    assert CLIENT_VERSION == "0.9.4", "V0.9.8 changes the server only"
    assert "\n## V0.9.8 — " in (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")


def test_the_update_backs_up_first_checks_readiness_and_has_a_way_back():
    section = _section()
    assert "Garage16_Lmu_V0_9_8" in section and "/volumeUSB2/usbshare/garage16-backups" in section
    assert section.index("backup.sh") < section.index("up -d --build") < section.index("/health/ready")
    assert "down -v" not in section and "Keine Datenbank-Migration" in section
    assert "Garage16_Lmu_V0_9_7/Garage16 - LMU\"\nsudo docker compose --profile tunnel up -d" in section


def test_no_migration_in_this_release():
    versions = sorted(p.name for p in (ROOT / "alembic/versions").glob("0*.py"))
    # V0.9.8 added none: after V0.9.7's 0016 comes V0.9.9's 0017
    i = next(k for k, name in enumerate(versions) if name.startswith("0016_"))
    assert versions[i + 1:i + 2] in ([], [v for v in versions if v.startswith("0017_")])


def test_the_new_documents_exist_and_point_at_real_things():
    for name in ("ARCHITECTURE.md", "RUNBOOKS.md", "DATA_PROTECTION.md", "QUALITY.md"):
        text = (ROOT / "docs" / name).read_text(encoding="utf-8")
        for ref in [w.strip("`") for w in text.split() if w.startswith("`scripts/") or w.startswith("`server/")]:
            ref = ref.rstrip("`.,);:").split(" ")[0]
            if ref.endswith(".py") or ref.endswith(".sh"):
                assert (ROOT / ref).exists(), f"{name} mentions {ref}, which does not exist"
    runbooks = (ROOT / "docs/RUNBOOKS.md").read_text(encoding="utf-8")
    assert "/health/ready" in runbooks and "rotate_secret_key" in runbooks and "--delete-orphans" in runbooks


def test_the_gate_floors_are_sane_and_scripts_are_in_the_image():
    floors = json.loads((ROOT / "tests/coverage_floors.json").read_text(encoding="utf-8"))
    for name, f in floors["files"].items():
        assert (ROOT / name).exists(), name
        assert 0 <= f["branches"] <= 100 and 0 <= f["lines"] <= 100
    dockerfile = (ROOT / "docker/Dockerfile.server").read_text(encoding="utf-8")
    for script in ("consistency_check", "rotate_secret_key"):
        assert f"COPY scripts/{script}.py ./scripts/{script}.py" in dockerfile
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "/health/ready" in compose and "LMU_GARAGE_TRUSTED_PROXIES" in compose


def test_tool_caches_never_change_the_tested_tree(tmp_path):
    """V0.9.8: the gate's linter must not write into the staged tree (it did: .ruff_cache), and a
    cache left in the working tree must never be released."""
    from scripts import release_check
    src = (ROOT / "scripts" / "release_check.py").read_text(encoding="utf-8")
    assert '"ruff", "check", "--no-cache"' in src
    for name in (".ruff_cache", ".pytest_cache", "__pycache__"):
        assert name in release_check.EXCLUDE_DIRS
