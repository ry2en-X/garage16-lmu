#!/bin/sh
# docker/entrypoint.sh (server) — resolves secrets, assembles the DB URL,
# runs migrations as an explicit deploy step, THEN starts the app.
#
# Secrets: works with EITHER plain .env values OR Docker secrets, with no
# code change either way — server/config.py only ever sees a normal
# environment variable. For each secret NAME below, if NAME_FILE is set
# (pointing at a file — a Docker secret is mounted as one under
# /run/secrets/<name> by default), its content is read into NAME. Compose
# already resolves NAME directly from .env if NAME_FILE isn't set, so
# nothing else has to change to support either path — see
# docker-compose.yml's `secrets:` blocks for the Docker-secrets side of
# this, and .env.example for the plain-.env side.
#
# DB URL: built HERE from POSTGRES_* pieces rather than in
# docker-compose.yml's `environment:` block. That matters for secrets
# support specifically: docker-compose.yml's ${POSTGRES_PASSWORD}
# interpolation happens on the HOST before any container starts, so it
# can never see a secret file that only exists inside a container at
# runtime. Building the URL in this shell script, after resolving
# POSTGRES_PASSWORD_FILE, is what makes the DB password itself
# secrets-compatible, not just the standalone tokens.
#
# Safe to run on every container start, including restarts of an
# already-migrated DB: alembic upgrade head is a no-op if already at head.
#
# Runs as root initially (see Dockerfile.server — no USER directive there
# anymore) so it can fix ownership of the telemetry storage volume before
# anything else runs; drops to the non-root `garage` user via `gosu`
# before migrations and the server process — see the ownership-fix
# section below for why this can't be done at image build time instead.
set -e

resolve_secret() {
    # $1 = variable name. If <name>_FILE is set and points at a readable
    # file, exports <name> from its content. Otherwise leaves <name> as
    # whatever it already was (normal env var / .env value / unset).
    var_name="$1"
    file_var_name="${var_name}_FILE"
    eval "file_path=\${${file_var_name}:-}"
    if [ -n "$file_path" ]; then
        if [ ! -r "$file_path" ]; then
            echo "[entrypoint] ERROR: ${file_var_name}=${file_path} but that file isn't readable." >&2
            exit 1
        fi
        eval "${var_name}=\$(cat \"\$file_path\")"
        export "${var_name?}"
    fi
}

for secret_name in POSTGRES_PASSWORD LMU_GARAGE_SECRET_KEY LMU_GARAGE_REGISTRATION_SECRET LMU_GARAGE_ADMIN_TOKEN; do
    resolve_secret "$secret_name"
done

# Build LMU_GARAGE_DB_URL from pieces unless it was already set directly
# (e.g. pointing at an external/managed Postgres instance instead of the
# db container — still supported, this is just the default path).
if [ -z "${LMU_GARAGE_DB_URL:-}" ]; then
    : "${POSTGRES_HOST:=db}"
    : "${POSTGRES_PORT:=5432}"
    : "${POSTGRES_DB:=garage16}"
    : "${POSTGRES_USER:=garage16}"
    if [ -z "${POSTGRES_PASSWORD:-}" ]; then
        echo "[entrypoint] ERROR: POSTGRES_PASSWORD (or POSTGRES_PASSWORD_FILE) is not set." >&2
        exit 1
    fi
    export LMU_GARAGE_DB_URL="postgresql+psycopg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@${POSTGRES_HOST}:${POSTGRES_PORT}/${POSTGRES_DB}"
fi

# BUGFIX (found via real deployment testing, 2026-09-26): fix ownership
# of the telemetry storage directory before dropping to the non-root
# `garage` user. When LMU_GARAGE_STORAGE_DIR points at a Docker volume
# (the normal docker-compose case — see that file's `telemetry_data`
# mount), Docker creates the mount point itself the first time it's
# used, owned by root — a Dockerfile-time `chown` can't reach it, since
# the volume doesn't exist yet at build time; it's only attached when
# the container actually starts. Without this, the first real upload
# fails with PermissionError, since `garage` can traverse but not write
# to a root-owned directory. This container therefore starts as root
# (see Dockerfile.server — no more `USER garage` there) and drops
# privileges itself, right here, via gosu — after this point everything
# (migrations, the server process) runs as `garage`, not root.
: "${LMU_GARAGE_STORAGE_DIR:=/app/server_telemetry_storage}"
mkdir -p "$LMU_GARAGE_STORAGE_DIR"
chown -R garage:garage "$LMU_GARAGE_STORAGE_DIR"

echo "[entrypoint] Running migrations (alembic upgrade head)..."
gosu garage alembic upgrade head

echo "[entrypoint] Starting server..."
# LMU_GARAGE_WORKERS: number of uvicorn worker processes. Each worker has
# its OWN in-memory rate-limit state (server/rate_limit.py) and its own DB
# connection pool — see that module's docstring for the multi-worker
# caveat. Defaults to 1 (safest, matches this project's current scale);
# raise it once traffic actually needs it, and replace the rate limiter
# with a Redis-backed one first if you do.
exec gosu garage uvicorn server.main:app --host 0.0.0.0 --port 8000 --workers "${LMU_GARAGE_WORKERS:-1}"
