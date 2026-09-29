#!/bin/sh
# docker/entrypoint-discord-bot.sh — same secrets resolution and DB URL
# assembly as docker/entrypoint.sh (server), so the bot supports Docker
# secrets and .env equally, and reaches the same database the server
# writes to.
#
# See docker/entrypoint.sh for the detailed reasoning — this is
# deliberately the same pattern, not a different one, so the two
# containers behave identically with respect to secrets regardless of
# which config style (plain .env or Docker secrets) is used.
set -e

resolve_secret() {
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

for secret_name in POSTGRES_PASSWORD DISCORD_BOT_TOKEN; do
    resolve_secret "$secret_name"
done

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

if [ -z "${DISCORD_BOT_TOKEN:-}" ]; then
    echo "[entrypoint] ERROR: DISCORD_BOT_TOKEN (or DISCORD_BOT_TOKEN_FILE) is not set." >&2
    exit 1
fi

echo "[entrypoint] Starting Discord bot..."
exec python -m discord_bot.bot
