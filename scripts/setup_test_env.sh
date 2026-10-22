#!/bin/bash
# scripts/setup_test_env.sh — a test environment in which the WHOLE suite runs (V0.9.7).
#
#   ./scripts/setup_test_env.sh            creates .venv (Python 3.12 if available) with the exact
#                                          versions of constraints.txt, and tests/js/node_modules
#                                          (jsdom) from package-lock.json
#   . .venv/bin/activate && python -m scripts.release_check
#
# System packages it cannot install for you (Debian/Ubuntu names):
#   python3-tk xvfb nodejs (18+) — and caddy (https://caddyserver.com/download, or CADDY_BIN=...)
# The release check lists whatever is still missing instead of passing without it.

set -euo pipefail
cd "$(dirname "$0")/.."

PYTHON="${PYTHON:-$(command -v python3.12 || command -v python3)}"
echo "--- Python: $("$PYTHON" --version) ($PYTHON)"
"$PYTHON" -m venv .venv
.venv/bin/python -m pip install --upgrade pip >/dev/null
.venv/bin/python -m pip install \
  -r server/requirements.txt -r discord_bot/requirements.txt -r client/requirements.txt -r requirements-dev.txt \
  -c constraints.txt

echo "--- Node: $(node --version 2>/dev/null || echo 'MISSING — install Node.js 18+')"
if command -v npm >/dev/null; then
  (cd tests/js && npm ci --no-audit --no-fund)
fi

# V0.9.8: the backup/restore drill (tests/test_v098_backup_restore.py) runs the scripts' Alpine
# containers as chroots in this exact root file system (pinned by its SHA-256)
ALPINE_URL="https://dl-cdn.alpinelinux.org/alpine/v3.21/releases/x86_64/alpine-minirootfs-3.21.8-x86_64.tar.gz"
ALPINE_SHA256="6ea461b0225faad280b7e13df878b30fdd6d969dde25ef05f85a1bcec9033348"
ALPINE_FILE="tests/.cache/alpine-minirootfs-3.21.8-x86_64.tar.gz"
mkdir -p tests/.cache
if [ ! -s "$ALPINE_FILE" ]; then
  echo "--- Alpine root file system for the backup/restore drill"
  curl -fsSL -o "$ALPINE_FILE.tmp" "$ALPINE_URL" && mv "$ALPINE_FILE.tmp" "$ALPINE_FILE" || echo "    download failed — the drill will report it"
fi
if [ -s "$ALPINE_FILE" ] && [ "$(sha256sum "$ALPINE_FILE" | cut -d' ' -f1)" != "$ALPINE_SHA256" ]; then
  echo "    WRONG SHA-256 — removed"; rm -f "$ALPINE_FILE"
fi

echo
echo "The backup/restore drill also needs: PostgreSQL client tools (pg_dump, psql), root or"
echo "passwordless sudo, and a PostgreSQL superuser URL in GARAGE16_TEST_PG_ADMIN_URL."
echo
echo "Done. Next:  . .venv/bin/activate && python -m scripts.release_check"
