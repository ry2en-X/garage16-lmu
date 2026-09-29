"""client/update_notice.py — the single place that words the
"you need a newer Garage16" message (V0.8.6), used both when GET /health
says so in advance and when an upload gets a 426 UPDATE_REQUIRED back,
so a friend always sees the same plain-language text either way."""

from __future__ import annotations

from typing import Optional
from urllib.parse import urlparse


def format_update_required_message(min_version: Optional[str], download_url: Optional[str]) -> str:
    lines = ["A new version of Garage16 is required."]
    if min_version:
        lines[0] = f"A new version of Garage16 (v{min_version} or newer) is required."
    lines.append("Please update Garage16 to keep using the server. Your recorded laps are kept and will upload after you update.")
    if download_url:
        lines.append(f"Download the new version here: {download_url}")
    else:
        lines.append("Ask whoever runs your Garage16 server for the new version.")
    return " ".join(lines)


def is_safe_download_url(url: Optional[str]) -> bool:
    """The download link comes from the SERVER (LMU_GARAGE_CLIENT_DOWNLOAD_URL
    in /health and 426 responses), so it's treated as untrusted input
    before the GUI offers to open it in a browser: plain http(s) with a
    host only — never file:, javascript:, or anything else a browser or
    the OS might act on differently. The client never downloads or runs
    anything itself; this only ever gates opening a page for the user."""
    if not url or not isinstance(url, str) or len(url) > 2048:
        return False
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)
