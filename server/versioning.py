"""server/versioning.py — client-version comparison shared by the upload
endpoint (hard rejection) and GET /health (advance notice), so both
always agree on what "too old" means (V0.8.6)."""

from __future__ import annotations


def parse_version(v: str) -> tuple:
    """Best-effort semver-ish tuple for comparison. Falls back to (0,) for
    anything unparseable, so a garbage client_version fails the minimum
    check rather than crashing it."""
    try:
        return tuple(int(p) for p in v.split(".")[:3])
    except (ValueError, AttributeError):
        return (0,)


def is_client_outdated(client_version: str, min_client_version: str) -> bool:
    return parse_version(client_version) < parse_version(min_client_version)
