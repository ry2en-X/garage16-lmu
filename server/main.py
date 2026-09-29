"""
main.py — Server entry point.

Run with:  uvicorn server.main:app --reload
Production: see docker/entrypoint.sh (runs `alembic upgrade head` first,
then starts uvicorn without --reload, optionally with --workers N).
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Optional

from fastapi import Query

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from .config import settings
from .database import Base, SessionLocal, engine
from .routers import accounts, admin, drivers, leaderboard, teams, telemetry
from .routers.teams import invitations_router
from .versioning import is_client_outdated

VERSION = "0.8.10"


def _configure_logging() -> None:
    """settings.log_format: "text" (default, human-readable — fine for
    local dev / journald) or "json" (one JSON object per line, for feeding
    a log aggregator like Loki/ELK). See config.py."""
    if settings.log_format == "json":
        class _JsonFormatter(logging.Formatter):
            def format(self, record: logging.LogRecord) -> str:
                payload = {
                    "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
                    "level": record.levelname,
                    "logger": record.name,
                    "message": record.getMessage(),
                }
                request_id = getattr(record, "request_id", None)
                if request_id:
                    payload["request_id"] = request_id
                if record.exc_info:
                    payload["exc_info"] = self.formatException(record.exc_info)
                return json.dumps(payload)

        handler = logging.StreamHandler()
        handler.setFormatter(_JsonFormatter())
        logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
    else:
        logging.basicConfig(
            level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s %(message)s"
        )


_configure_logging()
logger = logging.getLogger("lmu_garage.main")

# create_all() only in development — production must use Alembic migrations.
# Running create_all on multi-worker deployments races, and it can't add
# columns to existing tables (only Alembic can).
if not settings.is_production:
    Base.metadata.create_all(bind=engine)

def _docs_urls(cfg) -> dict:
    """Pure decision, factored out so it's unit-testable without spinning
    up (or reloading) a real app/settings singleton — see
    tests/test_security_headers.py. `cfg` is anything with
    `.is_production` and `.enable_api_docs` (in practice, always the real
    `settings`, but a test can pass a stand-in)."""
    enabled = (not cfg.is_production) or cfg.enable_api_docs
    return {
        "docs_url": "/docs" if enabled else None,
        "redoc_url": "/redoc" if enabled else None,
        "openapi_url": "/openapi.json" if enabled else None,
    }


app = FastAPI(title="LMU Garage Backend", version=VERSION, **_docs_urls(settings))
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    """V0.7.2 security pass (§14). A handful of response headers that cost
    nothing and have no compatibility downside for a JSON API + a
    same-origin-by-default static frontend:

      - X-Content-Type-Options: nosniff — stops a browser from
        second-guessing a response's declared Content-Type (the classic
        vector: an uploaded file served back as if it were HTML/JS).
      - X-Frame-Options: DENY — this API/frontend has no legitimate
        reason to be framed by another site; blocks clickjacking-style
        embedding outright.
      - Referrer-Policy: strict-origin-when-cross-origin — a reasonable
        modern default; doesn't leak full URLs (which could contain
        query-string tokens) to third-party origins.
      - Strict-Transport-Security — only when require_https is on (i.e.
        only for a deployment that's told this app it's actually served
        over HTTPS via a reverse proxy). Sending HSTS for a plain-HTTP
        local dev server would be actively harmful (browsers remember it).

    No Content-Security-Policy here: this process serves a JSON API (and,
    optionally, the Swagger UI under /docs, which needs jsdelivr.net for
    its own assets) — it does not render the driver-facing web/ frontend
    itself (that's Caddy's job, a separate static file server). A CSP
    tuned for a JSON API buys little; one tuned for the frontend belongs
    in that frontend's own <meta> tag or Caddy's response headers, next
    to the actual HTML/fonts/CDN scripts it loads, not here.
    """
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    if settings.require_https:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


@app.middleware("http")
async def request_id_and_timing(request: Request, call_next):
    """Attaches a request id (X-Request-ID, or a fresh uuid4 if the proxy
    didn't set one) to every request/response and logs method, path,
    status, and duration. Cheap, dependency-free structured-ish logging —
    enough to correlate a slow/failed request across proxy and app logs
    without pulling in a tracing stack."""
    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
    start = time.monotonic()
    response = await call_next(request)
    duration_ms = (time.monotonic() - start) * 1000
    response.headers["X-Request-ID"] = request_id
    logger.info(
        "%s %s -> %d (%.1fms) [%s]",
        request.method, request.url.path, response.status_code, duration_ms, request_id,
    )
    return response


@app.middleware("http")
async def require_https(request: Request, call_next):
    """No-op unless LMU_GARAGE_REQUIRE_HTTPS is set (see config.py).

    uvicorn itself only ever speaks plain HTTP here — this process is
    meant to sit behind a TLS-terminating reverse proxy (Caddy, nginx,
    Cloudflare Tunnel, ...) for anything beyond localhost, not to
    terminate TLS itself. When require_https is on, that proxy is expected
    to set X-Forwarded-Proto so this can tell a real HTTPS request from a
    plaintext one reaching it directly. /health is exempt — it must stay
    reachable over plain HTTP for the reverse proxy's / orchestrator's own
    healthcheck, which typically hits the container directly, not through
    itself.
    """
    if (
        settings.require_https
        and request.url.path != "/health"
        and request.headers.get("x-forwarded-proto", "http") != "https"
    ):
        return JSONResponse(
            status_code=400,
            content={"detail": "HTTPS required. Access this server via its https:// URL."},
        )
    return await call_next(request)


app.include_router(telemetry.router)
app.include_router(leaderboard.router)
app.include_router(accounts.router)
app.include_router(teams.router)
app.include_router(invitations_router)
app.include_router(admin.router)
app.include_router(drivers.router)


@app.get("/health")
def health(client_version: Optional[str] = Query(default=None, max_length=32)) -> dict:
    """Checks DB connectivity (a plain SELECT 1), not just "the process is
    alive" — a process that's up but can't reach its database should fail
    an orchestrator's healthcheck, not report 'ok'. Also reports version
    info so a client (or a human) can tell if the server has moved past
    what a given desktop client expects."""
    db_ok = True
    db_error: str | None = None
    try:
        db = SessionLocal()
        try:
            db.execute(text("SELECT 1"))
        finally:
            db.close()
    except Exception as exc:  # noqa: BLE001 — healthcheck must never itself 500
        db_ok = False
        db_error = str(exc)[:200]

    return {
        "status": "ok" if db_ok else "degraded",
        "version": VERSION,
        "min_client_version": settings.min_client_version,
        "db_ok": db_ok,
        **({"db_error": db_error} if db_error else {}),
        # V0.7.2 §7: present only during a deliberate server migration
        # (LMU_GARAGE_MIGRATED_TO set on this, the OLD instance) — see
        # client/main.py's startup health-check for how a client acts on it.
        **({"migrated_to": settings.migrated_to_url} if settings.migrated_to_url else {}),
        # V0.8.6 version negotiation: a client that passes its own
        # version (?client_version=0.8.1) gets the server's verdict
        # directly, so the "am I too old" rule lives in exactly one place
        # (server/versioning.py) instead of being re-implemented per client.
        **({"update_required": is_client_outdated(client_version, settings.min_client_version)} if client_version else {}),
        **({"client_download_url": settings.client_download_url} if settings.client_download_url else {}),
    }
