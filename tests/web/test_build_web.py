"""The website for Cloudflare Pages (scripts/release/build_web.py) and the versioned files.

* the files go to /v/<content hash>/, index.html names exactly that folder, a changed file changes
  the hash (a year of caching never serves an old file);
* _headers carries the same security headers as docker/Caddyfile and the year-long caching of
  /v/*; _routes.json keeps the plain files away from the Pages function;
* the committed index.html names /v/<server version>/ (the NAS serves web/ as it is).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from scripts.release import build_web

ROOT = Path(__file__).resolve().parents[2]


def test_the_build_puts_the_files_under_a_content_hash(tmp_path):
    version = build_web.build(tmp_path / "pages")
    out = tmp_path / "pages"
    index = (out / "index.html").read_text(encoding="utf-8")
    assert set(re.findall(r'/v/([0-9A-Za-z.\-]+)/', index)) == {version}
    for rel in ("js/main.js", "js/core/router.js", "css/styles.css", "config.js", "fonts/oswald-latin-wght.woff2"):
        assert (out / "v" / version / rel).is_file(), rel
    assert not (out / "v" / version / "functions").exists() and not (out / "v" / version / "README.md").exists()
    for name in ("favicon.svg", "robots.txt", "icon-192.png"):
        assert (out / name).is_file()


def test_a_changed_file_changes_the_address(tmp_path, monkeypatch):
    web = tmp_path / "web"
    shutil.copytree(ROOT / "web", web)
    monkeypatch.setattr(build_web, "WEB", web)
    before = build_web.content_hash()
    (web / "js" / "core" / "format.js").write_text((web / "js" / "core" / "format.js").read_text() + "\n// x\n")
    assert build_web.content_hash() != before


def test_headers_and_routes_for_pages(tmp_path):
    build_web.build(tmp_path / "pages")
    headers = (tmp_path / "pages" / "_headers").read_text(encoding="utf-8")
    caddy = (ROOT / "docker" / "Caddyfile").read_text(encoding="utf-8")
    csp = re.search(r'Content-Security-Policy "([^"]+)"', caddy).group(1)
    assert f"Content-Security-Policy: {csp}" in headers, "the same policy as on the NAS"
    for header in ("X-Frame-Options: DENY", "X-Content-Type-Options: nosniff", "Referrer-Policy", "Permissions-Policy"):
        assert header in headers
    assert "/v/*\n  Cache-Control: public, max-age=31536000, immutable" in headers
    routes = json.loads((tmp_path / "pages" / "_routes.json").read_text())
    assert routes["include"] == ["/*"] and "/v/*" in routes["exclude"] and "/favicon.svg" in routes["exclude"]


def test_the_committed_index_names_this_release():
    from server.main import VERSION

    assert build_web.index_asset_versions() == {VERSION}, "web/index.html must load /v/<server VERSION>/ (bump it with the release)"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_the_pages_function_parses():
    out = subprocess.run(["node", "--check", str(ROOT / "web" / "functions" / "_middleware.js")], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
