"""Builds the website for Cloudflare Pages: build/pages/, deployed by .github/workflows/pages.yml.

    python -m scripts.release.build_web [--out build/pages]

* Every file of web/ (except index.html, the README and the Pages function) goes to
  /v/<hash>/..., where <hash> is taken from the files' content: a changed file gets a new address,
  so the files may be cached for a year (`immutable`) and no browser ever mixes old and new ones.
  index.html — the only file asked for again on every visit — names that folder.
* _headers: the security headers of the pages (the same as docker/Caddyfile) and the long caching
  of /v/*. _routes.json: the Pages function (web/functions/_middleware.js) runs for everything but
  the plain files, so the files cost no function call.

The NAS serves web/ as it is, with /v/<version>/ (index.html as committed, see check_index_version).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "web"
TOP_LEVEL = ("favicon.ico", "favicon.svg", "icon-192.png", "apple-touch-icon.png", "robots.txt", "manifest.webmanifest")
NOT_SHIPPED = ("README.md", "functions", "index.html")
ASSET_PREFIX = re.compile(r'/v/[0-9A-Za-z.\-]+/')

CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
       "font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; "
       "frame-ancestors 'none'")

HEADERS = f"""# written by scripts/release/build_web.py — the same security headers as docker/Caddyfile
/*
  Content-Security-Policy: {CSP}
  X-Content-Type-Options: nosniff
  X-Frame-Options: DENY
  Referrer-Policy: strict-origin-when-cross-origin
  Permissions-Policy: camera=(), microphone=(), geolocation=(), payment=()

/v/*
  Cache-Control: public, max-age=31536000, immutable
"""


def asset_files():
    for path in sorted(WEB.rglob("*")):
        rel = path.relative_to(WEB)
        if path.is_file() and rel.parts[0] not in NOT_SHIPPED and rel.name not in TOP_LEVEL and "__pycache__" not in rel.parts:
            yield path, rel


def content_hash() -> str:
    digest = hashlib.sha256()
    for path, rel in asset_files():
        digest.update(str(rel).replace("\\", "/").encode("utf-8") + b"\0")
        digest.update(path.read_bytes())
    return digest.hexdigest()[:12]


def build(out: Path) -> str:
    version = content_hash()
    if out.exists():
        shutil.rmtree(out)
    assets = out / "v" / version
    for path, rel in asset_files():
        target = assets / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
    for name in TOP_LEVEL:
        if (WEB / name).exists():
            shutil.copy2(WEB / name, out / name)
    index = (WEB / "index.html").read_text(encoding="utf-8")
    (out / "index.html").write_text(ASSET_PREFIX.sub(f"/v/{version}/", index), encoding="utf-8")
    (out / "_headers").write_text(HEADERS, encoding="utf-8")
    routes = {"version": 1, "include": ["/*"], "exclude": ["/v/*", *[f"/{n}" for n in TOP_LEVEL]]}
    (out / "_routes.json").write_text(json.dumps(routes, indent=2) + "\n", encoding="utf-8")
    return version


def index_asset_versions() -> set:
    """The /v/<x>/ folders web/index.html names (the release gate: exactly the server VERSION)."""
    return set(re.findall(r'/v/([0-9A-Za-z.\-]+)/', (WEB / "index.html").read_text(encoding="utf-8")))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default=str(ROOT / "build" / "pages"))
    args = parser.parse_args()
    version = build(Path(args.out))
    print(f"website built into {args.out} (assets /v/{version}/)")


if __name__ == "__main__":
    main()
