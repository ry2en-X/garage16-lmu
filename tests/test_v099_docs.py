"""V0.9.9 — versions, changelog, the update guide with its way back over migration 0017, the new
documents and scripts, the image, CI and the gate's pieces agree."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _section():
    guide = (ROOT / "docs/ADMIN_GUIDE.md").read_text(encoding="utf-8")
    return guide.split("**Neu in V0.9.9", 1)[1].split("**Neu in V0.9.8", 1)[0]


def test_versions_and_changelog():
    from client.uploader.uploader import CLIENT_VERSION
    from server.main import VERSION

    assert VERSION == "0.9.9"
    assert CLIENT_VERSION == "0.9.4", "V0.9.9 changes the server and the web page only"
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert changelog.split("\n## ", 2)[1].startswith("V0.9.9")
    entry = changelog.split("\n## V0.9.9", 1)[1].split("\n## V0.9.8", 1)[0]
    for needle in ("0017", "Known limits", "held", "INTEGRITY_FAILED", "downgrade 0016"):
        assert needle in entry, needle
    assert "server 0.9.9 / desktop client 0.9.4" in (ROOT / "README.md").read_text(encoding="utf-8")


def test_the_update_backs_up_first_signs_the_laps_and_has_a_way_back_over_the_migration():
    section = _section()
    assert "Garage16_Lmu_V0_9_9" in section and "/volumeUSB2/usbshare/garage16-backups" in section
    assert section.index("backup.sh") < section.index("up -d --build") < section.index("/health/ready")
    assert section.index("integrity_check'") < section.index("--apply --backfill")
    assert "revalidate_laps --hold'" in section and "down -v" not in section
    back = section.split("Zurück auf V0.9.8", 1)[1]
    assert back.index("alembic downgrade 0016") < back.index("--profile tunnel down")
    assert "LMU_GARAGE_INTEGRITY_KEY" in section


def test_the_migration_is_the_head_additive_and_reversible():
    versions = sorted(p.name for p in (ROOT / "alembic/versions").glob("0*.py"))
    assert versions[-1].startswith("0017_")
    text = (ROOT / "alembic/versions" / versions[-1]).read_text(encoding="utf-8")
    upgrade = text.split("def upgrade", 1)[1].split("def downgrade", 1)[0]
    downgrade = text.split("def downgrade", 1)[1]
    assert "add_column" in upgrade and "create_table" in upgrade and "drop" not in upgrade.lower()
    assert "nullable=True" in upgrade and "drop_table(\"audit_events\")" in downgrade
    assert 'revision: str = "0017"' in text and 'down_revision: Union[str, None] = "0016"' in text


def test_the_new_documents_exist_and_point_at_real_things():
    for name in ("V0_9_9_BESTANDSAUFNAHME.md", "V0_9_9_PROTOKOLL.md", "V0_9_9_NAS_ANLEITUNG.md",
                 "ARCHITECTURE.md", "RUNBOOKS.md", "DATA_PROTECTION.md", "QUALITY.md"):
        text = (ROOT / "docs" / name).read_text(encoding="utf-8")
        for ref in [w.strip("`,.:;()") for w in text.split() if w.startswith(("`scripts/", "`server/", "`tests/"))]:
            ref = ref.split("`")[0]
            if ref.endswith((".py", ".md", ".sh", ".json", ".mjs")):
                assert (ROOT / ref).exists(), f"{name} points at {ref}"
    guide = (ROOT / "docs/V0_9_9_NAS_ANLEITUNG.md").read_text(encoding="utf-8")
    for part in ("Teil A", "Teil B", "Teil C", "Teil D", "Teil E", "Teil F", "Wenn es nicht klappt", "Zurückschicken"):
        assert part in guide, part
    assert (ROOT / "docs/trackcheck_labels_vorlage.csv").read_text(encoding="utf-8").startswith("lap_id,label,note")
    runbooks = (ROOT / "docs/RUNBOOKS.md").read_text(encoding="utf-8")
    for topic in ("Quarantäne", "aus dem Backup zurückholen", "abgebrochenen Datei-Operationen",
                  "verdächtigen Upload", "Sicherer Rückweg", "Integritäts-Schlüssel", "Audit-Kette"):
        assert topic in runbooks, topic
    quality = (ROOT / "docs/QUALITY.md").read_text(encoding="utf-8")
    for needle in ("VALID", "INVALID", "REVIEW", "not a probability", "Limits of the detection", "real laps"):
        assert needle in quality, needle


def test_the_image_has_the_new_scripts_and_compose_passes_the_new_settings():
    dockerfile = (ROOT / "docker/Dockerfile.server").read_text(encoding="utf-8")
    for script in ("integrity_check.py", "trackcheck_eval.py", "bench_server.py"):
        assert f"COPY scripts/{script} ./scripts/{script}" in dockerfile
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "LMU_GARAGE_INTEGRITY_KEY: ${LMU_GARAGE_INTEGRITY_KEY:-}" in compose
    assert "LMU_GARAGE_AUDIT_RETENTION_DAYS" in compose
    env = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "LMU_GARAGE_INTEGRITY_KEY=" in env and "LMU_GARAGE_AUDIT_RETENTION_DAYS" in env


def test_the_gate_checks_the_archive_and_measures_the_new_logic():
    import yaml

    ci = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    steps = " ".join(str(s.get("run", "")) for s in ci["jobs"]["tests"]["steps"])
    assert "--zip release-archive/" in steps and "--verify-archive release-archive/" in steps
    floors = json.loads((ROOT / "tests/coverage_floors.json").read_text(encoding="utf-8"))["files"]
    for module in ("server/lap_decision.py", "server/integrity.py", "server/audit.py", "server/official.py"):
        assert module in floors, module
    from scripts.mutation_check import TARGETS

    for target in ("lap_decision", "integrity", "official", "upload_limits", "audit_chain", "decision_status"):
        assert target in TARGETS
    from scripts.release_check import EXCLUDE_DIRS

    assert {".hypothesis", "release-archive", "release-verify"} <= EXCLUDE_DIRS
