"""
crypto.py — Encrypts client_secret at rest.

Driver.client_secret has to be recoverable in plaintext server-side (it's
an HMAC *key*, used to recompute and check upload signatures — unlike a
password, you can't verify against just a hash of it). "Encrypted at rest"
here means: not sitting in the DB file as plaintext, decryptable only by
whoever holds LMU_GARAGE_SECRET_KEY (an operational secret, not something
that lives in the DB alongside the data it protects).

This uses Fernet (AES-128-CBC + HMAC, from the `cryptography` package) —
authenticated symmetric encryption, which is the right tool for "this
server needs to read this value back later", as opposed to hashing (for
"I only ever need to check equality") or asymmetric crypto (which would be
overkill for a single service that both encrypts and decrypts).

Setup:
    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    export LMU_GARAGE_SECRET_KEY=<the output>

Without LMU_GARAGE_SECRET_KEY set:
  - LMU_GARAGE_ENV=production (or unset/anything else — "production" is
    the one value that changes this): hard failure at import time. A
    known, publicly-committed key protecting real data is worse than an
    obvious startup crash; there is no safe fallback to fall back to.
  - otherwise (local/solo dev): a fixed dev-only key is used (loudly
    logged) so `uvicorn server.main:app` keeps working with zero setup —
    anyone with this file's source can decrypt anything encrypted with
    that key, so it must never be reachable outside a machine only you use.
"""

from __future__ import annotations

import logging
import os

from cryptography.fernet import Fernet, InvalidToken

from .config import settings

logger = logging.getLogger("lmu_garage.crypto")

# NOT SECRET — a fixed fallback so `python -m uvicorn server.main:app` keeps
# working with zero setup in development. Never used when
# settings.is_production is true (see _load_key) — refusing to start is
# the correct behavior there, not silently protecting real secrets with a
# key anyone reading this file already has.
_DEV_ONLY_FALLBACK_KEY = b"c2FtcGxlLWRldi1vbmx5LWtleS1kby1ub3QtdXNlISE="


def _load_key() -> bytes:
    raw = os.environ.get("LMU_GARAGE_SECRET_KEY")
    if raw:
        return raw.encode()

    if settings.is_production:
        raise RuntimeError(
            "LMU_GARAGE_SECRET_KEY is not set and LMU_GARAGE_ENV=production — refusing "
            "to start. Generate a real key (see this module's docstring) and set "
            "LMU_GARAGE_SECRET_KEY before running in production; there is no safe "
            "default for encrypting client secrets at rest."
        )

    logger.warning(
        "LMU_GARAGE_SECRET_KEY is not set — using an insecure, publicly-known "
        "dev key to encrypt client secrets. Fine for local dev; generate a "
        "real key before running against a shared or public database (see "
        "server/crypto.py docstring)."
    )
    return _DEV_ONLY_FALLBACK_KEY


_fernet = Fernet(_load_key())


def encrypt_secret(plaintext: str) -> str:
    return _fernet.encrypt(plaintext.encode()).decode()


def decrypt_secret(ciphertext: str) -> str:
    try:
        return _fernet.decrypt(ciphertext.encode()).decode()
    except InvalidToken as exc:
        # Most likely cause: LMU_GARAGE_SECRET_KEY changed (or was unset,
        # then set) since this secret was stored — it can no longer be
        # decrypted with the current key. Rotating the affected driver's
        # secret (POST /accounts/rotate-secret) is the only fix.
        raise ValueError(
            "Could not decrypt stored client_secret — LMU_GARAGE_SECRET_KEY may "
            "have changed. Affected drivers need to rotate their secret."
        ) from exc
