"""V0.9.3.1 — the update steps, the rules and the probe are documented as they work."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _section():
    guide = (ROOT / "docs/ADMIN_GUIDE.md").read_text(encoding="utf-8")
    return guide.split("**Neu in V0.9.3.1", 1)[1].split("**Neu in V0.9.3 —", 1)[0]


def test_the_update_backs_up_first_and_judges_laps_in_a_dry_run_before_changing_any():
    section = _section()
    assert "/volume1/homes/Moritz/garage16/Garage16_Lmu_V0_9_3_1" in section and "/volumeUSB2/usbshare" in section
    assert section.index("backup.sh") < section.index("up -d --build")
    dry = section.index("python -m scripts.revalidate_laps'")
    assert dry < section.index("revalidate_laps --apply'") < section.index("revalidate_laps --apply --restore'")
    assert section.index("python -m scripts.car_identity_report'") < section.index("car_identity_report --apply'")
    assert "alembic downgrade 0013" in section and "down -v" not in section
    assert "py -m scripts.track_limits_probe" in section and "LMU_GARAGE_TRACK_LIMITS_INVALIDATING" in section


def test_the_setting_is_passed_to_the_container_and_explained():
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "LMU_GARAGE_TRACK_LIMITS_INVALIDATING: ${LMU_GARAGE_TRACK_LIMITS_INVALIDATING:-}" in compose
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    from server.track_limits import PROMOTABLE
    listed = set(re.findall(r"[a-z_]+", example.split("LMU_GARAGE_TRACK_LIMITS_INVALIDATING", 1)[0].rsplit("# Possible:", 1)[1]))
    assert PROMOTABLE <= listed, "every code that can be promoted is listed in .env.example"


def test_the_protocol_has_the_open_points_and_the_probe_exists():
    protocol = (ROOT / "docs/LMU_VERIFICATION_PROTOCOL.md").read_text(encoding="utf-8")
    for point in ("(r)", "(s)", "(t)", "(u)", "(v)"):
        assert f"## {point} V0.9.3.1" in protocol
    source = (ROOT / "scripts/track_limits_probe.py").read_text(encoding="utf-8")
    assert "server.database" not in source and "server.config" not in source, "runs on the PC without a server"


def test_the_correction_release_stays_in_the_changelog():
    # V0.9.4 is the current version (tests/test_v094_docs.py); the 0.9.3.1 entry stays
    assert "\n## V0.9.3.1 — " in (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
