"""
config.py — Server-side settings, overridable via environment variables so
dev/staging/prod can point at different DBs and storage locations without
code changes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from server.core.client_ip import parse_trusted
from server.core.env import env

# The public GitHub repository whose releases carry the Windows installers. Empty
# (GARAGE16_GITHUB_REPO=) = no default download links.
DEFAULT_GITHUB_REPO = "ry2en-X/garage16-lmu"


def _github_repo() -> str:
    return env("GITHUB_REPO", DEFAULT_GITHUB_REPO).strip().strip("/")


def discord_digest_seconds(default: int = 300) -> int:
    """V1.0.3: GARAGE16_DISCORD_DIGEST_S — how long the Discord bot holds a record announcement back
    for a newer improvement of the same record. Read at call time; invalid -> default, negative -> 0.
    (Lives here, not in discord_bot/, so the docs check finds every documented setting in server/.)"""
    raw = env("DISCORD_DIGEST_S", str(default))
    try:
        return max(0, int(float(raw)))
    except (TypeError, ValueError):
        return default


def _github_asset(filename: str) -> Optional[str]:
    """Direct link to a file of the newest (non-pre-)release: GitHub redirects
    /releases/latest/download/<name> to the newest release that has a file of that name."""
    repo = _github_repo()
    return f"https://github.com/{repo}/releases/latest/download/{filename}" if repo else None


def _github_release_page() -> Optional[str]:
    repo = _github_repo()
    return f"https://github.com/{repo}/releases/latest" if repo else None


def _latest_client() -> str:
    from server.core.versioning import LATEST_CLIENT_VERSION

    return LATEST_CLIENT_VERSION


@dataclass
class Settings:
    database_url: str = field(
        default_factory=lambda: env("DB_URL", "sqlite:///./lmu_garage_server.db"
        )
    )
    telemetry_storage_dir: Path = field(
        default_factory=lambda: Path(
            env("STORAGE_DIR", "./server_telemetry_storage")
        )
    )
    # Safety cap so a malformed/malicious upload can't fill the disk in one shot.
    max_upload_bytes: int = int(env("MAX_UPLOAD_BYTES", 50 * 1024 * 1024))
    # If set, /accounts/register requires a matching X-Registration-Secret
    # header — closes the "anyone can mint themselves a driver identity"
    # gap for anything reachable outside localhost. Unset (None) keeps
    # registration open, which is fine for solo/localhost dev only.
    registration_secret: Optional[str] = field(
        default_factory=lambda: env("REGISTRATION_SECRET") or None
    )
    # If true, rejects requests that didn't arrive over HTTPS (as reported
    # by a TLS-terminating reverse proxy's X-Forwarded-Proto header — this
    # process itself doesn't terminate TLS). See server/main.py.
    require_https: bool = field(
        default_factory=lambda: env("REQUIRE_HTTPS", "").lower()
        in ("1", "true", "yes")
    )
    # The interactive Swagger/ReDoc UI at
    # /docs and /redoc documents the full API surface for free — handy in
    # dev, unnecessary reconnaissance surface to expose by default on a
    # public production deployment. Off in production unless explicitly
    # re-enabled; always on in dev.
    enable_api_docs: bool = field(
        default_factory=lambda: env("ENABLE_API_DOCS", "").lower()
        in ("1", "true", "yes")
    )
    # "development" (default) or "production" — gates fail-safe behavior
    # that would otherwise be too disruptive for local dev, such as
    # crypto.py refusing to start with a known, publicly-committed
    # encryption key. Set GARAGE16_ENV=production for any deployment
    # that isn't purely local/solo dev.
    environment: str = field(
        default_factory=lambda: env("ENV", "development").strip().lower()
    )
    # Comma-separated list of allowed CORS origins for the web frontend.
    # Defaults to localhost dev server; set to your real domain(s) in
    # production, e.g. "https://garage16.example.com,https://www.garage16.example.com"
    cors_origins: list = field(
        default_factory=lambda: [
            o.strip()
            for o in env("CORS_ORIGINS", "http://localhost:5173"
            ).split(",")
            if o.strip()
        ]
    )
    # Rejects uploads from clients older than this (envelope.client_version).
    # Bump this after shipping a fix for a client-side bug serious enough
    # that old clients shouldn't keep uploading (e.g. the P0-1 upload
    # contract break in V0.5.2) — see routers/telemetry.py.
    min_client_version: str = field(
        default_factory=lambda: env("MIN_CLIENT_VERSION", "0.5.3")
    )
    # Bearer token for the /admin router (server/api/v1/admin.py). No
    # default — admin endpoints are simply unreachable (503) until this is
    # explicitly set, rather than silently open or silently protected by
    # a guessable default.
    admin_token: Optional[str] = field(
        default_factory=lambda: env("ADMIN_TOKEN") or None
    )
    # The proxies whose X-Forwarded-For is believed (server/core/client_ip.py). Comma-separated
    # addresses / networks or "private"; empty = nobody (the socket address counts). docker-compose
    # sets "private": the app publishes no port, only the reverse proxy on the Docker network reaches it.
    trusted_proxies: list = field(
        default_factory=lambda: parse_trusted(env("TRUSTED_PROXIES", ""))
    )
    # {bucket_name: (max_requests, window_seconds)}. See rate_limit.py for
    # what "bucket" means and this module's multi-worker caveat.
    rate_limits: dict = field(
        default_factory=lambda: {
            "register": (
                int(env("RATELIMIT_REGISTER_MAX", 5)),
                float(env("RATELIMIT_REGISTER_WINDOW", 3600)),
            ),
            "upload": (
                int(env("RATELIMIT_UPLOAD_MAX", 120)),
                float(env("RATELIMIT_UPLOAD_WINDOW", 3600)),
            ),
            "team_join": (
                int(env("RATELIMIT_TEAMJOIN_MAX", 10)),
                float(env("RATELIMIT_TEAMJOIN_WINDOW", 3600)),
            ),
            "link_code": (
                int(env("RATELIMIT_LINKCODE_MAX", 10)),
                float(env("RATELIMIT_LINKCODE_WINDOW", 3600)),
            ),
            "report": (
                int(env("RATELIMIT_REPORT_MAX", 20)),
                float(env("RATELIMIT_REPORT_WINDOW", 3600)),
            ),
            # Deliberately tighter than "register": a login endpoint is
            # the actual brute-force target here (register only lets you
            # mint a fresh, empty identity — login lets you guess into an
            # existing one).
            "login": (
                int(env("RATELIMIT_LOGIN_MAX", 10)),
                float(env("RATELIMIT_LOGIN_WINDOW", 900)),
            ),
            # Per email address (rate_limit.limit_key), on top of the per-IP limits:
            # failed sign-ins against one account from many addresses, and the emails one address
            # can be sent (sign-up notices, resets, verification links).
            "login_account": (
                int(env("RATELIMIT_LOGINACCOUNT_MAX", 10)),
                float(env("RATELIMIT_LOGINACCOUNT_WINDOW", 900)),
            ),
            "email_to_address": (
                int(env("RATELIMIT_EMAILADDRESS_MAX", 3)),
                float(env("RATELIMIT_EMAILADDRESS_WINDOW", 3600)),
            ),
            "password_reset": (
                int(env("RATELIMIT_PASSWORDRESET_MAX", 5)),
                float(env("RATELIMIT_PASSWORDRESET_WINDOW", 3600)),
            ),
            # V0.7.2 §9.2/§13 — team invitations, per-driver (the inviter).
            "team_invite": (
                int(env("RATELIMIT_TEAMINVITE_MAX", 20)),
                float(env("RATELIMIT_TEAMINVITE_WINDOW", 3600)),
            ),
            # Lap analysis (compare, outlines, lap lists). Each call
            # reads telemetry files, so it is limited per driver and per IP; the
            # default leaves room for exploring (every channel change is a call). V0.9.1:
            # 300 -> 900 — zooming far into a lap now fetches finer data for the visible
            # stretch (one call per settled zoom/pan; the browser remembers stretches it
            # already has and backs off for a minute after a 429).
            "analysis": (
                int(env("RATELIMIT_ANALYSIS_MAX", 900)),
                float(env("RATELIMIT_ANALYSIS_WINDOW", 3600)),
            ),
            # The desktop app's analysis window signing itself in. One call
            # per window that is not signed in yet; 30/hour is far above that.
            "app_session": (
                int(env("RATELIMIT_APPSESSION_MAX", 30)),
                float(env("RATELIMIT_APPSESSION_WINDOW", 3600)),
            ),
            # V0.8-NAS §2 — resending a verification email.
            "email_verification": (
                int(env("RATELIMIT_EMAILVERIFY_MAX", 5)),
                float(env("RATELIMIT_EMAILVERIFY_WINDOW", 3600)),
            ),
        }
    )
    # "text" (human-readable, default — fine for local dev / journald) or
    # "json" (one JSON object per line — easier to feed to a log
    # aggregator like Loki/ELK in production). See main.py's logging setup.
    log_format: str = field(
        default_factory=lambda: env("LOG_FORMAT", "text").strip().lower()
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
    # Where a too-old desktop client should send its user to get
    # the new version (e.g. a GitHub release page or a NAS share link).
    # Purely informational — advertised in UPDATE_REQUIRED responses and
    # GET /health; the server never serves or verifies any download
    # itself. Unset = the message just says to ask whoever runs the server.
    # Unset = the newest GitHub release of GARAGE16_GITHUB_REPO (see below), so the
    # download buttons fetch the installer itself without any .env entry.
    client_download_url: Optional[str] = field(
        default_factory=lambda: env("CLIENT_DOWNLOAD_URL")
        or _github_asset("Garage16-Client-Setup.exe")
    )
    # The client comes in two editions — "Client" (analysis opens in the
    # browser; GARAGE16_CLIENT_DOWNLOAD_URL above) and "Client + analysis app"
    # (analysis in its own window; this one). Both are shown side by side on the
    # website's download page, and a too-old client is sent to its own edition.
    client_app_download_url: Optional[str] = field(
        default_factory=lambda: env("CLIENT_APP_DOWNLOAD_URL")
        or _github_asset("Garage16-Client-Analysis-Setup.exe")
    )
    # The page with every download of the newest release (fallback link under the buttons).
    client_release_page_url: Optional[str] = field(
        default_factory=lambda: env("CLIENT_RELEASE_PAGE_URL")
        or _github_release_page()
    )

    # --- V1.0: open registration and the legal pages ---
    # Without a registration secret a production server refuses to start — unless this says on
    # purpose that ANYBODY may create an account (on the website's sign-up form, which requires
    # accepting the terms and confirming the minimum age).
    open_registration: bool = field(
        default_factory=lambda: env("OPEN_REGISTRATION", "").strip().lower()
        in ("1", "true", "yes")
    )
    # Minimum age for an account (terms of use, privacy policy; the sign-up form asks for it).
    min_age: int = field(default_factory=lambda: int(env("MIN_AGE", 16)))

    # --- V1.0.1: account rules for an open server ---
    # The desktop client connects (POST /accounts/desktop-login) only after the account's email was
    # confirmed. "0" turns it off (a closed circle that does not need it).
    require_email_verification: bool = field(
        default_factory=lambda: env("REQUIRE_EMAIL_VERIFICATION", "1").strip().lower()
        not in ("0", "false", "no")
    )
    # Accounts made on the sign-up form whose email was never confirmed, without laps or teams, are
    # deleted after this many days (0 = never).
    unverified_account_days: int = field(
        default_factory=lambda: int(env("UNVERIFIED_ACCOUNT_DAYS", 7))
    )
    # Fair play (server/laps/fair_play.py): a driver with fewer official laps than this whose lap would
    # beat the current Garage16 Best of that track and car is held for an admin (0 = off).
    new_driver_review_laps: int = field(
        default_factory=lambda: int(env("NEW_DRIVER_REVIEW_LAPS", 10))
    )
    # How many PCs one account may have connected at the same time (desktop-login); the one used
    # longest ago is disconnected when another one connects.
    # The newest desktop client (GET /health latest_client_version / update_available). Defaults to
    # the client version this server was released with (server/core/versioning.py).
    latest_client_version: str = field(
        default_factory=lambda: (env("LATEST_CLIENT_VERSION") or "").strip() or _latest_client()
    )
    max_devices: int = field(default_factory=lambda: max(1, int(env("MAX_DEVICES", 5))))
    # The operator shown in the legal notice (Impressum) and the privacy policy. Kept in .env, not in
    # the code: the repository is public and other instances have other operators. Address lines
    # are separated by "|", e.g. "Musterstraße 1|12345 Musterstadt".
    operator_name: Optional[str] = field(
        default_factory=lambda: (env("OPERATOR_NAME") or "").strip() or None
    )
    operator_address: list = field(
        default_factory=lambda: [
            line.strip() for line in env("OPERATOR_ADDRESS", "").split("|") if line.strip()
        ]
    )
    operator_email: Optional[str] = field(
        default_factory=lambda: (env("OPERATOR_EMAIL") or "").strip() or None
    )

    # Track-limits heuristics that may invalidate a lap on their own (comma
    # separated codes, e.g. "four_wheels_off"). Empty by default: every heuristic only sends
    # the lap to review until real LMU recordings confirm it (server/laps/track_limits.py RULES,
    # docs/LMU_VERIFICATION_PROTOCOL.md). LMU's own verdict is never affected by this.
    track_limits_invalidating: str = field(
        default_factory=lambda: env("TRACK_LIMITS_INVALIDATING", "").strip()
    )

    db_pool_size: int = int(env("DB_POOL_SIZE", 5))
    db_max_overflow: int = int(env("DB_MAX_OVERFLOW", 10))

    # Only set once an operator is actually
    # decommissioning this instance in favor of another (e.g. NAS → VPS).
    # Advertised in /health so a client that's still pointed here can find
    # out where to go next — see main.py's startup health-check.
    migrated_to_url: Optional[str] = field(
        default_factory=lambda: env("MIGRATED_TO") or None
    )

    # --- Email ---
    # "development" (default) logs the email instead of sending it — see
    # server/accounts/email_provider.py. Set to "smtp" once real SMTP credentials
    # below are configured for production.
    email_provider: str = field(
        default_factory=lambda: env("EMAIL_PROVIDER", "development").strip().lower()
    )
    smtp_host: Optional[str] = field(default_factory=lambda: env("SMTP_HOST") or None)
    smtp_port: int = int(env("SMTP_PORT", 587))
    smtp_user: Optional[str] = field(default_factory=lambda: env("SMTP_USER") or None)
    smtp_password: Optional[str] = field(default_factory=lambda: env("SMTP_PASSWORD") or None)
    smtp_from_address: str = field(
        default_factory=lambda: env("SMTP_FROM", "garage16@localhost")
    )
    # Used to build the link inside a password-reset email
    # (<frontend_url>/#/reset-password?token=...) — the server itself has
    # no notion of what domain the web frontend is served from otherwise.
    frontend_url: str = field(
        default_factory=lambda: env("FRONTEND_URL", "http://localhost:5173")
    )

    # The secret the Cloudflare Pages function sends with the visitor's address
    # (server/core/client_ip.py edge_client_ip). Empty = the header is never believed.
    edge_key: Optional[str] = field(default_factory=lambda: env("EDGE_KEY") or None)
    # The website's files (web/). Only index.html is read, by GET /api/v1/public/page — the
    # page paths with their own title for link previews when the NAS serves the website itself
    # (docker/Caddyfile). docker-compose.yml mounts web/ read-only at /srv/web.
    web_dir: Path = field(
        default_factory=lambda: Path(env("WEB_DIR")
                                     or Path(__file__).resolve().parents[2] / "web")
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
# imports server.core.config transitively — like the Discord bot, which needs
# server.core.database for DB access but never touches telemetry storage —
# inherited this side effect and crashed on it if it lacked write
# permission for the path (which it correctly does, since it has no
# reason to ever write there).

def check_registration_settings(cfg: Settings) -> None:
    """A production server needs EITHER a registration secret (closed circle) OR an explicit
    GARAGE16_OPEN_REGISTRATION=1 (anybody may sign up on the website). Neither = refuse to start,
    so a forgotten setting never opens the server by accident."""
    if cfg.registration_secret is None and not cfg.open_registration and cfg.is_production:
        raise RuntimeError(
            "Neither GARAGE16_REGISTRATION_SECRET nor GARAGE16_OPEN_REGISTRATION=1 is set and "
            "GARAGE16_ENV is not 'development' — refusing to start. Set a registration secret "
            "(only people who know it can sign up), or GARAGE16_OPEN_REGISTRATION=1 (anybody can "
            "sign up on the website), or GARAGE16_ENV=development for local-only use."
        )


check_registration_settings(settings)

if settings.registration_secret is None and not settings.open_registration:
    logging.getLogger("lmu_garage.config").warning(
        "GARAGE16_REGISTRATION_SECRET is not set — /accounts/register is open "
        "to anyone who can reach this server. Fine for solo/localhost dev; set "
        "it before exposing this server beyond localhost."
    )


def warn_about_questionable_client_facing_urls(cfg: Settings) -> list:
    """GARAGE16_MIGRATED_TO and GARAGE16_CLIENT_DOWNLOAD_URL
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
                f"GARAGE16_MIGRATED_TO={cfg.migrated_to_url!r} is not a valid https:// URL — "
                "desktop clients only follow a move to an https:// address and will ignore this."
            )
    for name, value in (("GARAGE16_CLIENT_DOWNLOAD_URL", cfg.client_download_url),
                        ("GARAGE16_CLIENT_APP_DOWNLOAD_URL", getattr(cfg, "client_app_download_url", None))):
        if not value:
            continue
        parsed = urlparse(value)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            messages.append(
                f"{name}={value!r} is not a valid http(s) URL — "
                "clients and the download page won't offer it."
            )
    for message in messages:
        log.warning(message)
    return messages


warn_about_questionable_client_facing_urls(settings)
