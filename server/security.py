"""
security.py — HMAC verification for signed uploads, and token helpers.

This mirrors the placeholder scheme in client/uploader.py: a shared
`client_secret` HMACs the upload envelope, and a separate `auth_token`
identifies the driver's account. It's still not a full account system
(registration has no password/email, so a revoked token has no recovery
path — see routers/accounts.py) — but the concrete gaps flagged earlier
now have fixes: client_secret is encrypted at rest (crypto.py), and both
the token and the secret can be rotated or revoked (routers/accounts.py:
rotate-token, rotate-secret, revoke) instead of being valid forever with
no way to invalidate them.

What this HMAC check is, and isn't, for: it proves an upload came from a
client that holds this driver's client_secret (authentication) and that
the envelope bytes weren't altered in transit (integrity) — the same
category of guarantee a password-authenticated API call gives you.
It is explicitly NOT anti-cheat. It cannot detect a driver's own client
lying to itself (a modified/fake client can compute a perfectly valid
signature over fabricated lap data — it has the secret) or a physically
implausible but internally-consistent forged telemetry file. Catching
that class of problem is server/validation.py's job (server-computed
is_valid from the telemetry itself), not this module's — don't extend
this file to try to do both.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, InvalidHashError

# Argon2id, argon2-cffi's own defaults (time_cost=3, memory_cost=64 MiB,
# parallelism=4) — these are the library's current recommended baseline,
# not hand-tuned here. Deliberately not our own KDF/iteration scheme (the
# spec explicitly calls for "kein selbst entwickeltes Hashing").
_password_hasher = PasswordHasher()

MIN_PASSWORD_LENGTH = 10


def hash_password(password: str) -> str:
    return _password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """True if `password` matches `password_hash`. Never raises on a wrong
    password or a malformed/foreign hash — both are just "no match" to a
    caller deciding whether to let someone in."""
    try:
        return _password_hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    """True if `password_hash` was created with weaker-than-current
    parameters (e.g. this project raises Argon2's cost factors later) and
    should be silently re-hashed on the next successful login. Not called
    anywhere yet — here so a future parameter bump has an obvious place
    to plug into, instead of every existing hash needing a forced reset."""
    return _password_hasher.check_needs_rehash(password_hash)


def validate_password_strength(password: str) -> None:
    """Raises ValueError with a user-facing message if `password` is too
    weak to accept. Deliberately simple (length + a little variety) rather
    than a rigid composition rule (\"must contain a symbol\") — those are
    known to push people toward predictable substitutions (\"password!\")
    without actually improving real-world guessability. Length is the
    single strongest lever; NIST SP 800-63B recommends exactly this
    trade-off over composition rules.
    """
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters long.")
    if len(password) > 200:
        # Argon2 has no practical upper bound, but an absurdly long input
        # is either a mistake or a deliberate attempt to waste CPU on the
        # hashing step (Argon2's cost is roughly proportional to input
        # size) — reject rather than hash it.
        raise ValueError("Password is too long.")
    if re.fullmatch(r"(.)\1*", password):
        raise ValueError("Password must not be a single repeated character.")


def normalize_email(email: str) -> str:
    return email.strip().lower()


_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def is_valid_email(email: str) -> bool:
    # Deliberately not a full RFC 5322 validator — those are notoriously
    # over-engineered for what's actually needed here. This catches
    # obvious garbage; real validation of "does this address exist and
    # can it receive mail" only ever happens by actually sending mail to
    # it, which this project doesn't do yet (see EmailProvider).
    return bool(_EMAIL_RE.match(email))


def compute_hmac(secret: bytes, payload: bytes) -> str:
    return hmac.new(secret, payload, hashlib.sha256).hexdigest()


def verify_hmac(secret: bytes, payload: bytes, signature: str) -> bool:
    if not signature:
        return False
    expected = compute_hmac(secret, payload)
    return hmac.compare_digest(expected, signature)


def generate_token() -> str:
    return secrets.token_urlsafe(32)


def generate_secret() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    # Tokens are bearer credentials; store only a hash, same idea as a password.
    return hashlib.sha256(token.encode()).hexdigest()
