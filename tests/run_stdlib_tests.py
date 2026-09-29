"""
Fallback test runner for environments without pytest installed. Runs every
test_*() function in the test modules that need only packages already
confirmed available without the full server venv (stdlib, pandas, numpy,
requests) — not tests/test_upload_streaming.py or any future test needing
fastapi/sqlalchemy/pydantic, which pytest picks up once those are
installed but this runner deliberately does not import (it would crash on
import before running anything else).

Normal usage once dependencies are installed: just run `pytest` from the
project root — it will discover and run these plus the fastapi/sqlalchemy-
dependent modules this runner skips.

Usage: python -m tests.run_stdlib_tests
"""

from __future__ import annotations

import sys
import traceback

from tests import (
    test_lmu_structs,
    test_parser,
    test_recorder,
    test_uploader_retry,
    test_validation,
)

_MODULES = (test_lmu_structs, test_parser, test_validation, test_uploader_retry, test_recorder)


def main() -> int:
    total = 0
    failed = 0
    for module in _MODULES:
        for name in sorted(dir(module)):
            if not name.startswith("test_"):
                continue
            total += 1
            try:
                getattr(module, name)()
                print(f"PASS {module.__name__}.{name}")
            except Exception as exc:  # noqa: BLE001 — test runner, want to keep going
                failed += 1
                print(f"FAIL {module.__name__}.{name}: {exc}")
                traceback.print_exc()
    print()
    print(f"{total - failed}/{total} passed")
    print(
        "(tests/test_upload_streaming.py needs fastapi and is not included here — "
        "run with `pytest` once the server's dependencies are installed)"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
