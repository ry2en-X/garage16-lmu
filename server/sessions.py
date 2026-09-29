"""sessions.py — Web login sessions (V0.7.2), delivered as an HttpOnly
cookie. Deliberately its own module, its own DB table (models.Session),
and its own token namespace — never touches Driver.token_hash /
client_secret, which remain exclusively the desktop client's (see
security.py's module docstring, and Session's own docstring in models.py
for why that separation matters: a real V0.7.0 bug had web login
silently invalidate a driver's desktop client credential by reusing the
same column).

Session tokens use the same generate_token()/hash_token() primitives as
everything else in this project that needs a high-entropy, hashed-at-rest
credential (auth tokens, password-reset tokens) — no new crypto here.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session as DbSession

from .models import Driver, Session
from .security import generate_token, hash_token

# How long a web session stays valid without the driver logging in again.
# Deliberately fixed (not sliding-window-refreshed on every request) —
# simpler to reason about, and "log back in after a month" is a
# perfectly normal thing to ask of a small, low-traffic community
# platform. Revisit if that turns out to be annoying in practice.
SESSION_LIFETIME = timedelta(days=30)

# The cookie's name on the wire. Prefixed so it doesn't collide with
# anything else a browser might have set for this host.
SESSION_COOKIE_NAME = "garage16_session"


def _now_naive_utc() -> datetime:
    # Same naive-vs-aware pitfall fixed twice already in this project
    # (discord_bot/bot.py's link-code check, accounts.py's password-reset
    # confirm): DateTime columns round-trip as naive under SQLite AND
    # PostgreSQL alike. Every comparison against a DB-read expires_at/
    # revoked_at in this module uses this, not a tz-aware datetime.
    return datetime.now(timezone.utc).replace(tzinfo=None)


def create_session(db: DbSession, driver: Driver, *, user_agent: Optional[str] = None) -> str:
    """Creates a new session row for `driver` and returns the RAW token
    (never stored — only its hash is). Caller is responsible for setting
    this as the session cookie; this function has no knowledge of
    HTTP/cookies at all, so it stays trivially testable without a
    request/response cycle."""
    raw_token = generate_token()
    db.add(Session(
        session_token_hash=hash_token(raw_token),
        driver_id=driver.id,
        expires_at=_now_naive_utc() + SESSION_LIFETIME,
        user_agent=(user_agent or "")[:255] or None,
    ))
    return raw_token


def get_driver_for_session_token(db: DbSession, raw_token: str) -> Optional[Driver]:
    """Resolves a raw session-cookie value to its Driver, or None if the
    token doesn't exist, is expired, or was revoked. Updates
    last_used_at on success (best-effort — for the account "Sessions"
    list, never a security decision) but does NOT commit; caller's
    existing request-scoped commit (or FastAPI's session-per-request
    teardown) covers it."""
    session = (
        db.query(Session)
        .filter(Session.session_token_hash == hash_token(raw_token))
        .one_or_none()
    )
    if session is None:
        return None
    now = _now_naive_utc()
    if session.revoked_at is not None or session.expires_at < now:
        return None
    session.last_used_at = now
    db.add(session)
    driver = db.query(Driver).filter(Driver.id == session.driver_id).one_or_none()
    if driver is None or driver.is_locked:
        return None
    return driver


def revoke_session(db: DbSession, raw_token: str) -> None:
    session = (
        db.query(Session)
        .filter(Session.session_token_hash == hash_token(raw_token))
        .one_or_none()
    )
    if session is not None and session.revoked_at is None:
        session.revoked_at = _now_naive_utc()
        db.add(session)


def revoke_all_sessions_for_driver(db: DbSession, driver_id: int) -> None:
    """'Logout all devices' — revokes every non-expired session for this
    driver. Deliberately does NOT touch Driver.token_hash/client_secret:
    those are the desktop client's credentials, a separate domain (see
    module docstring) with their own revoke/rotate endpoints
    (POST /accounts/revoke, /rotate-token) for when THAT needs cutting
    off instead."""
    now = _now_naive_utc()
    db.query(Session).filter(
        Session.driver_id == driver_id, Session.revoked_at.is_(None)
    ).update({Session.revoked_at: now}, synchronize_session=False)
