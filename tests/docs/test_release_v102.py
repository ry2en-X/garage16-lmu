"""V1.0.2 — versions, changelog and the NAS update guide stay in step."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DB_URL = 'GARAGE16_DB_URL="postgresql+psycopg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@${POSTGRES_HOST}:${POSTGRES_PORT}/${POSTGRES_DB}"'


def _read(name):
    return (ROOT / name).read_text(encoding="utf-8")


def _section():
    return _read("docs/ADMIN_GUIDE.md").split("### 8.2 Neu in V1.0.2", 1)[1].split("\n### ", 1)[0]


def test_versions_and_changelog():
    from client.uploader.uploader import CLIENT_VERSION
    from server.core.versioning import LATEST_CLIENT_VERSION
    from server.main import VERSION

    assert tuple(int(x) for x in VERSION.split(".")) >= (1, 0, 2)
    assert CLIENT_VERSION == VERSION == LATEST_CLIENT_VERSION
    changelog = _read("CHANGELOG.md")
    assert changelog.index("\n## V1.0.2 — ") < changelog.index("\n## V1.0.1 — ")
    entry = changelog.split("\n## V1.0.2 — ", 1)[1].split("\n## V1.0.1 — ", 1)[0]
    for needle in ("0020", "/api/v1", "GARAGE16_", "Cloudflare Pages", "telemetry-store"):
        assert needle in entry, needle


def test_the_update_backs_up_first_and_has_a_way_back_over_the_migration():
    section = _section()
    assert "Garage16_Lmu_V1_0_2" in section and "Garage16_Lmu_V1_0_1" in section and '"$BACKUPS"' in section
    assert section.index("backup.sh") < section.index("up -d --build") < section.index("/health/ready")
    assert section.index("up -d --build") < section.index("scripts.ops.consistency_check")
    assert "scripts/ops/backup.sh" in section, "the DSM task must point at the new script path"
    assert "down -v" not in section
    back = section.split("Zurück auf V1.0.1", 1)[1]
    assert DB_URL in back and back.index("alembic downgrade 0019") < back.index("--profile tunnel down")


def test_the_old_env_names_still_work_and_are_documented():
    assert "LMU_GARAGE_" in _read(".env.example")
    assert "${LMU_GARAGE_" in _read("docker-compose.yml"), "compose falls back to the old names"


def test_source_folders_named_like_output_folders_are_released_and_tracked():
    """V1.0.2 regression: scripts/release/ was dropped from the release tree (and ignored by git),
    because "release" was excluded at any depth — the CI gate then could not import it."""
    import subprocess

    from scripts.release.release_check import released_files

    files = {str(p).replace("\\", "/") for p in released_files()}
    for name in ("scripts/release/__init__.py", "scripts/release/release_check.py", "scripts/release/build_web.py",
                 "scripts/release/test_requirements.py"):
        assert name in files, name
    assert not any(f.startswith(("release/", "build/", "dist/")) for f in files)
    top = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=ROOT, capture_output=True, text=True)
    if top.returncode != 0 or Path(top.stdout.strip()).resolve() != ROOT.resolve():
        return   # not the checkout itself (an unpacked release, or the gate's staged copy inside one)
    ignored = subprocess.run(["git", "check-ignore", "scripts/release/build_web.py", "scripts/release/__init__.py"],
                             cwd=ROOT, capture_output=True, text=True)
    assert ignored.stdout.strip() == "", ignored.stdout
