#!/bin/sh
# docker/backup-loop.sh — runs inside the `backup` profile service
# (docker-compose.yml). Dumps PostgreSQL and archives the telemetry
# storage volume on an interval, with retention pruning.
#
# This is the containerized alternative to a NAS-native scheduled task
# (Synology Task Scheduler running scripts/backup.sh on the host) — pick
# whichever fits your setup (see README.md's "Backups" section):
#   - NAS-native: works with Synology's own backup/monitoring UI, but is
#     Synology-specific and needs re-doing if you move to a VPS.
#   - Containerized (this script): identical on NAS and VPS, no
#     host-level cron/Task Scheduler needed at all — just
#     `docker compose --profile backup up -d`.
#
# Where backups land: /backups inside this container, which
# docker-compose.yml mounts from ${BACKUP_HOST_PATH:-backup_data} — set
# that env var to a real path (a second disk's mount point, a
# cloud-sync folder, etc.) to have backups end up somewhere durable
# outside Docker's own volume storage.
set -e

mkdir -p /backups

echo "[backup] Starting backup loop (interval: ${BACKUP_INTERVAL_SECONDS:-86400}s, retention: ${BACKUP_RETENTION_DAYS:-14} days)"

while true; do
    stamp=$(date +%Y%m%d_%H%M%S)
    db_file="/backups/garage16_db_${stamp}.sql.gz"
    telemetry_file="/backups/garage16_telemetry_${stamp}.tar.gz"

    echo "[backup] $(date -Iseconds): dumping database..."
    if pg_dump | gzip > "${db_file}.tmp"; then
        mv "${db_file}.tmp" "${db_file}"
        echo "[backup] Database dump OK: ${db_file}"
    else
        echo "[backup] ERROR: pg_dump failed, skipping this cycle's telemetry archive too" >&2
        rm -f "${db_file}.tmp"
        sleep "${BACKUP_INTERVAL_SECONDS:-86400}"
        continue
    fi

    echo "[backup] Archiving telemetry storage..."
    if tar czf "${telemetry_file}.tmp" -C /data/telemetry . 2>/dev/null; then
        mv "${telemetry_file}.tmp" "${telemetry_file}"
        echo "[backup] Telemetry archive OK: ${telemetry_file}"
    else
        echo "[backup] WARNING: telemetry archive failed (empty volume on a fresh install is normal)" >&2
        rm -f "${telemetry_file}.tmp"
    fi

    echo "[backup] Pruning backups older than ${BACKUP_RETENTION_DAYS:-14} days..."
    find /backups -name 'garage16_*.sql.gz' -mtime "+${BACKUP_RETENTION_DAYS:-14}" -delete
    find /backups -name 'garage16_*.tar.gz' -mtime "+${BACKUP_RETENTION_DAYS:-14}" -delete

    echo "[backup] Cycle complete. Current backups:"
    ls -la /backups/ 2>/dev/null || true

    sleep "${BACKUP_INTERVAL_SECONDS:-86400}"
done
