#!/bin/sh
# docker/backup-loop.sh — the `backup` profile service (docker-compose.yml): a CONSISTENT backup
# on an interval, with retention (V0.9.8; first version V0.8.8).
#
# The same guarantees as scripts/backup.sh, inside one container (postgres:16-alpine: pg_dump,
# busybox flock, tar, sha256sum):
#   * maintenance is announced in the telemetry volume and the write lock taken EXCLUSIVELY
#     (server/write_lock.py) — new uploads get 503 and are retried by the client, running ones
#     finish first; the dump and the archive are made while the lock is held;
#   * both files are checked (gzip, pg_dump's completion line, the archive lists), a manifest with
#     sizes, SHA-256, schema revision and lap count is written, and only then the folder
#     garage16_<stamp>/ appears (it is built as garage16_<stamp>.partial) — scripts/restore.sh
#     takes that folder;
#   * if this container dies mid-backup the kernel releases the lock; the marker file is removed
#     on the next start, and the server ignores a marker older than 2 hours anyway.
#
# Where backups land: /backups, mounted from ${BACKUP_HOST_PATH:-backup_data} — set it to a real
# path (a second disk, a cloud-synced folder) to have them outside Docker's own storage.
set -u

DATA=/data/telemetry
LOCK="$DATA/.garage16-write.lock"
MARKER="$DATA/.garage16-maintenance"
INTERVAL="${BACKUP_INTERVAL_SECONDS:-86400}"
RETENTION="${BACKUP_RETENTION_DAYS:-14}"

mkdir -p /backups
rm -f "$MARKER"     # a leftover of a killed run
trap 'rm -f "$MARKER"; exit 130' INT TERM

log() { echo "[backup] $(date -Iseconds) $*"; }

audit_head_from_dump() {  # $1 = db.sql.gz -> "<id> <mac>" of the newest audit row, or nothing
    gzip -dc "$1" | awk -F '\t' '
        /^COPY public\.audit_events \(/ {
            h = $0; sub(/^COPY public\.audit_events \(/, "", h); sub(/\) FROM stdin;.*$/, "", h)
            n = split(h, c, ", "); ci = 0; cm = 0
            for (i = 1; i <= n; i++) { if (c[i] == "id") ci = i; if (c[i] == "mac") cm = i }
            inside = 1; next
        }
        inside && $0 == "\\." { inside = 0; next }
        inside && ci && cm { if ($ci + 0 > best) { best = $ci + 0; mac = $cm } }
        END { if (best > 0) print best " " mac }'
}

one_backup() {
    stamp=$(date +%Y%m%d_%H%M%S)
    final="/backups/garage16_${stamp}"
    work="${final}.partial"
    mkdir -p "$work" || return 1

    log "announcing maintenance, waiting for running uploads..."
    echo backup > "$MARKER"
    # everything between here and the end of the flock runs with the write lock held exclusively
    if ! flock -x "$LOCK" sh -c '
        set -e
        pg_dump | gzip > "$1/db.sql.gz"
        psql -Atc "select version_num from alembic_version" > "$1/.schema"
        psql -Atc "select count(*) from laps" > "$1/.laps"
        cd "$2"
        tar czf "$1/telemetry.tar.gz" --exclude "./.garage16-*" --exclude "./.pre_restore_*" .
    ' sh "$work" "$DATA"; then
        rm -f "$MARKER"
        log "ERROR: dump or archive failed — nothing published"
        rm -rf "$work"
        return 1
    fi
    rm -f "$MARKER"
    log "write lock released"

    if ! gzip -t "$work/db.sql.gz" || ! gzip -dc "$work/db.sql.gz" | tail -n 5 | grep -q "PostgreSQL database dump complete" \
            || ! gzip -t "$work/telemetry.tar.gz"; then
        log "ERROR: the backup did not pass its checks — nothing published"
        rm -rf "$work"
        return 1
    fi
    files=$(tar tzf "$work/telemetry.tar.gz" | grep -vc '/$' || true)
    schema=$(tr -d '[:space:]' < "$work/.schema")
    laps=$(tr -d '[:space:]' < "$work/.laps")
    # V0.9.9: head of the audit chain (server/audit.py); V0.9.9.1: from the dump itself (see
    # scripts/backup.sh — audit rows are also written outside the upload write lock)
    audit_head="null"
    head_row=$(audit_head_from_dump "$work/db.sql.gz")
    if [ -n "$head_row" ]; then
        audit_head="{\"id\": ${head_row%% *}, \"mac\": \"${head_row#* }\"}"
    fi
    rm -f "$work/.schema" "$work/.laps"
    db_sha=$(sha256sum "$work/db.sql.gz" | cut -d' ' -f1)
    tel_sha=$(sha256sum "$work/telemetry.tar.gz" | cut -d' ' -f1)
    cat > "$work/manifest.json.tmp" <<EOF
{
  "format": 1,
  "created_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "program_version": "$(sed -n 's/^VERSION = "\(.*\)"/\1/p' /garage16/main.py 2>/dev/null)",
  "schema_revision": "${schema}",
  "consistent": true,
  "lap_count": ${laps:-0},
  "audit_head": ${audit_head},
  "database": {"file": "db.sql.gz", "bytes": $(wc -c < "$work/db.sql.gz" | tr -d ' '), "sha256": "${db_sha}"},
  "telemetry": {"file": "telemetry.tar.gz", "bytes": $(wc -c < "$work/telemetry.tar.gz" | tr -d ' '), "sha256": "${tel_sha}", "files": ${files:-0}}
}
EOF
    mv "$work/manifest.json.tmp" "$work/manifest.json" && mv "$work" "$final"
    log "backup OK: $final (schema $schema, $laps laps, $files files)"
}

log "Starting backup loop (interval: ${INTERVAL}s, retention: ${RETENTION} days)"
while true; do
    one_backup || true
    find /backups -maxdepth 1 -type d -name 'garage16_*' ! -name '*.partial' -mtime "+${RETENTION}" \
        -exec test -e '{}/manifest.json' ';' -exec rm -rf '{}' ';'
    find /backups -maxdepth 1 -type d -name 'garage16_*.partial' -mtime +1 -exec rm -rf '{}' ';'
    # backups of the V0.8.8–V0.9.7 loop (two loose files per run) age out the same way
    find /backups -maxdepth 1 -name 'garage16_*.sql.gz' -mtime "+${RETENTION}" -delete
    find /backups -maxdepth 1 -name 'garage16_*.tar.gz' -mtime "+${RETENTION}" -delete
    [ "${BACKUP_ONCE:-0}" = "1" ] && exit 0
    sleep "$INTERVAL"
done
