"""V0.9.6 — versions, changelog, the update guide and the font licences agree."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _section():
    guide = (ROOT / "docs/ADMIN_GUIDE.md").read_text(encoding="utf-8")
    return guide.split("**Neu in V0.9.6", 1)[1].split("**Neu in V0.9.5", 1)[0]


def test_versions_and_changelog():
    from client.uploader.uploader import CLIENT_VERSION
    from server.main import VERSION

    # V0.9.9 is the current server (tests/test_v099_docs.py); the V0.9.6 entry stays
    assert VERSION == "0.9.9"
    assert CLIENT_VERSION == "0.9.4", "V0.9.6 changes the server and the web page only"
    assert "\n## V0.9.6 — " in (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")


def test_the_update_backs_up_first_and_has_a_way_back():
    section = _section()
    assert "/volume1/homes/Moritz/garage16/Garage16_Lmu_V0_9_6" in section and "/volumeUSB2/usbshare/garage16-backups" in section
    assert section.index("backup.sh") < section.index("up -d --build")
    assert "down -v" not in section and "Garage16_Lmu_V0_9_5/Garage16 - LMU\"\nsudo docker compose --profile tunnel up -d" in section
    assert "Keine Datenbank-Migration" in section


def test_fonts_are_bundled_with_their_licence_and_nothing_loads_from_google():
    for name in ("oswald-latin-wght.woff2", "ibm-plex-mono-latin-400.woff2", "ibm-plex-mono-latin-500.woff2",
                 "ibm-plex-mono-latin-600.woff2", "OFL-Oswald.txt", "OFL-IBM-Plex-Mono.txt"):
        assert (ROOT / "web/fonts" / name).is_file(), name
    assert "SIL Open Font License" in (ROOT / "web/fonts/OFL-Oswald.txt").read_text(encoding="utf-8")
    assert "Oswald" in (ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    for path in (ROOT / "web").rglob("*"):
        if path.suffix in {".html", ".css", ".js"}:
            text = path.read_text(encoding="utf-8")
            assert "fonts.googleapis.com" not in text and "fonts.gstatic.com" not in text, path


def test_no_migration_in_this_release():
    # V0.9.6 added none: 0015 (V0.9.4) is followed directly by V0.9.7's 0016
    versions = sorted(p.name for p in (ROOT / "alembic/versions").glob("0*.py"))
    i = next(k for k, name in enumerate(versions) if name.startswith("0015_"))
    assert versions[i + 1].startswith("0016_")
