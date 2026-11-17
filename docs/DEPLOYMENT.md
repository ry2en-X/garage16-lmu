# Garage16 — deployment & operations reference

The technical reference for running a Garage16 server: access variants, frontend placement,
backups, storage cleanup, moving the server, monitoring, migrations, secrets, every server setting,
credentials, moderation and tests. For the step-by-step on the operator's NAS (in German) see
[`docs/ADMIN_GUIDE.md`](ADMIN_GUIDE.md); for drivers [`docs/FRIENDS_GUIDE.md`](FRIENDS_GUIDE.md).

## Production deployment

Platform-neutral by design: the same `docker-compose.yml` runs on a
Synology NAS or a VPS unchanged — only `.env` values and which `docker
compose` command line you use differ. Two independent choices:

1. **How the outside world reaches you** — Cloudflare Tunnel (no open
   router port, good for a NAS behind home internet) or a direct reverse
   proxy with Caddy's automatic HTTPS (needs a public IP, typical for a
   VPS). See "Access variant" below.
2. **Where the frontend is served from** — bundled with the API behind
   the same Caddy instance (default), or deployed separately to
   Cloudflare Pages (stays untouched across a server move). See
   "Frontend" below.

```
cp .env.example .env      # fill in real secrets — see comments in the file
```

### Access variant A — Cloudflare Tunnel (Synology NAS, no open port)

No inbound port needs to be opened on your router at all — `cloudflared`
makes an outbound-only connection to Cloudflare's edge, which then
carries traffic to this stack.

**One-time Cloudflare setup** (before first `docker compose up`):
1. [Cloudflare Zero Trust dashboard](https://one.dash.cloudflare.com/) →
   Networks → Tunnels → Create a tunnel (choose "Cloudflared").
2. Name it, copy the **token** it gives you into `.env` as
   `CLOUDFLARE_TUNNEL_TOKEN`.
3. Under that tunnel's **Public Hostname** tab: add a hostname (e.g.
   `garage16.yourdomain.com`), pointing at service type `HTTP`, URL
   `reverse_proxy:80` (that's the Docker service name + port from
   `docker-compose.yml` — Cloudflare resolves it because `cloudflared`
   runs on the same Docker network).
4. Leave `CADDY_SITE_ADDRESS` unset in `.env` (defaults to `:80` —
   internal only, no certificate needed; Cloudflare's edge terminates
   the public HTTPS connection).

**Run:**
```
docker compose --profile tunnel up -d
docker compose logs -f server
```

**Quick Tunnel (no account, no domain — for testing).** A container running
`cloudflared tunnel --url http://reverse_proxy:80` gives a random
`https://….trycloudflare.com` address without any Cloudflare account. Cloudflare offers this for
testing and development only, without uptime guarantee, and **the address changes every time that
container is recreated** — every desktop client that has the old address baked in or saved then
loses the server (only a manual "Reconnect account…" with the new address helps; a move announced
via `LMU_GARAGE_MIGRATED_TO` needs the old address to still answer). Do not recreate it casually,
and switch to a named tunnel with your own domain before inviting people broadly
(`docs/ADMIN_GUIDE.md`, chapter 10; `docs/RUNBOOKS.md` has the recovery steps).

**On Synology specifically:** Container Manager (DSM 7.2+) or
Portainer both work directly with this `docker-compose.yml` — in
Container Manager, create a new Project, point it at the folder
containing this file, and it detects the compose file automatically
(Container Manager calls it a "project" and handles `.env` the same
way). One Synology-specific note: Docker's named volumes (`db_data`,
`telemetry_data`, etc.) land under
`/volume1/@docker/volumes/<project>_<volume>/_data` — useful to know if
you ever need to browse them directly in File Station, but you never
need to reference that path in any config file (that's the whole point
of using named volumes instead of NAS-specific bind mounts here).

### Access variant B — Direct reverse proxy (VPS, public IP)

Caddy terminates a real Let's Encrypt certificate directly.

**Prerequisites:** a domain's DNS A/AAAA record pointing at the VPS's
public IP, and ports 80+443 reachable from the internet (most VPS
providers allow this by default; check your provider's firewall/security
group if not).

**Set in `.env`:**
```
CADDY_SITE_ADDRESS=garage16.example.com
```
(leave `CLOUDFLARE_TUNNEL_TOKEN` blank — the `tunnel` profile isn't
started in this variant, so it's simply unused either way)

**Run:**
```
docker compose -f docker-compose.yml -f docker-compose.vps.yml up -d
docker compose logs -f server
```

`docker-compose.vps.yml` only adds one thing on top of the base file:
publishing ports 80/443 on `reverse_proxy` (see that file's comments).
Everything else — images, volumes, healthchecks, migrations-on-start —
is identical to variant A.

### Umstieg NAS → VPS (switching variants later)

No code changes, no rebuild of the application images. On the new host:
1. Copy `.env`, `docker-compose.yml`, `docker-compose.vps.yml`, and
   `docker/` over.
2. Restore the database and telemetry volumes from a backup (see
   "Backups" below) — or start fresh if this is a clean move.
3. Update `.env`: set `CADDY_SITE_ADDRESS` to your domain, clear
   `CLOUDFLARE_TUNNEL_TOKEN` (or keep both set and just run whichever
   command line you want — unused variables don't hurt).
4. `docker compose -f docker-compose.yml -f docker-compose.vps.yml up -d`
   instead of the `--profile tunnel` command.
5. Point the domain's DNS at the new host and update
   `LMU_GARAGE_CORS_ORIGINS` / `LMU_GARAGE_FRONTEND_URL` if the domain
   changed. Desktop clients do NOT need reconfiguring by hand any more:
   set `LMU_GARAGE_MIGRATED_TO` on the old server and they follow
   automatically — see "Moving the server later" below. **The new
   server must use the SAME `LMU_GARAGE_SECRET_KEY` as the old one:** it
   encrypts every driver's stored client secret, so with a different key the
   restored secrets can't be decrypted and every desktop upload fails its
   signature check.

### Frontend as a separate deployment (Cloudflare Pages)

By default, Caddy serves `web/` from the same domain as the API (see
`docker/Caddyfile`'s catch-all `handle` block). To deploy the frontend
independently instead (so it survives a server move/migration untouched,
and gets Cloudflare's CDN for free):

1. In the Cloudflare dashboard: Workers & Pages → Create → Pages →
   connect your repo (or use direct upload) with `web/` as the build
   output directory (no build step — it's already static files).
2. Edit `web/config.js` before deploying: set
   `window.LMU_GARAGE_API_BASE_URL = "https://your-api-domain.example";`
   (see that file's comments — this is the one line that changes between
   "frontend bundled with the API" and "frontend on Pages").
3. Add the Pages domain (e.g. `https://garage16.pages.dev`, or your
   custom domain on Pages) to `LMU_GARAGE_CORS_ORIGINS` in the API's
   `.env`, then restart the `server` container.
4. Optional: remove the catch-all `handle { root * /srv/web ... }` block
   from `docker/Caddyfile` and the `./web:/srv/web:ro` volume mount from
   `docker-compose.yml`'s `reverse_proxy` service — the API doesn't need
   to serve those files anymore. Not required (harmless to leave it
   serving an unused copy), just tidier.

### Backups

Two options — pick based on your setup, and re-read "Umstieg NAS → VPS"
above if you might migrate later (the containerized option travels with
you unchanged; the NAS-native one doesn't).

**Option 1 — NAS-native (Synology Task Scheduler):**
Control Panel → Task Scheduler → Create → Scheduled Task → User-defined
script. Run daily, script:
```sh
cd /volume1/docker/garage16   # wherever this project lives on your NAS
./scripts/backup.sh /volume2/backups/garage16
```
(`scripts/backup.sh` already handles `pg_dump` + a `tar` of the
telemetry volume together — see that script's header comment. Point the
destination argument at your second disk/volume.) Ties you to
Synology's scheduler; re-do this step manually on a VPS.

**Option 2 — Containerized (works identically on NAS and VPS):**
```
docker compose --profile backup up -d
```
Runs `docker/backup-loop.sh` on a loop (default: daily, 14-day
retention — both configurable via `BACKUP_INTERVAL_SECONDS` /
`BACKUP_RETENTION_DAYS` in `.env`), dumping the DB and archiving
telemetry storage together into `${BACKUP_HOST_PATH}` (set this to a
real path — a second disk's mount point, a cloud-sync folder — or leave
it as a Docker-managed volume). No host-level cron or Task Scheduler
needed at all; the same `docker compose --profile backup up -d`
continues working unchanged after a NAS → VPS move.

Since V0.9.8 both write one folder `garage16_<stamp>/` (dump, archive, `manifest.json` with
SHA-256, schema revision, lap count) from ONE moment: the write lock (`server/write_lock.py`)
holds new uploads off with 503 (the client retries) while running ones finish; the folder only
appears once both files are checked.

Either way: **periodically test a real restore**, not just that the
backup file exists. `./scripts/restore.sh <backup-folder>` stages the backup in a new database and
volume, validates it there (migrations, every file's SHA-256), and only then switches — ending in
the validated backup or the state before, never a mix (`--rollback <stamp>`, `--cleanup <stamp>`)
— see `docs/ROLLBACK.md`.

### Tracks, classes and cars — nothing to enter by hand (V0.8.9)

Nobody has to maintain track, class or car lists. `server/catalog.py` holds a
small seeded list (12 tracks with search keywords, the 6 LMU classes with the
spellings LMU uses) so every known track and class exists before its first
lap, and everything else is **learned from uploaded laps**: a track layout
appears the first time LMU sends it, an unknown track or class becomes its own
entry, and cars are tied to the class LMU reports for them. LMU's raw class
string is kept in `laps.car_class_raw` (the readable name, e.g. "Hypercar" for
LMU's "Hyper", is in `laps.car_class`).

The seeded lists are best-effort. To see what LMU really sends on YOUR server
(and where a name or grouping should be adjusted in `server/catalog.py`):
```sh
docker compose exec -T db psql -U garage16 -d garage16 -c "SELECT track_name, car_class_raw, car_class, car_model, count(*) FROM laps GROUP BY 1,2,3,4 ORDER BY 1,2,4;"
```

### Telemetry storage cleanup

`scripts/telemetry_cleanup.py` (V0.7.2) reports two kinds of drift
between the DB and the telemetry storage directory: a `Lap` row whose
file has gone missing (never auto-resolved — that's a data-loss event
worth a human looking at, not a silent fix), and a file on disk with no
matching `Lap` row (a genuine cleanup candidate, but only once it's
older than an hour — recent files might just be mid-upload, see the
script's own docstring for why that matters). Dry-run by default:
```sh
python -m scripts.telemetry_cleanup          # reports only, deletes nothing
python -m scripts.telemetry_cleanup --execute  # actually deletes confirmed orphans
```
**On a Docker deployment (NAS/VPS) use exactly this** — `docker compose exec`
does NOT inherit the database URL the container's entrypoint builds for the
server, so it has to be passed explicitly (otherwise the tool would look at
an empty SQLite default and every file would look orphaned; since V0.8.8 it
prints which database it uses and refuses `--execute` when that looks wrong):
```sh
docker compose exec -T server sh -c 'LMU_GARAGE_DB_URL="postgresql+psycopg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@${POSTGRES_HOST}:${POSTGRES_PORT}/${POSTGRES_DB}" python -m scripts.telemetry_cleanup'
# read the output first — only then, if the orphans are what you expect:
docker compose exec -T server sh -c 'LMU_GARAGE_DB_URL="postgresql+psycopg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@${POSTGRES_HOST}:${POSTGRES_PORT}/${POSTGRES_DB}" python -m scripts.telemetry_cleanup --execute'
```
Same caution as any destructive tool: read its dry-run output before ever
passing `--execute`.

### Moving the server later (NAS → VPS/domain)

Friends' clients keep the server address in exactly one place — their own
saved config (`client/config.py`) — and follow a move **automatically**;
nobody re-installs, re-registers or types a command. What you do:

1. Stand up the new instance (same Docker Compose, new host/domain,
   restore the DB — see Backup/Restore above) **with the same
   `LMU_GARAGE_SECRET_KEY` in its `.env`** (without it the restored client
   secrets are undecryptable). It must be reachable via **https://**
   (Cloudflare Tunnel or Caddy both give you that).
2. On the **old** instance set `LMU_GARAGE_MIGRATED_TO` to the new URL
   and restart it. It keeps working normally — this isn't a hard
   cutover — but `GET /health` now advertises the new address.
3. Every running client checks `/health` at startup and every ~10
   minutes. When it sees the move it verifies the new server answers,
   saves the new address, and switches — auth token, client secret,
   recorded laps, account and teams carry over untouched (they belong to
   the account, which lives in the database you restored). The friend
   just sees a line in the activity list.
4. When everyone has switched, decommission the old instance.

**Security model — why HTTPS on both ends is required.** A client only
follows a move announced over an `https://` connection to a server whose
certificate it verified, and only into another `https://` address; over
plain `http://` the announcement could be forged by anyone on the network,
so it is **not** followed automatically (the friend sees a notice to ask
you for the new address). The new address is probed with an unauthenticated
`GET /health` before anything is saved, and no credentials are sent to it
during that check. A malformed, unreachable or non-Garage16 target is
ignored and the working configuration is left untouched. Trust anchor:
the old server's TLS identity — if you can't trust what your own server
says, you have bigger problems. Developers/admins can still move a
plain-HTTP (LAN) client by hand with `python -m client.main --follow-migration`.

**A client that is too old for the new server** gets an `UPDATE_REQUIRED`
answer (HTTP 426), shows a plain-language banner, keeps every lap it
recorded meanwhile (they upload after the update), and offers an "Open
download page" button when `LMU_GARAGE_CLIENT_DOWNLOAD_URL` is set. See
`docs/CLIENT_BUILD.md` for building and publishing the installer.

### Monitoring

Each service has a container-level healthcheck (`docker compose ps`
shows `healthy`/`unhealthy` per service — see `docker-compose.yml`):
`db` (`pg_isready`), `server` (`GET /health`, which itself checks DB
connectivity — see `server/main.py`), `discord_bot` (a heartbeat file
touched every poll cycle, since a bot has no port to probe directly —
see `discord_bot/bot.py`'s `_touch_heartbeat()`), and `reverse_proxy`
(Caddy's own admin API).

For actually being notified of an outage rather than having to run
`docker compose ps` yourself, the simplest option is an external
uptime-ping service hitting your public `/health` endpoint and posting
to a Discord webhook on failure — e.g.
[UptimeRobot](https://uptimerobot.com)'s free tier (HTTP(S) monitor +
its built-in Discord-webhook integration), checked every 5 minutes, no
extra container needed. A self-hosted alternative
([Uptime Kuma](https://github.com/louislam/uptime-kuma)) is a fine
upgrade later if you want history/dashboards, but is arguably more
infrastructure than a friends-and-league-sized deployment needs right
now — start with the external free-tier option and revisit only if it's
not enough.

### Database migrations

Run automatically as an explicit deploy step, before the app starts —
see `docker/entrypoint.sh` / `docker/entrypoint-discord-bot.sh` (both run
`alembic upgrade head`, a no-op if already at head). This happens
identically on every `docker compose up`, on NAS or VPS. See
`alembic/README.md` for the manual/local-dev equivalent.

### Secrets: .env vs Docker secrets

Every credential works either way, chosen per-value, no code changes:
- **Plain `.env`** (default, simplest — fine for a closed
  friends/league deployment): set the value directly, as shown in
  `.env.example`.
- **Docker secrets** (file-based, avoids credentials sitting in a
  Compose-visible environment variable, and secrets management via a
  team-shared vault becomes possible via a wrapper on top of it later):
  uncomment the `secrets:` blocks in `docker-compose.yml` (bottom of the
  file, plus one per service that needs them), create the referenced
  files under `./secrets/` (gitignored — see `.gitignore`) with just the
  raw secret value inside, and leave the corresponding plain `.env`
  values blank. `docker/entrypoint.sh` / `docker/entrypoint-discord-bot.sh`
  resolve either form transparently — see those files' comments.

## Server environment variables

All optional except where noted "REQUIRED in production" — sensible dev
defaults apply otherwise. Full list with production notes: `.env.example`.

| Variable | Purpose | Dev default |
|---|---|---|
| `LMU_GARAGE_DB_URL` | Database connection string (Docker: assembled from `POSTGRES_*` pieces by the entrypoint script instead — see "Secrets" above) | `sqlite:///./lmu_garage_server.db` |
| `LMU_GARAGE_STORAGE_DIR` | Where uploaded telemetry files land | `./server_telemetry_storage` |
| `LMU_GARAGE_MAX_UPLOAD_BYTES` | Per-upload size cap | 50 MB |
| `LMU_GARAGE_SECRET_KEY` | Fernet key encrypting `client_secret` at rest — **REQUIRED in production** | insecure built-in dev key (loudly logged) |
| `LMU_GARAGE_REGISTRATION_SECRET` | If set (and registration is not open), sign-up needs this secret (`X-Registration-Secret`) — production needs it **or** `LMU_GARAGE_OPEN_REGISTRATION=1` | unset — registration open |
| `LMU_GARAGE_ADMIN_TOKEN` | Bearer token for `/admin/*` moderation endpoints — unset means those endpoints 503 | unset |
| `LMU_GARAGE_REQUIRE_HTTPS` | If `1`/`true`, rejects requests not forwarded as HTTPS by a reverse proxy | unset — off |
| `LMU_GARAGE_ENV` | Only the literal string `development` gets dev defaults; anything else (including unset) is treated as production and hard-fails without real secrets | `development` |
| `LMU_GARAGE_CORS_ORIGINS` | Comma-separated allowed origins for the web frontend — include the Cloudflare Pages domain here if the frontend is deployed separately | `http://localhost:5173` |
| `LMU_GARAGE_MIN_CLIENT_VERSION` | Uploads from an older `client_version` are rejected with 400 | `0.5.3` |
| `LMU_GARAGE_LOG_FORMAT` | `text` or `json` | `text` |
| `LMU_GARAGE_DB_POOL_SIZE` / `LMU_GARAGE_DB_MAX_OVERFLOW` | PostgreSQL connection pool sizing (ignored for SQLite) | 5 / 10 |
| `LMU_GARAGE_INTEGRITY_KEY` | V0.9.9: key of the server's MACs over stored laps and the audit log (`server/integrity.py`); comma-separated for rotation (first signs, all verify). Empty = derived from `LMU_GARAGE_SECRET_KEY`. | empty |
| `LMU_GARAGE_AUDIT_RETENTION_DAYS` | V0.9.9: days the audit log is kept (minimum 30). | 365 |
| `LMU_GARAGE_TRUSTED_PROXIES` | V0.9.8: whose `X-Forwarded-For` is believed (addresses / networks, or `private`); read from the right, so a header the visitor writes himself never changes the address the rate limits count (`server/client_ip.py`). docker-compose sets `private` (the app publishes no port). Empty = the socket address. | empty (compose: `private`) |
| `LMU_GARAGE_RATELIMIT_*_MAX` / `_WINDOW` | Per-bucket rate limits (register, login, upload, …; V1.0.1: `LOGINACCOUNT` = failed sign-ins per email address, `EMAILADDRESS` = emails sent to one address) — see `server/config.py` / `server/rate_limit.py` | see `server/config.py` |
| `LMU_GARAGE_OPEN_REGISTRATION` | V1.0: `1` = anybody may sign up on the website (terms + minimum age). A production server needs this **or** `LMU_GARAGE_REGISTRATION_SECRET`. | unset |
| `LMU_GARAGE_MIN_AGE` | V1.0: minimum age asked at sign-up | 16 |
| `LMU_GARAGE_OPERATOR_NAME` / `_ADDRESS` / `_EMAIL` | V1.0: operator shown on the Legal page (address lines separated by `|`) — kept in `.env`, never in the repository | unset |
| `LMU_GARAGE_GITHUB_REPO` | V1.0: the download buttons default to this repository's newest release (empty = off) | `ry2en-X/garage16-lmu` |
| `LMU_GARAGE_REQUIRE_EMAIL_VERIFICATION` | V1.0.1: the desktop client connects only after the email was confirmed (`0` = off) | `1` |
| `LMU_GARAGE_UNVERIFIED_ACCOUNT_DAYS` | V1.0.1: sign-ups never confirmed (no laps, no team) are deleted after this many days (`0` = never) | 7 |
| `LMU_GARAGE_NEW_DRIVER_REVIEW_LAPS` | V1.0.1: a driver with fewer official laps whose lap beats the Garage16 Best of others is held for an admin (`server/fair_play.py`, `0` = off) | 10 |
| `LMU_GARAGE_MAX_DEVICES` | V1.0.1: PCs one account may have connected at once (the one used longest ago is disconnected) | 5 |
| `LMU_GARAGE_LATEST_CLIENT_VERSION` | V1.0.1: the newest desktop client; older clients show "a new version is available" | the client released with the server |
| `CADDY_SITE_ADDRESS` | Reverse proxy access variant switch — `:80` (Tunnel) or a real domain (direct/VPS) | `:80` |
| `CLOUDFLARE_TUNNEL_TOKEN` | Only used with `--profile tunnel` (Variant A) | unset |
| `BACKUP_HOST_PATH` / `BACKUP_INTERVAL_SECONDS` / `BACKUP_RETENTION_DAYS` | Only used with `--profile backup` | see `.env.example` |

**Before running this anywhere but localhost:** set `LMU_GARAGE_SECRET_KEY`,
`LMU_GARAGE_ADMIN_TOKEN`, and either `LMU_GARAGE_REGISTRATION_SECRET` (closed circle) or
`LMU_GARAGE_OPEN_REGISTRATION=1` (open sign-up), and put a TLS-terminating reverse proxy in front
of it (this process only ever speaks plain HTTP itself). `LMU_GARAGE_ENV` unset or anything other
than `development` hard-fails at startup when these are missing — see `server/config.py`.



## Credential lifecycle

Since V1.0 a driver signs up on the website with name, email and password (`POST /accounts/signup`;
V1.0.1: the same answer for every address, the browser is not signed in until the email is
confirmed). The desktop client connects with the same email and password
(`POST /accounts/desktop-login`) and receives:

- an **auth token for that PC** (V1.0.1, `device_tokens`: one per PC, up to
  `LMU_GARAGE_MAX_DEVICES`; listed and disconnectable under Account → Connected PCs,
  `GET/DELETE /accounts/devices`), and
- the account's **client secret** (HMAC key for uploads; the same on every PC).

The client stores both encrypted with the Windows Data Protection API (V1.0.1,
`client/secret_store.py`). Further endpoints:

- `POST /accounts/rotate-token` / `POST /accounts/rotate-secret` — new per-driver token / secret
  (the old token-only accounts and the "advanced" token login use them).
- `POST /accounts/revoke` — with a PC's token: disconnects that PC; with the per-driver token:
  kills it (the web login with email and password keeps working).
- `POST /accounts/accept-terms` — accept the current terms (V1.0.1; the website asks after
  signing in when they changed).
- `GET /accounts/me/export` — self-service data export; `DELETE /accounts/me` — full deletion.

## Moderation

`/admin/*` endpoints (bearer-token-gated via `LMU_GARAGE_ADMIN_TOKEN`):
lock/unlock a driver, soft-invalidate a lap with a reason, review/resolve
driver-submitted lap reports (`POST /telemetry/laps/{id}/report`), delete
a driver and all their data, and decide the review queue (`GET /admin/review`): laps the
track-limits check could not decide, and (V1.0.1, `server/fair_play.py`) laps of new drivers that
would beat the Garage16 Best of others. See `server/routers/admin.py`.

## Running tests

**The release gate (V0.9.7)** — what decides whether a state may be released:

```
bash scripts/setup_test_env.sh          # .venv with the exact versions of constraints.txt + jsdom
. .venv/bin/activate
python -m scripts.release_check         # whole suite, strict: a skipped test is a failure
python -m scripts.release_check --zip Garage16_Lmu.zip   # ... and the archive, only if green
```

It first checks the environment (every Python package, Node + jsdom, tkinter, bash/gzip,
xvfb-run, caddy) and stops with **exit code 6 — "environment incomplete, nothing was tested"**
when something is missing, instead of letting those tests skip themselves into a green-looking
run. Installed versions are compared with `constraints.txt` (the versions the Docker images
install). The report lands in `release/TEST_REPORT.md` (+ JUnit XML, `environment.json` with a
hash over the released files) and goes into the release archive. `.github/workflows/ci.yml` runs
the same gate on every push, plus the suite against PostgreSQL and the Docker image builds; the
client build (`build-client.yml`) only starts after it passed.

A quick local run without the gate is still just:

```
pip install -r requirements-dev.txt -r server/requirements.txt -r client/requirements.txt -c constraints.txt
pytest
```

`tests/conftest.py` isolates each test run into a fresh temp SQLite DB
and storage directory — nothing touches the repo-root `lmu_garage_server.db`
you get from running the server locally.

A handful of modules (`test_lmu_structs.py`, `test_parser.py`,
`test_validation.py`, `test_uploader_retry.py`, `test_recorder.py`) need
only pandas/numpy/requests + stdlib — they also run without pytest via
`python -m tests.run_stdlib_tests`. Everything else (integration,
concurrency, moderation, rate-limit tests) needs the full stack
(`fastapi`, `httpx` for `TestClient`) and only runs under `pytest`.

`tests/test_concurrency.py` verifies the SQLite race-safety path for real
(two threads, real HTTP requests, real DB). **The PostgreSQL
`pg_advisory_xact_lock` path is now also verified for real** (V0.8.3) —
run `./scripts/test_against_postgres.sh` against a real PostgreSQL
instance (see that script's header for how to set one up in ~2 minutes)
to run the full suite, including the concurrency tests, against it. Do
this once before a production deploy and after any change touching
`server/database.py` or `server/records.py` — SQLite alone can't catch a
Postgres-specific regression there.

`tests/test_migrations.py` runs the full Alembic chain (upgrade head,
downgrade base, upgrade head again) against a fresh SQLite DB as part of
the normal `pytest` run — every future migration gets this round-trip
check automatically, not just the one being actively written.

## LMU shared-memory verification (needs Windows + LMU)

`client/lmu/structs.py`'s scoring struct is independently offset-verified;
several other assumptions (scoring/telemetry sync timing, `count_lap_flag`
semantics, `mmap` "not running" detection, car identity/encoding,
staleness detection) are not, and can't be from this development
environment (no Windows, no LMU). `docs/LMU_VERIFICATION_PROTOCOL.md` has
a concrete test procedure for each open item, and `scripts/dump_shared_memory.py`
is the read-only diagnostic tool those procedures use. Set
`LMU_GARAGE_DEBUG=1` when running the real client to get the matching
debug log lines from `client/telemetry/parser.py`.

The protocol's items were run against real LMU sessions on the developer's PC; repeat the relevant
ones after a change to the shared-memory structs or the parser.

## Known limitations

- `server/rate_limit.py` is in-process only — correct for a single `uvicorn` worker,
  under-protective with several workers (each has its own counters). See that module's docstring.
- The upload endpoint's DB transaction spans lap insert + record evaluation together (by design,
  for atomicity — see `routers/telemetry.py`).
- The Windows installers are built by GitHub Actions but **not code-signed** — Windows SmartScreen
  warns ("More info" → "Run anyway"). Every release carries `SHA256SUMS.txt`.
- The HMAC signature proves which account sent a lap, not that it was really driven (the client
  holds the key). Protection against forged laps is the server's own check of the telemetry plus
  the review queue (`server/lap_check.py`, `server/fair_play.py`).
- Verified on real hardware so far: Variant A (Cloudflare Tunnel) on a Synology NAS, a real
  backup + restore drill, real LMU laps. Not verified: Variant B (VPS), Docker secrets, the
  `--profile backup` loop.
- See `discord_bot/DISCORD_INTEGRATION.md` and `alembic/README.md` for component-specific detail.
