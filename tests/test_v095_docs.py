"""V0.9.5 — versions, changelog and the update guide agree."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _section():
    guide = (ROOT / "docs/ADMIN_GUIDE.md").read_text(encoding="utf-8")
    return guide.split("**Neu in V0.9.5", 1)[1].split("**Neu in V0.9.4", 1)[0]


def test_versions_and_changelog():
    from client.uploader.uploader import CLIENT_VERSION
    from server.main import VERSION

    # V0.9.9 is the current server (tests/test_v099_docs.py); the V0.9.5 entry stays
    assert VERSION == "1.0.0"  # V1.0 moved both on
    assert CLIENT_VERSION == "1.0.0", "V0.9.5 changes the server and the web page only (client 1.0.0 since V1.0)"
    assert "\n## V0.9.5 — " in (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")


def test_the_update_backs_up_first_and_changes_no_validity():
    section = _section()
    assert "/volume1/homes/Moritz/garage16/Garage16_Lmu_V0_9_5" in section and "/volumeUSB2/usbshare/garage16-backups" in section
    assert section.index("backup.sh") < section.index("up -d --build")
    assert section.index("python -m scripts.revalidate_laps'") < section.index("revalidate_laps --apply'")
    assert "--update-validity" not in section and "--restore" not in section and "down -v" not in section
    assert "Keine Datenbank-Migration" in section


def test_no_migration_in_this_release():
    versions = sorted(p.name for p in (ROOT / "alembic/versions").glob("0*.py"))
    assert versions[versions.index("0015_stint_analyzer.py") + 1].startswith("0016_"), "V0.9.5 added none; the next one is V0.9.7's"


def test_the_grid_editor_is_documented_for_friends():
    friends = (ROOT / "docs/FRIENDS_GUIDE.md").read_text(encoding="utf-8")
    assert "Raster" in friends and "Wirkende Kräfte" in friends and "Kasten" in friends
