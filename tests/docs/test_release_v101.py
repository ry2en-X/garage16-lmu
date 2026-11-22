"""Versions, changelog, the update guide with its way back over migration 0019, the new
settings passed to the container and documented, the legal texts' date, the security headers, and a
documentation tree whose links all resolve."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DB_URL = 'GARAGE16_DB_URL="postgresql+psycopg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@${POSTGRES_HOST}:${POSTGRES_PORT}/${POSTGRES_DB}"'
NEW_SETTINGS = ("GARAGE16_REQUIRE_EMAIL_VERIFICATION", "GARAGE16_UNVERIFIED_ACCOUNT_DAYS",
                "GARAGE16_NEW_DRIVER_REVIEW_LAPS", "GARAGE16_MAX_DEVICES", "GARAGE16_LATEST_CLIENT_VERSION")


def _read(name):
    return (ROOT / name).read_text(encoding="utf-8")


def _section():
    # V1.0.3: the chapter moved to the archive (the guide keeps the two newest updates)
    archive = (ROOT / "docs" / "archive" / "UPDATES_V1_0_1.md").read_text(encoding="utf-8")
    return archive.split("Neu in V1.0.1", 1)[1]


def test_versions_and_changelog():
    from client.uploader.uploader import CLIENT_VERSION
    from server.main import VERSION
    from server.core.versioning import LATEST_CLIENT_VERSION

    assert tuple(int(x) for x in VERSION.split(".")) >= (1, 0, 1)  # later releases keep this entry
    assert LATEST_CLIENT_VERSION == CLIENT_VERSION   # V1.0.3: the server may be ahead of the client
    changelog = _read("CHANGELOG.md")
    assert changelog.index("\n## V1.0.1 — ") < changelog.index("\n## V1.0.0 — ")
    entry = changelog.split("\n## V1.0.1 — ", 1)[1].split("\n## V1.0.0 — ", 1)[0]
    for needle in ("0019", "downgrade 0018", "device_tokens", "email_not_verified", "terms_required", "fair_play",
                   "Content-Security-Policy", "SHA256SUMS.txt", "docs/archive/"):
        assert needle in entry, needle
    assert "docs/archive/CHANGELOG_BIS_V0_8.md" in changelog and "\n## V0.8.10" not in changelog
    assert "\n## V0.8.10 — " in _read("docs/archive/CHANGELOG_BIS_V0_8.md")


def test_the_update_backs_up_first_and_has_a_way_back_over_the_migration():
    section = _section()
    assert 'G16=/volume1/homes/Moritz/garage16' in _read("docs/ADMIN_GUIDE.md")
    assert "Garage16_Lmu_V1_0_1" in section and "Garage16_Lmu_V1_0_0" in section and '"$BACKUPS"' in section
    assert section.index("backup.sh") < section.index("up -d --build") < section.index("/health/ready")
    assert section.index("up -d --build") < section.index("scripts.ops.consistency_check")
    assert "down -v" not in section
    back = section.split("Zurück auf V1.0.0", 1)[1]
    assert DB_URL in back and back.index("alembic downgrade 0018") < back.index("--profile tunnel down")
    assert "quicktunnel" in section, "the guide warns not to recreate the quick-tunnel container"


def test_the_migration_is_the_head_additive_and_reversible():
    versions = sorted(p.name for p in (ROOT / "alembic/versions").glob("0*.py"))
    assert "0019_names_devices.py" in versions   # 0020 follows it (tests for the head: tests/db/)
    text = _read("alembic/versions/0019_names_devices.py")
    upgrade = text.split("def upgrade", 1)[1].split("def downgrade", 1)[0]
    downgrade = text.split("def downgrade", 1)[1]
    assert 'revision: str = "0019"' in text and 'down_revision: Union[str, None] = "0018"' in text
    assert "add_column" in upgrade and "create_table" in upgrade and "drop" not in upgrade.lower()
    assert "nullable=True" in upgrade and 'drop_table("device_tokens")' in downgrade and 'drop_column("display_name_key")' in downgrade


def test_the_new_settings_reach_the_container_and_are_documented():
    compose = _read("docker-compose.yml")
    env = _read(".env.example")
    reference = _read("docs/DEPLOYMENT.md")
    for name in NEW_SETTINGS:
        assert f"      {name}: ${{{name}:-" in compose, name
        assert name in env and name in reference, name


def test_the_legal_texts_are_dated_like_the_terms_version():
    from server.api.v1.legal import TERMS_VERSION

    year, month, day = (int(x) for x in TERMS_VERSION.split("-"))
    german_months = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November", "Dezember"]
    english_months = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
    assert f'export const DATE = "{day}. {german_months[month - 1]} {year}";' in _read("web/js/legal/de.js")
    assert f'export const DATE = "{day} {english_months[month - 1]} {year}";' in _read("web/js/legal/en.js")
    for text in (_read("web/js/legal/de.js"), _read("web/js/legal/en.js")):
        assert "Verbundene PCs" in text or "Connected PCs" in text


def test_the_pages_are_served_with_security_headers_and_without_inline_scripts():
    caddy = _read("docker/Caddyfile")
    # One snippet, imported by the blocks that serve the pages and their files
    static = caddy.split("(page_headers) {", 1)[1].split("\n}\n", 1)[0]
    site = caddy.split("{$CADDY_SITE_ADDRESS:80} {", 1)[1]
    assert site.count("import page_headers") == 2 and "handle_path /v/* {\n        import page_headers" in site
    for header in ("Content-Security-Policy", "script-src 'self'", "frame-ancestors 'none'", "X-Content-Type-Options",
                   "X-Frame-Options", "Referrer-Policy", "Permissions-Policy"):
        assert header in static, header
    html = _read("web/index.html")
    assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", html), "an inline script would be blocked by the CSP"
    assert not re.search(r"\son[a-z]+=", html), "inline event handlers would be blocked by the CSP"
    assert 'name="description"' in html and 'property="og:title"' in html
    assert "Disallow: /accounts/" in _read("web/robots.txt")


def test_the_web_code_never_uses_inline_handlers_or_eval():
    for path in (ROOT / "web" / "js").rglob("*.js"):
        text = path.read_text(encoding="utf-8")
        assert "eval(" not in text and "new Function(" not in text, path
        assert "setAttribute(\"on" not in text and ".innerHTML = `" not in text, path


def test_every_relative_link_in_the_documentation_resolves():
    docs = [ROOT / "README.md", ROOT / "CHANGELOG.md"] + sorted((ROOT / "docs").rglob("*.md")) + [ROOT / "web" / "README.md"]
    broken = []
    for doc in docs:
        text = doc.read_text(encoding="utf-8")
        for target in re.findall(r"\]\(([^)#\s]+)(?:#[^)]*)?\)", text):
            if target.startswith(("http://", "https://", "mailto:")):
                continue
            if not (doc.parent / target).exists():
                broken.append(f"{doc.relative_to(ROOT)} -> {target}")
    assert broken == []


def test_the_docs_index_and_the_archive_list_what_is_there():
    index = _read("docs/README.md")
    for name in sorted(p.name for p in (ROOT / "docs").glob("*.md")):
        if name != "README.md":
            assert name in index, f"docs/README.md does not mention {name}"
    archive = _read("docs/archive/README.md")
    for path in sorted((ROOT / "docs" / "archive").iterdir()):
        if path.name != "README.md":
            assert f"`{path.name}" in archive, f"archive README does not list {path.name}"
    readme = _read("README.md")
    assert "all rights reserved" in readme.lower() and "THIRD_PARTY_NOTICES.md" in readme


def test_the_admin_guide_is_short_again_and_keeps_its_chapters():
    guide = _read("docs/ADMIN_GUIDE.md")
    assert guide.count("\n") < 700, "version-by-version update steps belong in docs/archive/"
    for chapter in range(1, 13):
        assert f"\n## {chapter}. " in guide, chapter
    assert "**Neu in V0.9" not in guide, "old update sections live in docs/archive/UPDATES_V0_9_BIS_V1_0.md"
