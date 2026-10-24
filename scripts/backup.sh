#!/bin/bash
# scripts/backup.sh — a CONSISTENT backup of Garage16: the PostgreSQL database and the telemetry
# files from the same moment, checked, with a manifest (V0.9.8; first version V0.8.8).
#
# Usage (works from any directory; it changes into the project root itself):
#   ./scripts/backup.sh /path/to/backup/dir
#
# Result: <backup-dir>/garage16_<stamp>/{db.sql.gz, telemetry.tar.gz, manifest.json}
# The folder only gets its final name once both files are written AND checked and the manifest
# exists; until then it is garage16_<stamp>.partial (an interrupted run leaves nothing that looks
# like a backup). scripts/restore.sh checks the manifest before it touches anything.
#
# How the two halves stay consistent (server/write_lock.py):
#   1. A small helper container announces maintenance in the telemetry volume and takes the write
#      lock EXCLUSIVELY. New uploads / driver deletions are refused with 503 from that moment (the
#      desktop client keeps the lap and retries); the ones already running finish first.
#   2. Database dump and telemetry archive are made while the lock is held — nothing can change
#      rows and files in between. Reading (pages, analysis, leaderboards) keeps working.
#   3. The lock is released. The helper gives up the lock on its own after
#      GARAGE16_BACKUP_LOCK_MAX_S seconds (default 1800) even if this script is killed — uploads
#      are never blocked for longer; a backup that took longer is discarded, not published.
#
# Checks before publishing: both files are valid gzip, the dump ends with pg_dump's completion
# line (a truncated dump has none), the archive can be listed; the manifest records time, program
# version, schema revision, sizes, SHA-256 of both files, the number of laps and files.
#
# Suggested schedule (Synology Task Scheduler / cron, daily 3:00):
#   0 3 * * * /path/to/project/scripts/backup.sh /volumeUSB2/usbshare/garage16-backups
#
# Copy the backups to a second place yourself — a backup on the same disk does not survive it.

set -euo pipefail

BACKUP_DIR="${1:?Usage: backup.sh <backup-dir>}"
cd "$(dirname "${BASH_SOURCE[0]}")/.."

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
FINAL_DIR="${BACKUP_DIR}/garage16_${TIMESTAMP}"
WORK_DIR="${FINAL_DIR}.partial"
RETENTION_DAYS="${GARAGE16_BACKUP_RETENTION_DAYS:-14}"
LOCK_MAX_S="${GARAGE16_BACKUP_LOCK_MAX_S:-1800}"
LOCK_WAIT_S="${GARAGE16_BACKUP_LOCK_WAIT_S:-300}"
PG_USER="${POSTGRES_USER:-garage16}"
PG_DB="${POSTGRES_DB:-garage16}"

# The telemetry volume is "<compose project>_telemetry_data" — docker-compose.yml pins the project
# name "garage16" (V0.8.8: never derive it from the folder name).
PROJECT_NAME="${COMPOSE_PROJECT_NAME:-garage16}"
TELEMETRY_VOLUME="${PROJECT_NAME}_telemetry_data"
HELPER="garage16_backup_lock_${TIMESTAMP}_$$"
LOCKFILE="/data/.garage16-write.lock"
MARKER="/data/.garage16-maintenance"
STATE="/data/.garage16-backup-${TIMESTAMP}-$$"

log() { echo "[backup] $*"; }
fail() { echo "[backup] ERROR: $*" >&2; exit 1; }

if ! docker volume inspect "${TELEMETRY_VOLUME}" >/dev/null 2>&1; then
    echo "[backup] ERROR: Docker volume '${TELEMETRY_VOLUME}' does not exist." >&2
    echo "[backup] Refusing to run: backing up a missing volume would silently produce an empty archive." >&2
    echo "[backup] Check the name with:  docker volume ls | grep telemetry" >&2
    echo "[backup] (Different project name? Run with COMPOSE_PROJECT_NAME=<name> ./scripts/backup.sh ...)" >&2
    exit 1
fi

mkdir -p "${WORK_DIR}"

cleanup() {
    local code=$?
    # always: let go of the lock and the maintenance marker, never leave uploads blocked
    docker rm -f "${HELPER}" >/dev/null 2>&1 || true
    docker run --rm -v "${TELEMETRY_VOLUME}:/data" alpine \
        sh -c "rm -f '${MARKER}' '${STATE}.locked' '${STATE}.release' '${STATE}.expired'" >/dev/null 2>&1 || true
    if [ "$code" -ne 0 ]; then
        rm -rf "${WORK_DIR}"
        echo "[backup] FAILED — nothing was published (exit ${code})." >&2
    fi
}
trap cleanup EXIT
trap 'exit 130' INT TERM

# --- 1. announce maintenance, wait for running writes, hold the lock --------------------------
log "Taking the write lock (running uploads finish first, new ones are asked to retry)..."
# V0.9.9.3: this helper runs as root — the lock file must keep the owner of the volume (the
# server's user), or the server cannot open it and every upload fails (server/write_lock.py).
docker run -d --name "${HELPER}" -v "${TELEMETRY_VOLUME}:/data" alpine sh -c "
    [ -e '${LOCKFILE}' ] || : > '${LOCKFILE}'
    chown \"\$(stat -c %u:%g /data)\" '${LOCKFILE}'; chmod 664 '${LOCKFILE}'
    echo backup > '${MARKER}'
    exec flock -x '${LOCKFILE}' sh -c '
        touch \"${STATE}.locked\"
        i=0
        while [ ! -e \"${STATE}.release\" ]; do
            i=\$((i + 1))
            if [ \$i -gt \$((${LOCK_MAX_S} * 5)) ]; then touch \"${STATE}.expired\"; break; fi
            sleep 0.2
        done
        rm -f \"${MARKER}\"
    '
" >/dev/null

waited=0
until docker exec "${HELPER}" test -e "${STATE}.locked" 2>/dev/null; do
    sleep 1
    waited=$((waited + 1))
    if [ "$waited" -ge "$LOCK_WAIT_S" ]; then fail "running writes did not finish within ${LOCK_WAIT_S} s"; fi
    if [ "$(docker inspect -f '{{.State.Running}}' "${HELPER}" 2>/dev/null || echo false)" != "true" ]; then
        fail "the lock helper container stopped unexpectedly"
    fi
done
log "Write lock held — database and files are frozen together."

# --- 2. database dump + telemetry archive, inside the locked window ----------------------------
log "Dumping PostgreSQL..."
docker compose exec -T db pg_dump -U "${PG_USER}" "${PG_DB}" | gzip > "${WORK_DIR}/db.sql.gz"
SCHEMA_REVISION=$(docker compose exec -T db psql -U "${PG_USER}" -d "${PG_DB}" -Atc "select version_num from alembic_version" | tr -d '[:space:]')
LAP_COUNT=$(docker compose exec -T db psql -U "${PG_USER}" -d "${PG_DB}" -Atc "select count(*) from laps" | tr -d '[:space:]')
# V0.9.9: the head of the audit chain (server/audit.py) — a copy of it outside the server is what
# shows later whether the chain was rewritten as a whole. V0.9.9.1: read from the dump itself (the
# highest id in its audit_events data), not with a second query: admin decisions and quarantines
# append audit rows without the upload write lock, so a query after pg_dump could see a row the
# dump does not contain — and scripts/restore.sh would then refuse a sound backup.
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
AUDIT_HEAD="null"
HEAD_ROW=$(audit_head_from_dump "${WORK_DIR}/db.sql.gz")
if [ -n "${HEAD_ROW}" ]; then
    AUDIT_HEAD="{\"id\": ${HEAD_ROW%% *}, \"mac\": \"${HEAD_ROW#* }\"}"
fi

log "Archiving telemetry storage volume (${TELEMETRY_VOLUME})..."
docker run --rm -v "${TELEMETRY_VOLUME}:/data:ro" -v "${WORK_DIR}:/backup" alpine \
    tar czf /backup/telemetry.tar.gz -C /data \
        --exclude './.garage16-*' --exclude './.pre_restore_*' .

# --- 3. the lock must still have been ours the whole time, then let go -------------------------
if docker exec "${HELPER}" test -e "${STATE}.expired" 2>/dev/null \
        || [ "$(docker inspect -f '{{.State.Running}}' "${HELPER}" 2>/dev/null || echo false)" != "true" ]; then
    fail "the write lock expired before the backup finished (GARAGE16_BACKUP_LOCK_MAX_S=${LOCK_MAX_S}) — discarded"
fi
docker exec "${HELPER}" touch "${STATE}.release"
docker wait "${HELPER}" >/dev/null 2>&1 || true
log "Write lock released — uploads are accepted again."

# --- 4. check both files before calling it a backup --------------------------------------------
log "Checking the backup..."
gzip -t "${WORK_DIR}/db.sql.gz" || fail "the database dump is not valid gzip"
if [ "$(gzip -dc "${WORK_DIR}/db.sql.gz" | wc -c)" -lt 100 ]; then
    fail "the database dump is empty — is the 'db' container running?"
fi
gzip -dc "${WORK_DIR}/db.sql.gz" | tail -n 5 | grep -q "PostgreSQL database dump complete" \
    || fail "the database dump is incomplete (no completion line)"
gzip -t "${WORK_DIR}/telemetry.tar.gz" || fail "the telemetry archive is not valid gzip"
FILE_COUNT=$(tar tzf "${WORK_DIR}/telemetry.tar.gz" | grep -vc '/$' || true)
[ -n "${SCHEMA_REVISION}" ] || fail "could not read the schema revision"

sha() { sha256sum "$1" | cut -d' ' -f1; }
size() { wc -c < "$1" | tr -d ' '; }
PROGRAM_VERSION=$(sed -n 's/^VERSION = "\(.*\)"/\1/p' server/main.py)

cat > "${WORK_DIR}/manifest.json.tmp" <<EOF
{
  "format": 1,
  "created_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "program_version": "${PROGRAM_VERSION}",
  "schema_revision": "${SCHEMA_REVISION}",
  "consistent": true,
  "lap_count": ${LAP_COUNT:-0},
  "audit_head": ${AUDIT_HEAD},
  "database": {"file": "db.sql.gz", "bytes": $(size "${WORK_DIR}/db.sql.gz"), "sha256": "$(sha "${WORK_DIR}/db.sql.gz")"},
  "telemetry": {"file": "telemetry.tar.gz", "bytes": $(size "${WORK_DIR}/telemetry.tar.gz"), "sha256": "$(sha "${WORK_DIR}/telemetry.tar.gz")", "files": ${FILE_COUNT:-0}}
}
EOF
mv "${WORK_DIR}/manifest.json.tmp" "${WORK_DIR}/manifest.json"

# --- 5. publish: one rename — the folder is complete or not there --------------------------------
mv "${WORK_DIR}" "${FINAL_DIR}"
log "Done: ${FINAL_DIR}  (schema ${SCHEMA_REVISION}, ${LAP_COUNT} laps, ${FILE_COUNT} files)"

log "Pruning complete backups older than ${RETENTION_DAYS} days and leftovers of interrupted runs..."
find "${BACKUP_DIR}" -maxdepth 1 -type d -name 'garage16_*' ! -name '*.partial' -mtime "+${RETENTION_DAYS}" \
    -exec test -e '{}/manifest.json' ';' -exec rm -rf '{}' ';'
find "${BACKUP_DIR}" -maxdepth 1 -type d -name 'garage16_*.partial' -mtime +1 -exec rm -rf '{}' ';'

log "Remaining backups:"
ls -la "${BACKUP_DIR}"
