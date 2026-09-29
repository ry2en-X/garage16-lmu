"""
discord_bot/records.py — Compatibility re-export.

Record evaluation (PR / WR / TEAM_BEST) is server-side business logic that
runs once, inside the upload transaction in server/routers/telemetry.py.
The bot only ever *reads* RecordEvent rows that the server already wrote
(see announce_loop in discord_bot/bot.py) — it must never compute or write
records itself, or the two implementations will drift. This used to be a
second, independent copy of evaluate_and_record(); it's now just a
re-export of the one real implementation in server/records.py.
"""

from __future__ import annotations

from server.records import evaluate_and_record

__all__ = ["evaluate_and_record"]
