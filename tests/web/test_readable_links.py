"""Readable links: /driver/moritz-lachnit, /team/hypermotion-esports,
/leaderboard/fuji-speedway/hypercar — and what a link preview (Discord, WhatsApp) sees.

* slugs (server/core/slugs.py) and the browser's twin (web/js/core/link.js) agree;
* a driver or team is found by its slug; after a rename the old link still leads there; a name
  that would share a link with another one is refused;
* every page path gets its own title and description (GET /api/v1/public/page-meta), unknown
  addresses a 404 — the same list of pages as the browser's route table (web/js/core/routes.js);
* GET /api/v1/public/page fills index.html for Caddy (the NAS serving the website itself).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from server.core.slugs import slugify
from tests.helpers.synthetic_lap import make_lap
from tests.helpers.v093 import headers, register, upload_df

ROOT = Path(__file__).resolve().parents[2]

SAMPLES = ["Moritz Lachnit", "HyperMotion Esports", "Fuji Speedway - Classic", "Porsche 963", "Circuit de la Sarthe",
           "Autodromo Enzo e Dino Ferrari (Imola)", "Müller · Ørsted", "Straße 1", "  --Max__1--  ", "ＭＡＸ", "张三",
           "Élan Vital!", "911 GT3 R", "397_25_499P", "LMGT3", "", "???"]


@pytest.mark.parametrize("name,slug", [
    ("Moritz Lachnit", "moritz-lachnit"), ("HyperMotion Esports", "hypermotion-esports"),
    ("Fuji Speedway - Classic", "fuji-speedway-classic"), ("Porsche 963", "porsche-963"),
    ("Müller · Ørsted", "muller-ørsted"), ("Straße 1", "strasse-1"), ("???", ""), ("911 GT3 R", "911-gt3-r"),
])
def test_slugs(name, slug):
    assert slugify(name) == slug


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_the_browser_makes_the_same_slugs():
    script = ("globalThis.window={location:{origin:'http://x'}};"
              "const {slugify}=await import('./web/js/core/link.js');"
              f"console.log(JSON.stringify({json.dumps(SAMPLES)}.map(slugify)));")
    out = subprocess.run(["node", "--input-type=module", "-e", script], cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout) == [slugify(s) for s in SAMPLES]


def test_a_driver_is_found_by_the_link_and_an_old_name_still_leads_there(client):
    me = register(client, "Moritz Lachnit")
    found = client.get("/api/v1/public/drivers/by-slug/moritz-lachnit").json()
    assert found == {"driver_id": me["driver_id"], "display_name": "Moritz Lachnit", "slug": "moritz-lachnit", "renamed": False}
    assert client.get(f"/api/v1/public/drivers/by-slug/{me['driver_id']}").json()["renamed"] is True   # a number: show the name
    assert client.patch("/api/v1/accounts/me", json={"display_name": "Moritz L."}, headers=headers(me)).status_code == 200
    old = client.get("/api/v1/public/drivers/by-slug/moritz-lachnit").json()
    assert old["driver_id"] == me["driver_id"] and old["slug"] == "moritz-l" and old["renamed"] is True
    assert client.get("/api/v1/public/drivers/by-slug/nobody-here").status_code == 404


def test_somebody_taking_an_old_name_gets_its_link(client):
    first = register(client, "Alex Racer")
    client.patch("/api/v1/accounts/me", json={"display_name": "Alex R"}, headers=headers(first))
    second = register(client, "Alex Racer")       # the name is free again
    assert client.get("/api/v1/public/drivers/by-slug/alex-racer").json()["driver_id"] == second["driver_id"]


def test_a_name_with_the_same_link_as_another_is_refused(client):
    register(client, "Max 1")
    resp = client.post("/api/v1/accounts/register", params={"display_name": "Max_1"})
    assert resp.status_code == 409, resp.text


def test_a_team_is_found_by_the_link_and_after_a_rename(client):
    owner = register(client, "Team Owner Slug")
    team = client.post("/api/v1/teams", json={"name": "HyperMotion Esports"}, headers=headers(owner)).json()
    assert client.get("/api/v1/public/teams/by-slug/hypermotion-esports").json()["team_id"] == team["id"]
    assert client.patch(f"/api/v1/teams/{team['id']}", json={"name": "HyperMotion Racing"}, headers=headers(owner)).status_code == 200
    moved = client.get("/api/v1/public/teams/by-slug/hypermotion-esports").json()
    assert moved["team_id"] == team["id"] and moved["slug"] == "hypermotion-racing" and moved["renamed"] is True
    other = register(client, "Second Owner Slug")
    assert client.post("/api/v1/teams", json={"name": "HyperMotion-Racing"}, headers=headers(other)).status_code in (400, 409)


def _meta(client, path):
    resp = client.get("/api/v1/public/page-meta", params={"path": path})
    assert resp.status_code == 200
    return resp.json()


def test_every_page_has_its_own_title(client):
    me = register(client, "Mika Häkkinen")
    upload_df(client, me, make_lap(seed=3), track="Fuji Speedway", car_class="Hyper", car_model="Porsche 963")
    assert _meta(client, "/")["title"] == "Garage16 — Le Mans Ultimate telemetry & leaderboards"
    assert _meta(client, "/leaderboard/fuji-speedway/hypercar")["title"] == "Fuji Speedway · Hypercar – Garage16"
    assert _meta(client, "/leaderboard/fuji-speedway/porsche-963")["title"] == "Fuji Speedway · Porsche 963 – Garage16"
    assert _meta(client, "/leaderboard/fuji-speedway")["title"] == "Fuji Speedway · Leaderboard – Garage16"
    driver = _meta(client, "/driver/mika-hakkinen")
    assert driver["title"] == "Mika Häkkinen – Garage16" and "Mika Häkkinen" in driver["description"]
    assert _meta(client, f"/driver/{me['driver_id']}")["canonical"] == "/driver/mika-hakkinen"
    assert _meta(client, "/analysis/12/vs/34")["title"] == "LapTime Analyzer – Garage16"
    assert _meta(client, "/analysis/fuji-speedway/porsche-963/vs/class-best")["title"] == "Fuji Speedway · Porsche 963 – Garage16"
    assert _meta(client, "/stints/fuji-speedway/2026-11-14")["title"] == "Fuji Speedway · 2026-11-14 · Stint Analyzer – Garage16"
    assert _meta(client, "/datenschutz")["title"] == "Privacy policy – Garage16"
    for missing in ("/nowhere", "/driver/nobody", "/team/none", "/leaderboard/a/b/c"):
        assert _meta(client, missing)["status"] == 404, missing


def test_the_server_knows_every_page_of_the_route_table(client):
    routes = (ROOT / "web" / "js" / "core" / "routes.js").read_text(encoding="utf-8")
    literal_pages = {p for p in re.findall(r'"(/[a-z-]*)"', routes) if p.count("/") == 1}
    for path in sorted(literal_pages):
        assert _meta(client, path)["status"] == 200, path


def test_the_page_html_carries_the_title_for_link_previews(client, monkeypatch):
    register(client, "Preview Driver")
    page = client.get("/api/v1/public/page", headers={"X-Garage16-Page-Path": "/driver/preview-driver"})
    assert page.status_code == 200 and page.headers["content-type"].startswith("text/html")
    assert "<title>Preview Driver – Garage16</title>" in page.text
    assert '<meta property="og:title" content="Preview Driver – Garage16"' in page.text
    assert re.search(r'<meta property="og:url" content="[^"]*/driver/preview-driver"', page.text)
    assert re.search(r'<meta property="og:image" content="[^"]+/icon-192\.png"', page.text)
    missing = client.get("/api/v1/public/page", headers={"X-Garage16-Page-Path": "/driver/<script>"})
    assert missing.status_code == 404 and "<script>" not in missing.text.split("<body>")[0]


def test_without_a_readable_web_folder_the_page_comes_from_the_image(client, monkeypatch, tmp_path):
    """Found on the NAS (23.11.2026): files from the ZIP (via Windows) arrived as rwx--x--x, Caddy
    (root) could read web/, the app's user `garage` could not — every page address answered
    "The website files are not available". The image now carries its own readable copy of
    index.html (docker/Dockerfile.server), which the server uses when the mounted folder fails."""
    from server.api.v1 import pages
    from server.core.config import settings

    monkeypatch.setattr(settings, "web_dir", tmp_path / "unreadable")
    page = client.get("/api/v1/public/page", headers={"X-Garage16-Page-Path": "/leaderboard"})
    assert page.status_code == 200, page.text
    assert "<title>Leaderboard – Garage16</title>" in page.text
    assert pages.BUNDLED_INDEX.is_file()

    # neither the folder nor the image copy: the old, honest message
    monkeypatch.setattr(pages, "BUNDLED_INDEX", tmp_path / "missing" / "index.html")
    gone = client.get("/api/v1/public/page", headers={"X-Garage16-Page-Path": "/leaderboard"})
    assert gone.status_code == 404


def test_the_image_carries_a_readable_index_html():
    dockerfile = (ROOT / "docker" / "Dockerfile.server").read_text(encoding="utf-8")
    assert "COPY web/index.html ./web/index.html" in dockerfile
    chmod = next(line for line in dockerfile.splitlines() if line.startswith("RUN chmod -R a+rX"))
    assert "./web" in chmod.split()
    ignored = (ROOT / ".dockerignore").read_text(encoding="utf-8").split()
    assert "web/" not in ignored and "web" not in ignored
