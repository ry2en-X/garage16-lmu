"""HTTP caching of the public API answers.

Everything under /api/v1/public/ is the same for every visitor — leaderboards, recent activity, the
catalog, driver profiles, the legal page's data. Those answers get a short `Cache-Control: public`
and an ETag, so

  * Cloudflare can keep them for that time (a Cache Rule makes /api/v1/public/* cacheable,
    docs/DEPLOYMENT.md): 100 visitors a minute cost the NAS one or two requests, not 100;
  * a browser asking again with If-None-Match gets a 304 without the data.

An endpoint that sets its own Cache-Control keeps it (the legal data: 1 hour, the channel catalog:
5 minutes).

The analysis answers of a signed-in driver (a lap's figures, its sessions, corner history, best
sectors, a circuit outline, a driver's lap list …) get an ETag too, but `private, no-cache`: the
browser keeps them and asks "still the same?" — a repeat answer is a 304 without the data. The heavy
answers (compare, line, track surface, distribution, events) already decide on their ETag before
computing anything (server/api/v1/analysis.py) and are passed through unchanged.
"""

from __future__ import annotations

import hashlib
from typing import Dict, Tuple

PUBLIC_PREFIX = "/api/v1/public/"
PRIVATE_PREFIXES = ("/api/v1/laps/", "/api/v1/analysis/", "/api/v1/circuits/", "/api/v1/drivers/", "/api/v1/stints/")
PRIVATE_CACHE = "private, no-cache"

# path prefix below /api/v1/public/ -> seconds a copy may be used (first match wins)
MAX_AGE: Tuple[Tuple[str, int], ...] = (
    ("catalog/", 60),
    ("circuits", 60),
    ("page-meta", 60),
    ("", 30),       # leaderboards, activity, driver and team profiles
)


def max_age_for(path: str) -> int:
    rest = path[len(PUBLIC_PREFIX):]
    for prefix, seconds in MAX_AGE:
        if rest.startswith(prefix):
            return seconds
    return 30


def _matches(if_none_match: str, etag: str) -> bool:
    wanted = etag.removeprefix("W/").strip('"')
    for token in if_none_match.split(","):
        token = token.strip()
        if token == "*" or token.removeprefix("W/").strip('"') == wanted:
            return True
    return False


class CacheMiddleware:
    """Pure ASGI: buffers only the 200 answers of GET /api/v1/public/* and the analysis reads."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if (scope["type"] != "http" or scope["method"] != "GET"
                or not scope["path"].startswith((PUBLIC_PREFIX,) + PRIVATE_PREFIXES)):
            await self.app(scope, receive, send)
            return
        start: Dict = {}
        chunks = []

        async def capture(message):
            if message["type"] == "http.response.start":
                start.update(message)
                # not 200, or an endpoint that made its own validator: nothing to add, stream it on
                start["passthrough"] = message["status"] != 200 or any(
                    k.lower() == b"etag" for k, _ in message.get("headers", []))
                if start["passthrough"]:
                    await send(message)
                return
            if start["passthrough"]:
                await send(message)
                return
            chunks.append(message.get("body", b""))
            if message.get("more_body"):
                return
            await self._finish(scope, start, b"".join(chunks), send)

        await self.app(scope, receive, capture)

    async def _finish(self, scope, start, body: bytes, send):
        headers = [(k, v) for k, v in start.get("headers", []) if k.lower() != b"content-length"]
        names = {k.lower() for k, _ in headers}
        etag = next((v.decode("latin-1") for k, v in headers if k.lower() == b"etag"), None)
        if etag is None:
            etag = '"' + hashlib.blake2b(body, digest_size=12).hexdigest() + '"'
            headers.append((b"etag", etag.encode("latin-1")))
        public = scope["path"].startswith(PUBLIC_PREFIX)
        if b"cache-control" not in names:
            value = f"public, max-age={max_age_for(scope['path'])}" if public else PRIVATE_CACHE
            headers.append((b"cache-control", value.encode("latin-1")))
        if not public and b"vary" not in names:
            headers.append((b"vary", b"Authorization, Cookie"))
        request_headers = {k.lower(): v for k, v in scope.get("headers", [])}
        if_none_match = request_headers.get(b"if-none-match", b"").decode("latin-1")
        if if_none_match and _matches(if_none_match, etag):
            keep = [(k, v) for k, v in headers if k.lower() not in (b"content-type",)]
            await send({"type": "http.response.start", "status": 304, "headers": keep})
            await send({"type": "http.response.body", "body": b""})
            return
        headers.append((b"content-length", str(len(body)).encode("latin-1")))
        await send({"type": "http.response.start", "status": 200, "headers": headers})
        await send({"type": "http.response.body", "body": body})
