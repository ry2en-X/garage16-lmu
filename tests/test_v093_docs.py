"""V0.9.3 — the admin guide's commands must work as written.

Found while writing the V0.9.3 update steps: the server image copies scripts ONE BY ONE, so a
new script that the guide tells the admin to run inside the server container would simply be
missing there ("No module named scripts.xyz") unless the Dockerfile copies it too."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_every_script_the_admin_runs_in_the_server_container_is_in_the_image():
    guide = (ROOT / "docs/ADMIN_GUIDE.md").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    dockerfile = (ROOT / "docker/Dockerfile.server").read_text(encoding="utf-8")
    in_container = set()
    for text in (guide, readme):
        for line in text.splitlines():
            if "exec" in line and "server" in line:
                in_container.update(re.findall(r"python -m scripts\.([a-z_]+)", line))
    assert {"telemetry_cleanup", "car_identity_report", "revalidate_laps"} <= in_container
    for name in in_container:
        assert f"COPY scripts/{name}.py ./scripts/{name}.py" in dockerfile, f"scripts/{name}.py is not copied into the server image"
        assert (ROOT / "scripts" / f"{name}.py").is_file()


def test_the_pc_probe_named_in_the_guide_exists_and_needs_no_server():
    guide = (ROOT / "docs/ADMIN_GUIDE.md").read_text(encoding="utf-8")
    assert "py -m scripts.car_identity_probe" in guide
    source = (ROOT / "scripts/car_identity_probe.py").read_text(encoding="utf-8")
    assert "from server.car_identity import" in source
    assert "server.database" not in source and "server.config" not in source, "the probe runs on the PC without a database"


def test_the_update_steps_name_the_real_nas_folders_and_back_up_first():
    guide = (ROOT / "docs/ADMIN_GUIDE.md").read_text(encoding="utf-8")
    section = guide.split("**Neu in V0.9.3", 1)[1].split("**Neu in V0.9.2", 1)[0]
    assert "/volume1/homes/Moritz/garage16/Garage16_Lmu_V0_9_3" in section
    assert section.index("backup.sh") < section.index("up -d --build"), "back up before the migration runs"
    assert "/volumeUSB2/usbshare" in section
    assert "down -v" not in section.replace("ohne `-v`", "")
    assert "alembic downgrade 0012" in section
