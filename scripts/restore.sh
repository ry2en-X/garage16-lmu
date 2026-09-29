#!/bin/bash
# scripts/restore.sh — restores a Garage16 backup: the PostgreSQL dump AND the
# telemetry files, together (V0.8.8).
#
# Usage (from anywhere; it changes into the project root itself):
#   ./scripts/restore.sh <db.sql.gz> <telemetry.tar.gz>
#
# Both backup styles are supported — just pass the two files:
#   scripts/backup.sh        -> <backup-dir>/garage16_<stamp>/db.sql.gz
#                                              + .../telemetry.tar.gz
#   `--profile backup` loop  -> <BACKUP_HOST_PATH>/garage16_db_<stamp>.sql.gz
#                                              + garage16_telemetry_<stamp>.tar.gz
# The two files MUST come from the same backup run (same timestamp): a
# database that references telemetry files the archive doesn't contain is a
# data-integrity problem the app can't repair.
#
# What it does, in order:
#   1. checks both files (exist, valid gzip, non-empty) and asks you to type
#      RESTORE — this REPLACES the live database and telemetry (pass --yes to
#      skip the question in automation);
#   2. stops the writers (server, discord_bot);
#   3. makes a safety dump of the CURRENT database next to your backup file
#      (pre_restore_<stamp>.sql.gz) — best effort: if the database is what's
#      broken, that dump may legitimately fail, and it says so;
#   4. drops and re-creates the database and loads the dump. A plain
#      `psql < dump` into the existing database does NOT work — PostgreSQL
#      answers 'relation "alembic_version" already exists' (verified against
#      a real PostgreSQL 16) — so the database is recreated first;
#   5. replaces the telemetry volume's contents with the archive;
#   6. starts server + discord_bot again (the server's entrypoint runs
#      `alembic upgrade head`, so restoring an older backup into a newer
#      version migrates it forward automatically).
#
# Does not touch the reverse proxy / tunnel containers.
#
# RESTORE_START_SERVICES (env, default "server discord_bot"): which services
# to start again at the end. When you restore onto a NEW server while the old
# one is still running (a server move), start only the API and leave the bot
# off until the cutover — two bots with the same token announce every record
# twice:
#   RESTORE_START_SERVICES="server" ./scripts/restore.sh db.sql.gz telemetry.tar.gz

set -euo pipefail

ASSUME_YES=0
ARGS=()
for arg in "$@"; do
    if [ "$arg" = "--yes" ]; then ASSUME_YES=1; else ARGS+=("$arg"); fi
done
if [ "${#ARGS[@]}" -ne 2 ]; then
    echo "Usage: restore.sh [--yes] <db.sql.gz> <telemetry.tar.gz>" >&2
    exit 2
fi
DB_FILE="$(cd "$(dirname "${ARGS[0]}")" && pwd)/$(basename "${ARGS[0]}")"
TELEMETRY_FILE="$(cd "$(dirname "${ARGS[1]}")" && pwd)/$(basename "${ARGS[1]}")"

cd "$(dirname "${BASH_SOURCE[0]}")/.."

PG_USER="${POSTGRES_USER:-garage16}"
PG_DB="${POSTGRES_DB:-garage16}"
PROJECT_NAME="${COMPOSE_PROJECT_NAME:-garage16}"
TELEMETRY_VOLUME="${PROJECT_NAME}_telemetry_data"

for f in "$DB_FILE" "$TELEMETRY_FILE"; do
    if [ ! -s "$f" ]; then echo "[restore] ERROR: '$f' does not exist or is empty." >&2; exit 1; fi
    if ! gzip -t "$f" 2>/dev/null; then echo "[restore] ERROR: '$f' is not a valid gzip file." >&2; exit 1; fi
done
if ! docker volume inspect "$TELEMETRY_VOLUME" >/dev/null 2>&1; then
    echo "[restore] ERROR: Docker volume '$TELEMETRY_VOLUME' does not exist (has the stack ever been started?)." >&2
    exit 1
fi

echo "[restore] Database dump : $DB_FILE"
echo "[restore] Telemetry     : $TELEMETRY_FILE"
echo "[restore] This REPLACES the current database '$PG_DB' and ALL telemetry files."
if [ "$ASSUME_YES" -ne 1 ]; then
    read -r -p "[restore] Type RESTORE to continue: " answer
    if [ "$answer" != "RESTORE" ]; then echo "[restore] Aborted — nothing was changed."; exit 1; fi
fi

echo "[restore] Stopping server and discord_bot..."
docker compose stop server discord_bot
echo "[restore] Making sure the database container is up..."
docker compose up -d db
for _ in $(seq 1 60); do
    if docker compose exec -T db pg_isready -U "$PG_USER" -d postgres >/dev/null 2>&1; then break; fi
    sleep 1
done
docker compose exec -T db pg_isready -U "$PG_USER" -d postgres >/dev/null

SAFETY="$(dirname "$DB_FILE")/pre_restore_$(date +%Y%m%d_%H%M%S).sql.gz"
echo "[restore] Safety dump of the current database -> $SAFETY"
if docker compose exec -T db pg_dump -U "$PG_USER" "$PG_DB" 2>/dev/null | gzip > "$SAFETY" && [ "$(gzip -dc "$SAFETY" | wc -c)" -gt 100 ]; then
    echo "[restore] Safety dump written."
else
    rm -f "$SAFETY"
    echo "[restore] WARNING: could not make a safety dump (the current database may be the broken part) — continuing." >&2
fi

echo "[restore] Re-creating database '$PG_DB'..."
docker compose exec -T db psql -U "$PG_USER" -d postgres -v ON_ERROR_STOP=1 -q \
    -c "DROP DATABASE IF EXISTS \"$PG_DB\" WITH (FORCE);" \
    -c "CREATE DATABASE \"$PG_DB\" OWNER \"$PG_USER\";"

echo "[restore] Loading the dump..."
gzip -dc "$DB_FILE" | docker compose exec -T db psql -U "$PG_USER" -d "$PG_DB" -q -v ON_ERROR_STOP=1

echo "[restore] Restoring telemetry files into volume $TELEMETRY_VOLUME..."
docker run --rm \
    -v "${TELEMETRY_VOLUME}:/data" \
    -v "$(dirname "$TELEMETRY_FILE"):/backup:ro" \
    alpine sh -c "find /data -mindepth 1 -delete && tar xzf /backup/$(basename "$TELEMETRY_FILE") -C /data"

START_SERVICES="${RESTORE_START_SERVICES:-server discord_bot}"
echo "[restore] Starting ${START_SERVICES}..."
# shellcheck disable=SC2086  # intentional word splitting: a list of service names
docker compose up -d ${START_SERVICES}

echo "[restore] Done. Check:  docker compose ps   and   docker compose logs --tail 30 server"
