"""The API under /api/v1, and the paths of V1.0.1 still answering for old clients.

* every path of the V1.0.1 API (tests/reference/openapi_v101_paths.txt, taken from the reviewed
  contract of V1.0.1) is translated by server/api/legacy.py to a path the app really serves;
* the reverse proxies send exactly the old API requests to the server and the look-alike page
  addresses to the website: docker/Caddyfile and the Pages function agree with
  server/api/legacy.py is_legacy_api_request;
* old and new path give the same answer.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from server.api.legacy import LEGACY_API_EXACT, LEGACY_API_PREFIXES, is_legacy_api_request, translate

ROOT = Path(__file__).resolve().parents[2]
OLD_PATHS = [line.split() for line in (ROOT / "tests" / "reference" / "openapi_v101_paths.txt").read_text().splitlines() if line.strip()]


def _new_paths():
    from server.main import app

    return app.openapi()["paths"]


def _pattern(path: str) -> re.Pattern:
    return re.compile("^" + re.sub(r"\\\{[^}]+\\\}", "[^/]+", re.escape(path)) + "$")


@pytest.mark.parametrize("method,old", OLD_PATHS, ids=[f"{m} {p}" for m, p in OLD_PATHS])
def test_every_v101_path_reaches_an_endpoint_of_the_same_kind(method, old):
    concrete = re.sub(r"\{[^}]+\}", "7", old)
    new = translate(concrete) if old not in ("/health", "/health/live", "/health/ready") else concrete
    assert new, f"{method} {old} has no translation"
    matches = [p for p, ops in _new_paths().items() if _pattern(p).match(new) and method.lower() in ops]
    assert matches, f"{method} {old} -> {new}: no such endpoint"


def test_nothing_is_translated_twice_and_new_paths_are_left_alone():
    assert translate("/api/v1/laps/upload") is None
    assert translate("/api/v1") is None
    assert translate("/driver/moritz-lachnit") is None and translate("/leaderboard") is None
    assert translate("/telemetry/upload") == "/api/v1/laps/upload"
    assert translate("/telemetry/analysis/laps/12") == "/api/v1/laps/12/analysis"
    assert translate("/leaderboard/class/Fuji Speedway/Hypercar") == "/api/v1/public/leaderboards/class/Fuji Speedway/Hypercar"


@pytest.mark.parametrize("method,path,accept,api", [
    ("POST", "/telemetry/upload", "*/*", True),
    ("GET", "/leaderboard/recent", "*/*", True),
    ("GET", "/leaderboard/class/Le Mans/Hypercar", "text/html", True),   # an old API path, even from a browser
    ("GET", "/leaderboard/fuji-speedway/hypercar", "*/*", False),       # a page, whatever a preview bot accepts
    ("GET", "/leaderboard", "*/*", False),
    ("GET", "/teams", "*/*", False),
    ("POST", "/teams", "*/*", True),
    ("GET", "/teams/mine", "*/*", True),
    ("GET", "/legal", "application/json", True),
    ("GET", "/legal", "text/html,application/xhtml+xml", False),
    ("GET", "/driver/moritz-lachnit", "*/*", False),
    ("GET", "/drivers/2/public", "*/*", True),
    ("GET", "/account", "*/*", False),
    ("POST", "/accounts/desktop-login", "*/*", True),
    ("GET", "/admin", "text/html", False),
    ("GET", "/admin/reports", "*/*", True),
])
def test_which_old_paths_the_proxies_send_to_the_api(method, path, accept, api):
    assert is_legacy_api_request(path, method, accept) is api


def test_caddy_routes_exactly_the_legacy_api_paths():
    caddy = (ROOT / "docker" / "Caddyfile").read_text(encoding="utf-8")
    block = re.search(r"@legacy_api \{\s*path ([^\n]+)\n", caddy)
    assert block, "docker/Caddyfile has no @legacy_api matcher"
    paths = set(block.group(1).split())
    assert paths == {p + "*" for p in LEGACY_API_PREFIXES} | set(LEGACY_API_EXACT)
    assert re.search(r"@legacy_teams \{\s*path /teams\s*not method GET HEAD", caddy)
    assert re.search(r"@legacy_legal \{\s*path /legal\s*not header Accept \*text/html\*", caddy)


def test_the_pages_function_routes_exactly_the_legacy_api_paths():
    js = (ROOT / "web" / "functions" / "_middleware.js").read_text(encoding="utf-8")
    prefixes = re.search(r"const LEGACY_API_PREFIXES = \[([^\]]+)\]", js).group(1)
    exact = re.search(r"const LEGACY_API_EXACT = \[([^\]]+)\]", js).group(1)
    assert set(re.findall(r'"([^"]+)"', prefixes)) == set(LEGACY_API_PREFIXES)
    assert set(re.findall(r'"([^"]+)"', exact)) == set(LEGACY_API_EXACT)
    assert 'pathname === "/teams"' in js and 'pathname === "/legal"' in js


def test_old_and_new_path_give_the_same_answer(client):
    from tests.helpers.v093 import headers, register

    me = register(client, "Path Twin")
    for old, new in (("/leaderboard/catalog/class-options", "/api/v1/public/catalog/class-options"),
                     ("/leaderboard/recent", "/api/v1/public/activity"),
                     ("/legal", "/api/v1/public/legal"),
                     (f"/drivers/{me['driver_id']}/public", f"/api/v1/public/drivers/{me['driver_id']}")):
        a, b = client.get(old), client.get(new)
        assert a.status_code == b.status_code == 200 and a.json() == b.json(), old
    a = client.get("/accounts/me", headers=headers(me))
    b = client.get("/api/v1/accounts/me", headers=headers(me))
    assert a.status_code == b.status_code == 200 and a.json() == b.json()


def test_the_website_calls_only_api_v1():
    api_js = (ROOT / "web" / "js" / "core" / "api.js").read_text(encoding="utf-8")
    calls = re.findall(r'request\(\s*[`"]([^`"]+)[`"]', api_js)
    assert calls and all(c.startswith("/api/v1/") for c in calls)
