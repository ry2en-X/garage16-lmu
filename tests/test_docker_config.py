"""Static Docker/deployment configuration checks (V0.8.6 §13). These
never start Docker (no daemon in the dev sandbox — a real `docker
compose up` on the NAS remains BLOCKED, USER TEST REQUIRED); they make
sure the files it would consume are internally consistent so a whole
class of "works in dev, misconfigured on the NAS" mistakes fails here."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
COMPOSE = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
ENV_EXAMPLE = (ROOT / ".env.example").read_text(encoding="utf-8")


def test_compose_is_valid_and_has_the_expected_services_and_volumes():
    assert {"db", "server", "discord_bot", "reverse_proxy"} <= set(COMPOSE["services"])
    assert {"db_data", "telemetry_data"} <= set(COMPOSE["volumes"])  # persistence


def test_every_lmu_garage_variable_compose_references_is_documented_in_env_example():
    referenced = set(re.findall(r"\$\{(LMU_GARAGE_[A-Z_]+)", (ROOT / "docker-compose.yml").read_text(encoding="utf-8")))
    documented = set(re.findall(r"^#?\s*(LMU_GARAGE_[A-Z_]+)=", ENV_EXAMPLE, re.MULTILINE))
    assert referenced - documented == set()


def test_client_update_and_migration_settings_reach_the_server_container():
    """Without these in compose, the operator couldn't set them on the
    NAS at all — the settings would exist in server/config.py but never
    arrive inside the container."""
    env = COMPOSE["services"]["server"]["environment"]
    for var in ("LMU_GARAGE_MIGRATED_TO", "LMU_GARAGE_CLIENT_DOWNLOAD_URL", "LMU_GARAGE_MIN_CLIENT_VERSION"):
        assert var in env, var


def test_data_services_restart_after_a_nas_reboot_and_use_named_volumes():
    for name in ("db", "server"):
        assert COMPOSE["services"][name]["restart"] == "unless-stopped"
    db_volumes = " ".join(COMPOSE["services"]["db"]["volumes"])
    assert "db_data" in db_volumes
    assert "telemetry_data" in " ".join(COMPOSE["services"]["server"]["volumes"])
