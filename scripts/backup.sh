#!/bin/bash
# scripts/backup.sh — Backs up the PostgreSQL DB and telemetry storage
# together, since a restore needs both consistent with each other (a Lap
# row pointing at a telemetry_path that doesn't exist in the storage
# backup is a data-integrity problem the app doesn't handle gracefully).
#
# Usage (works from any directory; it changes into the project root itself):
#   ./scripts/backup.sh /path/to/backup/dir
#
# Suggested cron (daily at 3am, keep last 14 days — adjust retention to
# your actual requirements/storage budget):
#   0 3 * * * cd /opt/garage16 && ./scripts/backup.sh /opt/garage16-backups >> /var/log/garage16-backup.log 2>&1
#
# This does NOT manage offsite replication — copy the resulting archive
# to separate storage (another host, S3-compatible bucket, etc.)
# yourself; a backup that lives on the same disk as the data it backs up
# doesn't survive that disk failing.

set -euo pipefail

BACKUP_DIR="${1:?Usage: backup.sh <backup-dir>}"

# Always operate from the project root (where docker-compose.yml lives),
# whatever directory this was started from — Synology's Task Scheduler and
# cron start scripts in / or /root, where `docker compose exec` can't find
# the project (V0.8.8).
cd "$(dirname "${BASH_SOURCE[0]}")/.."

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
WORK_DIR="${BACKUP_DIR}/garage16_${TIMESTAMP}"
RETENTION_DAYS="${GARAGE16_BACKUP_RETENTION_DAYS:-14}"

# V0.8.8 FIX: the telemetry volume is named "<compose project>_telemetry_data".
# docker-compose.yml pins `name: garage16`, so that is `garage16_telemetry_data`
# — but this script used to derive the prefix from the CURRENT FOLDER's name
# (`$(basename "$(pwd)")`). In a folder called "Garage16 - LMU" that produced
# "Garage16 - LMU_telemetry_data": an invalid volume name, so the telemetry
# step failed after the DB dump had already been written, and every backup was
# incomplete. (Worse: for a folder whose name WAS a valid but different name,
# `docker run -v` would silently create a brand-new EMPTY volume and back that
# up — hence the explicit existence check below.)
PROJECT_NAME="${COMPOSE_PROJECT_NAME:-garage16}"
TELEMETRY_VOLUME="${PROJECT_NAME}_telemetry_data"

if ! docker volume inspect "${TELEMETRY_VOLUME}" >/dev/null 2>&1; then
    echo "[backup] ERROR: Docker volume '${TELEMETRY_VOLUME}' does not exist." >&2
    echo "[backup] Refusing to run: backing up a missing volume would silently produce an empty archive." >&2
    echo "[backup] Check the name with:  docker volume ls | grep telemetry" >&2
    echo "[backup] (Different project name? Run with COMPOSE_PROJECT_NAME=<name> ./scripts/backup.sh ...)" >&2
    exit 1
fi

mkdir -p "${WORK_DIR}"

echo "[backup] Dumping PostgreSQL..."
docker compose exec -T db pg_dump -U "${POSTGRES_USER:-garage16}" "${POSTGRES_DB:-garage16}" \
    | gzip > "${WORK_DIR}/db.sql.gz"

# A dump that is only gzip's empty-input header means pg_dump produced nothing.
if [ "$(gzip -dc "${WORK_DIR}/db.sql.gz" | wc -c)" -lt 100 ]; then
    echo "[backup] ERROR: the database dump is empty — is the 'db' container running?" >&2
    exit 1
fi

echo "[backup] Archiving telemetry storage volume (${TELEMETRY_VOLUME})..."
docker run --rm \
    -v "${TELEMETRY_VOLUME}:/data:ro" \
    -v "${WORK_DIR}:/backup" \
    alpine tar czf /backup/telemetry.tar.gz -C /data .

echo "[backup] Done: ${WORK_DIR}"

echo "[backup] Pruning backups older than ${RETENTION_DAYS} days..."
find "${BACKUP_DIR}" -maxdepth 1 -type d -name 'garage16_*' -mtime "+${RETENTION_DAYS}" -exec rm -rf {} \;

echo "[backup] Remaining backups:"
ls -la "${BACKUP_DIR}"
