"""
scripts/test_requirements.py — what the FULL test suite needs, in one place (V0.9.7).

Used by tests/conftest.py (strict mode refuses to start without it) and by
scripts/release_check.py (the release gate reports it as an infrastructure problem, exit code 6,
never as "tests passed"). Plain standard library: it must work in an environment where nothing
else is installed yet — that is exactly when it is needed.
"""

from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# (import name, package name as in the requirements files)
PYTHON_PACKAGES = (
    ("fastapi", "fastapi"), ("httpx", "httpx"), ("sqlalchemy", "sqlalchemy"), ("alembic", "alembic"),
    ("numpy", "numpy"), ("pandas", "pandas"), ("pyarrow", "pyarrow"), ("yaml", "pyyaml"),
    ("argon2", "argon2-cffi"), ("cryptography", "cryptography"), ("multipart", "python-multipart"),
    ("discord", "discord.py"), ("requests", "requests"), ("pytest", "pytest"), ("psycopg", "psycopg"),
    # V0.9.8: the quality steps of the release gate
    ("ruff", "ruff"), ("coverage", "coverage"), ("bandit", "bandit"), ("pip_audit", "pip-audit"),
    # V0.9.9: property-based tests of the lap decision
    ("hypothesis", "hypothesis"),
)

# tools some tests drive as real programs (each test says so when it skips)
TOOLS = (
    ("node", "node (Node.js 18 or newer) — the frontend DOM tests"),
    ("bash", "bash — the backup/restore script tests"),
    ("gzip", "gzip — the backup/restore script tests"),
    ("xvfb-run", "xvfb-run (package xvfb) — the desktop client window tests"),
)


def missing_test_dependencies(include_tools: bool = True) -> list:
    """Human-readable list of what is missing; empty when the suite can run completely."""
    missing = []
    for module, package in PYTHON_PACKAGES:
        if importlib.util.find_spec(module) is None:
            missing.append(f"python package {package}")
    if importlib.util.find_spec("tkinter") is None:
        missing.append("tkinter (package python3-tk) — the desktop client window tests")
    if not (ROOT / "tests" / "js" / "node_modules" / "jsdom").exists():
        missing.append("jsdom — run `npm ci` in tests/js")
    if include_tools:
        for tool, label in TOOLS:
            if shutil.which(tool) is None:
                missing.append(label)
        import os
        caddy = os.environ.get("CADDY_BIN") or shutil.which("caddy")
        if not caddy or not Path(caddy).exists():
            missing.append("caddy (set CADDY_BIN or put caddy on PATH) — the reverse-proxy tests")
        # V0.9.8: the real backup/restore drill
        if not os.environ.get("GARAGE16_TEST_PG_ADMIN_URL"):
            missing.append("GARAGE16_TEST_PG_ADMIN_URL (a PostgreSQL superuser URL) — the backup/restore drill")
        for tool in ("pg_dump", "psql", "unshare", "nsenter", "chroot", "flock"):
            if shutil.which(tool) is None:
                missing.append(f"{tool} — the backup/restore drill")
        alpine = Path(os.environ.get("GARAGE16_ALPINE_ROOTFS", ROOT / "tests" / ".cache" / "alpine-minirootfs-3.21.8-x86_64.tar.gz"))
        if not alpine.exists():
            missing.append("the Alpine root file system (scripts/setup_test_env.sh fetches it) — the backup/restore drill")
        if os.geteuid() != 0 and shutil.which("sudo") is None:
            missing.append("root or passwordless sudo — the backup/restore drill")
    return missing
