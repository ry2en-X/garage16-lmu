"""The website for local development (V1.0.2): python -m scripts.dev.serve_web [--port 5173] [--api http://localhost:8000]

Serves web/ like Caddy and Cloudflare Pages do: /v/<version>/... are the files of web/, every page
address (/leaderboard/fuji-speedway/hypercar, /driver/…) gets index.html, and /api/* plus /health go
to the API (the dev server, `uvicorn server.main:app --reload`). So config.js stays as committed and
cookies work as in production (one address for pages and API).

Standard library only; not for production.
"""

from __future__ import annotations

import argparse
import http.server
import re
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "web"
ASSET = re.compile(r"^/v/[^/]+(/.*)$")


class Handler(http.server.SimpleHTTPRequestHandler):
    api = "http://localhost:8000"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB), **kwargs)

    def _is_api(self) -> bool:
        path = self.path.split("?", 1)[0]
        return path.startswith("/api/") or path == "/health" or path.startswith("/health/")

    def _proxy(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None
        headers = {k: v for k, v in self.headers.items() if k.lower() not in ("host", "content-length", "connection")}
        request = urllib.request.Request(self.api + self.path, data=body, method=self.command, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=60) as resp:   # noqa: S310  # nosec B310 — the developer's own API (http://localhost)
                status, resp_headers, data = resp.status, resp.headers.items(), resp.read()
        except urllib.error.HTTPError as exc:
            status, resp_headers, data = exc.code, exc.headers.items(), exc.read()
        except OSError as exc:
            self.send_error(502, f"API not reachable at {self.api}: {exc}")
            return
        self.send_response(status)
        for key, value in resp_headers:
            if key.lower() not in ("transfer-encoding", "connection", "content-length"):
                self.send_header(key, value)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _rewrite(self) -> None:
        path, _, query = self.path.partition("?")
        m = ASSET.match(path)
        if m:
            path = m.group(1)
        elif not (WEB / path.lstrip("/")).is_file():
            path = "/index.html"                       # a page address: the router draws it
        self.path = path + (f"?{query}" if query else "")

    def do_GET(self):  # noqa: N802 — http.server's naming
        if self._is_api():
            return self._proxy()
        self._rewrite()
        return super().do_GET()

    def do_HEAD(self):  # noqa: N802
        self._rewrite()
        return super().do_HEAD()

    def do_POST(self):  # noqa: N802
        return self._proxy()

    do_PUT = do_PATCH = do_DELETE = do_POST

    def end_headers(self):
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=5173)
    parser.add_argument("--api", default="http://localhost:8000")
    args = parser.parse_args()
    Handler.api = args.api.rstrip("/")
    print(f"Garage16 website on http://localhost:{args.port}  (API: {Handler.api})")
    http.server.ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
