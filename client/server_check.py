"""client/server_check.py — ONE health request per check, three answers
(V0.8.6): is the server reachable, is this client too old for it, and has
it moved. client/main.py's upload loop calls this at startup and then
periodically (so a migration or a minimum-version bump that happens while
a friend's client is already running is picked up without a restart).

Pure logic with no GUI import — tests/test_client_server_check.py drives
it against real local HTTP servers.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import List, Optional

from client.config import ClientConfig
from client.migration import HEALTH_CHECK_TIMEOUT_SECONDS, apply_migration_if_safe, check_for_migration
from client.update_notice import format_update_required_message, is_safe_download_url

logger = logging.getLogger("lmu_garage.server_check")


def _version_tuple(v: str) -> tuple:
    try:
        return tuple(int(p) for p in v.split(".")[:3])
    except (ValueError, AttributeError):
        return (0,)


@dataclass
class ServerCheckResult:
    config: ClientConfig
    reachable: bool
    update_required: bool = False
    # Text for the persistent GUI banner: "" = nothing to show (clears
    # any previous banner), otherwise the message.
    notice: str = ""
    notice_url: Optional[str] = None
    log_lines: List[str] = field(default_factory=list)
    migrated: bool = False


def run_server_check(
    config: ClientConfig, client_version: str, timeout: float = HEALTH_CHECK_TIMEOUT_SECONDS
) -> ServerCheckResult:
    body = check_for_migration(config.api_url, timeout=timeout, client_version=client_version)
    if body is None:
        return ServerCheckResult(
            config=config,
            reachable=False,
            notice="Can't reach the Garage16 server right now — your laps are saved and will upload automatically once it's back.",
            log_lines=[f"Could not reach the server at {config.api_url}. Will keep retrying in the background."],
        )

    log_lines: List[str] = []
    notice_parts: List[str] = []

    # --- migration first: if the server moved, the update verdict below
    # is about the OLD server and will be re-evaluated against the new one
    # on the very next check (seconds later, since a successful switch
    # schedules an immediate re-check in main.py). ---
    migration = apply_migration_if_safe(config, body)
    if migration.message:
        log_lines.append(migration.message)
        # A successful switch is good news, not a warning — only surface a
        # banner for the "couldn't follow automatically" cases.
        if not migration.migrated:
            notice_parts.append(migration.message)

    # --- update required: prefer the server's own verdict (single source
    # of truth, server/versioning.py); fall back to a local comparison
    # against min_client_version for servers that predate ?client_version. ---
    if "update_required" in body:
        update_required = bool(body["update_required"])
    else:
        min_v = body.get("min_client_version")
        update_required = bool(min_v) and _version_tuple(client_version) < _version_tuple(min_v)

    if update_required:
        msg = format_update_required_message(body.get("min_client_version"), body.get("client_download_url"))
        log_lines.append(msg)
        notice_parts.insert(0, msg)

    download_url = body.get("client_download_url")
    return ServerCheckResult(
        config=migration.config,
        reachable=True,
        update_required=update_required,
        notice=" ".join(notice_parts),
        notice_url=download_url if update_required and is_safe_download_url(download_url) else None,
        log_lines=log_lines,
        migrated=migration.migrated,
    )
