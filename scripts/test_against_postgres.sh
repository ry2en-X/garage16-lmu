#!/bin/bash
# scripts/test_against_postgres.sh — Runs the full pytest suite AND the
# full Alembic migration chain (upgrade head, downgrade base, upgrade
# head again) against a real PostgreSQL database instead of the SQLite
# one the default `pytest` run uses.
#
# Why this exists (V0.8-NAS §10): every previous test run in this
# project's history — including the SQLite-specific concurrency fix in
# V0.8.1 and the security review in V0.8.2 — only ever ran against
# SQLite. PostgreSQL is the actual production database, and it takes a
# genuinely different code path in at least one place
# (server/database.py's acquire_record_lock — a no-op on SQLite, a real
# pg_advisory_xact_lock on Postgres) that SQLite-only testing can never
# exercise. This script closes that gap.
#
# Usage:
#   ./scripts/test_against_postgres.sh
#
# Requires a reachable PostgreSQL server. Either:
#   - one you already have (set PGHOST/PGPORT/PGUSER/PGPASSWORD/PGDATABASE
#     env vars, or pass a full URL as the first argument), or
#   - a local one for this purpose only, e.g. on Debian/Ubuntu:
#       sudo apt-get install -y postgresql
#       sudo -u postgres createuser garage16_test --pwprompt --createdb
#       sudo -u postgres createdb garage16_test -O garage16_test
#
# This creates and destroys tables in the target database repeatedly
# (Base.metadata.drop_all/create_all, plus the full migration chain) —
# point it at a throwaway test database, never at a real deployment's
# data.

set -euo pipefail

DB_URL="${1:-${LMU_GARAGE_TEST_PG_URL:-postgresql://garage16_test:test_password_123@localhost:5432/garage16_test}}"

echo "=== Testing against: ${DB_URL%%:*}://...(credentials hidden)...@$(echo "$DB_URL" | sed -E 's#.*@##') ==="

echo
echo "--- 1. Full migration chain: upgrade head ---"
LMU_GARAGE_DB_URL="$DB_URL" LMU_GARAGE_ENV=development python3 -m alembic upgrade head

echo
echo "--- 2. Full migration chain: downgrade to base ---"
LMU_GARAGE_DB_URL="$DB_URL" LMU_GARAGE_ENV=development python3 -m alembic downgrade base

echo
echo "--- 3. Full migration chain: upgrade head again (round-trip) ---"
LMU_GARAGE_DB_URL="$DB_URL" LMU_GARAGE_ENV=development python3 -m alembic upgrade head

echo
echo "--- 4. Full pytest suite against this PostgreSQL database ---"
LMU_GARAGE_DB_URL="$DB_URL" python3 -m pytest tests/ -q

echo
echo "=== All checks passed against real PostgreSQL. ==="
