"""
Regression test for a real bug reported by the user (2026-09-26): the
client GUI's "Last lap" line always showed "—" instead of the actual
time. Root cause: the telemetry loop pushes a StatusUpdate on every frame
(~60/s), and only the one right after a lap completes carries
last_lap_time — every other one defaults it to None. The GUI's _apply()
applied every update unconditionally, so the very next per-frame update
immediately overwrote the real value back to the placeholder.

client/gui/app.py's should_update_last_lap() is the extracted decision
logic — testable here without a real Tk display, which this project's
Linux dev sandbox doesn't have (`ModuleNotFoundError: No module named
'tkinter'`). The actual widget rendering can only be confirmed on a real
Windows run — see docs/CLIENT_INSTALL.md.
"""

from client.gui.status import StatusUpdate, format_time, should_update_last_lap


def test_should_update_last_lap_true_when_lap_time_present():
    update = StatusUpdate(connected=True, last_lap_time=71.4, last_lap_valid=True)
    assert should_update_last_lap(update) is True


def test_should_update_last_lap_false_for_routine_per_frame_update():
    """This is the exact update that caused the bug: a routine per-frame
    status push with no lap_time attached — must NOT be treated as
    "clear the last-lap display"."""
    update = StatusUpdate(connected=True, track_name="Le Mans", car_name="499P", lap_number=3)
    assert should_update_last_lap(update) is False


def test_format_time_handles_none_and_zero():
    assert format_time(None) == "—"
    assert format_time(0.0) == "—"
    assert format_time(-1.0) == "—"


def test_format_time_formats_minutes_and_seconds():
    assert format_time(71.4) == "1:11.400"
    assert format_time(45.0) == "0:45.000"
