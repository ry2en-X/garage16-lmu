"""
config.py — Server-side settings, overridable via environment variables so
dev/staging/prod can point at different DBs and storage locations without
code changes.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class Settings:
    database_url: str = field(
        default_factory=lambda: os.environ.get(
            "LMU_GARAGE_DB_URL", "sqlite:///./lmu_garage_server.db"
        )
    )
    telemetry_storage_dir: Path = field(
        default_factory=lambda: Path(
            os.environ.get("LMU_GARAGE_STORAGE_DIR", "./server_telemetry_storage")
        )
    )
    # Safety cap so a malformed/malicious upload can't fill the disk in one shot.
    max_upload_bytes: int = int(os.environ.get("LMU_GARAGE_MAX_UPLOAD_BYTES", 50 * 1024 * 1024))
    # If set, /accounts/register requires a matching X-Registration-Secret
    # header — closes the "anyone can mint themselves a driver identity"
    # gap for anything reachable outside localhost. Unset (None) keeps
    # registration open, which is fine for solo/localhost dev only.
    registration_secret: Optional[str] = field(
        default_factory=lambda: os.environ.get("LMU_GARAGE_REGISTRATION_SECRET") or None
    )
    # If true, rejects requests that didn't arrive over HTTPS (as reported
    # by a TLS-terminating reverse proxy's X-Forwarded-Proto header — this
    # process itself doesn't terminate TLS). See server/main.py.
    require_https: bool = field(
        default_factory=lambda: os.environ.get("LMU_GARAGE_REQUIRE_HTTPS", "").lower()
        in ("1", "true", "yes")
    )
    # V0.7.2 security pass (§14): the interactive Swagger/ReDoc UI at
    # /docs and /redoc documents the full API surface for free — handy in
    # dev, unnecessary reconnaissance surface to expose by default on a
    # public production deployment. Off in production unless explicitly
    # re-enabled; always on in dev.
    enable_api_docs: bool = field(
        default_factory=lambda: os.environ.get("LMU_GARAGE_ENABLE_API_DOCS", "").lower()
        in ("1", "true", "yes")
    )
    # "development" (default) or "production" — gates fail-safe behavior
    # that would otherwise be too disruptive for local dev, such as
    # crypto.py refusing to start with a known, publicly-committed
    # encryption key. Set LMU_GARAGE_ENV=production for any deployment
    # that isn't purely local/solo dev.
    environment: str = field(
        default_factory=lambda: os.environ.get("LMU_GARAGE_ENV", "development").strip().lower()
    )
    # Comma-separated list of allowed CORS origins for the web frontend.
    # Defaults to localhost dev server; set to your real domain(s) in
    # production, e.g. "https://garage16.example.com,https://www.garage16.example.com"
    cors_origins: list = field(
        default_factory=lambda: [
            o.strip()
            for o in os.environ.get(
                "LMU_GARAGE_CORS_ORIGINS", "http://localhost:5173"
            ).split(",")
            if o.strip()
        ]
    )
    # Rejects uploads from clients older than this (envelope.client_version).
    # Bump this after shipping a fix for a client-side bug serious enough
    # that old clients shouldn't keep uploading (e.g. the P0-1 upload
    # contract break in V0.5.2) — see routers/telemetry.py.
    min_client_version: str = field(
        default_factory=lambda: os.environ.get("LMU_GARAGE_MIN_CLIENT_VERSION", "0.5.3")
    )
    # Bearer token for the /admin router (server/routers/admin.py). No
    # default — admin endpoints are simply unreachable (503) until this is
    # explicitly set, rather than silently open or silently protected by
    # a guessable default.
    admin_token: Optional[str] = field(
        default_factory=lambda: os.environ.get("LMU_GARAGE_ADMIN_TOKEN") or None
    )
    # {bucket_name: (max_requests, window_seconds)}. See rate_limit.py for
    # what "bucket" means and this module's multi-worker caveat.
    rate_limits: dict = field(
        default_factory=lambda: {
            "register": (
                int(os.environ.get("LMU_GARAGE_RATELIMIT_REGISTER_MAX", 5)),
                float(os.environ.get("LMU_GARAGE_RATELIMIT_REGISTER_WINDOW", 3600)),
            ),
            "upload": (
                int(os.environ.get("LMU_GARAGE_RATELIMIT_UPLOAD_MAX", 120)),
                float(os.environ.get("LMU_GARAGE_RATELIMIT_UPLOAD_WINDOW", 3600)),
            ),
            "team_join": (
                int(os.environ.get("LMU_GARAGE_RATELIMIT_TEAMJOIN_MAX", 10)),
                float(os.environ.get("LMU_GARAGE_RATELIMIT_TEAMJOIN_WINDOW", 3600)),
            ),
            "link_code": (
                int(os.environ.get("LMU_GARAGE_RATELIMIT_LINKCODE_MAX", 10)),
                float(os.environ.get("LMU_GARAGE_RATELIMIT_LINKCODE_WINDOW", 3600)),
            ),
            "report": (
                int(os.environ.get("LMU_GARAGE_RATELIMIT_REPORT_MAX", 20)),
                float(os.environ.get("LMU_GARAGE_RATELIMIT_REPORT_WINDOW", 3600)),
            ),
            # Deliberately tighter than "register": a login endpoint is
            # the actual brute-force target here (register only lets you
            # mint a fresh, empty identity — login lets you guess into an
            # existing one).
            "login": (
                int(os.environ.get("LMU_GARAGE_RATELIMIT_LOGIN_MAX", 10)),
                float(os.environ.get("LMU_GARAGE_RATELIMIT_LOGIN_WINDOW", 900)),
            ),
            "password_reset": (
                int(os.environ.get("LMU_GARAGE_RATELIMIT_PASSWORDRESET_MAX", 5)),
                float(os.environ.get("LMU_GARAGE_RATELIMIT_PASSWORDRESET_WINDOW", 3600)),
            ),
            # V0.7.2 §9.2/§13 — team invitations, per-driver (the inviter).
            "team_invite": (
                int(os.environ.get("LMU_GARAGE_RATELIMIT_TEAMINVITE_MAX", 20)),
                float(os.environ.get("LMU_GARAGE_RATELIMIT_TEAMINVITE_WINDOW", 3600)),
            ),
            # V0.8-NAS §2 — resending a verification email.
            "email_verification": (
                int(os.environ.get("LMU_GARAGE_RATELIMIT_EMAILVERIFY_MAX", 5)),
                float(os.environ.get("LMU_GARAGE_RATELIMIT_EMAILVERIFY_WINDOW", 3600)),
            ),
        }
    )
    # "text" (human-readable, default — fine for local dev / journald) or
    # "json" (one JSON object per line — easier to feed to a log
    # aggregator like Loki/ELK in production). See main.py's logging setup.
    log_format: str = field(
        default_factory=lambda: os.environ.get("LMU_GARAGE_LOG_FORMAT", "text").strip().lower()
    )
    # SQLAlchemy connection pool sizing — irrelevant for SQLite (which
    # ignores these), matters once this points at PostgreSQL under real
    # concurrent load. pool_pre_ping issues a cheap "is this connection
    # still alive" check before handing a pooled connection to a request,
    # which is what actually matters for a Postgres server that may
    # restart, rotate connections, or sit behind a load balancer that
    # silently drops idle ones — without it, the first request after such
    # an event gets an opaque "connection has been closed" error instead
    # of the pool quietly reconnecting.
    # V0.8.6: where a too-old desktop client should send its user to get
    # the new version (e.g. a GitHub release page or a NAS share link).
    # Purely informational — advertised in UPDATE_REQUIRED responses and
    # GET /health; the server never serves or verifies any download
    # itself. Unset = the message just says to ask whoever runs the server.
    client_download_url: Optional[str] = field(
        default_factory=lambda: os.environ.get("LMU_GARAGE_CLIENT_DOWNLOAD_URL") or None
    )

    db_pool_size: int = int(os.environ.get("LMU_GARAGE_DB_POOL_SIZE", 5))
    db_max_overflow: int = int(os.environ.get("LMU_GARAGE_DB_MAX_OVERFLOW", 10))

    # V0.7.2 §7 (server migration): only set once an operator is actually
    # decommissioning this instance in favor of another (e.g. NAS → VPS).
    # Advertised in /health so a client that's still pointed here can find
    # out where to go next — see main.py's startup health-check.
    migrated_to_url: Optional[str] = field(
        default_factory=lambda: os.environ.get("LMU_GARAGE_MIGRATED_TO") or None
    )

    # --- Email (V0.6.9: password reset) ---
    # "development" (default) logs the email instead of sending it — see
    # server/email_provider.py. Set to "smtp" once real SMTP credentials
    # below are configured for production.
    email_provider: str = field(
        default_factory=lambda: os.environ.get("LMU_GARAGE_EMAIL_PROVIDER", "development").strip().lower()
    )
    smtp_host: Optional[str] = field(default_factory=lambda: os.environ.get("LMU_GARAGE_SMTP_HOST") or None)
    smtp_port: int = int(os.environ.get("LMU_GARAGE_SMTP_PORT", 587))
    smtp_user: Optional[str] = field(default_factory=lambda: os.environ.get("LMU_GARAGE_SMTP_USER") or None)
    smtp_password: Optional[str] = field(default_factory=lambda: os.environ.get("LMU_GARAGE_SMTP_PASSWORD") or None)
    smtp_from_address: str = field(
        default_factory=lambda: os.environ.get("LMU_GARAGE_SMTP_FROM", "garage16@localhost")
    )
    # Used to build the link inside a password-reset email
    # (<frontend_url>/#/reset-password?token=...) — the server itself has
    # no notion of what domain the web frontend is served from otherwise.
    frontend_url: str = field(
        default_factory=lambda: os.environ.get("LMU_GARAGE_FRONTEND_URL", "http://localhost:5173")
    )

    @property
    def is_production(self) -> bool:
        # P0-6: Only the explicit string "development" gets dev defaults.
        # Everything else (unset, "production", typos like "prod") is
        # treated as production and hard-fails without real secrets.
        return self.environment != "development"


settings = Settings()
# Deliberately no directory creation here. storage.py's
# telemetry_storage_dir() already creates the full path (including all
# parents) lazily, exactly when a file is actually about to be written —
# see that module. Creating it eagerly here, at import time, was a bug
# (found via real deployment testing, 2026-09-25): any OTHER process that
# imports server.config transitively — like the Discord bot, which needs
# server.database for DB access but never touches telemetry storage —
# inherited this side effect and crashed on it if it lacked write
# permission for the path (which it correctly does, since it has no
# reason to ever write there).

if settings.registration_secret is None:
    if settings.is_production:
        raise RuntimeError(
            "LMU_GARAGE_REGISTRATION_SECRET is not set and LMU_GARAGE_ENV is not "
            "'development' — refusing to start. Set a registration secret before "
            "exposing this server beyond localhost, or set LMU_GARAGE_ENV=development "
            "for local-only use."
        )
    logging.getLogger("lmu_garage.config").warning(
        "LMU_GARAGE_REGISTRATION_SECRET is not set — /accounts/register is open "
        "to anyone who can reach this server. Fine for solo/localhost dev; set "
        "it before exposing this server beyond localhost."
    )


def warn_about_questionable_client_facing_urls(cfg: Settings) -> list:
    """V0.8.6: LMU_GARAGE_MIGRATED_TO and LMU_GARAGE_CLIENT_DOWNLOAD_URL
    are handed to every desktop client. A typo'd value fails silently
    THERE (the client ignores it — by design) — so say so here, where the
    operator is looking. Warnings only, never a startup failure: a wrong
    optional URL must not take the server down. Returns the messages
    (also logged) so it's testable."""
    from urllib.parse import urlparse

    log = logging.getLogger("lmu_garage.config")
    messages = []
    if cfg.migrated_to_url:
        parsed = urlparse(cfg.migrated_to_url)
        if parsed.scheme != "https" or not parsed.netloc:
            messages.append(
                f"LMU_GARAGE_MIGRATED_TO={cfg.migrated_to_url!r} is not a valid https:// URL — "
                "desktop clients only follow a move to an https:// address and will ignore this."
            )
    if cfg.client_download_url:
        parsed = urlparse(cfg.client_download_url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            messages.append(
                f"LMU_GARAGE_CLIENT_DOWNLOAD_URL={cfg.client_download_url!r} is not a valid http(s) URL — "
                "clients won't offer a download button for it."
            )
    for message in messages:
        log.warning(message)
    return messages


warn_about_questionable_client_facing_urls(settings)
