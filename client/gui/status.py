"""
status.py — StatusUpdate and its pure helper logic, with no GUI
dependency.

Split out from app.py so this logic is testable without importing
tkinter (unavailable in some environments, e.g. this project's Linux dev
sandbox — see tests/test_gui_last_lap.py). Mirrors the same pattern
already used for client/logging_config.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class StatusUpdate:
    connected: bool = False
    track_name: str = "—"
    car_name: str = "—"
    lap_number: int = 0
    last_lap_time: Optional[float] = None
    last_lap_valid: Optional[bool] = None
    log_line: Optional[str] = None
    # V0.8.6: log_only=True marks an update that carries ONLY a log line
    # and/or notice (everything from the uploader thread: server checks,
    # upload results). Before this, such updates went through _apply like
    # telemetry updates and — since every other field has a default —
    # silently reset the "Connected" indicator to Disconnected and
    # track/car/lap back to "—" each time the uploader logged anything.
    log_only: bool = False
    # A persistent, prominent banner at the top of the window (update
    # required, server unreachable, server moved) — unlike log_line it
    # doesn't scroll away. "" clears it; None leaves it untouched.
    notice: Optional[str] = None
    # Optional link shown as an 'Open download page' button under the
    # banner (only offered when it passes update_notice.is_safe_download_url).
    notice_url: Optional[str] = None


def format_time(seconds: Optional[float]) -> str:
    if not seconds or seconds <= 0:
        return "—"
    minutes = int(seconds // 60)
    secs = seconds - minutes * 60
    return f"{minutes}:{secs:06.3f}"


def should_update_last_lap(update: StatusUpdate) -> bool:
    """BUGFIX (reported 2026-09-26): the telemetry loop pushes a
    StatusUpdate on every frame (~60/s) to keep track/car/lap current, but
    only ONE update per lap — the one right after recorder.save() — ever
    carries last_lap_time. Applying every update unconditionally (the
    previous behavior) meant the very next per-frame update, whose
    last_lap_time defaults to None, immediately overwrote the real value
    back to the "—" placeholder — the driver saw it flash for a fraction
    of a second at 60 updates/sec and never actually caught it. Only
    updating the "Last lap" line when an update genuinely carries a
    completed lap — and leaving it untouched otherwise — fixes that."""
    return update.last_lap_time is not None


def should_update_telemetry_fields(update: StatusUpdate) -> bool:
    """False for log-only updates — see StatusUpdate.log_only."""
    return not update.log_only
