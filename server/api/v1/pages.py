"""The website's readable links: which driver or team a link names, and what a page is called.

    GET /api/v1/public/drivers/by-slug/{slug}   /driver/moritz-lachnit -> driver id (old names redirect)
    GET /api/v1/public/teams/by-slug/{slug}     /team/hypermotion-esports -> team id
    GET /api/v1/public/page-meta?path=...       title + description of any page path (link previews)
    GET /api/v1/public/page?path=...            the site's index.html with that title filled in

Link previews (Discord, WhatsApp, …) do not run JavaScript: they read the <title> and the
og: tags of the HTML they get. The page paths therefore must be answered with the right tags
already in the HTML — by the Cloudflare Pages function (web/functions/_middleware.js asks
page-meta), or, when the NAS serves the website itself, by Caddy sending page requests to
/api/v1/public/page (docker/Caddyfile).

The page list here mirrors web/js/core/routes.js (tests/web/test_routes.py keeps them equal).
"""

from __future__ import annotations

import html
import re
from functools import lru_cache
from pathlib import Path
from typing import Optional, Tuple
from urllib.parse import unquote

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from server.catalog.catalog import known_class_names, venue_for
from server.core.config import settings
from server.core.database import get_db
from server.core.models import Driver, Lap, SlugRedirect, Team
from server.core.slugs import slugify

router = APIRouter(prefix="/public", tags=["pages"])

SITE = "Garage16"
DEFAULT_TITLE = "Garage16 — Le Mans Ultimate telemetry & leaderboards"
DEFAULT_DESCRIPTION = ("Record your Le Mans Ultimate laps, compare them in the lap and stint analysis, "
                       "and race your friends on the leaderboards.")

# path -> (title, description); the title gets " – Garage16" appended
STATIC_PAGES = {
    "dashboard": ("Dashboard", "Your laps, records and team at a glance."),
    "teams": ("Teams", "Teams on Garage16 — team records, leaderboards and activity."),
    "analysis": ("LapTime Analyzer", "Compare two Le Mans Ultimate laps channel by channel, corner by corner."),
    "stints": ("Stint Analyzer", "Whole sessions: pace, tyres, fuel, pit stops and consistency."),
    "leaderboard": ("Leaderboard", "The fastest valid Le Mans Ultimate laps per track and class."),
    "circuits": ("Circuits", "Every circuit on Garage16 with its layouts and fastest laps."),
    "account": ("Account", "Your Garage16 account."),
    "download": ("Download", "The Garage16 desktop client for Windows."),
    "setup": ("Setup guide", "How to set up Garage16 with Le Mans Ultimate."),
    "impressum": ("Legal notice", "Legal notice of this Garage16 server."),
    "datenschutz": ("Privacy policy", "Which data Garage16 keeps, where, and for how long."),
    "terms": ("Terms of use", "The terms of use of this Garage16 server."),
    "licenses": ("Licences", "Open-source licences used by Garage16."),
    "admin": ("Admin", "Garage16 administration."),
    "reset-password": ("Reset password", "Choose a new password for your Garage16 account."),
    "verify-email": ("Confirm email", "Confirm the email address of your Garage16 account."),
    # the addresses of V1.0.1 that the website forwards (web/js/core/routes.js MOVED)
    "legal": ("Legal notice", "Legal notice of this Garage16 server."),
    "sessions": ("Stint Analyzer", "Whole sessions: pace, tyres, fuel, pit stops and consistency."),
}


# ------------------------------------------------------------------ drivers and teams by slug

def _by_slug(db: Session, model, kind: str, slug: str) -> Tuple[Optional[object], bool]:
    """(row, renamed) for a slug — the current owner first (the oldest when names from before
    V1.0.2 share one), then an earlier name; a number is an id (links of names without letters)."""
    slug = slugify(unquote(slug))
    if not slug:
        return None, False
    if slug.isdigit():
        return db.get(model, int(slug)), False
    row = db.execute(select(model).where(model.slug == slug).order_by(model.id).limit(1)).scalar_one_or_none()
    if row is not None:
        return row, False
    redirect = db.execute(select(SlugRedirect).where(SlugRedirect.kind == kind, SlugRedirect.slug == slug)).scalar_one_or_none()
    if redirect is not None:
        return db.get(model, redirect.target_id), True
    return None, False


def _link_slug(row, name: str) -> str:
    return (row.slug or str(row.id)) if row is not None else slugify(name)


@router.get("/drivers/by-slug/{slug}")
def driver_by_slug(slug: str, db: Session = Depends(get_db)) -> dict:
    """{driver_id, display_name, slug, renamed}. `renamed`: the link used an earlier name — the page
    replaces the address with `slug`."""
    driver, renamed = _by_slug(db, Driver, "driver", slug)
    if driver is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Driver not found.")
    return {"driver_id": driver.id, "display_name": driver.display_name, "slug": _link_slug(driver, driver.display_name),
            "renamed": renamed or (slugify(unquote(slug)) != (driver.slug or str(driver.id)))}


@router.get("/teams/by-slug/{slug}")
def team_by_slug(slug: str, db: Session = Depends(get_db)) -> dict:
    team, renamed = _by_slug(db, Team, "team", slug)
    if team is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Team not found.")
    return {"team_id": team.id, "name": team.name, "slug": _link_slug(team, team.name),
            "renamed": renamed or (slugify(unquote(slug)) != (team.slug or str(team.id)))}


# ------------------------------------------------------------------ titles of pages

def _track_for(db: Session, track_slug: str) -> Optional[str]:
    names = db.execute(select(Lap.track_name).distinct()).scalars().all()
    return next((n for n in names if slugify(n) == track_slug), None)


def _track_label(track_name: str) -> str:
    venue = venue_for(track_name)
    return venue or track_name


def _class_or_model(db: Session, track_name: str, key_slug: str) -> Optional[str]:
    for name in known_class_names():
        if slugify(name) == key_slug:
            return name
    classes = db.execute(select(Lap.car_class).where(Lap.track_name == track_name).distinct()).scalars().all()
    models = db.execute(select(Lap.car_model).where(Lap.track_name == track_name).distinct()).scalars().all()
    return next((n for n in [*classes, *models] if n and slugify(n) == key_slug), None)


def page_meta_for(db: Session, path: str) -> dict:
    """{status, title, description, canonical} for a page path ("/leaderboard/fuji-speedway/hypercar")."""
    clean = "/" + "/".join(p for p in unquote(path.split("?", 1)[0].split("#", 1)[0]).split("/") if p)
    parts = [p for p in clean.split("/") if p]

    def page(title: str, description: str = DEFAULT_DESCRIPTION, code: int = 200, canonical: str = clean) -> dict:
        return {"status": code, "title": f"{title} – {SITE}", "description": description, "canonical": canonical}

    if not parts:
        return {"status": 200, "title": DEFAULT_TITLE, "description": DEFAULT_DESCRIPTION, "canonical": "/"}
    head, rest = parts[0], parts[1:]
    not_found = page("Page not found", "This page does not exist on Garage16.", code=404)
    # the shapes of web/js/core/routes.js — anything longer is not a page
    if (head == "leaderboard" and len(rest) > 2) or (head in ("driver", "team") and len(rest) != 1) \
            or (head == "analysis" and len(rest) > 4) or (head == "stints" and len(rest) > 2):
        return not_found
    if head == "leaderboard" and rest:
        track = _track_for(db, slugify(rest[0]))
        if track is None:
            return page("Leaderboard", STATIC_PAGES["leaderboard"][1])
        label = _track_label(track)
        if len(rest) > 1:
            key = _class_or_model(db, track, slugify(rest[1]))
            if key:
                return page(f"{label} · {key}", f"The fastest valid {key} laps at {label} on Garage16.")
        return page(f"{label} · Leaderboard", f"The fastest valid laps at {label} on Garage16.")
    if head == "driver" and len(rest) == 1:
        driver, _renamed = _by_slug(db, Driver, "driver", rest[0])
        if driver is None:
            return page("Driver not found", code=404)
        return page(driver.display_name, f"Records, laps and team of {driver.display_name} on Garage16.",
                    canonical=f"/driver/{_link_slug(driver, driver.display_name)}")
    if head == "team" and len(rest) == 1:
        team, _renamed = _by_slug(db, Team, "team", rest[0])
        if team is None:
            return page("Team not found", code=404)
        return page(team.name, f"Team {team.name} on Garage16 — records, leaderboards and activity.",
                    canonical=f"/team/{_link_slug(team, team.name)}")
    if head in ("analysis", "stints") and rest:
        base, description = STATIC_PAGES[head]
        if rest[0].isdigit() or rest[0] == "lap":
            return page(base, description)
        track = _track_for(db, slugify(rest[0]))
        if track is not None:
            label = _track_label(track)
            if head == "analysis" and len(rest) > 1 and not rest[1] == "vs":
                key = _class_or_model(db, track, slugify(rest[1]))
                return page(f"{label} · {key or base}", description)
            if head == "stints" and len(rest) > 1 and re.fullmatch(r"\d{4}-\d{2}-\d{2}(-\d+)?", rest[1]):
                return page(f"{label} · {rest[1][:10]} · {base}", description)
            return page(f"{label} · {base}", description)
        return page(base, description)
    if len(parts) == 1 and head in STATIC_PAGES:
        title, description = STATIC_PAGES[head]
        return page(title, description)
    return not_found


@router.get("/page-meta")
def page_meta(path: str = Query(default="/", max_length=400), db: Session = Depends(get_db)) -> dict:
    return page_meta_for(db, path)


# ------------------------------------------------------------------ the HTML (NAS serves the site)

@lru_cache(maxsize=1)
def _index_html(path: str, mtime: float) -> str:
    return Path(path).read_text(encoding="utf-8")


# The copy of index.html inside the image (docker/Dockerfile.server copies it, readable for all).
# Used when the mounted web folder can't be read by the app's user — on the NAS the files can
# arrive from the ZIP (via Windows) with rwx--x--x, which Caddy (root) reads but `garage` can't.
BUNDLED_INDEX = Path(__file__).resolve().parents[3] / "web" / "index.html"


def _read_index() -> str | None:
    """index.html: the mounted web folder first (always the deployed version), else the image's copy."""
    for candidate in (settings.web_dir / "index.html", BUNDLED_INDEX):
        try:
            return _index_html(str(candidate), candidate.stat().st_mtime)
        except OSError:
            continue
    return None


def _attr(text: str) -> str:
    return html.escape(text, quote=True)


def fill_index(index: str, meta: dict, origin: str) -> str:
    """index.html with the page's title, description, canonical URL and absolute image filled in."""
    title, description = _attr(meta["title"]), _attr(meta["description"])
    url = _attr(origin.rstrip("/") + meta["canonical"])
    out = re.sub(r"<title>.*?</title>", f"<title>{title}</title>", index, count=1, flags=re.S)
    replacements = {
        r'<meta name="description" content="[^"]*"': f'<meta name="description" content="{description}"',
        r'<meta property="og:title" content="[^"]*"': f'<meta property="og:title" content="{title}"',
        r'<meta property="og:description" content="[^"]*"': f'<meta property="og:description" content="{description}"',
        r'<meta property="og:url" content="[^"]*"': f'<meta property="og:url" content="{url}"',
        r'<meta property="og:image" content="/?([^"]*)"': f'<meta property="og:image" content="{_attr(origin.rstrip("/"))}/\\1"',
        r'<link rel="canonical" href="[^"]*"': f'<link rel="canonical" href="{url}"',
    }
    for pattern, value in replacements.items():
        out = re.sub(pattern, value, out, count=1)
    return out


@router.get("/page", include_in_schema=False)
def page_html(request: Request, path: str = Query(default="/", max_length=400), db: Session = Depends(get_db)) -> Response:
    """For Caddy (docker/Caddyfile): every page path is answered with index.html — the browser's
    router draws the page — but with the right title for link previews, and 404 for unknown paths.
    Caddy passes the page's path in X-Garage16-Page-Path (it rewrote the request to this endpoint)."""
    path = (request.headers.get("x-garage16-page-path") or path)[:400]
    index = _read_index()
    if index is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "The website files are not available on this server.")
    meta = page_meta_for(db, path)
    body = fill_index(index, meta, settings.frontend_url or "")
    return Response(body, status_code=meta["status"], media_type="text/html; charset=utf-8",
                    headers={"Cache-Control": "no-cache"})
