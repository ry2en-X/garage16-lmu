# LMU Garage

Telemetry tracking and leaderboard platform for Le Mans Ultimate: a
desktop client reads shared memory and uploads laps, a FastAPI server
stores and evaluates them (PR/WR/TEAM_BEST), a Discord bot announces
records, and a static web app shows leaderboards/teams.

**Current version: server 1.0.0 / desktop client 1.0.0.** The instance runs at **https://garage16.de**. See `CHANGELOG.md` for what changed and
`docs/` for deployment, privacy, and LMU-verification protocols.

**Running Garage16 or playing on it?** Two short, practical guides —
everything below this README is the full technical reference:
- **[docs/ADMIN_GUIDE.md](docs/ADMIN_GUIDE.md)** — NAS install, Docker,
  `.env`, PostgreSQL, backup/restore, Discord, SMTP, updates,
  troubleshooting.
- **[docs/FRIENDS_GUIDE.md](docs/FRIENDS_GUIDE.md)** — installation,
  account, client, LMU, teams, leaderboard, common errors. No developer
  knowledge required.

## Components

| Dir | What it is | Run with |
|---|---|---|
| `server/` | FastAPI backend + DB | `uvicorn server.main:app --reload` (dev) or `docker compose up` (prod) |
| `client/` | Desktop recorder/uploader | `python -m client.main` |
| `discord_bot/` | Discord announcements + slash commands | `python -m discord_bot.bot` |
| `web/` | Static leaderboard/account frontend | `python3 -m http.server 5173` (from `web/`) |
| `alembic/` | DB schema migrations | see `alembic/README.md` |
| `docker/` | Dockerfiles, entrypoint, Caddy reverse proxy config | see "Production deployment" below |
| `scripts/` | Ops tooling: LMU diagnostics, backups | see individual scripts |
| `docs/` | Deployment, rollback, privacy, LMU verification protocol | — |

## First-time setup (local dev)

```
pip install -r server/requirements.txt
pip install -r client/requirements.txt
pip install -r discord_bot/requirements.txt
uvicorn server.main:app --reload      # creates lmu_garage_server.db on first run
```

Register a driver via the web app's Account page (or `POST
/accounts/register`), then:

```
python -m client.main                 # first run prompts for the server URL,
                                       # auth token and client secret, and
                                       # saves them to ~/.lmu_garage/client_config.json
```

Friends install the packaged app (see `docs/FRIENDS_GUIDE.md`; you build it per `docs/CLIENT_BUILD.md`). `docs/CLIENT_INSTALL.md` is the developer/source-install reference
(manual `pip install` + run — for developers, not for friends).

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

### Lap analysis (V0.9.0)

The client now records **every telemetry channel LMU's shared memory offers**
(about 180 per sample — suspension, tyre forces/loads/slip velocities, wheel
speeds, accelerations, driver aids such as TC and ABS, hybrid data, ...; the full
list is `client/telemetry/channels.py`). The web app turns that into:

- **Analysis** (`#/analysis?a=<lap>&b=<lap>`): two laps in one chart per channel
  (one line each) over the same stretch of track, with the time difference
  underneath, exact values on hover, a shared cursor, zoom/pan, sector markers and
  a value table. Lap 2 can be **any valid lap** — pick a leaderboard row (e.g. the
  number one), one of your own laps, or another driver's.
- **Track map** coloured by speed, throttle, brake, gear, G or by *where time is
  gained/lost*; hovering or clicking it moves the cursor everywhere.
- **Car physics**: the four wheels (suspension travel, load, slip angle, temperature,
  sliding share) and a G-G diagram at the cursor.
- **Circuits**: a card per track with all layouts, lap/driver counts, classes, the
  fastest lap and an outline drawn from that lap's own position data.

API (all under `/telemetry/analysis/`, i.e. already routed by the Caddyfile; V0.9.1 additions
below): `GET channels` (public), `GET compare?lap_a&lap_b&channels&step`,
`GET laps/{id}`, `GET drivers/{id}/laps`, `GET circuits` (public) and
`GET circuits/outline` (signed-in). Any signed-in driver may open any driver's
**valid** lap; invalid laps are only visible to their owner. Rate limit bucket
`analysis` (`LMU_GARAGE_RATELIMIT_ANALYSIS_MAX`, default 900/hour since V0.9.1; it was 300).

How the numbers are made trustworthy: files hold the game's raw values (Kelvin,
Newtons, metres, rFactor-2 local axes); `server/telemetry_channels.py` converts
them for display. Because LMU's axis/sign conventions are only documented for
rFactor 2, the sign of steering, lateral/longitudinal G, yaw, slip angle and the
track map's handedness are **decided from each lap's own data** (anchored on tyre
loads, which depend on no convention) and reported in the response
(`orientation`). Laps recorded before V0.9.0 still work; they simply lack the new
channels and say so. A lap now takes roughly 4-10 MB (it was ~0.3 MB) — see
`docs/ADMIN_GUIDE.md`, "Speicherbedarf".

### Lap analysis in V0.9.1 — compare, layout, data volume

**Comparing.** Open a lap (leaderboard "Analyse", Dashboard, a circuit) and press **Compare
lap** (German: *Runde vergleichen*): *Garage16 Best* (the fastest valid lap on Garage16 for the same track layout and
car model — the rule behind the `WR` records, deliberately not called "World Record"), *Team
Best* (fastest valid lap among everyone who shares a team with you), *My Best* or a *Lap
ID*. If nothing suitable exists the answer is a message, never a different lap; the lap you are
looking at is never offered as its own reference. "You vs. …" on the analysis start page does the
whole pairing in one step.

**Language.** The web app is English by default and fully available in German: a popup on the first
visit asks, and an EN | DE switch in the menu changes it later (kept in the browser; the page reloads).
Every visible text is written as `t("English text")` in `web/js/**`, the German versions are in
`web/js/i18n/de.js` (one `"English": "Deutsch",` per line); `python scripts/i18n_check.py` lists what is
missing, stale or broken, and `tests/test_i18n.py` makes the build fail on it. To add a text: write it
with `t()`, run the script, add the German line. Messages from the server stay English, as do names,
lap times and the server-defined channel names.

**Signs.** Everywhere in the analysis it is *Lap 1 minus Lap 2*: negative = Lap 1 faster (time gained,
green), positive = slower (time lost, red) — delta chart, sectors, lap difference, biggest gain / loss and net.
"Biggest gain / loss" is the steepest change of the delta over a corner-sized stretch (1/40 of the lap,
between 100 and 400 m; `FACT_WINDOW` in `web/js/analysis/insights.js`), so it points at a place on track.
The server measures it on a fixed 0.5 m grid (`delta_facts`, same constants in
`server/telemetry_analysis.py`), so every screen size shows the same numbers.

**Corners and precision.** The *Corners* window lists every corner with its apex, the time Lap 1
gained/lost there, the braking point difference and minimum/exit speeds — measured on the raw samples
(`corner_analysis` in `server/telemetry_analysis.py`; thresholds `CORNER_*`, `BRAKE_ON_PCT`,
`THROTTLE_FULL_PCT`). Corners run from braking zone to braking zone and the first one also holds the
straight from the line, so they add up to the lap-time difference. Values are sent with a precision per
unit (`DECIMALS_BY_UNIT`: km/h 1 decimal, % 2, rpm whole numbers, times and delta 3 = ms). For cells of
the overview grid whose raw samples reach further than the drawn line (> 2 % of the channel's range —
a brake tap, a kerb), the response carries the raw min/max (`a_spikes` / `b_spikes`: `{i, lo, hi}`)
and the chart draws them as thin vertical strokes.

**Layout.** First open = the Garage16 standard layout. "Customise view" turns the analysis into a
grid editor (V0.9.5): tiles sit on a 12-column grid of 40 px rows (`x, y, w, h` in cells), are moved
by their handle, resized at the corner, and added from the tile picker (What for? → Kind → Tile,
with a live preview and ready-made sizes) or by drawing a box on empty cells (then only tiles that
fit are offered); tiles in the way move down, nothing overlaps. "Restore standard layout" removes
only that stored preference. It lives in the browser's `localStorage` (key `garage16_analysis_layout`, version 2; a version-1 layout is migrated),
never on the server, never with telemetry. Window types are defined in
`web/js/analysis/modules.js`, the layout model in `layout.js`, the shared cursor/zoom state in
`model.js`; channel names, units and groups stay defined by the server (`/channels`).

**API additions** (all under `/telemetry/analysis/`, additive):

| Endpoint | What |
|---|---|
| `GET compare?...&max_points=&start=&end=` | `max_points` bounds points per series (server-side downsampling; 50–8000, default = V0.9.0's 8000); `start`/`end` (metres) return one stretch of track, e.g. for zoom at a finer `step`. Response gains `analysis_version`, per channel `a_spikes`/`b_spikes` (only when something would be hidden), and for whole-lap comparisons `corners` and `delta_facts`. `ETag`/`If-None-Match` → `304`. |
| `GET reference?kind=wr\|team_best\|my_best&lap_id=&scope=model\|class` | The lap to compare `lap_id` with: `found`, `lap`, `reason` (`no_lap`, `no_team`, `no_team_lap`, `no_own_lap`, `already_best`), `already_best`. Needs a lap the viewer may open. |
| `GET quick?mode=wr\|team_best\|my_best[&track_name=&car_model=]` | "You vs …": your best valid lap (for `my_best`: your latest, compared with your best) plus its reference; without `track_name`, the track of your most recent valid lap. |

**What is read, computed, cached, sent.** Only the requested channels' columns are read from the
parquet file (plus ~22 calibration columns), they are resampled to at most `max_points` points
(the browser asks for 1,000–3,000 depending on its width), and the compact JSON is compressed by
Caddy's `encode gzip` (not by the app). An in-process LRU (`server/analysis_cache.py`: 128 entries
/ 48 MiB / 15 min, versioned by `ANALYSIS_CACHE_VERSION`) keeps recent results; it is only an
optimisation — each request first checks permissions and that the file exists, the rate limit
counts every call, and with several workers each simply has its own cache. Bump
`ANALYSIS_CACHE_VERSION` (now 3) whenever a channel formula, the resampling or the response shape
changes. Below that, decoded laps (only the columns read) stay in a second LRU of 32 MiB per process
(`LAP_CACHE_MAX_BYTES`, keyed by path, size, modification time and columns), and lap files are opened
memory-mapped — so zooming or comparing the same lap again does not decode the file again.
The browser never receives a raw parquet file, and moving the cursor never calls the server.

**Zoom refinement** (`web/js/analysis/refine.js`, wired in `pages/analysis.js`): the overview of the
whole lap is the first load. When the user zooms in at least 2x and fewer than 600 overview points
remain inside the view, the page — after the gesture settled for 300 ms — requests only that stretch
(`compare?start=&end=&step=`, the view plus 25 % margin on each side, step down to the server's 0.5 m
floor, at most ~2,000 points) and swaps it in under the unchanged view and cursor. Leaving the stretch
shows the overview immediately; zooming out returns to it. Only zoom/pan can trigger a request, never
the cursor. Whole-lap windows (summary, sectors, biggest gain/loss, track map) keep reading the
overview. The browser remembers 6 fetched stretches, ignores failures silently (the overview stays)
and pauses for a minute after a 429. Thresholds are in the `REFINE` object of `refine.js`.

**Measured on a synthetic lap** (`python scripts/benchmark_pipeline.py`; ~15,000 samples x 196
columns — *not* a real LMU recording, absolute numbers will differ):

| | V0.9.0 request | V0.9.1 standard layout |
|---|---|---|
| ~3.3 km lap: points / JSON / gzip | 1,672 / 219 KiB / 26 KiB | 1,500 / 109 KiB / 18 KiB |
| ~14 km lap: points / JSON / gzip | 7,200 / 882 KiB / 139 KiB | 1,500 / 114 KiB / 19 KiB |
| ~14 km lap: compute, cold / laps in RAM / cache hit | ~85 ms / ~70 ms / ~7 ms | ~50 ms / ~21 ms / ~7 ms |
| upload validation (8.8 MiB file): time / Python heap | 45 ms / 17.5 MiB | 6 ms / 1.6 MiB |
| upload of a 40 MiB file: client peak memory | 85 MiB | < 3 MiB |

(The "V0.9.0 request" column is the old request — 8 channels, 2 m grid, no `max_points` — sent to the
V0.9.1 server. In-process timings on the build machine, which vary by ±20 % between runs: no network,
no Caddy. The 14 km rows include corners, spikes and `delta_facts`.)

In the browser (real Chromium 153, 14 km lap, standard layout): a cursor move costs ~0.4 ms of script
(the charts copy a cached layer instead of redrawing every line); finer zoom data is swapped into the
existing charts (~3.5 ms per zoom step instead of ~1 s for rebuilding all windows). The desktop client
reads only the three parts of LMU's shared memory it needs (~11 µs per frame instead of ~350 µs for the
whole ~317 KiB block) and keeps a recorded lap's extended values as `float32` (~26 MiB instead of
~98 MiB of RAM for 15,000 samples) — synthetic measurements, not taken with the game running. zstd levels 1/3/5/7 differ by < 2 % in
size (8.85 / 8.77 / 8.68 / 8.68 MiB) while write time grows from 85 to 139 ms, so the client uses
**3**; row groups made files larger and reads slower, so none are used.

**Storage.** One parquet file per lap, content-addressed (`<sha256>.parquet`), is the only copy of
the telemetry on the server — the cache is RAM only and lost on restart, no decompressed or JSON
copy is kept, and the database holds the metadata. The upload streams: client hashes and sends in
1 MiB chunks, the server writes a temp file while hashing, validates from the file and moves it
atomically; on any failure neither a temp file nor an unreferenced final file stays behind. The
client deletes the local `.parquet` of an *uploaded* lap after 14 days; from then on the server (and
your backup) holds the only copy. **Setting (client side):** environment variable
`LMU_GARAGE_LOCAL_RETENTION_DAYS`, or `"local_retention_days"` in the client's `client_config.json`
(the environment wins); a number of days, `0` = never delete, anything that is not a non-negative
number is ignored and the default applies, the maximum is 3650. Only laps the server confirmed are
ever deleted, only the `.parquet` next to its own `.json` (which stays, so nothing is uploaded
twice), and reconnecting an account or a server migration keeps the setting.

**Verification status.** Implemented and tested with synthetic data (Caddy gzip, `304` and the static-file
header were also checked against a real Caddy 2.8.4 — `tests/test_reverse_proxy.py`); requires real LMU / real
deployment verification: real file sizes and CPU/RAM, the Cloudflare tunnel in front of Caddy, touch and
drag in real browsers, PostgreSQL behaviour of the upload cleanup under real concurrency, TC/ABS/
motor-map/brake-migration values and the sign conventions (steering, G, map mirroring), the client's
three-part shared-memory read with the game running, and the corner thresholds on real laps.

### Analysis on the PC — two client editions (V0.9.2)

The desktop client is built in two editions from the same code (`packaging/build_windows.ps1
-Edition standard|analysis`, `GARAGE16_EDITION` for `packaging/garage16.spec`; the edition is baked
in as `garage16_edition.txt`, read by `client/edition.py`):

| Edition | Files | "Open analysis" |
|---|---|---|
| Client | `Garage16-Client-Setup.exe`, `Garage16-Portable.zip` | the web analysis in the default browser |
| Client + analysis app | `Garage16-Client-Analysis-Setup.exe`, `Garage16-Analysis-Portable.zip` | its own window (`client/analysis_window.py`, pywebview on Microsoft Edge WebView2) |

Both install as the same app (same Inno Setup AppId), so installing one over the other switches
the edition. The window shows the server's own web app — nothing is computed or stored locally,
so it is always in sync — and runs as a separate process (`Garage16.exe --analysis-window [--lap N]`).
It signs in without asking: the page calls the app bridge (`web/js/app_bridge.js` →
`window.pywebview.api.app_session()`), the client calls `POST /accounts/app-session` with its Bearer
token (a cookie is refused) and gets an ordinary 30-day web session, which the page turns into the
HttpOnly cookie via `POST /accounts/app-session/activate` (same-origin only, against login CSRF). The
bridge answers only pages of the configured server. Without the WebView2 runtime, or if the window
cannot start, the analysis opens in the browser. `Garage16.exe --analysis-selftest` checks the
packaged engine (the build runs it).

The website's **Download** page (`#/download`) shows both editions side by side; the links come
from `LMU_GARAGE_CLIENT_DOWNLOAD_URL` and `LMU_GARAGE_CLIENT_APP_DOWNLOAD_URL` (via `/health`), and an
outdated client is sent to its own edition's download. CI (`.github/workflows/build-client.yml`)
builds both editions; a `client-v*` tag also publishes a GitHub release with all four files.
Verified: unit/jsdom tests, real PyInstaller builds of both editions on Linux, and the real window
(pywebview, Qt WebEngine under Xvfb) against the real stack. Requires real Windows verification: the
WebView2 window, the Windows bundle of pythonnet/WebView2, the installer edition switch.

### V0.9.9 — track limits you can follow, stored laps you can trust

- **VALID / INVALID / REVIEW** for every lap (`server/lap_decision.py`): a lap that cannot be checked
  (signals missing, gaps, implausible motion, another driver's file) is REVIEW — stored and
  analysable, but **held** out of leaderboards and records until an admin clears it. Laps flagged
  before V0.9.9 keep counting: no record changes with the update.
- **Measured against a labelled reference set** (truth from geometry, `scripts/trackcheck_eval.py`):
  curated set precision 100 %, recall 83.3 %, no false positives; every miss is the same documented
  limit (all four wheels on the kerb beyond the line with LMU reporting nothing). Not yet measured on
  real laps — `--labels` does that on the NAS.
- **Stored laps are tamper-evident**: size, sha256 and a server MAC per lap, checked on every
  analysis access and by `scripts/integrity_check.py`; a changed file or an edited lap time puts the
  lap into quarantine (Admin → Integrität). Chained, append-only **audit log**; its head is in every
  backup manifest.
- One additive migration (0017). Details: `CHANGELOG.md`, `docs/QUALITY.md`, `docs/ARCHITECTURE.md`;
  NAS steps: `docs/ADMIN_GUIDE.md` and `docs/V0_9_9_NAS_ANLEITUNG.md`.

### V0.9.8 — trustworthy operation: proxy trust, consistent backups, safe restores

- **Rate limits count the real address** (`server/client_ip.py`, `LMU_GARAGE_TRUSTED_PROXIES`):
  forwarding headers only from our proxies, read from the right.
- **Backups from one moment** (`server/write_lock.py`): uploads pause with 503 for the seconds of
  the dump; one folder per backup with a SHA-256 manifest, published only when checked.
- **Restores that end in the backup or the state before** (`scripts/restore.sh`): staged, validated,
  swapped by renaming, verified; `--rollback` / `--cleanup`. Proven by a real drill with a fault in
  every step (`tests/test_v098_backup_restore.py`).
- Operations: `/health/live`, `/health/ready`, request id in every error, upload-log drill-down,
  key rotation (`scripts/rotate_secret_key.py`), a complete data export.
- Release gate: linter, bandit, pip-audit, coverage floors, the tested tree is the zipped tree;
  `scripts/mutation_check.py`; API contract snapshot.
- Docs: `docs/ARCHITECTURE.md`, `docs/RUNBOOKS.md`, `docs/DATA_PROTECTION.md`, `docs/QUALITY.md`.

### V0.9.7 — verifiable: release gate, reference laps, data quality, operations

- **Release gate** (`scripts/release_check.py`, `.github/workflows/ci.yml`, `constraints.txt`): the
  whole suite in a reproducible environment (pinned versions, also in the Docker images), strict —
  a missing dependency is an infrastructure error (exit 6), a skipped test a failure; a report per
  release. A database filled at every older schema is migrated to head and read by the current code.
- **Reference laps with known values** (`tests/helpers/reference_lap.py`): a 2 kHz simulation is the
  truth; recorded like LMU/the client (50 Hz frames, 60 Hz polls, `lap_dist` at 5 Hz), analysed by
  the real code. Braking/throttle points and apex within one sample spacing, minimum/exit speed and
  corner times/deltas within derived bounds; faults (duplicate polls, gaps, NaN, jitter, spikes,
  restarts, truncated files) tested; a golden file ties result changes to the cache versions.
  Found and fixed: a few NaN speed samples hid every corner; a restart inside a file was analysed as
  a lap.
- **Data quality per lap** (`server/telemetry_health.py`, `GET /telemetry/analysis/laps/{id}/health`,
  tile "Data quality"): channels, frame rate, gaps, time/distance monotonic, `lap_dist` update rate,
  speed vs distance, duration vs lap time, implausible values, resolution of a braking point.
- **Hints with their measuring accuracy**: every corner point carries its resolution (`res`); a
  difference only becomes a hint above it, shown as ±. Corners can be ordered by time lost. Stint
  Analyzer: 95 % ranges for degradation and the fuel effect, consistency with the trend removed.
- **Operations**: every upload attempt recorded (`upload_events`, migration 0016) with outcome,
  reason, request id, lap id, duration; admin tab "Operations"; `scripts/consistency_check.py`
  (database vs files, dry run by default); a permission matrix test derived from the app's own API
  description; `scripts/load_test.py` (read-only).

### V0.9.6 — clearer, simpler, faster (overview & usability pass)

- **Menu in groups** (My garage · Analysis · Racing, account and download at the foot), brand
  "Garage16" everywhere. **Fonts self-hosted** (`web/fonts`, SIL OFL) — no request to Google.
- **Pages load on demand** (`router.js`, dynamic `import()`): the landing page loads 9 JS files /
  ~130 KB instead of 50 / ~580 KB.
- **Dashboard**: quick actions (analyse last lap, you vs. Garage16 Best, Stint Analyzer), bests per
  track with "vs. Garage16 Best", team side by side, the lap list filtered in the browser; the three
  requests and the team records run in parallel.
- **Record feeds** (dashboard, driver, team; `web/js/activity.js`): one row per lap with every
  record as a badge, the car model, a link into the analysis (`lap_id`, `car_model` added to
  `/drivers/{id}/public`, `/teams/{id}/activity`, `/leaderboard/recent`).
- **Leaderboard** shows a table at once (busiest track and class), reloads on every change, keeps
  the choice in the address, shows the gap to P1. Team leaderboard likewise.
- **Team page**: results first, people next, settings folded; sections load in parallel; one
  overview card. **Account page**: often-used first, credentials / sessions / data folded.
- **LapTime Analyzer**: compact toolbar; lap picker grouped per track, best lap marked, whole row
  clickable. **Circuits**: undriven tracks as one compact list. Tables scroll inside their box on
  phones; nothing is wider than the screen. Caddy compresses with zstd or gzip.

### V0.9.5 — the LapTime Analyzer on a grid, every channel at telemetry rate

- **Grid layout** (`web/js/analysis/layout.js` v2, `grid.js`, `tile_catalog.js`, `tile_picker.js`):
  tiles on a 12-column grid with fixed rows; move by the handle (ghost: green = free, amber = tiles
  in the way move down), resize at the corner, arrow keys / Shift+arrows; side panel **What for? →
  Kind → Tile** with search, live preview of the real tile and ready-made sizes, drag onto the grid
  or place automatically; or draw a box on empty cells and choose from the tiles that fit. Phones
  stack the tiles. V0.9.1–V0.9.4 layouts are migrated in their order.
- **Track surface behind the racing lines** (`server/track_surface.py`,
  `GET /telemetry/analysis/track-surface`): asphalt, edges and kerbs rebuilt from LMU's
  `path_lateral` / `track_edge` / surface type of several laps; a car-width band around every lap.
- **Distance axis at telemetry rate** (`server/distance_axis.py`): `lap_dist` (LMU's scoring value,
  possibly refreshed only ~5×/s) is kept where it advances; the samples in between are placed by the
  integrated speed (exact at every `lap_dist` update). Before, those samples were dropped. Repeated
  polls of one telemetry frame are dropped. `orientation.*.distance_axis` in `/compare` reports
  how many samples carried a fresh `lap_dist`.
- Corner metrics: pedal points (braking, back on throttle, 50 %, full throttle, turn-in)
  interpolated between samples; peak deceleration = highest 0.1 s average; means over time.
  Analysis cache version 6, insights v4 (`scripts.revalidate_laps --apply` recomputes them).

### V0.9.4 — two dashboards: LapTime Analyzer and Stint Analyzer

- **LapTime Analyzer** (`#/analysis`): new windows **Racing lines** (`web/js/analysis/linemap.js`
  — both driven lines on a zoomable map, pinch zoom, car-width band, scale bar, braking ▲ /
  turn-in ◆ (estimated) / apex ● / full throttle ▼ per lap, "Lap 2 1.4 m further inside" at the
  cursor, 0.5 m detail from `GET /telemetry/analysis/line` when zoomed in), **Corner in detail**
  (time per phase braking / turn-in / apex / exit / straight — the phases add up exactly to the
  corner's delta —, every measured value of both laps, the same corner on earlier laps from
  `GET /telemetry/analysis/corner-history`) and **Sectors of the session**
  (`GET /telemetry/analysis/lap-sectors`). The coach groups hints by phase; "brake later" only
  when the reference is not slower at apex and exit. Up to two more laps over the pair
  (`c=`, `d=` in the URL, `extra=` on `/compare`), delta or elapsed time, the pair channel
  `line_offset`.
- **Stint Analyzer** (`#/stints`, `#/sessions` still works): sessions split into stints at pit
  stops (measured pit lane + standstill, LMU's pit counter) or measured new tyres / refuelling;
  lap flags from measured values; pace with filters, degradation (Theil–Sen), fuel/energy with
  target consumption, vehicle state with correlations ("not a cause"), traffic, pit stops with
  estimated time loss, forecast (≥ 5 clean laps), strategy calculator with the admin's
  **race rule sets**, stint and team-mate comparison. `server/stints.py`,
  `server/routers/stints.py`; statistics in `web/js/stints/stats.js`.
- **Client 0.9.4** records per sample `in_pits`, `place`, `flag`, the time gaps to the cars
  ahead/behind, and per lap `lap_context` (pit stops, compound, damage, tank size, position, time
  left). Migration **0015** (`laps.lap_context`, `race_rule_sets`). Per-lap figures (insights v3)
  add pit lane, measured refuelling and tyre changes, traffic, fuel per sector, engine map, the
  lap's corners; `scripts.revalidate_laps --apply` fills them in for stored laps.
- Desktop client: buttons **LapTime Analyzer**, **Stint Analyzer**, **Analyse last lap**
  (`--analysis-window --view stints`).

### V0.9.3.1 — track limits in tiers, laps can be judged again (correction release)

- **Track limits in tiers** (`server/track_limits.py` `RULES`): LMU's own verdict and decided
  Garage16 rules invalidate; measurements (track-limits steps, four wheels off, beyond the edge,
  late reports, …) only send a lap to Admin → Track-limits review. `GET /admin/track-limits/rules`
  lists every rule with its tier and effect; `LMU_GARAGE_TRACK_LIMITS_INVALIDATING` lets a
  confirmed measurement invalidate (default: none).
- **Late window:** the client holds a finished lap 6 s after the line and records what LMU still
  reports (`client/telemetry/lap_verdict.py`); the server links a step rise at the start of lap
  N+1 to lap N (`server/lap_check.py` `link_neighbours`). Unclear attribution → review on both laps.
- **Same rules on both sides:** the client sends reason codes (`lap_report`); its yes/no follows
  the server's table.
- **Re-judging stored laps:** `laps.check_inputs` (migration 0014); `python -m scripts.revalidate_laps`
  reports transitions (dry run), `--apply`, `--apply --restore`, `--apply --update-validity`.
- **Car identity:** cut model names (30-byte field) repaired or marked "…"; grid-only aliases used
  after two reports; identities to check by hand in the report, the PC probe and the admin page.
- **Theoretical best:** car model required, dry/wet/unknown laps never mixed silently.
- On the PC: `py -m scripts.track_limits_probe --csv tracklimits.csv` records the LMU signals
  for docs/LMU_VERIFICATION_PROTOCOL.md (r)–(v).

### V0.9.3 — car models, server-side track limits, deeper analysis

- **Car identity** (`server/car_identity.py`): a car is its MODEL — LMU's own model name
  (`mVehicleModel`), else an alias learned from what clients report about the session's grid,
  else `veh_filename` without the car number ("499P (2025)"). Raw values stay per lap
  (`car_model_raw`, `car_vehicle_model`, `car_identity_source`); aliases in `car_aliases`.
  `python -m scripts.car_identity_report` (dry run / `--apply`), and on the PC
  `py -m scripts.car_identity_probe --csv cars.csv` with LMU running.
- **Track limits** (`server/track_limits.py`): the server decides from the uploaded telemetry —
  LMU's `lap_invalidated`, track-limits points, `count_lap_flag` (2 = time counts), surface type
  per wheel, distance to LMU's track edge, driven distance, penalties, plus the client's verdict.
  Every finding is stored with its lap distance (`laps.validation`); undecided laps go to
  Admin → Track-limits review (`GET /admin/review`, `POST /admin/laps/{id}/review`).
  `python -m scripts.revalidate_laps` re-checks stored laps (dry run by default).
- **Analysis:** new derived channels (damper velocity, heave/roll/pitch, rake, tyre tread
  inner/middle/outer, carcass, wheel slip, wheels off track, edge distance, …);
  `GET /telemetry/analysis/distribution` (histograms, time-weighted; damper bump/rebound and
  low/high-speed shares), `GET /telemetry/analysis/events` (track-limits findings, lock-ups,
  wheelspin); windows "Where to find time", "Track limits & events", "Tyres & brakes", "Damper
  histograms", "Histogram".
- **Per-lap figures** (`server/lap_insights.py`, `laps.lap_stats`) computed once at upload from the
  same file read; `GET /telemetry/analysis/theoretical-best` and `GET /telemetry/analysis/sessions`
  work from the database only. Web page `#/sessions` ("My sessions").

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

### Deployment verification status

**Verified end-to-end on real hardware (2026-09-26):** a Synology NAS
running Variant A (Cloudflare Quick Tunnel), all four containers healthy,
migrations ran automatically, a real driver registered, a real LMU lap
uploaded successfully over the public tunnel URL, the Discord bot
connected for real (`Logged in as ...`), and data survived both an
individual container restart and a full stack restart. This test also
found and fixed five real bugs that no amount of unit/TestClient testing
had caught — see `CHANGELOG.md`'s V0.6.5 entry for all five (a Caddy
header issue, two file-permission issues from the ZIP→Windows→NAS
transfer path, a Docker-volume-ownership issue, and a confirmed
LMU scoring/telemetry sync bug in the parser).

**Still not verified:**
- Variant B (direct VPS reverse proxy with Caddy's automatic HTTPS) —
  only Variant A (Tunnel) has been run for real so far.
- The Docker Secrets path (`secrets:` blocks) — the deployment above used
  plain `.env` values.
- The `--profile backup` containerized backup path, and a real
  backup+restore cycle (see `docs/ROLLBACK.md` for the restore steps to
  exercise).
- PostgreSQL-specific concurrency (`pg_advisory_xact_lock`) was verified
  separately, directly against PostgreSQL (see `CHANGELOG.md`'s earlier
  entries), not as part of this NAS deployment test.

Treat these as the next concrete verification steps before a larger
public launch, the same way the LMU shared-memory items were worked
through — see `docs/ROLLBACK.md` if something doesn't come up clean.

## Server environment variables

All optional except where noted "REQUIRED in production" — sensible dev
defaults apply otherwise. Full list with production notes: `.env.example`.

| Variable | Purpose | Dev default |
|---|---|---|
| `LMU_GARAGE_DB_URL` | Database connection string (Docker: assembled from `POSTGRES_*` pieces by the entrypoint script instead — see "Secrets" above) | `sqlite:///./lmu_garage_server.db` |
| `LMU_GARAGE_STORAGE_DIR` | Where uploaded telemetry files land | `./server_telemetry_storage` |
| `LMU_GARAGE_MAX_UPLOAD_BYTES` | Per-upload size cap | 50 MB |
| `LMU_GARAGE_SECRET_KEY` | Fernet key encrypting `client_secret` at rest — **REQUIRED in production** | insecure built-in dev key (loudly logged) |
| `LMU_GARAGE_REGISTRATION_SECRET` | If set, `/accounts/register` requires a matching `X-Registration-Secret` header — **REQUIRED in production** | unset — registration open |
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
| `LMU_GARAGE_RATELIMIT_*_MAX` / `_WINDOW` | Per-bucket rate limits (register/upload/team_join/link_code/report) — see `server/rate_limit.py` | see that module |
| `CADDY_SITE_ADDRESS` | Reverse proxy access variant switch — `:80` (Tunnel) or a real domain (direct/VPS) | `:80` |
| `CLOUDFLARE_TUNNEL_TOKEN` | Only used with `--profile tunnel` (Variant A) | unset |
| `BACKUP_HOST_PATH` / `BACKUP_INTERVAL_SECONDS` / `BACKUP_RETENTION_DAYS` | Only used with `--profile backup` | see `.env.example` |

**Before running this anywhere but localhost:** set `LMU_GARAGE_SECRET_KEY`,
`LMU_GARAGE_REGISTRATION_SECRET`, and `LMU_GARAGE_ADMIN_TOKEN`, and put a
TLS-terminating reverse proxy in front of it (this process only ever
speaks plain HTTP itself). `LMU_GARAGE_ENV` unset or anything other than
`development` now hard-fails at startup if the first two are missing —
see `server/config.py`.



## Credential lifecycle

Registration returns an `auth_token` (identifies the driver) and a
`client_secret` (HMAC-signs uploads), both shown once. From the web app's
Account → Security section, or directly via the API:

- `POST /accounts/rotate-token` / `POST /accounts/rotate-secret` — issue a
  fresh credential, invalidate the old one, keep the same driver identity.
- `POST /accounts/revoke` — kill the current token outright (compromised
  credential response). No recovery path afterwards; there's no
  password/email to prove ownership and get back in.
- `GET /accounts/me/export` — self-service data export.
- `DELETE /accounts/me` — self-service full account + data deletion.

## Moderation

`/admin/*` endpoints (bearer-token-gated via `LMU_GARAGE_ADMIN_TOKEN`):
lock/unlock a driver, soft-invalidate a lap with a reason, review/resolve
driver-submitted lap reports (`POST /telemetry/laps/{id}/report`), delete
a driver and all their data. See `server/routers/admin.py`.

## Running tests

**The release gate (V0.9.7)** — what decides whether a state may be released:

```
bash scripts/setup_test_env.sh          # .venv with the exact versions of constraints.txt + jsdom
. .venv/bin/activate
python -m scripts.release_check         # whole suite, strict: a skipped test is a failure
python -m scripts.release_check --zip Garage16_Lmu_V0_9_7.zip   # ... and the archive, only if green
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

## LMU shared-memory verification (P0-7 — needs Windows + LMU)

`client/lmu/structs.py`'s scoring struct is independently offset-verified;
several other assumptions (scoring/telemetry sync timing, `count_lap_flag`
semantics, `mmap` "not running" detection, car identity/encoding,
staleness detection) are not, and can't be from this development
environment (no Windows, no LMU). `docs/LMU_VERIFICATION_PROTOCOL.md` has
a concrete test procedure for each open item, and `scripts/dump_shared_memory.py`
is the read-only diagnostic tool those procedures use. Set
`LMU_GARAGE_DEBUG=1` when running the real client to get the matching
debug log lines from `client/telemetry/parser.py`.

**Do not distribute the client to other drivers before running through
that protocol at least once against a real LMU session.**

## Deferred / known limitations

- Registration still has no password or email — `LMU_GARAGE_REGISTRATION_SECRET`
  gates *who can register at all*, not per-driver login.
- `server/rate_limit.py` is in-process only — correct for a single
  `uvicorn` worker, under-protective if you scale to multiple workers
  without first replacing it with a shared (Redis-backed) limiter. See
  that module's docstring.
- The upload endpoint's DB transaction still spans lap insert + record
  evaluation together (by design, for atomicity — see `routers/telemetry.py`),
  but validation itself now runs off the event loop (`run_in_threadpool`)
  rather than blocking it; a further restructure to shorten the
  transaction itself is still open (P1).
- The Windows installer is fully scripted (`packaging/`, `docs/CLIENT_BUILD.md`) but has never been built on a real Windows machine — build and test it there before handing it to friends. The exe is unsigned, so Windows SmartScreen will warn.
- Legal/privacy documents in `docs/` are templates, explicitly not legal
  advice — see that file's own disclaimer.
- See `discord_bot/DISCORD_INTEGRATION.md` and `alembic/README.md` for
  component-specific detail.
