"""client/migration.py — Automatic server-migration handling (V0.8.6).

Replaces the V0.8.1 manual `--follow-migration` command as the primary
UX: a friend running the packaged app never types a command. The client
checks for a migration signal on every health check it already makes,
and — only when it's safe to do so — updates its own saved config
automatically. When it's NOT safe (or the new URL turns out to be
unreachable/broken), it changes nothing and says so; a friend's working
setup must never be silently destroyed by a bad or spoofed signal.

Security model (spec §4: "einfachste sichere Lösung, die zum bestehenden
System passt", no new PKI):

  - The migration signal (`migrated_to` in GET {api_url}/health's JSON
    body) is only trusted when the CURRENT connection to the old server
    is HTTPS. `requests` validates the server's TLS certificate by
    default (no `verify=False` anywhere in this codebase) — that
    existing certificate validation IS the trust anchor here: if the
    response body actually came from `https://old-domain`, a real CA
    already vouched for that at the TLS layer, so a response saying
    "I've moved to https://new-domain" is exactly as trustworthy as any
    other authenticated response from that server. Over plain HTTP
    there's no such guarantee (trivially spoofable on a LAN — ARP/DNS
    spoofing, a rogue access point) — auto-follow is refused for that
    case, no exception.
  - The new URL must itself be `https://` — never auto-follow a
    migration into plain HTTP, which would downgrade the connection's
    own security for every request after.
  - The new URL is verified reachable (a real GET .../health that
    actually responds) BEFORE the saved config is touched at all. A
    typo'd or dead `migrated_to` value changes nothing.
  - Credentials (auth_token, client_secret) are never sent to the new
    URL as part of this check — verifying reachability only ever calls
    the unauthenticated /health endpoint, the same one already used for
    the version-check. The first real request to the new URL happens
    through the exact same upload path as any other request, with the
    exact same driver-supplied credentials as before — nothing new is
    ever transmitted anywhere as a side effect of following a migration.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

import requests

from client.config import ClientConfig, save_config

logger = logging.getLogger("lmu_garage.migration")

HEALTH_CHECK_TIMEOUT_SECONDS = 5


@dataclass
class MigrationResult:
    config: ClientConfig
    # Non-None exactly when something happened worth telling the user
    # about (either a successful switch, or a migration signal that was
    # deliberately NOT followed) — client/main.py surfaces this as a GUI
    # status line. None means "checked, nothing to report" (the common
    # case — no migration in progress).
    message: Optional[str] = None
    migrated: bool = False


def _is_https(url: str) -> bool:
    return urlparse(url).scheme == "https"


def _is_well_formed_server_url(url: str) -> bool:
    """Rejects anything that isn't a plain http(s) URL with a host —
    no `javascript:`, `file:`, empty netloc, or other nonsense a
    compromised or buggy server could hand back."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def check_for_migration(
    current_api_url: str,
    timeout: float = HEALTH_CHECK_TIMEOUT_SECONDS,
    client_version: Optional[str] = None,
) -> Optional[dict]:
    """Fetches GET {current_api_url}/health and returns the parsed JSON
    body, or None on any failure (unreachable, non-200, invalid JSON) —
    this is a best-effort check that must never raise; the caller's
    normal retry loop already handles "server unreachable" for the
    actual upload path."""
    try:
        resp = requests.get(
            f"{current_api_url.rstrip('/')}/health",
            params={"client_version": client_version} if client_version else None,
            timeout=timeout,
        )
        if resp.status_code != 200:
            return None
        return resp.json()
    except (requests.RequestException, ValueError):
        return None


def _verify_new_server_reachable(new_url: str, timeout: float = HEALTH_CHECK_TIMEOUT_SECONDS) -> bool:
    """Fall C's guard: an invalid/dead new URL must never be adopted.
    Deliberately only checks /health (no credentials involved) — see
    module docstring."""
    try:
        resp = requests.get(f"{new_url.rstrip('/')}/health", timeout=timeout)
        if resp.status_code != 200:
            return False
        body = resp.json()
        return isinstance(body, dict) and "status" in body
    except (requests.RequestException, ValueError):
        return False


def apply_migration_if_safe(
    config: ClientConfig, health_body: Optional[dict], allow_insecure: bool = False
) -> MigrationResult:
    """The core decision, given a health-check response already fetched
    by the caller (see check_for_migration). Pure with respect to the
    network except for the one verification call to the NEW url — never
    mutates `config` in place, never writes the config file unless the
    migration is fully validated and applied.

    `allow_insecure=True` is ONLY for the explicit, manual
    `--follow-migration` command (client/main.py): a person deliberately
    typing that command at a terminal is themselves the consent that the
    automatic path has to derive from HTTPS. Everything else — well-
    formedness, reachability of the new server — is still enforced.
    """
    if not health_body:
        return MigrationResult(config=config)

    new_url = health_body.get("migrated_to")
    if not new_url:
        return MigrationResult(config=config)

    if new_url == config.api_url:
        # Already there (e.g. this got called again after a previous
        # successful switch, before the operator removed the flag on
        # their end) — nothing to do, not an error.
        return MigrationResult(config=config)

    if not _is_well_formed_server_url(new_url):
        logger.warning("Ignoring malformed migration target: %r", new_url)
        return MigrationResult(
            config=config,
            message=f"Server reported an invalid migration target ({new_url!r}) — ignoring it. Your current connection is unaffected.",
        )

    if not allow_insecure and (not _is_https(config.api_url) or not _is_https(new_url)):
        # Fall-back path: tell the user plainly, change nothing. A
        # friend running the packaged app has no terminal to run a
        # manual command in — this message just needs to be honest
        # about why nothing happened automatically.
        logger.warning(
            "Migration signal seen (%s -> %s) but not over HTTPS on both ends — not auto-following.",
            config.api_url, new_url,
        )
        return MigrationResult(
            config=config,
            message=(
                f"This server may have moved to {new_url}, but the connection isn't secure enough "
                "to switch automatically. Please ask whoever runs your Garage16 server for updated "
                "connection details."
            ),
        )

    if not _verify_new_server_reachable(new_url):
        logger.warning("Migration target %s did not respond to a health check — not switching.", new_url)
        return MigrationResult(
            config=config,
            message=f"This server reported moving to {new_url}, but that address isn't responding — staying on the current connection.",
        )

    new_config = ClientConfig(api_url=new_url, auth_token=config.auth_token, client_secret=config.client_secret)
    try:
        save_config(new_config)
    except OSError as exc:
        # Can't persist the switch (full disk, permissions, ...) — do NOT
        # adopt it in memory either: running against a new URL that a
        # restart would silently forget is worse than staying put and
        # trying again on the next check. The (atomic) save_config
        # guarantees the old file is still intact.
        logger.error("Could not save migrated config (%s) — staying on %s.", exc, config.api_url)
        return MigrationResult(
            config=config,
            message="Your server has moved, but Garage16 couldn't save the new address — staying on the current connection for now.",
        )
    logger.info("Followed server migration: %s -> %s", config.api_url, new_url)
    return MigrationResult(
        config=new_config,
        message=f"Your Garage16 server has moved — switched to {new_url} automatically.",
        migrated=True,
    )


def check_and_apply_migration(config: ClientConfig) -> MigrationResult:
    """Convenience wrapper combining both steps for the common call site
    (client/main.py's startup and periodic checks)."""
    health_body = check_for_migration(config.api_url)
    return apply_migration_if_safe(config, health_body)
