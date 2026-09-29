"""
logging_config.py — Resolves the client's log level from LMU_GARAGE_DEBUG.

Split out from main.py (rather than inlined into its logging.basicConfig
call) for two reasons: it keeps the env-var-to-level mapping testable in
isolation (see tests/test_debug_logging.py) without needing to import
client.main — which pulls in client.gui.app (tkinter), unavailable in
some environments (e.g. headless CI/dev sandboxes) — and it documents,
in one place, exactly what turns on the verbose parser.py debug logging
referenced by docs/LMU_VERIFICATION_PROTOCOL.md.
"""

from __future__ import annotations

import logging
import os


def resolve_log_level() -> int:
    """LMU_GARAGE_DEBUG=1/true/yes -> DEBUG, anything else -> INFO.

    This is the single source of truth both main.py's root logger level
    and parser.py's `_DEBUG` gate should agree with — a mismatch between
    the two is exactly what caused a real bug (2026-09-24 LMU
    verification session): parser.py's `_DEBUG` gate decided whether to
    *call* logger.debug(...), but main.py separately hardcoded the root
    logger to INFO, so the stdlib logging module silently dropped every
    debug record regardless of what parser.py tried to log.
    """
    debug_enabled = os.environ.get("LMU_GARAGE_DEBUG", "").strip() in ("1", "true", "yes")
    return logging.DEBUG if debug_enabled else logging.INFO
