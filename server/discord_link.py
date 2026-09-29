"""server/discord_link.py — linking a Garage16 driver to a Discord account
(V0.8.7).

One module, used by BOTH the web API (server/routers/accounts.py: create a
link code, see status, unlink) and the Discord bot (discord_bot/bot.py:
`/link <code>`, `/unlink`), so the rules live in exactly one place and can
be tested with a real database instead of through a Discord gateway.

The flow: a signed-in driver asks the web app for a one-time link code
("profile link token"); they then type `/link ABCD-EFGH` in Discord; the
bot redeems it and stores the invoking Discord user id on the driver. The
code is what proves the web session and the Discord account belong to the
same person.

Rules this module enforces (several were missing before V0.8.7):

  - A code is single-use and expires after 10 minutes.
  - Generating a new code invalidates every earlier unused one for that
    driver — never several live codes at once.
  - A driver who is already linked can't generate a code (409 at the
    API), and a code can't overwrite an existing link even if one was
    issued earlier. Switching accounts means unlinking first — which now
    actually exists (web button and `/unlink`); the bot used to tell
    people to "unlink first" with no way to do it.
  - One Discord account ↔ one driver, both directions.
  - Codes use an unambiguous alphabet (no 0/O, 1/I/L, no `-`/`_`), are 8
    characters, shown as ABCD-EFGH, and matching ignores case, spaces and
    dashes — it's typed by hand into a chat box. Old 6-character codes
    still redeem.
  - Repeated wrong guesses from one Discord user are throttled.

Timestamps are naive UTC throughout: DateTime columns carry no timezone
under either SQLite or PostgreSQL (see the bug notes in bot.py/teams.py).

Only a short-lived, low-value code is stored in plaintext in
`link_codes` (by that table's design — see models.PasswordResetToken's
docstring for why account-takeover-grade tokens are hashed instead): it
only ever binds a Discord identity to the driver who generated it, expires
in minutes, and dies on first use.
"""

from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .models import Driver, LinkCode

# Unambiguous when read off a screen and typed: no 0/O, 1/I/L.
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 8
CODE_LIFETIME = timedelta(minutes=10)
ACCOUNT_KIND = "account"

MAX_FAILED_ATTEMPTS = 5
FAILED_ATTEMPT_WINDOW_SECONDS = 600
# Expired/used rows older than this are swept whenever a new code is made,
# so the table doesn't grow without bound.
STALE_ROW_AGE = timedelta(days=1)


class DiscordAlreadyLinked(Exception):
    """The driver already has a Discord account linked."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def normalize_code(raw: str) -> str:
    """Case-, space- and dash-insensitive: "abcd-efgh", "ABCD EFGH" and
    "ABCDEFGH" are the same code."""
    return "".join(ch for ch in (raw or "").upper() if ch not in " -_\t\n")


def format_code(code: str) -> str:
    """ABCDEFGH -> ABCD-EFGH (easier to read and to type). Codes of any
    other length (legacy 6-character ones) are shown as-is."""
    return f"{code[:4]}-{code[4:]}" if len(code) == CODE_LENGTH else code


def generate_code() -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))


# ------------------------------------------------------------ web side

def create_account_link_code(db: Session, driver: Driver) -> Tuple[str, datetime]:
    """Returns (code, expires_at). Raises DiscordAlreadyLinked if the
    driver is already linked."""
    if driver.discord_user_id is not None:
        raise DiscordAlreadyLinked()

    now = _utcnow()
    # Invalidate every earlier unused code of this driver: one live code.
    for old in (
        db.query(LinkCode)
        .filter(LinkCode.driver_id == driver.id, LinkCode.kind == ACCOUNT_KIND, LinkCode.used_at.is_(None), LinkCode.expires_at > now)
        .all()
    ):
        old.expires_at = now
    # Housekeeping.
    db.query(LinkCode).filter(LinkCode.kind == ACCOUNT_KIND, LinkCode.expires_at < now - STALE_ROW_AGE).delete(synchronize_session=False)

    expires_at = now + CODE_LIFETIME
    for _ in range(5):  # a collision on a 31^8 space is astronomically unlikely; retry anyway
        code = generate_code()
        db.add(LinkCode(code=code, kind=ACCOUNT_KIND, driver_id=driver.id, expires_at=expires_at))
        try:
            db.commit()
            return code, expires_at
        except IntegrityError:
            db.rollback()
    raise RuntimeError("could not allocate a unique link code")  # pragma: no cover


def unlink_driver(db: Session, driver: Driver) -> bool:
    """Web-side unlink. True if a link was actually removed."""
    was_linked = driver.discord_user_id is not None
    driver.discord_user_id = None
    now = _utcnow()
    # A code generated earlier must not re-link the account behind the
    # user's back right after they unlinked.
    for pending in (
        db.query(LinkCode)
        .filter(LinkCode.driver_id == driver.id, LinkCode.kind == ACCOUNT_KIND, LinkCode.used_at.is_(None), LinkCode.expires_at > now)
        .all()
    ):
        pending.expires_at = now
    db.add(driver)
    db.commit()
    return was_linked


# ------------------------------------------------------------ bot side

@dataclass
class RedeemResult:
    ok: bool
    message: str
    driver_name: Optional[str] = None


class _FailedAttemptLimiter:
    """In-process throttle for wrong codes, per Discord user. The bot is
    a single process, so in-memory state is enough (a restart just resets
    the counters); Discord's own per-command rate limits sit on top."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._failures: Dict[str, List[float]] = {}

    def _recent(self, key: str, now: float) -> List[float]:
        recent = [t for t in self._failures.get(key, []) if now - t < FAILED_ATTEMPT_WINDOW_SECONDS]
        self._failures[key] = recent
        return recent

    def blocked(self, key: str, now: Optional[float] = None) -> bool:
        now = time.monotonic() if now is None else now
        with self._lock:
            return len(self._recent(key, now)) >= MAX_FAILED_ATTEMPTS

    def record_failure(self, key: str, now: Optional[float] = None) -> None:
        now = time.monotonic() if now is None else now
        with self._lock:
            self._recent(key, now).append(now)

    def clear(self, key: Optional[str] = None) -> None:
        with self._lock:
            if key is None:
                self._failures.clear()
            else:
                self._failures.pop(key, None)


failed_attempts = _FailedAttemptLimiter()


def redeem_account_link_code(db: Session, raw_code: str, discord_user_id: str) -> RedeemResult:
    """What the bot's `/link <code>` does. Never raises for the expected
    failure cases — returns a message written for the person typing it."""
    discord_user_id = str(discord_user_id)

    if failed_attempts.blocked(discord_user_id):
        return RedeemResult(False, "Too many wrong codes — please wait a few minutes and generate a fresh code on the website.")

    already = db.query(Driver).filter(Driver.discord_user_id == discord_user_id).one_or_none()
    if already is not None:
        return RedeemResult(
            False,
            f"This Discord account is already linked to **{already.display_name}**. "
            "Run `/unlink` first if you want to link a different driver.",
        )

    code = normalize_code(raw_code)
    link_code = (
        db.query(LinkCode)
        .filter(LinkCode.code == code, LinkCode.kind == ACCOUNT_KIND)
        .with_for_update()  # PostgreSQL: two concurrent /link calls can't both consume it (no-op on SQLite, which serializes writers)
        .one_or_none()
    ) if code else None

    if link_code is None or link_code.used_at is not None or link_code.expires_at < _utcnow():
        failed_attempts.record_failure(discord_user_id)
        return RedeemResult(False, "That code is invalid or expired. Generate a new one on the website (Account → Link Discord).")

    driver = db.get(Driver, link_code.driver_id)
    if driver is None:
        failed_attempts.record_failure(discord_user_id)
        return RedeemResult(False, "That code is invalid or expired. Generate a new one on the website (Account → Link Discord).")

    if driver.discord_user_id is not None and driver.discord_user_id != discord_user_id:
        # Defence in depth: create_account_link_code refuses linked drivers,
        # but a code issued BEFORE they linked elsewhere must not overwrite it.
        link_code.used_at = _utcnow()
        db.add(link_code)
        db.commit()
        return RedeemResult(False, f"**{driver.display_name}** is already linked to a different Discord account. Unlink it on the website first.")

    driver.discord_user_id = discord_user_id
    link_code.used_at = _utcnow()
    db.add_all([driver, link_code])
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return RedeemResult(False, "Could not link — this Discord account or driver may already be linked.")

    failed_attempts.clear(discord_user_id)
    return RedeemResult(True, f"Linked to driver **{driver.display_name}**.", driver_name=driver.display_name)


def unlink_discord_user(db: Session, discord_user_id: str) -> Optional[str]:
    """What the bot's `/unlink` does. Returns the driver's display name if
    something was unlinked, else None."""
    driver = db.query(Driver).filter(Driver.discord_user_id == str(discord_user_id)).one_or_none()
    if driver is None:
        return None
    name = driver.display_name
    unlink_driver(db, driver)
    return name
