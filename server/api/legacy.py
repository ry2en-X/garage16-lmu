"""The API paths from before V1.0.2, translated to /api/v1.

Until V1.0.1 the API lived on the same paths as the website's pages (/leaderboard, /teams, /legal).
Since V1.0.2 it lives under /api/v1 and the pages get readable paths of their own. Desktop clients
1.0.1 and older keep calling the old paths (upload, sign-in, /health), so this middleware rewrites
an old path to its new one INSIDE the server before routing — no redirect, so a POST keeps its body
and an old client sees exactly the answer it got before.

The reverse proxy decides which old paths reach the server at all (is_legacy_api_request below,
docker/Caddyfile, web/functions/_middleware.js): the old API paths go to the API, the page
addresses that look alike (/leaderboard/fuji-speedway, /teams) to the website. Remove this module once `min_client_version` is at least 1.0.2 and
no browser tab of an older page can still be open.
"""

from __future__ import annotations

import re
from typing import List, Optional, Tuple
from urllib.parse import quote

API_PREFIX = "/api/v1"

# (old path pattern, new path below /api/v1) — first match wins, so the specific ones come first
LEGACY_RULES: List[Tuple[re.Pattern, str]] = [(re.compile(p), r) for p, r in (
    (r"^/telemetry/upload$", "/laps/upload"),
    (r"^/telemetry/laps$", "/laps"),
    (r"^/telemetry/laps/([^/]+)/report$", r"/laps/\1/report"),
    (r"^/telemetry/analysis/channels$", "/public/analysis/channels"),
    (r"^/telemetry/analysis/laps/([^/]+)$", r"/laps/\1/analysis"),
    (r"^/telemetry/analysis/laps/([^/]+)/health$", r"/laps/\1/health"),
    (r"^/telemetry/analysis/drivers/([^/]+)/laps$", r"/drivers/\1/laps"),
    (r"^/telemetry/analysis/circuits$", "/public/circuits"),
    (r"^/telemetry/analysis/circuits/outline$", "/circuits/outline"),
    (r"^/telemetry/analysis/stints/([a-z-]+)$", r"/stints/\1"),
    (r"^/telemetry/analysis/([a-z-]+)$", r"/analysis/\1"),
    (r"^/leaderboard/recent$", "/public/activity"),
    (r"^/leaderboard/(class|car)/(.+)$", r"/public/leaderboards/\1/\2"),
    (r"^/leaderboard/catalog/([a-z-]+)$", r"/public/catalog/\1"),
    (r"^/drivers/([^/]+)/public$", r"/public/drivers/\1"),
    (r"^/drivers/lookup$", "/drivers/lookup"),
    (r"^/legal$", "/public/legal"),
    (r"^/(accounts|teams|invitations|admin)((?:/.*)?)$", r"/\1\2"),
)]

# Which requests the reverse proxies send to the server on the OLD paths (docker/Caddyfile,
# web/functions/_middleware.js — tests/api/test_api_paths.py keeps the three equal). Precise on
# purpose: /leaderboard/<track>/<class>, /teams and /driver/<name> are pages now, and a link preview
# bot asking for a page must get the page, whatever Accept header it sends.
LEGACY_API_PREFIXES = ("/accounts/", "/telemetry/", "/drivers/", "/invitations/", "/admin/", "/teams/",
                       "/leaderboard/class/", "/leaderboard/car/", "/leaderboard/catalog/")
LEGACY_API_EXACT = ("/leaderboard/recent",)


def is_legacy_api_request(path: str, method: str = "GET", accept: str = "") -> bool:
    """True when a request on an old path is meant for the API, not for a page."""
    if path.startswith(LEGACY_API_PREFIXES) or path in LEGACY_API_EXACT:
        return True
    if path == "/teams":                      # POST /teams created a team; GET /teams is the page
        return method.upper() not in ("GET", "HEAD")
    if path == "/legal":                      # the old legal data; a browser gets the page
        return "text/html" not in (accept or "")
    return False


def translate(path: str) -> Optional[str]:
    """The /api/v1 path for an old API path, or None when `path` is not one."""
    if path.startswith(API_PREFIX + "/") or path == API_PREFIX:
        return None
    for pattern, replacement in LEGACY_RULES:
        if pattern.match(path):
            return API_PREFIX + pattern.sub(replacement, path)
    return None


class LegacyPathMiddleware:
    """Pure ASGI (no response buffering): rewrites scope["path"] for old API paths."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            new_path = translate(scope["path"])
            if new_path is not None:
                scope = dict(scope)
                scope["path"] = new_path
                scope["raw_path"] = quote(new_path, safe="/").encode("ascii")
                scope["garage16_legacy_path"] = True
        await self.app(scope, receive, send)
