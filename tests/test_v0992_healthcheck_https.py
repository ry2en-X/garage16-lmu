"""V0.9.9.2 regression (found on the first real NAS update): docker-compose.yml runs the server
with LMU_GARAGE_REQUIRE_HTTPS=1 and its healthcheck probes /health/ready over plain HTTP inside the
container. The HTTPS middleware exempted only /health, so the probe got 400 ("HTTPS required"), the
server never became healthy, and the reverse proxy never started. Every path a container healthcheck
uses must be answered without X-Forwarded-Proto; everything else must still be refused."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from server.config import settings

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture()
def https_required(client, monkeypatch):
    monkeypatch.setattr(settings, "require_https", True)  # like production (docker-compose.yml)
    return client


@pytest.mark.parametrize("path", ["/health", "/health/live", "/health/ready"])
def test_health_endpoints_answer_over_plain_http_when_https_is_required(https_required, path):
    r = https_required.get(path)
    assert r.status_code in (200, 503), (path, r.status_code, r.text)
    assert "HTTPS required" not in r.text


def test_everything_else_still_needs_https(https_required):
    assert https_required.get("/leaderboard/catalog/tracks").status_code == 400
    assert https_required.get("/health/ready/../../accounts/me").status_code in (400, 404)
    ok = https_required.get("/leaderboard/catalog/tracks", headers={"X-Forwarded-Proto": "https"})
    assert ok.status_code == 200


def test_the_compose_healthcheck_probes_an_exempt_path_and_compose_requires_https():
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert 'LMU_GARAGE_REQUIRE_HTTPS: "1"' in compose
    probed = set(re.findall(r"http://localhost:8000(/[a-z/]+)", compose))
    assert "/health/ready" in probed
    main = (ROOT / "server/main.py").read_text(encoding="utf-8")
    exempt = re.search(r"request\.url\.path not in \(([^)]*)\)", main).group(1)
    for path in probed:
        assert f'"{path}"' in exempt, f"the compose healthcheck probes {path}, which the HTTPS middleware refuses"
