"""server/versioning.py — client-version comparison shared by the upload
endpoint (hard rejection) and GET /health (advance notice), so both
always agree on what "too old" means (V0.8.6)."""

from __future__ import annotations


def parse_version(v: str) -> tuple:
    """Best-effort semver-ish tuple for comparison. Falls back to (0,) for
    anything unparseable, so a garbage client_version fails the minimum
    check rather than crashing it."""
    try:
        return tuple(int(p) for p in v.split(".")[:4])  # x.y.z[.w] (V0.9.3.1)
    except (ValueError, AttributeError):
        return (0,)


def is_client_outdated(client_version: str, min_client_version: str) -> bool:
    return parse_version(client_version) < parse_version(min_client_version)


# V0.9.0: the first client version that records the extended channels (suspension,
# tyre forces, accelerations, driver aids, ...). Older clients keep working — their
# laps just carry the basic channels — and the analysis says so.
FULL_DATA_CLIENT_VERSION = "0.9.0"


def data_level_for_client_version(client_version) -> str:
    """"full" or "basic" from the version the lap was uploaded with. Used for
    lists, where reading every lap's file would be far too slow; the analysis
    itself decides from the file's actual columns (see
    telemetry_analysis.recording_level), which is authoritative."""
    if not client_version:
        return "basic"
    return "basic" if is_client_outdated(client_version, FULL_DATA_CLIENT_VERSION) else "full"

