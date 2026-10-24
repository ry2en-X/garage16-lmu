"""V0.9.4 — the update is documented as it works, versions and texts agree."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _section():
    guide = (ROOT / "docs/ADMIN_GUIDE.md").read_text(encoding="utf-8")
    return guide.split("**Neu in V0.9.4", 1)[1].split("**Neu in V0.9.3.1", 1)[0]


def test_the_update_backs_up_first_and_fills_the_figures_after_a_dry_run():
    section = _section()
    assert "/volume1/homes/Moritz/garage16/Garage16_Lmu_V0_9_4" in section and "/volumeUSB2/usbshare/garage16-backups" in section
    assert section.index("backup.sh") < section.index("up -d --build")
    assert section.index("python -m scripts.revalidate_laps'") < section.index("revalidate_laps --apply'")
    assert "--update-validity'" not in section and "--restore'" not in section, "no command of the update changes a lap's validity"
    assert "0015" in section and "alembic downgrade 0014" in section and "down -v" not in section
    assert "py -m scripts.track_limits_probe" in section


def test_versions_and_changelog():
    from client.uploader.uploader import CLIENT_VERSION
    from server.main import VERSION

    # V0.9.5 is the current server (tests/test_v095_docs.py); the desktop client stays 0.9.4
    assert CLIENT_VERSION == "1.0.0" and VERSION == "1.0.0"  # V1.0 moved both on
    assert "\n## V0.9.4 — " in (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert '#define AppVersion "1.0.0"' in (ROOT / "packaging/version.iss").read_text(encoding="utf-8")


def test_the_protocol_has_the_stint_point_and_the_probe_records_it():
    protocol = (ROOT / "docs/LMU_VERIFICATION_PROTOCOL.md").read_text(encoding="utf-8")
    assert "## (w) V0.9.4" in protocol
    probe = (ROOT / "scripts/track_limits_probe.py").read_text(encoding="utf-8")
    for field in ("num_pitstops", "compound_front", "time_gap_ahead", "flag"):
        assert field in probe


def test_the_migration_is_the_new_head_and_purely_additive():
    source = (ROOT / "alembic/versions/0015_stint_analyzer.py").read_text(encoding="utf-8")
    assert 'down_revision: Union[str, None] = "0014"' in source
    upgrade = source.split("def upgrade", 1)[1].split("def downgrade", 1)[0]
    assert "update(" not in upgrade and "drop" not in upgrade


def test_notices_and_guides_mention_the_two_dashboards():
    notices = (ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    assert "## V0.9.4" in notices and "Theil" in notices
    friends = (ROOT / "docs/FRIENDS_GUIDE.md").read_text(encoding="utf-8")
    assert "LapTime Analyzer" in friends and "Stint Analyzer" in friends
