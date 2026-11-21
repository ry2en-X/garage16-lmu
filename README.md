# Garage16

Telemetry, lap analysis and leaderboards for **Le Mans Ultimate**. A small Windows client reads
LMU's shared memory while you drive and uploads every lap; a self-hosted server checks the laps
(track limits, data quality, integrity), keeps personal bests, team bests and the Garage16 Best per
track and car, and a web app shows leaderboards, teams, the **LapTime Analyzer** (lap against lap,
every channel, racing lines on the track) and the **Stint Analyzer** (pace, tyres, fuel, strategy).
A Discord bot announces records.

**Current version: server 1.0.2 / desktop client 1.0.2.** What changed: [`CHANGELOG.md`](CHANGELOG.md).

Deutsch: Anleitungen für Fahrer und Betreiber stehen auf Deutsch in [`docs/`](docs/README.md) —
[Fahrer-Leitfaden](docs/FRIENDS_GUIDE.md) · [Admin-Leitfaden](docs/ADMIN_GUIDE.md).

## Use it

- **Drivers:** create an account on the Garage16 website, download the client from the website's
  Download page (or the newest [release](https://github.com/ry2en-X/garage16-lmu/releases/latest)),
  connect it with your email and password, drive. Step by step:
  [`docs/FRIENDS_GUIDE.md`](docs/FRIENDS_GUIDE.md) and the website's *Setup guide* page.
- **Operators:** the server runs with Docker Compose on a NAS (Cloudflare Tunnel, no open port)
  or a VPS (Caddy with automatic HTTPS). Step by step on a Synology NAS:
  [`docs/ADMIN_GUIDE.md`](docs/ADMIN_GUIDE.md); technical reference (variants, backups, every
  setting): [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md).

## Components

| Dir | What it is | Run with |
|---|---|---|
| `server/` | FastAPI backend in areas (`core/`, `laps/`, `analysis/`, `accounts/`, `catalog/`, `ops/`), the API in `api/v1/` | `uvicorn server.main:app --reload` (dev) or `docker compose up` (prod) |
| `client/` | Windows recorder/uploader (Tkinter, optional analysis window) | `python -m client.main` |
| `web/` | Static web app (no build step; `js/core/` shared, `js/pages/` pages, `functions/` the Cloudflare Pages function) | Cloudflare Pages or Caddy; dev: `python -m scripts.dev.serve_web` |
| `discord_bot/` | Record announcements + slash commands | `python -m discord_bot.bot` |
| `alembic/` | Database migrations (run automatically at container start) | see `alembic/README.md` |
| `docker/` | Dockerfiles, entrypoints, Caddy configuration | see `docs/DEPLOYMENT.md` |
| `packaging/` | Windows installer (PyInstaller + Inno Setup) | see `docs/CLIENT_BUILD.md` |
| `scripts/` | `ops/` backup, restore, checks · `dev/` benchmarks, probes · `release/` gate, website build | see each script's header |
| `tests/` | ~1750 tests by topic (pytest + jsdom), run by the release gate | quick: `python -m pytest -m "not slow"`; all: `python -m scripts.release.release_check` |

## Develop

```
bash scripts/dev/setup_test_env.sh          # .venv with the exact versions of constraints.txt + jsdom
. .venv/bin/activate
uvicorn server.main:app --reload        # creates lmu_garage_server.db on first run (development)
python -m scripts.dev.serve_web         # the website on http://localhost:5173 (API proxied to :8000)
python -m client.main                   # connect window: http://localhost:8000, email + password
python -m pytest -m "not slow"          # the quick run (about 1.5 minutes)
python -m scripts.release.release_check         # the whole suite, strict: a skipped test counts as a failure
```

In development (`GARAGE16_ENV=development`) emails are only logged; the confirmation link is in
the server log. Use `GARAGE16_REQUIRE_EMAIL_VERIFICATION=0` to connect the client without it.
Details on tests, PostgreSQL runs and the gate: [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md#running-tests)
and [`docs/QUALITY.md`](docs/QUALITY.md).

## Operations in one minute

- `docker compose --profile tunnel up -d --build` — start/update (migrations run by themselves).
- `/health/ready` must say `ready`; `docker compose logs -f server` for everything else.
- Backups: `scripts/ops/backup.sh` (database + the new telemetry files from one moment, with a manifest);
  restore: `scripts/ops/restore.sh` (staged, validated, switched — or rolled back). Test a restore,
  not only the backup. Details: [`docs/ROLLBACK.md`](docs/ROLLBACK.md), [`docs/RUNBOOKS.md`](docs/RUNBOOKS.md).
- Moving the server: the new instance needs the SAME `GARAGE16_SECRET_KEY` (otherwise the
  stored client secrets cannot be decrypted); clients follow `GARAGE16_MIGRATED_TO` by
  themselves ([`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md#moving-the-server-later-nas--vpsdomain)).
- Orphaned telemetry files (dry run first, the database URL must be passed into the container):

  ```
  docker compose exec -T server sh -c 'GARAGE16_DB_URL="postgresql+psycopg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@${POSTGRES_HOST}:${POSTGRES_PORT}/${POSTGRES_DB}" python -m scripts.ops.telemetry_cleanup'
  ```

## Documentation

| Document | For | Language |
|---|---|---|
| [`docs/FRIENDS_GUIDE.md`](docs/FRIENDS_GUIDE.md) | drivers: account, client, LMU, analysis, common messages | German |
| [`docs/ADMIN_GUIDE.md`](docs/ADMIN_GUIDE.md) | the operator: NAS install, `.env`, updates, Discord, SMTP, domain | German |
| [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) | technical reference: variants, backups, settings, credentials, tests | English |
| [`docs/RUNBOOKS.md`](docs/RUNBOOKS.md) · [`docs/ROLLBACK.md`](docs/ROLLBACK.md) | what to do when something breaks | German |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) · [`docs/QUALITY.md`](docs/QUALITY.md) · [`docs/DATA_PROTECTION.md`](docs/DATA_PROTECTION.md) | how it is built, how it is tested, what is stored | English / German |
| [`docs/README.md`](docs/README.md) | index of everything else (client build, LMU protocol, archive) | — |

## Security

Found a vulnerability? Please do not open a public issue — write to the operator address on the
Legal page of the instance (or, for the code, contact the repository owner on GitHub). The
security model (credentials, HMAC uploads, integrity MACs, audit chain, rate limits, review queue)
is described in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Licence

No open-source licence is granted. The code is published so that drivers and operators can see what
runs on their PC and server. **All rights reserved** by the author — copying, modifying or
redistributing it needs permission. Third-party components and their licences (fonts under the SIL
Open Font License, parts derived from ApexDash under MIT, …): [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).

Garage16 is not an official product of Studio 397 or Le Mans Ultimate.
