"""client/api_paths.py — where the client finds the server's API.

From server 1.0.2 on, the API lives under /api/v1 (server/main.py). A server that is still on
1.0.1 or older only knows the old paths: before it the reverse proxy answers /api/v1/... with the
website (200 + HTML) or 405 (a POST to a static file), the app itself with 404. The client then
uses the old path for that server — once found out, remembered for the rest of the run — so a
client 1.0.2 works against a NAS that has not been updated yet.

/health stays where it always was (both server generations answer it there).
"""

from __future__ import annotations

import threading
from typing import Dict

# new path -> the same endpoint on a server before 1.0.2
LEGACY: Dict[str, str] = {
    "/api/v1/laps/upload": "/telemetry/upload",
    "/api/v1/accounts/desktop-login": "/accounts/desktop-login",
    "/api/v1/accounts/app-session": "/accounts/app-session",
}

_old_servers: set = set()
_lock = threading.Lock()


def _base(api_url: str) -> str:
    return (api_url or "").rstrip("/")


def url_for(api_url: str, path: str) -> str:
    """The full URL of `path` (a new /api/v1 path) on this server."""
    base = _base(api_url)
    with _lock:
        old = base in _old_servers
    return base + (LEGACY.get(path, path) if old else path)


def looks_like_an_older_server(resp) -> bool:
    """The answer of a server before 1.0.2 to an /api/v1 path."""
    content_type = ((getattr(resp, "headers", None) or {}).get("content-type") or "").lower()
    return resp.status_code in (404, 405) or (200 <= resp.status_code < 300 and "text/html" in content_type)


def fall_back(api_url: str, path: str, resp) -> bool:
    """True (and remembered) when `resp` says this server is older and `path` has an old twin —
    the caller then repeats the request with url_for(api_url, path)."""
    base = _base(api_url)
    with _lock:
        if path not in LEGACY or base in _old_servers or not looks_like_an_older_server(resp):
            return False
        _old_servers.add(base)
    return True


def forget() -> None:
    """Tests: start again without remembered servers."""
    with _lock:
        _old_servers.clear()
