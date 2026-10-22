#!/bin/bash
# scripts/restore.sh — restores a Garage16 backup so that the result is EITHER the validated backup
# OR the state before — never a half-replaced system (V0.9.8; first version V0.8.8).
#
# Usage (from anywhere; it changes into the project root itself):
#   ./scripts/restore.sh [--yes] <backup-folder>                 e.g. .../garage16_20261019_030000
#   ./scripts/restore.sh [--yes] --allow-unverified <db.sql.gz> <telemetry.tar.gz>
#                                         backups made before V0.9.8 (no manifest to check against)
#   ./scripts/restore.sh --rollback <stamp>   go back to the state before restore <stamp>
#   ./scripts/restore.sh --cleanup  <stamp>   delete the rollback point of restore <stamp>
#
# The steps — and what happens when one fails:
#   1 CHECK     manifest present, SHA-256 and sizes match, both files valid gzip, the dump ends
#               with pg_dump's completion line, the archive has no absolute or ../ paths, the
#               schema revision is one this program knows.          fails -> nothing touched
#   2 STAGE_DB  the dump goes into a NEW database <db>_restore_<stamp>. fails -> it is dropped
#   3 STAGE_FILES the archive goes into a NEW volume <project>_telemetry_restore_<stamp>.
#                                                                     fails -> both removed
#   4 VALIDATE  in a throw-away server container on the staged copy: `alembic upgrade head`
#               (an older backup is migrated forward HERE, not in production) and
#               `scripts.consistency_check --deep` (every lap's file present with its SHA-256,
#               no dangling rows, the audit chain intact) and, V0.9.9.1, the newest audit row
#               exactly the manifest's `audit_head` (a missing or rewritten last row otherwise
#               leaves an intact-looking chain). Backups without `audit_head` (before V0.9.9) are
#               not compared; `"audit_head": null` requires no audit rows.
#                                                                    fails -> both removed
#   --- only now the live system is touched ---
#   5 STOP      server and discord_bot are stopped (reading stops for a minute, nothing is lost)
#   6 SWAP_DB   in ONE transaction: live database -> <db>_pre_restore_<stamp>, staged -> live.
#               Atomic: it happens completely or not at all.         fails -> unchanged, restarted
#   7 SWAP_FILES the live files move into .pre_restore_<stamp>/ inside the volume (a rename, same
#               disk), the staged files are copied in.               fails -> files moved back,
#                                                                    database swapped back
#   8 START     server and discord_bot start.
#   9 VERIFY    the running server checks itself (health, consistency). fails -> everything back
#                                                                    (files, database), restarted
# The old database and files stay as the rollback point until `--cleanup <stamp>`; `--rollback
# <stamp>` returns to them later too. If a rollback itself fails, the script says exactly which
# names hold which state and exits with 3 — never silently.
#
# RESTORE_START_SERVICES (default "server discord_bot"): what to start at the end (a server move:
# "server" only, so two bots don't announce every record twice).

set -uo pipefail

ASSUME_YES=0
ALLOW_UNVERIFIED=0
MODE=restore
ARGS=()
for arg in "$@"; do
    case "$arg" in
        --yes) ASSUME_YES=1 ;;
        --allow-unverified) ALLOW_UNVERIFIED=1 ;;
        --rollback) MODE=rollback ;;
        --cleanup) MODE=cleanup ;;
        *) ARGS+=("$arg") ;;
    esac
done

abspath() { echo "$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"; }
usage() {
    echo "Usage: restore.sh [--yes] <backup-folder>" >&2
    echo "       restore.sh [--yes] --allow-unverified <db.sql.gz> <telemetry.tar.gz>" >&2
    echo "       restore.sh --rollback <stamp> | --cleanup <stamp>" >&2
    exit 2
}

MANIFEST=""
if [ "$MODE" = restore ]; then
    if [ "${#ARGS[@]}" -eq 1 ] && [ -d "${ARGS[0]}" ]; then
        BACKUP="$(cd "${ARGS[0]}" && pwd)"
        DB_FILE="$BACKUP/db.sql.gz"; TELEMETRY_FILE="$BACKUP/telemetry.tar.gz"; MANIFEST="$BACKUP/manifest.json"
    elif [ "${#ARGS[@]}" -eq 2 ]; then
        [ -e "${ARGS[0]}" ] && [ -e "${ARGS[1]}" ] || { echo "[restore] ERROR: '${ARGS[0]}' or '${ARGS[1]}' does not exist." >&2; exit 1; }
        DB_FILE="$(abspath "${ARGS[0]}")"; TELEMETRY_FILE="$(abspath "${ARGS[1]}")"
        [ -e "$(dirname "$DB_FILE")/manifest.json" ] && MANIFEST="$(dirname "$DB_FILE")/manifest.json"
    else
        usage
    fi
else
    [ "${#ARGS[@]}" -eq 1 ] || usage
    STAMP="${ARGS[0]}"
    [[ "$STAMP" =~ ^[0-9]{8}_[0-9]{6}$ ]] || { echo "[restore] ERROR: '$STAMP' is not a restore stamp (YYYYmmdd_HHMMSS)." >&2; exit 2; }
fi

cd "$(dirname "${BASH_SOURCE[0]}")/.."

PG_USER="${POSTGRES_USER:-garage16}"
PG_DB="${POSTGRES_DB:-garage16}"
PROJECT_NAME="${COMPOSE_PROJECT_NAME:-garage16}"
LIVE_VOLUME="${PROJECT_NAME}_telemetry_data"
START_SERVICES="${RESTORE_START_SERVICES:-server discord_bot}"
[ "$MODE" = restore ] && STAMP="$(date +%Y%m%d_%H%M%S)"
STAGE_DB="${PG_DB}_restore_${STAMP}"
OLD_DB="${PG_DB}_pre_restore_${STAMP}"
STAGE_VOLUME="${PROJECT_NAME}_telemetry_restore_${STAMP}"
OLD_DIR=".pre_restore_${STAMP}"

log() { echo "[restore] $*"; }
err() { echo "[restore] ERROR: $*" >&2; }
psql_admin() { docker compose exec -T db psql -U "$PG_USER" -d postgres -v ON_ERROR_STOP=1 -q "$@"; }
db_exists() { [ "$(psql_admin -Atc "select 1 from pg_database where datname = '$1'")" = "1" ]; }
terminate() { psql_admin -c "select pg_terminate_backend(pid) from pg_stat_activity where datname in ($1) and pid <> pg_backend_pid();" >/dev/null 2>&1 || true; }
in_volumes() {  # run a shell snippet with the live volume at /live and (optionally) the staged one at /stage
    local extra=()
    if docker volume inspect "$STAGE_VOLUME" >/dev/null 2>&1; then extra=(-v "${STAGE_VOLUME}:/stage"); fi
    docker run --rm -v "${LIVE_VOLUME}:/live" "${extra[@]}" alpine sh -c "$1"
}
start_writers() {
    # shellcheck disable=SC2086  # a list of service names
    docker compose up -d ${START_SERVICES}
}
manual() {  # the last resort: say exactly where everything is
    err "$1"
    err "MANUAL ATTENTION NEEDED. State of restore ${STAMP}:"
    err "  databases: live='${PG_DB}', before the restore='${OLD_DB}', staged backup='${STAGE_DB}' (whichever exist)"
    err "  files: live volume '${LIVE_VOLUME}' (the files from before the restore are in its folder ${OLD_DIR}/ if that exists), staged volume '${STAGE_VOLUME}'"
    err "  docs/ROLLBACK.md, section 'Restore manuell zurückdrehen', walks through it."
    exit 3
}

drop_stage() {
    terminate "'$STAGE_DB'"
    psql_admin -c "DROP DATABASE IF EXISTS \"$STAGE_DB\";" >/dev/null 2>&1 || err "could not drop the staged database $STAGE_DB — remove it by hand"
    docker volume rm "$STAGE_VOLUME" >/dev/null 2>&1 || true
}

# the database back: live (the restored one, if the swap happened) -> staged name, old -> live
db_back() {
    if db_exists "$OLD_DB"; then
        terminate "'$PG_DB', '$OLD_DB'"
        psql_admin -1 -c "ALTER DATABASE \"$PG_DB\" RENAME TO \"$STAGE_DB\";" -c "ALTER DATABASE \"$OLD_DB\" RENAME TO \"$PG_DB\";" \
            || return 1
    fi
    return 0
}

# the files back: everything except the rollback folder goes, the rollback folder's content returns
files_back() {
    in_volumes "set -e
        [ -d /live/${OLD_DIR} ] || exit 0
        find /live -mindepth 1 -maxdepth 1 ! -name '${OLD_DIR}' -exec rm -rf {} +
        find /live/${OLD_DIR} -mindepth 1 -maxdepth 1 -exec mv {} /live/ \\;
        rmdir /live/${OLD_DIR}"
}

# ------------------------------------------------------------------------------- --cleanup / --rollback
if [ "$MODE" = cleanup ]; then
    log "Deleting the rollback point of restore ${STAMP} (database ${OLD_DB}, folder ${OLD_DIR}/)..."
    terminate "'$OLD_DB'"
    psql_admin -c "DROP DATABASE IF EXISTS \"$OLD_DB\";" || { err "could not drop $OLD_DB"; exit 1; }
    in_volumes "rm -rf /live/${OLD_DIR}" || { err "could not remove ${OLD_DIR}/"; exit 1; }
    docker volume rm "$STAGE_VOLUME" >/dev/null 2>&1 || true
    log "Done."
    exit 0
fi
if [ "$MODE" = rollback ]; then
    db_exists "$OLD_DB" || { err "there is no rollback point for ${STAMP} (database ${OLD_DB} does not exist)"; exit 1; }
    log "Going back to the state before restore ${STAMP}..."
    docker compose stop server discord_bot || manual "could not stop the server"
    files_back || manual "could not move the files back"
    db_back || manual "could not rename the databases back"
    start_writers || manual "could not start the server"
    log "Back to the state before restore ${STAMP}. The restored backup is now database ${STAGE_DB} (drop it when no longer needed)."
    exit 0
fi

# ------------------------------------------------------------------------------- 1 CHECK
log "1/9 Checking the backup..."
for f in "$DB_FILE" "$TELEMETRY_FILE"; do
    [ -s "$f" ] || { err "'$f' does not exist or is empty."; exit 1; }
    gzip -t "$f" 2>/dev/null || { err "'$f' is not a valid gzip file."; exit 1; }
done
if [ -n "$MANIFEST" ] && [ -s "$MANIFEST" ]; then
    field() { sed -n "s/.*\"$1\": *{[^}]*\"$2\": *\"\{0,1\}\([^\",}]*\)\"\{0,1\}.*/\1/p" "$MANIFEST" | head -n1; }
    for part in database telemetry; do
        file=$([ "$part" = database ] && echo "$DB_FILE" || echo "$TELEMETRY_FILE")
        want_sha=$(field "$part" sha256); want_bytes=$(field "$part" bytes)
        have_sha=$(sha256sum "$file" | cut -d' ' -f1); have_bytes=$(wc -c < "$file" | tr -d ' ')
        if [ "$want_sha" != "$have_sha" ] || [ "$want_bytes" != "$have_bytes" ]; then
            err "$(basename "$file") does not match the manifest (sha256/size) — damaged or from another backup."
            exit 1
        fi
    done
    REVISION=$(sed -n 's/.*"schema_revision": *"\([^"]*\)".*/\1/p' "$MANIFEST" | head -n1)
    grep -q '"consistent": *true' "$MANIFEST" || { err "the manifest does not mark this backup as consistent."; exit 1; }
    # V0.9.9.1: the chain head of the audit log (compared with the staged database in step 4).
    # No key: a backup from before V0.9.9 -> "absent" (not compared). null -> "none". Anything the
    # key holds that is not exactly null or {"id": N, "mac": "..."} on one line is refused — an
    # unreadable anchor never counts as "no anchor".
    if grep -q '"audit_head"' "$MANIFEST"; then
        if grep -Eq '"audit_head": *null' "$MANIFEST"; then
            AUDIT_HEAD_SPEC="none"
        else
            head_value=$(sed -n 's/.*"audit_head": *\({[^}]*}\).*/\1/p' "$MANIFEST" | head -n1)
            head_id=$(printf '%s' "$head_value" | sed -n 's/.*"id": *\([0-9][0-9]*\).*/\1/p')
            head_mac=$(printf '%s' "$head_value" | sed -n 's/.*"mac": *"\([^"]*\)".*/\1/p')
            # strict shape (it is passed on to the validation command): v<n>:<key id>:<hex>
            if [ -z "$head_id" ] || ! printf '%s' "$head_mac" | grep -Eq '^v[0-9]+:[0-9a-f]+:[0-9a-f]+$'; then
                err "the manifest's audit_head cannot be read — refused."
                exit 1
            fi
            AUDIT_HEAD_SPEC="${head_id}:${head_mac}"
        fi
    else
        AUDIT_HEAD_SPEC="absent"
    fi
    log "    manifest OK (schema ${REVISION}, $(sed -n 's/.*"lap_count": *\([0-9]*\).*/\1/p' "$MANIFEST") laps)"
    case "$AUDIT_HEAD_SPEC" in
        absent) log "    no audit_head (backup from before V0.9.9): the audit chain is checked, its head not compared" ;;
        none)   log "    audit_head: none (the backup had no audit rows)" ;;
        *)      log "    audit_head: row ${AUDIT_HEAD_SPEC%%:*} (compared with the staged database in step 4)" ;;
    esac
elif [ "$ALLOW_UNVERIFIED" -eq 1 ]; then
    log "    WARNING: no manifest — a backup from before V0.9.8; database and files may be from different moments."
    REVISION=""
    AUDIT_HEAD_SPEC="absent"
else
    err "no manifest.json next to the backup. For a backup made before V0.9.8 pass --allow-unverified."
    exit 1
fi
gzip -dc "$DB_FILE" | tail -n 5 | grep -q "PostgreSQL database dump complete" || { err "the database dump is incomplete (no completion line)."; exit 1; }
if tar tzf "$TELEMETRY_FILE" | grep -qE '^/|(^|/)\.\.(/|$)'; then err "the archive contains absolute or ../ paths — refused."; exit 1; fi
if [ -n "$REVISION" ] && ! ls alembic/versions/"${REVISION}"_*.py >/dev/null 2>&1; then
    err "the backup has schema revision ${REVISION}, which this program does not know (a backup of a NEWER version?)."
    exit 1
fi
docker volume inspect "$LIVE_VOLUME" >/dev/null 2>&1 || { err "Docker volume '$LIVE_VOLUME' does not exist (has the stack ever been started?)."; exit 1; }

log "Backup: $DB_FILE"
log "        $TELEMETRY_FILE"
log "This REPLACES the live database '$PG_DB' and ALL telemetry files (the current state is kept as rollback point ${STAMP})."
if [ "$ASSUME_YES" -ne 1 ]; then
    read -r -p "[restore] Type RESTORE to continue: " answer
    if [ "$answer" != "RESTORE" ]; then log "Aborted — nothing was changed."; exit 1; fi
fi

docker compose up -d db >/dev/null 2>&1 || true
for _ in $(seq 1 60); do
    docker compose exec -T db pg_isready -U "$PG_USER" -d postgres >/dev/null 2>&1 && break
    sleep 1
done

# ------------------------------------------------------------------------------- 2-4 staged, offline
log "2/9 Loading the dump into a new database ${STAGE_DB}..."
if ! psql_admin -c "CREATE DATABASE \"$STAGE_DB\" OWNER \"$PG_USER\";" \
        || ! gzip -dc "$DB_FILE" | docker compose exec -T db psql -U "$PG_USER" -d "$STAGE_DB" -q -v ON_ERROR_STOP=1 >/dev/null; then
    err "loading the dump failed — the live system was not touched."
    drop_stage; exit 1
fi

log "3/9 Unpacking the files into a new volume ${STAGE_VOLUME}..."
if ! docker volume create "$STAGE_VOLUME" >/dev/null \
        || ! docker run --rm -v "${STAGE_VOLUME}:/stage" -v "$(dirname "$TELEMETRY_FILE"):/backup:ro" alpine \
            tar xzf "/backup/$(basename "$TELEMETRY_FILE")" -C /stage; then
    err "unpacking the files failed — the live system was not touched."
    drop_stage; exit 1
fi

log "4/9 Validating the staged copy (migrations, every file and its SHA-256, references)..."
# the staged volume at /restore (the rows say /data/telemetry/... — --stored-prefix maps them)
if ! docker compose run --rm --no-deps -T -v "${STAGE_VOLUME}:/restore" --entrypoint sh server -c \
        'export LMU_GARAGE_DB_URL="postgresql+psycopg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@${POSTGRES_HOST}:${POSTGRES_PORT}/'"$STAGE_DB"'" && alembic upgrade head && python -m scripts.consistency_check --deep --storage-dir /restore --stored-prefix "${LMU_GARAGE_STORAGE_DIR:-/data/telemetry}" --audit-head "'"$AUDIT_HEAD_SPEC"'"'; then
    err "the staged copy did not pass validation (see above) — the live system was not touched."
    drop_stage; exit 1
fi

# ------------------------------------------------------------------------------- 5-9 the switch
log "5/9 Stopping server and discord_bot..."
if ! docker compose stop server discord_bot; then
    start_writers || true
    drop_stage; err "could not stop the writers — nothing was switched."; exit 1
fi

log "6/9 Switching the databases (one transaction)..."
terminate "'$PG_DB', '$STAGE_DB'"
if ! psql_admin -1 -c "ALTER DATABASE \"$PG_DB\" RENAME TO \"$OLD_DB\";" -c "ALTER DATABASE \"$STAGE_DB\" RENAME TO \"$PG_DB\";"; then
    err "switching the databases failed — nothing changed; starting the server again."
    start_writers || manual "the database switch failed AND the server did not start again"
    drop_stage; exit 1
fi

log "7/9 Switching the files (the current ones are kept in ${OLD_DIR}/)..."
if ! in_volumes "set -e
        mkdir /live/${OLD_DIR}
        find /live -mindepth 1 -maxdepth 1 ! -name '${OLD_DIR}' -exec mv {} /live/${OLD_DIR}/ \\;
        cp -a /stage/. /live/"; then
    err "switching the files failed — going back."
    files_back || manual "the file switch failed and the old files could not be moved back"
    db_back || manual "the file switch failed and the database could not be switched back"
    start_writers || manual "rolled back, but the server did not start"
    drop_stage; exit 1
fi

log "8/9 Starting ${START_SERVICES}..."
started=1
start_writers || started=0

log "9/9 Verifying the running server..."
verified=0
if [ "$started" -eq 1 ]; then
    for _ in $(seq 1 60); do
        if docker compose exec -T server python -c "import urllib.request as u; u.urlopen('http://localhost:8000/health', timeout=3)" >/dev/null 2>&1; then
            verified=1; break
        fi
        sleep 2
    done
    if [ "$verified" -eq 1 ] && ! docker compose exec -T server sh -c \
            'LMU_GARAGE_DB_URL="postgresql+psycopg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@${POSTGRES_HOST}:${POSTGRES_PORT}/${POSTGRES_DB}" python -m scripts.consistency_check'; then
        verified=0
    fi
fi
if [ "$verified" -ne 1 ]; then
    err "the restored system did not pass its check — going back to the state before the restore."
    docker compose stop server discord_bot || true
    files_back || manual "verification failed and the old files could not be moved back"
    db_back || manual "verification failed and the database could not be switched back"
    start_writers || manual "rolled back, but the server did not start"
    drop_stage
    log "Back to the state before the restore. Nothing was lost."
    exit 1
fi

docker volume rm "$STAGE_VOLUME" >/dev/null 2>&1 || true
log "Done — the backup is live and verified."
log "Rollback point kept: database ${OLD_DB} + folder ${OLD_DIR}/ in the telemetry volume."
log "  back to before:  ./scripts/restore.sh --rollback ${STAMP}"
log "  free the space:  ./scripts/restore.sh --cleanup ${STAMP}   (once you are sure)"
