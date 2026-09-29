"""The reverse proxy in front of the app (docker/Caddyfile) — V0.8.8.

Why this exists: every other test talks to the app directly through
TestClient, so nothing ever exercised the Caddyfile, and it was broken in
two ways that only showed up on the real NAS:
  1. `handle /teams /teams/* {` is invalid Caddyfile syntax — Caddy refuses
     to start (found on the NAS in V0.6.8, hand-fixed there, never fixed in
     the project, so every fresh deploy from the ZIP hit it again).
  2. /drivers and /invitations (V0.7.2 features) were never routed — Caddy
     answered them with the web frontend's index.html instead of JSON.

Two layers:
  - static guard, always runs: every API prefix the app really serves
    (from its OpenAPI schema) and every prefix the frontend calls must be
    in the Caddyfile's API matcher, and the multi-path-`handle` mistake is
    rejected.
  - real Caddy in front of the real app, runs when a `caddy` binary is
    available (PATH, or CADDY_BIN=/path/to/caddy) and is SKIPPED — not
    faked — otherwise. Get one from
    https://github.com/caddyserver/caddy/releases (e.g. caddy_2.10.0_linux_amd64.tar.gz).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest
import requests

from tests.local_servers import RealAppServer, free_port

ROOT = Path(__file__).resolve().parent.parent
CADDYFILE = (ROOT / "docker" / "Caddyfile").read_text(encoding="utf-8")


def _api_matcher_paths() -> set:
    match = re.search(r"^\s*@api\s+path\s+(.+)$", CADDYFILE, re.MULTILINE)
    assert match, "docker/Caddyfile has no `@api path ...` matcher"
    return set(match.group(1).split())


def _covered(prefix: str, matchers: set) -> bool:
    return prefix in matchers or f"{prefix}/*" in matchers


# ------------------------------------------------------- static guards

def test_caddyfile_does_not_use_the_invalid_multi_path_handle_form():
    """`handle /a /b {` — two paths after `handle` — is a Caddy syntax error."""
    for lineno, line in enumerate(CADDYFILE.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        assert not re.match(r"handle\s+/\S+\s+/\S+", stripped), f"Caddyfile:{lineno}: multi-path handle is invalid Caddy syntax: {stripped}"


def test_every_api_prefix_the_app_serves_is_routed_by_caddy():
    from server.main import app

    matchers = _api_matcher_paths()
    prefixes = {"/" + p.strip("/").split("/")[0] for p in app.openapi()["paths"]}
    missing = sorted(p for p in prefixes if not _covered(p, matchers))
    assert missing == [], f"served by the app but not routed to it by Caddy (they'd get index.html): {missing}"


def test_every_prefix_the_frontend_calls_is_routed_by_caddy():
    api_js = (ROOT / "web" / "js" / "api.js").read_text(encoding="utf-8")
    called = set(re.findall(r'[`"](/[a-z]+)(?:[/`"?$])', api_js))
    assert {"/accounts", "/drivers", "/invitations", "/teams"} <= called  # the scan itself works
    missing = sorted(p for p in called if not _covered(p, _api_matcher_paths()))
    assert missing == [], f"the frontend calls these but Caddy doesn't route them: {missing}"


def test_compose_mounts_the_caddyfile_the_tests_check():
    assert "./docker/Caddyfile:/etc/caddy/Caddyfile:ro" in (ROOT / "docker-compose.yml").read_text(encoding="utf-8")


# --------------------------------------------- real Caddy, real app

CADDY_BIN = os.environ.get("CADDY_BIN") or shutil.which("caddy")
needs_caddy = pytest.mark.skipif(not CADDY_BIN, reason="no caddy binary (set CADDY_BIN or put caddy on PATH) — reverse-proxy integration test skipped, not faked")


@pytest.fixture
def proxied(client, tmp_path, monkeypatch):
    """Real Caddy running docker/Caddyfile (only the container-specific
    bits — upstream address, web root, log path, admin API — rewritten
    for localhost) in front of the real app. Yields
    (proxy_base_url, direct_server_url)."""
    from server.config import settings

    monkeypatch.setattr(settings, "require_https", True)  # like production: the app insists on X-Forwarded-Proto
    app_server = RealAppServer().start()
    web_dir = tmp_path / "web"
    shutil.copytree(ROOT / "web", web_dir)
    proxy_port = free_port()

    text = CADDYFILE.replace("server:8000", f"127.0.0.1:{app_server.port}")
    text = text.replace("/srv/web", str(web_dir)).replace("/data/access.log", str(tmp_path / "access.log"))
    (tmp_path / "Caddyfile").write_text("{\n    admin off\n}\n\n" + text, encoding="utf-8")

    proc = subprocess.Popen(
        [CADDY_BIN, "run", "--config", str(tmp_path / "Caddyfile"), "--adapter", "caddyfile"],
        env={**os.environ, "CADDY_SITE_ADDRESS": f":{proxy_port}", "XDG_DATA_HOME": str(tmp_path / "xdg"), "XDG_CONFIG_HOME": str(tmp_path / "xdgc")},
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )
    base = f"http://127.0.0.1:{proxy_port}"
    deadline = time.time() + 20
    while True:
        try:
            requests.get(base + "/health", timeout=1)
            break
        except requests.RequestException:
            if proc.poll() is not None:
                raise RuntimeError("caddy exited: " + proc.stderr.read().decode()[-1500:])
            if time.time() > deadline:
                proc.kill()
                raise RuntimeError("caddy did not come up")
            time.sleep(0.1)
    try:
        yield base, app_server.url
    finally:
        proc.terminate()
        proc.wait(timeout=10)
        app_server.stop()


def _register_via(base: str, name: str) -> dict:
    """Register THROUGH the proxy — the app runs with require_https on in
    these tests (like production), so a direct registration is refused."""
    resp = requests.post(base + "/accounts/register", params={"display_name": name}, timeout=5)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _is_json(resp) -> bool:
    return resp.headers.get("content-type", "").startswith("application/json")


@needs_caddy
def test_real_caddy_starts_and_routes_health_to_the_app(proxied):
    base, _ = proxied
    resp = requests.get(base + "/health", timeout=5)
    assert resp.status_code == 200 and _is_json(resp)
    assert resp.json()["status"] == "ok"


@needs_caddy
def test_real_caddy_routes_drivers_and_invitations_to_the_api_not_the_frontend(proxied, client):
    base, _ = proxied
    driver = _register_via(base, "ViaProxy")

    profile = requests.get(f"{base}/drivers/{driver['driver_id']}/public", timeout=5)
    assert profile.status_code == 200 and _is_json(profile), profile.text[:200]
    assert profile.json()["display_name"] == "ViaProxy"

    # unauthenticated: must be the API's JSON 401, NOT index.html with a 200
    for path in ("/invitations/mine", "/drivers/lookup?display_name=x", "/teams/mine", "/teams"):
        resp = requests.get(base + path, timeout=5)
        assert resp.status_code in (401, 403, 405, 422) and _is_json(resp), (path, resp.status_code, resp.text[:120])


@needs_caddy
def test_real_caddy_serves_the_frontend_and_spa_fallback(proxied):
    base, _ = proxied
    root = requests.get(base + "/", timeout=5)
    assert root.status_code == 200 and "text/html" in root.headers["content-type"]
    deep = requests.get(base + "/some/unknown/spa/route", timeout=5)
    assert deep.status_code == 200 and "text/html" in deep.headers["content-type"]  # try_files -> index.html


@needs_caddy
def test_real_caddy_forwards_https_so_the_app_accepts_the_request(proxied, client):
    """The V0.6.5 bug: without `header_up X-Forwarded-Proto https` the app
    (LMU_GARAGE_REQUIRE_HTTPS) rejected every request behind the proxy."""
    base, direct = proxied
    via_proxy = requests.post(base + "/accounts/register", params={"display_name": "Proxied"}, timeout=5)
    assert via_proxy.status_code == 200, via_proxy.text
    direct_call = requests.post(direct + "/accounts/register", params={"display_name": "Direct"}, timeout=5)
    assert direct_call.status_code >= 400  # same request without the proxy's header: refused


@needs_caddy
def test_real_caddy_passes_the_upload_path_and_a_lap_through(proxied, client, tmp_path):
    """A real desktop-client upload through the proxy (the path every friend uses)."""
    from client.telemetry.recorder import LapRecorder
    from client.uploader.uploader import Uploader
    from tests.test_integration_upload_flow import _write_recorded_lap

    base, _ = proxied
    account = _register_via(base, "Uploader")
    lap_dir = tmp_path / "laps"
    lap_dir.mkdir()
    _write_recorded_lap(lap_dir)
    uploader = Uploader(recorder=LapRecorder(data_dir=str(lap_dir)), backend_url=base, auth_token=account["auth_token"], client_secret=account["client_secret"])
    assert uploader.run_once() == 1
