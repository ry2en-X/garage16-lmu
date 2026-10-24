"""V0.9.7 — versions, changelog, the update guide (with its way back over the new migration), the
release gate's pieces and the pinned versions agree."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _section():
    guide = (ROOT / "docs/ADMIN_GUIDE.md").read_text(encoding="utf-8")
    return guide.split("**Neu in V0.9.7", 1)[1].split("**Neu in V0.9.6", 1)[0]


def test_versions_and_changelog():
    from client.uploader.uploader import CLIENT_VERSION
    from server.main import VERSION

    # V0.9.9 is the current server (tests/test_v099_docs.py); the V0.9.7 entry stays
    assert VERSION == "1.0.0"  # V1.0 moved both on
    assert CLIENT_VERSION == "1.0.0", "V0.9.7 changes the server and the web page only (client 1.0.0 since V1.0)"
    assert "\n## V0.9.7 — " in (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")


def test_the_update_backs_up_first_checks_consistency_and_has_a_way_back_over_the_migration():
    section = _section()
    assert "/volume1/homes/Moritz/garage16/Garage16_Lmu_V0_9_7" in section and "/volumeUSB2/usbshare/garage16-backups" in section
    assert section.index("backup.sh") < section.index("up -d --build") < section.index("scripts.consistency_check")
    assert "down -v" not in section
    back = section.split("Zurück auf V0.9.6", 1)[1]
    assert back.index("alembic downgrade 0015") < back.index("Garage16_Lmu_V0_9_6/Garage16 - LMU\"\nsudo docker compose --profile tunnel up -d")
    assert "LMU_GARAGE_DB_URL=" in back, "exec does not inherit the server's database URL"
    assert "0016" in section


def test_the_new_migration_is_the_head_and_purely_additive():
    versions = sorted(p.name for p in (ROOT / "alembic/versions").glob("0*.py"))
    [mine] = [v for v in versions if v.startswith("0016_")]      # V0.9.9 added 0017 after it
    text = (ROOT / "alembic/versions" / mine).read_text(encoding="utf-8")
    upgrade = text.split("def upgrade", 1)[1].split("def downgrade", 1)[0]
    assert "create_table" in upgrade and "alter" not in upgrade.lower() and "drop" not in upgrade.lower()


def test_the_release_gate_is_wired_up():
    import yaml

    ci = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    steps = " ".join(str(s.get("run", "")) for s in ci["jobs"]["tests"]["steps"])
    assert "scripts.release_check" in steps and "-c constraints.txt" in steps and "npm ci" in steps
    assert "test_against_postgres.sh" in steps
    build = yaml.safe_load((ROOT / ".github/workflows/build-client.yml").read_text(encoding="utf-8"))
    assert build["jobs"]["tests"]["uses"] == "./.github/workflows/ci.yml" and build["jobs"]["windows"]["needs"] == "tests"
    for dockerfile in ("docker/Dockerfile.server", "docker/Dockerfile.discord_bot"):
        text = (ROOT / dockerfile).read_text(encoding="utf-8")
        assert "COPY constraints.txt" in text and "-c constraints.txt" in text, dockerfile
    assert "COPY scripts/consistency_check.py ./scripts/consistency_check.py" in (ROOT / "docker/Dockerfile.server").read_text(encoding="utf-8")


def test_every_direct_requirement_is_pinned():
    from scripts.release_check import read_constraints, _norm

    pins = read_constraints()
    for req_file in ("server/requirements.txt", "discord_bot/requirements.txt", "client/requirements.txt", "requirements-dev.txt"):
        for line in (ROOT / req_file).read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            name = _norm(line.split(">=")[0].split("[")[0].split("==")[0].strip())
            assert name in pins, f"{name} ({req_file}) has no pin in constraints.txt"
