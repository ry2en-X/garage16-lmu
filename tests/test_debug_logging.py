"""
Regression test for a real bug found during LMU hardware verification
(2026-09-24): LMU_GARAGE_DEBUG=1 was set, but no BOUNDARY/EMIT/INIT/STALE
debug lines from client/telemetry/parser.py ever appeared in the log —
only INFO-and-above lines (warnings, errors) showed up.

Root cause: client/main.py's logging.basicConfig() had level=logging.INFO
hardcoded. parser.py's `if _DEBUG: logger.debug(...)` gate only controls
whether the call site attempts to log — it can't override the stdlib
logging module's own level filtering, which drops any DEBUG record when
the effective level is INFO. The env var was read in the wrong place.

Fix: client/logging_config.py's resolve_log_level() is now the single
source of truth main.py's root logger level is set from.
"""

import importlib
import logging

from client.logging_config import resolve_log_level


def test_resolve_log_level_defaults_to_info(monkeypatch):
    monkeypatch.delenv("LMU_GARAGE_DEBUG", raising=False)
    assert resolve_log_level() == logging.INFO


def test_resolve_log_level_debug_when_set_to_1(monkeypatch):
    monkeypatch.setenv("LMU_GARAGE_DEBUG", "1")
    assert resolve_log_level() == logging.DEBUG


def test_resolve_log_level_debug_when_set_to_true(monkeypatch):
    monkeypatch.setenv("LMU_GARAGE_DEBUG", "true")
    assert resolve_log_level() == logging.DEBUG


def test_resolve_log_level_info_for_other_values(monkeypatch):
    # Anything other than the recognized truthy strings must NOT silently
    # enable debug logging.
    monkeypatch.setenv("LMU_GARAGE_DEBUG", "0")
    assert resolve_log_level() == logging.INFO
    monkeypatch.setenv("LMU_GARAGE_DEBUG", "")
    assert resolve_log_level() == logging.INFO


def test_parser_debug_lines_actually_reach_the_log_when_level_is_debug(monkeypatch, caplog):
    """End-to-end regression: with the root logger actually at DEBUG (as
    main.py now sets it when LMU_GARAGE_DEBUG=1 via resolve_log_level()),
    a parser.py boundary event must be capturable by the logging
    framework — this is the exact symptom that was silently broken
    (warnings showed, debug lines never did)."""
    import client.telemetry.parser as parser_module

    monkeypatch.setenv("LMU_GARAGE_DEBUG", "1")
    importlib.reload(parser_module)  # picks up LMU_GARAGE_DEBUG at module load time

    from client.telemetry.reader import Frame
    from tests.test_parser import _telemetry, _scoring, _session  # reuse existing frame builders

    with caplog.at_level(logging.DEBUG, logger="lmu_garage.parser"):
        p = parser_module.LapParser()
        p.feed(Frame(telemetry=_telemetry(lap_number=1, elapsed_time=0.0), scoring=_scoring(), session=_session()))
        p.feed(Frame(telemetry=_telemetry(lap_number=2, elapsed_time=90.0), scoring=_scoring(last_lap_time=90.0), session=_session()))

    assert any("BOUNDARY" in r.message for r in caplog.records), (
        "parser.py's BOUNDARY debug line did not reach the log even at DEBUG level"
    )

    # Reset for other tests in the same process — reload picks up the
    # unset env var again.
    monkeypatch.delenv("LMU_GARAGE_DEBUG", raising=False)
    importlib.reload(parser_module)
