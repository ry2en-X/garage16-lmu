# Changelog

## V1.0.3 — Better charts, a calmer Discord bot, figures that keep themselves up to date

Server `1.0.3`, desktop client stays `1.0.2` (no client changes). **No migration.** Rollback: start
the V1.0.2 folder again. Two new optional settings: `GARAGE16_DISCORD_DIGEST_S`,
`GARAGE16_REFRESH_FIGURES` (`.env.example`).

### Stint Analyzer: interactive charts
- One chart component for every chart of the page (`web/js/stints/chart.js` on the shared core
  `web/js/core/chart_core.js`, which the LapTime Analyzer's ticks now come from too):
  - point at a lap: a cursor line and a value box with every series at that lap — and **every chart
    over the session shows the same lap at once** (pace, tyres, fuel, temperatures, position);
  - drag across a range to **zoom** (all session charts follow; double-click, Escape or "Reset zoom"
    goes back; Ctrl/⌘ + wheel zooms, a sideways wheel pans); a click pins the cursor; arrow keys
    move it lap by lap;
  - the legend is clickable (hide/show a stint or a series);
  - drawn for the real width (labels keep their size on a phone and on a wide screen);
  - a changed filter keeps the zoom.
- **Pace**: three views — lap time, gap to the best lap, and fuel-corrected (the fuel effect measured
  in the session taken out; offered only when it can be measured, marked as an estimate); a ±1σ
  band around the rolling average; the stints as coloured bands, pit stops marked with the
  standing time; one legend entry per stint.
- **Tyres per wheel** (new): temperature, pressure, tread left or brake temperature, one line per
  wheel, with the front/rear and left/right balance; a **wear forecast** for the current tyre set
  (which tyre wears fastest, % per lap, laps until a limit you set; an estimate).
- **Fuel**: the tank over the session with the projection to the session end at the median
  consumption and the safety margin, the consumption per lap as bars with the median.
- **Sectors** (new): a heat map of every lap's sector times against the best sector of the
  session, and the sector with the most time to find.
- **Position over the session** with the traffic laps marked; tyre and track temperature together.
- **Compare with another session** (new): one of your other sessions on the same track and car, or
  a team mate's (a driver change), both with the same filter: lap times over the session and the
  race figures side by side, the better value marked. `/stints?lap=…&compare=…` opens it directly.
- **Stints compared**: a bar chart of the difference per lap under the two lines.
- **All laps as CSV** (semicolon, UTF-8, times in seconds) for a spreadsheet.
- Server: `tyre_pressure_kpa` per wheel in the session data. The page is split into modules
  (`web/js/stints/ui.js`, `section_*.js`).

### LapTime Analyzer, driver profile, leaderboard
- Every LapTime Analyzer chart can be **saved as a PNG** (with its title and the values at the cursor).
- **Driver profile: progress** — the best lap per day and the personal best so far, per track and
  car (`GET /api/v1/public/drivers/{id}/progress`, valid laps only).
- `/leaderboard` without a choice opens at the track of the newest lap and the class driven most there.
- The dashboard's recent activity groups the records of one session into one line.
- A page that takes longer than a quarter second to load says so instead of leaving the old page standing.
- "Add to home screen" opens Garage16 like an app (`web/manifest.webmanifest`).

### Discord bot
- Announcements name the **car model** (as the leaderboard), not the livery ("Ferrari 499P", not
  "499P Custom Team 2026 #397").
- **One message per record and session**: an announcement waits `GARAGE16_DISCORD_DIGEST_S` seconds
  (default 300) for a further improvement of the same record; then one message with the newest
  time, the time before the first improvement and the number of improvements. `0` = at once.
- `/channels` lists this server's announcement channels (and marks the ones the bot cannot see);
  `/unlink_channel` removes one — also one that no longer exists. Both need "Manage channels".

### Operations
- **Lap figures refresh themselves after an update**: laps whose stored figures an older analysis
  computed are recomputed in the background after the start (`server/ops/figures_refresh.py`,
  started by `docker/entrypoint.sh`, batches of 25, low priority, progress in the server log; a
  restart continues). `GARAGE16_REFRESH_FIGURES=0` switches it off. The server container runs with
  `init: true` so the finished helper is reaped.
- **Backup progress**: the incremental backup says how many files are new and how big they are,
  warns before a long first copy, and reports progress about every 100 files / 10 s (also the
  restore). Old backup folders without `manifest.json` that are kept are named once.
- The commands written in the guides are tested (URLs against the server with HTTPS required,
  `python -m scripts…` modules, `scripts/…sh` paths) — the V1.0.2 update had a guide command that
  answered 400.
- The page addresses work even when the app cannot read the mounted `web/` folder (files from the
  ZIP via Windows arrive as `rwx--x--x` on the NAS; Caddy as root reads them, the app's user does
  not, and every page answered "The website files are not available"): the server image carries
  its own readable `index.html` (`docker/Dockerfile.server`), which `server/api/v1/pages.py` uses
  when the folder fails.
- `scripts/ops/backup.sh` without `sudo` says so instead of reporting a missing volume.
- `/api/v1/health/*` answers over plain HTTP like `/health/*`.

## V1.0.2 — Readable links, the website on Cloudflare Pages, less load on the NAS, a tidy code base

Server `1.0.2`, desktop client `1.0.2`. **One additive migration (0020)**: `drivers.slug`,
`teams.slug`, the table `slug_redirects` and two leaderboard indexes. Rollback:
`alembic downgrade 0019` (docs/ADMIN_GUIDE.md, 8.2). The existing `.env` keeps working unchanged
(the settings are called `GARAGE16_*` now, the old `LMU_GARAGE_*` names are still read). Clients
from 0.5.3 keep uploading — the API paths of V1.0.1 are translated inside the server. No feature
changes.

### Readable links
- Normal paths instead of `#/`: `/leaderboard/fuji-speedway/hypercar`,
  `/leaderboard/fuji-speedway/porsche-963`, `/driver/moritz-lachnit`, `/team/hypermotion-esports`,
  `/analysis/123/vs/456`, `/analysis/fuji-speedway/porsche-963/vs/class-best`,
  `/stints/fuji-speedway/2026-11-14` (`-2` for a second session that day), `/stints/lap/123`,
  `/impressum`, `/datenschutz`, `/terms`, `/licenses`. English for everybody, whatever language is set.
- **Old `#/` addresses are forwarded** (bookmarks, Discord posts, links of desktop clients 1.0.1):
  `web/js/core/routes.js legacyHashToPath`. `/legal` → `/impressum`, `/sessions` → `/stints`.
- Slugs: `server/core/slugs.py` (the browser twin `web/js/core/link.js slugify`, tested to agree).
  A renamed driver or team keeps its old link (`slug_redirects`); a name that would share a link with
  another is refused like a taken name. `GET /api/v1/public/drivers/by-slug/{slug}`,
  `GET /api/v1/public/teams/by-slug/{slug}`.
- Every page has its own title and link preview ("Fuji Speedway · Hypercar – Garage16"):
  `GET /api/v1/public/page-meta?path=` (server/api/v1/pages.py), filled into index.html by the Pages
  function or — when the NAS serves the website — by `GET /api/v1/public/page` behind Caddy.
- "Copy link" on the leaderboard, the lap analysis and the driver profile; a real **Page not found**
  page (and HTTP 404) instead of the home page.
- German menu: **Rundenanalyse** and **Stint-Analyse** (English unchanged).

### API under /api/v1
- Every endpoint lives under `/api/v1` with one module per area in `server/api/v1/`. The public,
  same-for-everybody answers are under `/api/v1/public/` (leaderboards, activity, catalog, driver
  profiles, legal data, channel catalog, circuit cards, page titles). Lap reads are
  `/api/v1/laps/{id}/analysis`, uploads `/api/v1/laps/upload`.
- **The paths of V1.0.1 keep working**: `server/api/legacy.py` rewrites them inside the server
  (no redirect — a POST keeps its body). The reverse proxies send exactly those old API paths to the
  server and the look-alike page addresses to the website (`is_legacy_api_request`). Remove it once
  `min_client_version` is 1.0.2. `/health` stays at the root (Docker, old clients).
- Client 1.0.2 calls `/api/v1/...` and falls back to the old path once for a server that is not
  updated yet (`client/api_paths.py`).
- **Discord bot: announcements no longer repeat.** A team or global channel the bot could not
  reach (deleted, bot removed, no permission) kept the event "not announced", so every 10 s the
  bot posted it again to the channels that worked. Each channel now gets an event once; a channel
  that is gone or refuses the bot is skipped (warning in the bot log); other errors retry for about
  five minutes; several waiting improvements of the same record become one message (the newest).
- `/api/v1/health`, `/health/live`, `/health/ready` answer over plain HTTP like `/health/...`
  (with `GARAGE16_REQUIRE_HTTPS=1` they returned 400 — found on the NAS update).
- **Installer: autostart in two steps.** "Start Garage16 automatically when I log in to Windows"
  (off unless ticked), and under it "Open the Garage16 window" (default) or "Start minimized (only
  in the taskbar)" — the shortcut then starts `Garage16.exe --minimized`. Running the setup again
  replaces the old autostart shortcut with the new choice (`packaging/garage16.iss`).

### Less load on the NAS
- **Website on Cloudflare Pages** (`.github/workflows/pages.yml`, after every green CI run on
  `main`; `scripts/release/build_web.py` builds `build/pages`). The Pages function
  (`web/functions/_middleware.js`) passes `/api/*` and the old API paths on to the NAS
  (`GARAGE16_API_ORIGIN`) with the visitor's address (`GARAGE16_EDGE_KEY`, new optional setting —
  without it all visitors would share one rate limit). Serving the website from the NAS keeps working.
- **Versioned files:** index.html loads everything from `/v/<version>/` (on Pages: a content hash),
  cached for a year (`immutable`); index.html itself stays `no-cache`.
- **Public API answers** get `Cache-Control: public, max-age=30|60` and an ETag, 304 on repeat
  (`server/api/caching.py`); a Cloudflare Cache Rule can keep them (docs/DEPLOYMENT.md).
- **Analysis reads** of a signed-in driver: ETag + `private, no-cache` + `Vary: Authorization,
  Cookie` on every read under `/laps/`, `/analysis/`, `/circuits/`, `/drivers/`, `/stints/` (the heavy
  ones already decided on their ETag before computing).
- **Leaderboard indexes** `ix_laps_board_class (track_name, car_class, is_valid, lap_time)` and
  `ix_laps_board_model (track_name, car_model, is_valid, lap_time)` (migration 0020).
- **Loaded when opened:** the line map (when its window comes into view), the tile editor and the
  tile picker (prefetched when idle), the stint charts (with the first session).
- **Incremental backups** (manifest format 2): the database is dumped completely as before; the
  telemetry files go once into `<backup dir>/telemetry-store/` and each backup lists them in
  `telemetry-files.txt` — a night copies only the new files (`docker/telemetry-store.sh`). The store
  keeps only files a remaining backup lists; old-format backups (`telemetry.tar.gz`) age out by the
  same retention and can still be restored. Same for the `backup` container.
- Cloudflare rate-limiting rule for sign-in, sign-up and upload documented (docs/DEPLOYMENT.md);
  the server's own limits stay as the second stage.
- Health checks every 30 s (database, server, Caddy) and 60 s (Discord bot) instead of 10–15 s.

### Code layout
- `server/` in areas: `core/`, `laps/`, `analysis/`, `accounts/`, `catalog/`, `ops/`, `api/v1/`. Split:
  `api/v1/analysis.py` (+ `circuits.py`, `analysis/lap_access.py`, `analysis/comparison.py`),
  `analysis/telemetry_analysis.py` (+ `corners.py`, `racing_line.py`, `distribution.py`),
  `api/v1/accounts.py` (+ `web_sessions.py`, `account_data.py`), `schemas.py` (per area).
- `web/js/core/` (api, router, routes, link, i18n, format, state, tracks, …), `web/js/pages/` only
  pages, `web/js/main.js` the entry point. One route table (`core/routes.js`), one link builder
  (`core/link.js`).
- Settings `GARAGE16_*` (old `LMU_GARAGE_*` still read: `server/core/env.py`, `client/env.py`,
  nested defaults in docker-compose.yml); client folder `~/.garage16` (an existing `~/.lmu_garage` is
  moved once); the website's config variable `GARAGE16_API_BASE_URL`.
- `scripts/ops/` (backup, restore, checks, revalidation), `scripts/dev/` (benchmarks, probes,
  mutation check, `serve_web.py` for local work), `scripts/release/` (release gate, `build_web.py`).
- Tests by topic (`tests/accounts/`, `tests/analysis/`, `tests/leaderboard/`, …) instead of by version;
  the quick run `python -m pytest -m "not slow"` takes about a minute and a half, the full run stays
  the release gate.
- Release tags ("V0.9.1: …") removed from comments and docstrings — the history is here.
- `docs/README.md`: three ways in — I drive, I run the NAS, I develop.

## V1.0.1 — Accounts for an open server, several PCs per account, a tidy repository

Server `1.0.1`, desktop client `1.0.1`. **One additive migration (0019)**: `drivers.display_name_key`
(unique) and the table `device_tokens`. Rollback: `alembic downgrade 0018` (docs/ADMIN_GUIDE.md,
8.2). No new `.env` entry is needed — every new setting has a default. Clients from 0.5.3 keep
uploading; clients 1.0.0 keep their connection. The new privacy policy (version `2026-10-26`) is
asked for once after signing in.

### Accounts
- **The email comes first.** `POST /accounts/signup` answers `202 {"status": "check_email"}` for every
  address and no longer signs the browser in; an address that already has an account gets a
  "you already have an account" email instead of a confirmation link (before, a 409 told anybody who
  is registered). `POST /accounts/desktop-login` refuses an unconfirmed email with
  `409 {"code": "email_not_verified"}` (`LMU_GARAGE_REQUIRE_EMAIL_VERIFICATION`, default on).
  Sign-ups never confirmed — no laps, no team — are deleted after 7 days
  (`LMU_GARAGE_UNVERIFIED_ACCOUNT_DAYS`; at server start and on every sign-up,
  `server/driver_lifecycle.py prune_unverified_signups`).
- **Terms accepted again when they change.** `GET /accounts/me` and the login answer carry
  `terms_required`; `POST /accounts/accept-terms` records the current version; the website shows a
  dialog right after signing in (`web/js/terms_gate.js`); desktop-login answers
  `409 {"code": "terms_required"}` unless the client sends `accept_terms`. Accounts from before V1.0
  are asked too.
- **One token per PC** (`device_tokens`, ADR-13): connecting a second PC no longer disconnects the
  first. `GET /accounts/devices`, `DELETE /accounts/devices/{id}`; Account → Desktop client &
  sign-ins → **Connected PCs**. Up to `LMU_GARAGE_MAX_DEVICES` (5), the PC used longest ago is
  disconnected above that. `POST /accounts/revoke` with a PC's token disconnects that PC only. The
  per-driver token keeps working for PCs connected before.
- **No hints about who is registered:** unknown addresses cost a password hash on sign-in (same
  time), failed sign-ins are also counted per address (`login_account`, 10 per 15 min), emails to one
  address are limited (`email_to_address`, 3 per hour: sign-up notices, resets, verification links).
  `server/rate_limit.py limit_key()` hashes the address; expired windows are now dropped (the
  in-memory table grew with every IP until a restart).
- **Unique driver names** (`server/names.py`): compared after NFKC, case folding, removing invisible
  characters and collapsing spaces — "Max", "MAX", "ＭＡＸ" and "Ma<zero-width space>x" are one name; "admin",
  "garage16" and similar are reserved; control characters are removed from the stored name.
  Registering or renaming to a taken name is a 409. Names already shared before V1.0.1 stay; the
  younger driver has no key (NULL) and picks a free name at the next rename.

### Fair play
- A driver with fewer than `LMU_GARAGE_NEW_DRIVER_REVIEW_LAPS` (10) official laps whose lap would beat
  the Garage16 Best of **other** drivers is held (`review_status = "held"`,
  `validation.fair_play`) until an admin clears it in the review queue — then its records and Discord
  announcements follow (`server/fair_play.py`, ADR-14). First laps on a combination and slower laps
  never wait. The admin queue shows the reason; `scripts/revalidate_laps.py` keeps such a hold.

### Web
- Security headers for the pages (Caddy): `Content-Security-Policy` (`script-src 'self'`,
  `frame-ancestors 'none'`, …), `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy`,
  `Permissions-Policy`. The API keeps its own headers.
- Page titles per page, meta description and Open Graph tags (link previews), `robots.txt`.
- Sign-up shows "Almost done — check your email"; signing in with an unconfirmed email opens the
  account page with a banner; `{code, message}` error answers are shown as text.
- Privacy policy (DE/EN): the PC's name per connected PC, deletion of unconfirmed sign-ups, the hashed
  email in the sign-in limits, the review of new drivers' record laps, encrypted credentials on the
  PC. `TERMS_VERSION = "2026-10-26"`.

### Desktop client 1.0.1
- The auth token and client secret are stored encrypted with the Windows Data Protection API
  (`client/secret_store.py`, ctypes, no new package); a plain file from 1.0.0 is rewritten encrypted
  on first read; credentials copied from another PC/user count as missing (connect again).
- Sends the computer name (Connected PCs) and its version at sign-in; understands
  `email_not_verified` and `terms_required` (a checkbox appears); warns once before a password would
  go over plain `http://` to another computer; links to the terms of use.
- Shows an optional "Garage16 vX is available — this version keeps working" notice
  (`GET /health` now reports `latest_client_version` / `update_available`,
  `LMU_GARAGE_LATEST_CLIENT_VERSION`).

### Releases, CI, small fixes
- `ci.yml` runs on pushes to `main` and pull requests only (it ran twice per release: branch and tag).
- Releases carry `SHA256SUMS.txt`; the release notes explain the SmartScreen warning.
- Deprecated Starlette names (`HTTP_413_REQUEST_ENTITY_TOO_LARGE`, `HTTP_422_UNPROCESSABLE_ENTITY`)
  replaced.

### Documentation
- `README.md` rewritten (what it is, use, develop, operate in one minute, documents, licence: all
  rights reserved); the technical reference moved to `docs/DEPLOYMENT.md`; `docs/README.md` indexes
  everything (German for drivers and the operator, English for the technical reference).
- `docs/ADMIN_GUIDE.md` 1338 → ~540 lines: the general update procedure, the V1.0.1 update with its
  way back, review queue / fair play, client release; the version-by-version update sections
  V0.9–V1.0 moved unchanged to `docs/archive/UPDATES_V0_9_BIS_V1_0.md`.
- Archived (`docs/archive/`): changelog entries up to V0.8.10, the V0.5 notes, the V0.9.9 reports,
  benchmark and mutation logs, the V0.9.3 open-source review, the old legal templates.
  `docs/V0_9_9_NAS_ANLEITUNG.md` → `docs/NAS_MESSUNGEN.md`.
- New runbooks: the quick-tunnel address changed, a driver cannot connect, a lap waits for fair play,
  locking an account / disconnecting a PC. Friends guide, data protection, architecture (ADR-13–15),
  rollback table, client build/install, web and alembic READMEs updated.

### Tests
- New: `tests/test_v101_accounts.py`, `tests/test_v101_fair_play.py`, `tests/test_v101_client.py`,
  `tests/test_v101_docs.py`, `tests/js/v101.test.mjs`; the OpenAPI contract snapshot updated
  (additive: three endpoints, new optional fields).

### Website and client pass (17.11.2026, same version number, archive rebuilt)
Built on the V1.0.1 archive of 26.10. after a review of the live site (`claude/website_analyse_2026_11_17.md`
in the project). No migration, no new dependency, no new `.env` entry.

- **Leaderboard:** a "Loading standings…" notice while the table loads (before, "Pick a track and
  class" stayed on screen for seconds although both were already chosen — it looked broken, most of
  all behind the tunnel); tracks and classes nobody has driven yet in their own group "No laps yet" at
  the end of the lists; every column has a heading (Status, Analysis) and P1's gap shows "—"; the
  table title uses the venue's name.
- **One name per track** (`web/js/tracks.js`): the dashboard, "Your Best", the activity feeds, the
  driver profile, the team records, the LapTime and Stint Analyzer showed LMU's own string
  ("Algarve International Circuit", "Autodromo Nazionale Monza") while the leaderboard said
  "Portimão (Algarve)", "Monza". Every page now shows the leaderboard's venue name (a venue with
  several layouts keeps the layout's name). Loaded once per page load from
  `/leaderboard/catalog/venues`; never blocks or breaks a page (falls back to LMU's string).
- **Silverstone** is one venue with its layouts ("Silverstone Grand Prix Circuit - ELMS",
  "Silverstone National Circuit") instead of two separate tracks (`server/catalog.py`).
- **Landing page:** "What Garage16 does" right after the hero and about the analysis — LapTime
  Analyzer, racing lines, Stint Analyzer, leaderboards — each with a small drawing (inline SVG, no
  files); a short lead text under the claim. **Recent activity shows sessions**, not ten single laps
  of the same driver: laps of one driver, car and track close together (90 min) are one row with the
  number of laps, the best lap (opens the analysis) and the records the session brought.
  `GET /leaderboard/recent` names those records per lap (`records`, additive field, one extra query).
- **Dashboard — "Your Best" per class:** one row per track **and class** (before: one row per track
  across every car, so a GT3 lap was hidden behind a Hypercar lap). Each row shows your time, that
  class's Garage16 Best with the gap (or "Your record"), and "vs. Garage16 Best →" opens exactly that
  pairing: your best lap in that car against the fastest lap of the same class on that track
  (`#/analysis?quick=wr&track=…&model=…&scope=class`; the server's `/telemetry/analysis/quick` already
  had `scope=class`). `GET /drivers/{id}/public` → `personal_records[]` carries `class_best_time`,
  `class_best_driver_id`, `class_best_driver_name` (additive, one query for all rows). A table over
  the full width; the team block below it.
- **Menu on short windows** (laptop, browser not full screen): more compact, and below 600 px height
  the whole menu scrolls as one instead of an inner scroll bar hiding Account/Download.
- **Client:** credentials from the environment without `LMU_GARAGE_API_URL` use the server baked into
  the package (was `http://localhost:8000`).
- **Tests:** window tests skip (not fail) where Python has no tkinter, the Discord-bot tests where
  discord.py is not installed (a server-only or system Python); in the release environment they run as
  before. New: `tests/js/v101_web.test.mjs` (EN/DE), records in `/leaderboard/recent`, Silverstone,
  bundled URL for environment credentials.
- `constraints.txt`: opentelemetry-api 1.45.2 (CVE-2026-126360; a dependency of a dependency, used only by
  the tooling — pip-audit flagged it on 17.11.).
- Checked, not changed: the Le Mans lap shown as 3:27.000 is LMU's own value — LMU's lap and sector
  times are stored in steps of a few milliseconds at that point of a session (the sectors 32.5390625 +
  81.171875 + 93.2890625 add up to exactly 207.0); the client reads them unchanged.

## V1.0.0 — Open for everyone: sign-up, legal pages, one-click downloads

Server `1.0.0`, desktop client `1.0.0`. **One additive migration (0018)**: two nullable columns on
`drivers` (`terms_accepted_at`, `terms_version`). Rollback: `alembic downgrade 0017`
(docs/ADMIN_GUIDE.md, "Neu in V1.0"). Old clients (from 0.5.3) keep uploading unchanged.

### Sign-up for everyone
- **`POST /accounts/signup`** — one form on the website: driver name, email, password, "I am at least
  16" and "I accept the terms of use and have read the privacy policy". Creates the account with
  email and password, records when and which version of the terms was accepted
  (`server/routers/legal.py` `TERMS_VERSION`), sends the verification email and signs the browser in.
  Before, a new driver registered, copied an auth token and a client secret shown once, then set an
  email and password in a second step.
- **`LMU_GARAGE_OPEN_REGISTRATION=1`** lets anybody sign up. A production server now needs a
  registration secret **or** this setting and still refuses to start with neither. With a secret
  and without open registration the form asks for the secret (as before).
- `POST /accounts/register` (token-only accounts) is kept for admins, scripts and tests; on an open
  production server without a secret it is refused, so it cannot bypass the terms.
- Minimum age `LMU_GARAGE_MIN_AGE` (default 16).

### The desktop client signs in with email and password (client 1.0.0)
- **`POST /accounts/desktop-login`** hands the client its credentials: a new auth token (one PC at a
  time — the previous one is disconnected and offers "Reconnect account…") and the account's
  existing client secret, so laps still waiting on the PC upload. Same generic error as the web
  login, rate limited like it, refuses locked accounts.
- The connect window asks for server address, email and password. Pasting token and secret is folded
  away under "Use an auth token instead (advanced)" (accounts without a password, servers older than
  1.0 — the window says so when the server does not know the new endpoint). Links in the window:
  create an account, setup guide, privacy & legal (only plain http(s) addresses are opened).

### Legal pages
- New page **Legal** (`#/legal`, menu and front page): legal notice (§ 5 DDG), privacy policy (GDPR
  Art. 13 — every stored field, purpose, legal basis, retention, recipients incl. Cloudflare,
  ALL-INKL, GitHub, Gmail, Discord, cookies and browser storage, rights), terms of use (minimum age,
  fair play, availability, liability, "not an official product of Studio 397 / LMU") and licences
  (fonts OFL, ApexDash MIT, shared-memory sources). German and English; the German text is binding.
- The operator's name, address and email come from `.env` (`LMU_GARAGE_OPERATOR_NAME`,
  `_ADDRESS` with `|` between lines, `_EMAIL`) through the new public `GET /legal` (sent with
  `X-Robots-Tag: noindex`), never from the public repository. Not configured = the page says so.
- The reverse proxy's access log (it contains IP addresses) is kept 7 days (`roll_keep_for 168h`),
  as the privacy policy states.

### Downloads and setup guide
- Without any `.env` entry the download buttons now fetch the installers of the newest GitHub
  release directly: `https://github.com/ry2en-X/garage16-lmu/releases/latest/download/Garage16-Client-Setup.exe`
  and `…/Garage16-Client-Analysis-Setup.exe` (`LMU_GARAGE_GITHUB_REPO`, empty = off; explicit
  `LMU_GARAGE_CLIENT_*_DOWNLOAD_URL` still win). `GET /health` also reports
  `client_release_page_url` (all files of the release, offered under the buttons) and
  `registration` (`open` / `secret` / `development`).
- New page **Setup guide** (`#/setup`): account → download → install (SmartScreen) → connect with
  email and password → LMU → see your times, plus the common problems. The sign-up form leads there,
  the download page points to it after a click, the client's connect window links it.
- Account page: rotating the token or secret now explains "Reconnect account…" in the Garage16
  window instead of a developer command.
- Validation errors from the server are shown as text in the web app (were "[object Object]").

### First CI run on GitHub (fixed before the release)
- `opentelemetry-api` 1.44.0 → **1.45.1** (CVE-2026-114603, found by pip-audit; pulled in by FastAPI).
- The release gate in `ci.yml` runs as root (`sudo -E`): the backup/restore drill extracts an Alpine
  root file system and reads the root-owned backups it makes, which the runner user cannot. The
  workspace is handed back to the runner afterwards.
- `tests/test_v097_operations.py` makes the read-only stored file writable before changing it (as
  root the 0440 never mattered). `tests/test_v100_analysis_units.py` keeps `server/telemetry_analysis.py`
  above its coverage floor.
- docs/ADMIN_GUIDE.md: the `/legal` check inside the container sends `X-Forwarded-Proto: https`.

### Tests
- `tests/test_v100_signup.py` (sign-up, confirmations, secret/open modes, the refused `/register`,
  desktop login incl. token replacement and locked accounts, `GET /legal`, start-up rule, download
  defaults tied to the installer names in `packaging/garage16.iss`), `tests/test_v100_client_login.py`
  (input checks, links, login against the real server over HTTP, a pre-1.0 server, the real Tk window
  signing in), `tests/js/v100.test.mjs` (legal page in both languages with and without operator,
  sign-up form, setup guide, download page). The permission matrix lists the three new public
  endpoints.

## V0.9.9 — Track limits you can follow, stored laps you can trust

Server `0.9.9`; desktop client unchanged (`0.9.4`). **One additive migration (0017)**: three nullable
lap columns (received size, upload request id, integrity MAC) and the `audit_events` table with an
append-only trigger. Rollback: `alembic downgrade 0016` (docs/ADMIN_GUIDE.md). Track-limits rule
version 3, decision version 1. Inventory and work log: `docs/V0_9_9_BESTANDSAUFNAHME.md`,
`docs/V0_9_9_PROTOKOLL.md`; what only the NAS can do: `docs/V0_9_9_NAS_ANLEITUNG.md`.

### V0.9.9.1 audit (before the first NAS update; same version number, archive rebuilt)
- Integrity cache: the file fingerprint includes `st_ctime_ns`. Before, a file changed in place with
  the same size and its mtime put back with `utime()` was answered "ok" from the cache for up to
  10 minutes (the full check always found it). Regression test included.
- Restore: step 4 compares the newest audit row of the fully validated staging database with the
  manifest's `audit_head` and aborts before anything is switched when they differ (a deleted last
  row leaves an intact-looking chain; a last row rewritten with the key verifies). Rules for older
  backups: no `audit_head` (V0.9.8) → not compared; `null` → no audit rows allowed; unreadable →
  refused (docs/RUNBOOKS.md 6). `consistency_check --audit-head`.
- Backups read `audit_head` from the dump itself: audit rows are also written outside the upload
  write lock (admin decisions, quarantine), so a separate query after `pg_dump` could record a row
  the dump does not contain — and the new restore check would refuse a sound backup.
- Mutation check: mutant descriptions are unique per function. Before, one equivalent-key text
  matched two mutants in four places (27 keys skipped 31 mutants); three of the skipped ones were not
  equivalent (all three are killed). Test `tests/test_v099_mutation_keys.py` guards it.

### V0.9.9.2 fix (found on the first real NAS update, archive rebuilt again)
- With `LMU_GARAGE_REQUIRE_HTTPS=1` (the compose default) the HTTPS middleware answered the
  container healthcheck's plain-HTTP `GET /health/ready` with 400, so the server never became healthy
  and the reverse proxy never started. `/health/live` and `/health/ready` are now exempt like
  `/health` (they carry no details). Present since V0.9.8. Regression test
  `tests/test_v0992_healthcheck_https.py` ties the exempt paths to the probe in docker-compose.yml.

### V0.9.9.3 fix (found on the NAS with the first Le Mans session; same version number, archive rebuilt)
- **Uploads failed with 500 after the first backup.** `.garage16-write.lock` was created first by the
  backup's helper container (root) as root:root 0644; the server (user `garage`) opens it `O_RDWR`
  and got PermissionError on every upload (the client kept the laps and resent them after a manual
  `chown`). Three layers now: `docker/entrypoint.sh` creates the file as `garage` before the server
  starts; `scripts/backup.sh` and `docker/backup-loop.sh` give an existing file the owner of the
  telemetry directory before taking the lock; `server/write_lock.py` opens a lock file it cannot
  write read-only (flock needs no write access, shared and exclusive locks still exclude each other).
- **The track surface never appeared on real data** (`{"surface": null}` for every lap since V0.9.5).
  The sign of `path_lateral` was decided by which sign gives the smoother centre path; on real LMU
  data both are equally smooth (measured ratio 1.00–1.02 on 8 Le Mans laps, 1.15 required). The sign
  is now decided by the laps agreeing: with the right sign the centre paths of different laps lie on
  top of each other (12 Le Mans laps: 0.23 m scatter against 1.12 m with the wrong sign; required:
  factor 2). The smoothness test remains the fallback for a single lap; laps on exactly the same line
  are never guessed. `/telemetry/analysis/track-surface` now returns `"reason"`
  (`no_surface_data` / `sign_undecided` / `no_edges`) when there is no surface, the map's „Track"
  option shows it as a tooltip, and the surface's cache key / ETag carries `SURFACE_VERSION = 2` so
  browsers drop the old empty answer (the other analysis answers and `ANALYSIS_CACHE_VERSION` are
  unchanged). Tests: `tests/test_v0993_track_surface_real.py` (data shaped like the real laps; the
  old code returns no surface on it).

### P0 — the track-limit decision
- `server/lap_decision.py` (new) completes the pipeline input → time axis → position → track limits
  → decision → record. Status VALID / INVALID / REVIEW, mapped onto the existing API fields
  (`is_valid`, `validation.status`, `review_status`). Every verdict records the validator versions,
  the signals used, data quality, every finding with place and source, and the evidence (a count of
  independent signal families — explicitly not a probability).
- **Missing data is no longer "clean"**: a lap without track-limits signals (`not_verifiable`, was
  INFO), a missing/empty/unreadable signal its client version always records (`signals_missing`),
  a single signal family (`insufficient_evidence`), gaps > 1 s, < 10 frames/s, lap distance jumping
  ahead, speed and distance disagreeing by > 5 %, > 8 G over 0.1 s, frozen samples, the same file
  from another driver, a crashing check (was: VALID) — all REVIEW. One wrong sample never invalidates.
- **REVIEW holds** (`review_status = held`, `server/official.py`): stored and analysable, not in
  leaderboards, team bests, profiles or records until an admin clears it; then PR / WR / TEAM_BEST are
  evaluated under the record lock. Laps flagged before V0.9.9 (`pending`) keep counting — no stored
  record changes with the update. A late finding from the next lap holds only a lap that is being
  uploaded, never one already stored. `revalidate_laps` reports how many laps hold a record and
  holds only with `--apply --hold`; every change is audited.
- Unknown sector times are shown, no longer a review reason (they say nothing about validity).
- Synthetic laps used by older tests ramp their speed (the old instant 75→38 m/s jump was an
  impossible deceleration).

### P0 — reference set and measured error rates
- `tests/helpers/trackcheck.py`: laps whose truth comes from geometry (FIA: all four wheels beyond
  the white line), three tracks, two cars, LMU strict / lenient / silent, recorded like the client;
  manipulations. `tests/helpers/trackcheck_cases.py`: 52 curated cases with expected result and
  reason; exact rule limits; hypothesis properties (noise, removed signals, reordering).
- `scripts/trackcheck_eval.py`: precision / recall / FPR / FNR with counts — curated 38 laps:
  precision 100 %, recall 83.3 %, FPR 0 %; generated grid 480 laps: 100 % / 75.0 % / 0 %. All false
  negatives are one kind (all four wheels on the kerb or a few cm beyond the line, LMU silent) —
  documented as a limit. `--labels labels.csv` measures the same on REAL laps (not done yet).

### P0 — protection of stored telemetry
- Size, request id and a server MAC (`LMU_GARAGE_INTEGRITY_KEY`, rotatable; derived from the secret
  key when not set) per lap; the original is read-only (0440). `server/integrity.py` checks size,
  sha256 (cached per unchanged file ≤ 10 min) and MAC on every analysis access; failures quarantine
  the lap (out of leaderboards, visible to owner/admin only), with an audit event carrying the
  request id. Admin → Integrität: diagnosis and release after the file verifies again.
- `scripts/integrity_check.py`: full sweep (dry run), `--apply` quarantine, `--backfill` signing of
  laps stored before V0.9.9, `--resign` after a key rotation, `--release-verified`, `--audit`.
- `consistency_check --deep` (and therefore every restore) also checks sizes, MACs and the audit
  chain: a backup whose lap times or hashes were edited is refused.
- Upload hardening: temp file `O_EXCL | O_NOFOLLOW` (0600), the storage folder must resolve inside the
  root (a planted symlink is refused, 500 `STORAGE_UNAVAILABLE`), decoding limits before any column
  is read (200,000 rows, 1,000 columns, 512 MiB uncompressed — upload, analysis, health).
- `server/audit.py`: chained HMAC audit log (accepted, quarantined, released, reviewed, invalidated,
  revalidated, backfilled, re-signed, sweeps, driver deleted, pruned), trigger against UPDATE,
  365-day retention, head in every backup manifest. Driver deletion clears the driver id only.
- Export: `lap_files` (sha256, size, request id per lap) and `audit_events`.

### P1 — quality
- Mutation targets for the decision, linking, integrity, quarantine, official laps, upload limits and
  the audit chain (`scripts/mutation_check.py`); results in docs/QUALITY.md.
- `release_check --verify-archive OUT.zip`: the gate again on a clean copy extracted from the archive,
  after checking its sha256 and that its tree hash is the tested one. CI runs both.
- `scripts/bench_compare.py`: the same performance measurements on V0.9.8 and V0.9.9.
- New tests: `tests/test_v099_trackcheck_reference.py`, `tests/test_v099_decision.py`,
  `tests/test_v099_decision_units.py`, `tests/test_v099_integrity.py` (incl. a process killed while
  storing), `tests/test_v099_integrity_units.py`, `tests/test_v099_mutation_keys.py`, `tests/js/v099.test.mjs`.
- Coverage floors for `server/lap_decision.py`, `integrity.py`, `audit.py`, `official.py`,
  `request_context.py`.
- A test helper's seed-dependent `hash()` salt made two catalog uploads byte-identical about once in
  a thousand runs (present since before V0.9.8, reproduced with `PYTHONHASHSEED=695`): now a counter.
- `certifi` 2026.7.22 → 2026.10.19 (`constraints.txt`): pip-audit reported PYSEC-2026-4320.

### API (additive only)
- New admin endpoints: `GET /admin/integrity`, `POST /admin/laps/{id}/integrity`, `GET /admin/audit`,
  `GET /admin/audit/verify`; `GET /admin/review?status=` also accepts `held` / `quarantined`
  (`pending` lists both waiting kinds); `POST /admin/laps/{id}/review` answers with `records`.
- Export: `lap_files`, `audit_events`. Upload response unchanged.
- A changed lap file answers the analysis with 422 and the text code `INTEGRITY_FAILED` (the status
  it already used for unusable files); a missing one with 404 as before.

### Known limits
- No labelled real laps yet: every threshold is physically or V0.9.7-derived, not tuned on real data.
- A client that forges a consistent file (every signal) cannot be detected; a hash only proves what
  happened after the upload; whoever holds the integrity key and the database can rewrite the chain.
- Performance measured in the build container, not on the NAS (docs/V0_9_9_PROTOKOLL.md).

## V0.9.8 — Trustworthy operation: proxy trust, consistent backups, safe restores, a stricter gate

Server `0.9.8`; desktop client unchanged (`0.9.4`). **No database migration.** Analysis results
unchanged for normal laps; cache version 8 and per-lap figures version 5 because laps with a long
speed gap are now measured correctly (below) — `consistency_check --recompute-figures` brings older
stored figures up to date (optional; the analysis itself always computes fresh). Prompted by a review that set the P0 order: rate
limits that cannot be bypassed, backups from one moment, restores that cannot leave a half-replaced
system — then the release gate, operations and documentation.

### P0.1 — the client address the rate limits count
- `server/client_ip.py`: `X-Forwarded-For` is believed only from configured proxies
  (`LMU_GARAGE_TRUSTED_PROXIES`, docker-compose: `private`) and read **from the right** — a header
  the visitor writes himself can no longer change the address (before: its first entry was taken,
  `X-Forwarded-For: 1.2.3.<n>` walked around every limit). Caddy trusts only private ranges.
- Tests: every chain shape, spoofed headers on direct requests, and through the real Caddy with the
  shipped Caddyfile for both variants (internet in front of Caddy; Cloudflare tunnel).

### P0.2 — consistent backups
- `server/write_lock.py`: changes to rows AND files (upload, driver deletion, removing orphans) hold a
  shared lock in the telemetry volume; a backup announces itself and takes it exclusively — new
  uploads get **503 `MAINTENANCE`** (the client keeps the lap and retries), running ones finish
  first. Dump and archive are made inside that window. The lock gives up on its own after
  `GARAGE16_BACKUP_LOCK_MAX_S` (1800 s); the server ignores a marker older than 2 h.
- `scripts/backup.sh` and the `backup` profile (`docker/backup-loop.sh`) write one folder
  `garage16_<stamp>/` (dump, archive, `manifest.json`: time, program version, schema revision, lap
  count, sizes, SHA-256) — built as `.partial`, checked (gzip, pg_dump's completion line, archive
  lists), published by one rename. An interrupted run publishes nothing.

### P0.3 — restores that end in the backup or the state before
- `scripts/restore.sh <backup-folder>`: check (manifest, hashes, schema known, no `../` paths) →
  load into a NEW database and volume → validate there (`alembic upgrade head`,
  `consistency_check --deep` with the stored paths mapped) → stop writers → swap the databases in
  ONE transaction → swap the files (old ones kept in `.pre_restore_<stamp>/`) → start → verify the
  running server. A failure in any step returns to the state before; a failing return says so (exit
  3, with the names of everything). `--rollback <stamp>`, `--cleanup <stamp>`; pre-V0.9.8 backups
  with `--allow-unverified`.
- **The drill** (`tests/test_v098_backup_restore.py`): the shipped scripts against a real PostgreSQL
  and real Alpine containers (chroots in mount namespaces, `tests/helpers/fake_docker.py`): uploads
  running during a backup (the archive and the dump always match lap for lap, checked
  independently), an interrupted and a killed backup, failing steps, the backup loop container, a
  restore with rollback and cleanup, a fault injected into **each** of the eight restore steps, a
  failing rollback, five kinds of damaged or foreign backups. 21 scenarios, all passing.

### Operations
- `/health/live` (process) and `/health/ready` (database + storage; 503 with what is missing; backup
  running and low disk space as warnings). Docker's healthcheck uses readiness. `/health` no longer
  shows database error texts (host, user) — they go to the log.
- Every error answer carries the request id (`{"detail", "request_id"}` + `X-Request-ID`); an
  unexpected error answers 500 without message or trace.
- Upload log: the 30 days hold whatever the upload frequency — removed at start and at most hourly,
  in batches of 5000. Operations view: filter by client version, uploads per hour, **drill-down by
  request id**; reads at most 200 000 rows.
- Consistency check: impossible metadata (lap time, sectors, hash, track/car), figures from a newer
  server; deleting re-checks every file against the database right before; a copy of the storage
  under another path (`--storage-dir`, `--stored-prefix`, what the restore validates); deleting asks
  first (`--yes` to skip).
- Key rotation without logging anybody out: `LMU_GARAGE_SECRET_KEY=<new>,<old>` and
  `scripts/rotate_secret_key.py` (dry run, `--apply`, idempotent).
- The data export holds everything the database has about the driver (e-mail, teams, sessions,
  records, upload log), the upload log is deleted with the driver.

### Release gate
- New steps: linter (`ruff.toml`), static security analysis (bandit, medium + high — two justified
  exceptions marked in place), known vulnerabilities of every pinned package (pip-audit — none),
  line AND branch coverage with floors per critical module (`tests/coverage_floors.json`; total 88 %
  lines / 77 % branches).
- With `--zip` the gate runs in a staged copy of exactly the released files and the archive is built
  from it (the tested tree is the published tree; `.zip.sha256` next to it).
- `scripts/mutation_check.py`: deliberately breaks the critical decisions (client address, records,
  distance axis, pedal crossings, write lock, rate limits) one change at a time — the tests must
  notice each one. **The first run let 16 of 46 and then 80 of 134 mutants survive**: ties (no test
  checked that a lap EQUAL to the current best is no new record), a team best of a driver in no
  team, rate-limit window boundaries, untested fallback paths of the distance axis. Each gap got a test
  (`tests/test_v098_mutation_gaps.py`, `tests/test_v098_axis_exact.py` with hand-computed exact
  values); the remaining survivors are listed with the reason they cannot change behaviour
  (`EQUIVALENT` in the script). Found on the way:
  * a stretch between two `lap_dist` updates with more than 0.5 s without a speed counted the gap as
    0 m/s, bunching the metres — such a stretch is now spread in time (also for infinite speeds);
  * a branch of the distance axis that could never run was removed.
- `tests/reference/openapi.json`: the reviewed API contract; any change fails until regenerated on
  purpose; what the shipped client 0.9.4 calls is checked separately.
- Concurrency: six uploads of one driver at once and two team members racing for the team best —
  every record type stays a strictly improving sequence.
- CI: a separate fast lint/security job; the drill runs in the gate (PostgreSQL service, Alpine
  root file system with pinned SHA-256).

### Documentation
- `docs/ARCHITECTURE.md` (components, data, upload lifecycle, analysis pipeline, nine architecture
  decisions), `docs/RUNBOOKS.md` (ten scenarios with diagnosis and safe steps), `docs/DATA_PROTECTION.md`
  (what is stored, why, how long, what deletion covers, the limits), `docs/QUALITY.md` (the gate,
  metric definitions with units and tolerances, coverage, **what is not proven yet**),
  `docs/ROLLBACK.md` rewritten for the new restore.

## V0.9.7 — Verifiable: release gate, reference laps, data quality, operations

Server `0.9.7`; desktop client unchanged (`0.9.4`). **One additive database migration (0016,
`upload_events`)**, runs on start. Analysis cache version 7 (corner points carry their resolution);
stored per-lap figures unchanged (version 4) — nothing to recompute.

Prompted by an external review of V0.9.6 whose test run could not start (pyarrow and jsdom were
missing in the reviewer's environment). Re-run here in a clean Python 3.12 environment with the
pinned versions (the Docker images' Python), tkinter, xvfb and caddy: **every test passes, none is
skipped** except three documented opt-in PyInstaller builds. The 11 failures of earlier local runs
were all `No module named 'tkinter'` — an environment gap that looked like test failures, exactly
what the new gate turns into an explicit infrastructure error.

### Release quality
- `scripts/release_check.py`: environment check (exit 6 when incomplete — "nothing was tested"),
  versions against `constraints.txt`, the whole suite with `GARAGE16_STRICT_TESTS=1` (a skip is a
  failure; opt-in checks and explicitly allowed reasons are listed), `release/TEST_REPORT.md` +
  JUnit XML + `environment.json` (versions, hash over the released files), `--zip` builds the archive
  only when green, with the report inside.
- `constraints.txt`: every package of the dependency tree pinned; used by both Docker images and CI
  (checked: wheels for Python 3.12 on x86_64 and aarch64). `scripts/setup_test_env.sh`.
- `.github/workflows/ci.yml`: the gate, the suite + migration chain on PostgreSQL 16, both Docker
  images; `build-client.yml` builds no client before it passed.
- `tests/test_v097_migrate_filled_db.py`: for EVERY older revision a database with a row in every
  table (filled from the reflected schema of that revision) is migrated to head; no row lost, every
  model of the current code reads every row.

### Analysis, verified against known values
- `tests/helpers/reference_lap.py` + `tests/test_v097_reference_laps.py`: laps simulated at 2 kHz
  (the truth), recorded like LMU and the client do, written as parquet, analysed by `load_lap` /
  `corner_analysis` / `lap_insights`. Pedal points and apex within one sample spacing, minimum speed
  within deceleration × frame time, exit speed within acceleration × frame time, corner times and
  the delta curve within 2 ms, the corners adding up to the lap-time difference — for five recording
  variants. Faults: duplicate polls (identical result), timestamp jitter, gaps on a straight and in
  a braking zone, NaN rows, a nonsense sample, a restart, a truncated file.
- `tests/reference/v097_golden.json`: the results on the reference laps with
  `ANALYSIS_CACHE_VERSION` / `INSIGHTS_VERSION` — results that change without a version bump fail.
- **Fixed** (found by the reference laps): three NaN speed samples made `corner_analysis` find **no
  corner at all** (the NaN spread through the smoothing) — detection now interpolates over the
  samples that have a speed, and the distance axis bridges NaN gaps up to 0.5 s (the channel
  itself stays as recorded). A **restart inside a file** (time jumping back) was silently cut to its
  first part and analysed as the lap — now refused with a clear message.
- **Fixed** (found by running the suite against a PostgreSQL whose time zone was Europe/Berlin):
  aware UTC datetimes were converted to the server's zone before being stored in the
  zone-less columns — an expired password-reset token was accepted. Sessions to PostgreSQL now run in
  UTC (`server/database.py`), whatever the database server is set to (the official image defaults
  to UTC, so the NAS was not affected).
- **Measuring accuracy per point**: every braking / turn-in / apex / throttle point of a corner
  carries `res` — the distance between the two recorded samples around it (normally speed / frame
  rate, wider where samples are missing). The reference tests check that it covers the true error.

### Data quality
- `server/telemetry_health.py`, `GET /telemetry/analysis/laps/{id}/health` (same access rule as
  every analysis endpoint, cached by file hash): channels (and blank ones), NaN share, frame rate and
  repeated polls, gaps (where), time and lap distance only forward, `lap_dist` updates per second,
  speed integral vs lap distance, recorded duration vs lap time, implausible values, resolution of a
  braking point; each check ok / warn / bad with its numbers; the lap's validity next to it.
- LapTime Analyzer: tile **"Data quality"** (both laps side by side), one line in the lap summary.

### Hints and statistics
- "Where to find time": a braking / throttle difference becomes a hint only when it is larger than
  the threshold AND the measuring accuracy of both points; the hint shows it (± x m).
- Corners tile: order **along the lap / biggest loss first**, each corner's share of the loss.
- Stint Analyzer: Sen's 95 % range of every degradation slope, 95 % ranges (t distribution) of the
  tyre and fuel effects of the separated model ("not distinguishable from zero" said when so),
  **consistency with the trend removed** (robust spread around the Theil–Sen line).

### Operations
- `upload_events` (migration 0016): every upload attempt — accepted / duplicate / rejected / error,
  status, reason (e.g. `UPDATE_REQUIRED`), request id (the client's `X-Request-ID` is kept when it
  looks like one), lap id, client version, size, duration; 30 days; deleted with the driver.
  JSON logs carry `request_id`, `lap_id`, `driver_id`, `outcome`, `duration_ms`.
- `GET /admin/operations`, `GET /admin/consistency`; admin tab **"Operations"**.
- `server/consistency.py` + `scripts/consistency_check.py`: missing / outside-storage / orphaned
  files, stale upload temps, unreadable files and sha256 mismatches (`--deep`), laps without a
  verdict, figures from an older analysis, rows pointing at nothing. Dry run by default; only
  `--delete-orphans` and `--recompute-figures` change something. Run it after every restore.
- `tests/test_v097_permissions.py`: from the app's OpenAPI description — everything but a documented
  public list refuses anonymous calls; every admin call refuses drivers and wrong tokens; every team
  call refuses outsiders, management refuses plain members; upload size and envelope limits.
- `scripts/load_test.py` (read-only, stdlib) and an in-process run of it in the suite.

## V0.9.6 — Clearer, simpler, faster: an overview & usability pass

Server `0.9.6`; desktop client unchanged (`0.9.4`). **No database migration**, **no new Python or
JS dependency** (two self-hosted fonts, SIL Open Font License). The analysis results are unchanged
(cache version 6, per-lap figures version 4).

Every page was checked in a browser (desktop 1440 px and phone 390 px) with a test data set
(3 drivers, a team, 25 laps on two tracks); the findings and what was done:

### Navigation & look
- **Menu in groups**: My garage (Dashboard, Teams) · Analysis (LapTime Analyzer, Stint Analyzer) ·
  Racing (Leaderboard, Circuits); Account and Download at the foot. On the phone the top bar stays
  at the top while scrolling.
- One name everywhere: **Garage16** (menu, phone bar, browser tab — was "LMU Garage").
- **Fonts are served by Garage16 itself** (`web/fonts`: Oswald, IBM Plex Mono; SIL OFL 1.1) instead
  of Google Fonts — no third-party request for visitors (DSGVO), works offline in the desktop app,
  one connection less through the tunnel.
- Headings have room above them; content may use up to 1120 px; wide tables scroll inside their
  box on a phone and no page is wider than the screen (checked on every page at 390 px).

### Faster
- **Pages are loaded when they are opened** (`web/js/router.js`, dynamic `import()`): the start page
  loads 9 JS files (~130 KB) instead of 50 (~580 KB); a page module is fetched once and cached.
  The page is cleared only after its module arrived (no blank flash), a click on another page while
  one loads wins.
- Dashboard: profile, teams and laps in parallel, team records in parallel (were one after the other).
- Team page: the five sections load in parallel into their places.
- Dashboard lap list: filtered in the browser — one request instead of one per filter.
- Caddy compresses with **zstd** (falls back to gzip).

### Clearer
- **Record feeds** (dashboard, driver profile, team page; new `web/js/activity.js`): one row per
  lap with every record it set as a badge (a new Garage16 Best used to appear two or three times —
  as Garage16 Best, team best and personal best), the car model instead of the livery name, and the
  lap time opens the lap in the analysis. The API adds `lap_id` and `car_model` to
  `/drivers/{id}/public` (`recent_activity`), `/teams/{id}/activity` and `/leaderboard/recent`.
- **Dashboard**: quick actions (Analyse last lap · You vs. Garage16 Best · Stint Analyzer); your
  best per track with the car and "vs. Garage16 Best"; your team next to it (or how to join one);
  the lap list with a track menu and "valid laps only" instead of two free-text fields and a button.
- **Leaderboard**: opens on the busiest track and its busiest class with the table already shown;
  every change reloads at once (no "Search" button); the choice is kept in the address
  (`#/leaderboard?track=…&class=…`) so a link opens the same table; new column: gap to P1;
  sectors in seconds. The team leaderboard on the team page opens the same way.
- **Team page**: one order — overview (members, team bests, PBs this week, then laps / PBs / active
  drivers / new team bests for the chosen period: one card, was two with the same numbers), team
  bests, team leaderboard, activity, members, invitations; **team settings folded** at the end.
- **Account page**: who you are and **Sign out** at the top, then name, e-mail & password, Discord;
  the desktop-client credentials with the browser sessions, and data export / deletion **folded**.
- **Landing page**: the main button is what the visitor most likely wants (signed in: your garage;
  otherwise: get Garage16); the duplicate link list is gone; car models instead of livery names.
- **LapTime Analyzer**: the introduction text only while choosing laps; one compact toolbar. The
  **lap picker** groups the laps per track (most recent first, open), marks the fastest valid lap,
  a click on the row selects it, the "basic data" note only appears when such a lap is listed and
  a single tab is not shown as a choice.
- **Circuits**: tracks with laps as cards (busiest first), the tracks nobody drove yet as one
  compact list instead of a dozen empty cards.

## V0.9.5 — LapTime Analyzer on a grid; every channel at telemetry rate

Server `0.9.5`; desktop client unchanged (`0.9.4`). **No database migration** (head stays `0015`),
**no new dependency**. The analysis window of the "Client + analysis app" edition loads the page
from the server and shows the new grid without a client update.

### Grid layout of the LapTime Analyzer
- "Customise view" turns the page into a **grid editor**: 12 columns, rows of 40 px; every tile has
  a cell position and size (`x, y, w, h`). Layout format version 2 in `localStorage`; a version-1
  layout (V0.9.1–V0.9.4) is migrated once, flowed in its old order (small 4 / medium 6 / large 12
  columns).
- **Move** a tile by its handle ⠿: a ghost shows the cells (green = free, amber = the tiles in the
  way move down). **Resize** at the corner ◢. Keyboard: arrow keys move by one cell (↑/↓ swap with
  the tile above/below), Shift + arrows resize. Tiles never overlap; gaps stay where the user
  leaves them; **Close gaps** moves everything up.
- **Tile picker** (side panel): *What for?* (lap time & delta, driver inputs, corners & line,
  forces acting on the car, tyres & brakes, suspension & dampers, engine & energy, driver aids,
  track & track limits, all values) → *Kind* (chart along the lap, histogram, map, special
  diagram, table, analysis) → the tiles, with search. Clicking a tile opens a **preview of the
  real tile with the open laps** and its ready-made sizes; then drag it onto the grid or place it
  automatically. A tile can also be dragged straight from the list by its grip.
- **Draw a box** on empty cells: the picker opens as an overlay with only the tiles that fit
  that box (each type has a minimum size), shows the preview at exactly that size and puts it
  there.
- Charts, delta, track map, racing lines and histograms fill their tile's height. Phones
  (≤ 700 px) stack the tiles in reading order.

### Racing lines on the track
- The racing-line map now shows the **track from above**: grass, asphalt between both edges, white
  edge lines and red/white **kerbs**, with the lines of every lap on top and a faint **car-width
  band around every lap** (before: Lap 1 only). Toggle "Track".
- Rebuilt on the server (`server/track_surface.py`, `GET /telemetry/analysis/track-surface`) from
  LMU's `path_lateral`, `track_edge` and the wheels' surface type of the laps on the map plus up to
  6 recent valid laps of the same layout (only geometry is returned): centre path = position minus
  `path_lateral`; each lap measures the edge on the side the car is on; kerbs from where wheels
  touched them. The sign of `path_lateral` is decided from the data (the right sign gives a smooth
  centre path), otherwise no surface. Gaps of one edge up to 300 m are interpolated between two
  measurements; an edge no lap ever drove along mirrors the other side and is drawn dashed
  ("estimated"). Laps from clients before 0.9.0 have no such data: the lines are shown as before.

### Channel analysis: accuracy
- **Distance axis at telemetry rate** (`server/distance_axis.py`, used by the analysis and the
  per-lap figures): `lap_dist` is LMU's scoring value; in rFactor 2 scoring is refreshed ~5×/s,
  telemetry ~50×/s. Up to V0.9.4 every sample whose `lap_dist` had not moved was dropped. Now
  every sample is kept: rows where `lap_dist` advances are anchors (LMU's distance, exactly); the
  rows between are placed by the integrated speed, scaled to meet the next anchor (linear in time
  where speed and `lap_dist` disagree). On a synthetic lap with 5 Hz `lap_dist`: all 4500 samples
  kept instead of 451, distance error ≤ 0.1 m, time-at-distance (delta) error 4 ms instead of
  33 ms. If LMU refreshes `lap_dist` every frame, the axis is identical to before.
  `orientation.<side>.distance_axis` in `/compare` reports rows / kept / anchors / duplicates /
  filled.
- **Repeated polls** of one telemetry frame (same game time; the client polls at 60 Hz) are
  dropped — they double-weighted moments in histograms and flattened derivatives (damper speed).
- **Corner metrics:** braking point, turn-in, back on throttle, 50 % and full throttle are
  interpolated between the two samples around the threshold (samples are ~1.5 m apart at speed);
  speed at braking at that point; peak deceleration = highest 0.1 s average (one kerb frame no
  longer sets it); mean deceleration and acceleration after the apex are means over time.
- Analysis cache version 6; per-lap figures version 4 (`scripts.revalidate_laps --apply` recomputes
  them for stored laps, validity unchanged).

## V0.9.4 — Two dashboards: LapTime Analyzer and Stint Analyzer

Server `0.9.4`; Desktop-Client `0.9.4`. **Database migration `0015`** (automatic at start,
additive, reversible: `alembic downgrade 0014`; changes no existing value and no lap's validity).
**No new dependency.** Includes every fix of V0.9.3.1. Clients 0.8.x–0.9.3.1 keep working; client
0.9.4 also works against a 0.9.3.1, 0.9.3 or 0.9.2 server (metadata level by level).

### Two dashboards
- The analysis is split into the **LapTime Analyzer** (`#/analysis` — one lap against another)
  and the **Stint Analyzer** (`#/stints` — whole sessions); `#/sessions` opens the Stint Analyzer.
  Menu, desktop client buttons (`LapTime Analyzer`, `Stint Analyzer`, `Analyse last lap`) and the
  analysis window (`--view stints`) follow.

### LapTime Analyzer
- **Racing lines** window: both laps' driven lines drawn equally on a map that zooms down to a
  single corner (wheel, buttons, two-finger pinch around the pinch centre, drag), car-width band,
  scale bar, per lap and corner braking point ▲, turn-in ◆ (estimated from the steering), apex ●,
  full throttle ▼; corner chips zoom to a corner and select it; "follow the cursor" / "show what
  the charts show"; a stretch from–to in metres. Zoomed into less than ~600 m, the lines are
  reloaded at 0.5 m (`GET /telemetry/analysis/line`, cached with ETag). At the cursor: Lap 2's
  lateral distance from Lap 1's line, inside of the nearest corner = + (`map.offset_b`,
  `map.side`; pair channel `line_offset` for a chart). The static map is cached; a cursor move
  only draws the dots.
- **Corner in detail**: time per phase (braking, turn-in, apex, exit, straight after) with
  boundaries common to both laps, so the phases add up exactly to the corner's delta; braking
  point, speed at braking, braking distance, maximum brake and pressure, peak deceleration,
  turn-in (estimate), trail braking, coasting, minimum speed, apex, back on throttle, 50 % and full
  throttle, exit speed, acceleration after the apex — both laps and the difference; the line
  distance at the apex; the same corner on the driver's earlier valid laps with this car
  (`GET /telemetry/analysis/corner-history`, from the database).
- **Coach** grouped by phase with each phase's measured time; "brake later" only when the
  reference lap is not slower at the apex and on the exit; with earlier laps: "in 4 of your last 5
  laps you braked later than here".
- **Sectors of the session**: every lap's sectors, the fastest driven sector of the session and of
  all your laps here, this lap's potential per sector (`GET /telemetry/analysis/lap-sectors`;
  the sum of best sectors is labelled as calculated).
- **Lap 3 / Lap 4** over the comparison (`c=` / `d=` in the URL, `extra=` on `/compare` and
  `/line`): dashed / dotted in every chart, their delta to the reference in the delta window, their
  lines on the map. The delta window can show the elapsed time instead.
- `ANALYSIS_CACHE_VERSION` 5.

### Stint Analyzer
- Sessions split into **stints** at pit stops (pit lane + standing still ≥ 1 s, LMU's pit stop
  counter) and — for laps without pit lane data — where new tyres (tread rising), refuelling (fuel
  rising) or another compound were **measured**; the reason is listed per stint. **Tyre sets**
  with their age in laps.
- **Lap flags from measured values**: invalid, out-lap, in-lap, pit stop, traffic (≥ 10 % of the
  lap < 1 s behind a car, LMU's time gap), blue flag, race start, wet, damage, laps missing,
  outlier (threshold adjustable). Filters switch what counts as clean.
- Pace: rolling average 3/5/10, best/median/mean, spread and MAD, best N in a row, thirds, trend.
  Tyres: degradation per stint (Theil–Sen, tyres and fuel together); tyres and fuel separated only
  with laps of different fuel at the same tyre age (several stints) — otherwise the page says why.
  Fuel and energy: per lap, per sector, by engine map, laps left, target consumption to the end of
  the session with an adjustable safety margin. Vehicle state: tyre temperature/pressure/tread,
  fuel load, track temperature against lap time as correlations ("not a cause"), damage. Traffic.
  Pit stops: pit lane and standing time, fuel added, tyres changed, compound (measured) and the
  time lost (estimate, needs 3 clean laps). Forecast of the last stint with a range (≥ 5 clean
  laps). **Strategy calculator** with measured values and the admin's **race rule sets**
  (Admin → Race rules; `race_rule_sets`, `GET/POST/PUT/DELETE /admin/rule-sets`,
  `GET /telemetry/analysis/stints/rule-sets`). Stint comparison, **you and your team mates**
  (valid laps only). Another driver's session shows their valid laps only.
- Endpoints: `GET /telemetry/analysis/stints/sessions|session|drivers|rule-sets` — database only.

### Fixes
- **Valid LMU laps were marked invalid** when LMU wrote the finished lap's time a few frames
  after the client finalised the lap: the lap went to the server with the previous lap's time
  (or none) and failed "telemetry duration does not match claimed lap_time". The client now
  recognises such a time (it does not fit the measured duration, or it is exactly the previous
  lap's) and takes LMU's time as soon as LMU reports it in the 6 s late window
  (`client/telemetry/parser.py` `_maybe_correct_lap_time`). Only LMU's own time is ever sent;
  `lap_report.lap_time_corrected` / `lap_time_unconfirmed` record what happened (kept in
  `laps.check_inputs`).
- **Valid LMU laps rejected for "lap_dist has implausible backward jumps" / "lap_dist (22m) does
  not match distance implied by the speed trace"** (found on the NAS: Fuji, Spa, Silverstone,
  client 0.9.2): LMU sometimes resets the lap distance a few frames BEFORE the lap number
  changes. The client waited for a reset that had already happened and put the first metres of
  the next lap at the end of the lap. Client 0.9.4 starts the next lap at that reset
  (`_early_reset_index`); the server reads such stored files correctly — a single reset in the
  last 3 s, after which the distance stays near zero, is the next lap's tail
  (`server/validation.py` `trailing_reset`), for the plausibility check, the track limits and
  the figures. The plausibility check's own reasons now count as automatic rejections, so
  `scripts.revalidate_laps --apply --restore` lets these laps count again (into the review
  queue). Laps an admin, a report or a moderator rejected are never touched.
- **Sector times LMU writes late** no longer reject a lap: the client waits for them in the late
  window (`lap_report.sectors_late_s`); a split at or beyond the lap time counts as not yet
  updated; `[S1, 0, 0]` means "sectors unknown" and is no longer a plausibility failure;
  `sector_times_implausible` is a review finding (was: invalid), reported by the server itself
  too. Older clients' unexplained rejections (0.9.2: pit lane, no time or LMU's flag) stay invalid.
- The Stint Analyzer shows under every invalid lap why the server did not count it.

### Icon
- An angular "16" (every corner cut at 45°) in Garage16 red: Garage16.exe (Explorer, taskbar,
  Alt+Tab), the client window, the setup dialog, the installer and the website's favicon
  (`client/assets/`, `packaging/garage16.ico`, `web/favicon.*`, `client/icon.py`).

### Client 0.9.4 and stored figures
- Per sample: `in_pits`, `place`, `flag`, `time_gap_ahead`, `time_gap_behind` (appended to the
  extended columns). Per lap: `lap_context` (pit stops, place, compound index/name front/rear,
  tank size, highest dent severity, overheating, detached part, time left, max laps) — only what
  LMU reports; nothing guessed. New metadata level (0.9.4 → 0.9.3.1 → 0.9.3 → 0.9.2).
- Per-lap figures (insights v3): pit lane, measured refuelling and tyre change, traffic and blue
  flag shares, positions gained/lost, fuel per sector, energy left, engine map, the lap's corners.
  `python -m scripts.revalidate_laps --apply` computes them for stored laps (validity unchanged).
- `scripts/track_limits_probe.py` also records the Stint Analyzer's signals
  (docs/LMU_VERIFICATION_PROTOCOL.md, point (w)).

## V0.9.3.1 — Correction release: track limits in tiers, laps can be judged again

Server `0.9.3.1`; Desktop-Client `0.9.3.1`. **Database migration `0014`** (automatic at start,
reversible: `alembic downgrade 0013`; changes no lap's validity). **No new dependency.** Clients
0.8.x–0.9.3 keep working; client 0.9.3.1 also works against a 0.9.3 or 0.9.2 server.

Why: an external code review of V0.9.3 and research into LMU's documented behaviour
(docs/LMU_VERIFICATION_PROTOCOL.md, V0.9.3.1) showed that V0.9.3 rejected laps LMU itself
accepts. LMU detects track limits by track width, scores an offence 2–5 s after rejoining by
the time gained, and keeps the score in "steps" that are only a fraction of a warning point.

### Track limits: LMU verdict, Garage16 rules and measurements are kept apart
- Every rule now has a **tier** (`server/track_limits.py` `RULES`): **lmu** (LMU's own
  `mLapInvalidated` / `mCountLapFlag` — invalidates), **rule** (pit lane, no lap time,
  implausible sectors — invalidates), **heuristic** (step counter rose, four wheels off, beyond
  the edge, shorter than usual, penalty, late reports — **review only**). Every stored finding
  carries its tier; the admin page shows the rule table with effect and whether the meaning
  is confirmed (`GET /admin/track-limits/rules`).
- **A rising step counter no longer invalidates a lap** (regression of V0.9.3). The finding
  shows the steps and, with LMU's ratio, the warning points (`steps / steps per point`) and
  whether the rise was at penalty level.
- **Four wheels on grass/dirt/gravel no longer invalidate** on their own (no time gained = no
  offence for LMU).
- A heuristic can be made to invalidate once real recordings confirm it:
  `LMU_GARAGE_TRACK_LIMITS_INVALIDATING` (comma-separated codes; default empty). LMU's own
  verdict cannot be weakened this way.

### Reports after the finish line reach the right lap
- **Client:** a finished lap is held for **6 s after the line** (`client/telemetry/lap_verdict.py`
  `LateWindow`) and records, change by change, what LMU reports meanwhile: the step counter, the
  finished lap's time (`mLastLapTime`), penalties, and when the new lap first left the track. Never
  held longer than 30 s of wall-clock time; saved at once when the session or the program ends.
- **Attribution** (`attribute_rise`): a rise < 1 s after the line belongs to the previous lap;
  < 6 s to the previous lap only if the new lap shows no excursion before it (and the signals to
  see one exist); otherwise it is **ambiguous and both laps go to review**; later it belongs to
  the current lap.
- **Server, for every client:** a step rise in the first seconds of lap N+1 gives lap N a
  `late_track_limits` finding (`server/lap_check.py` `link_neighbours`, in whichever order the
  two laps arrive; stored in `laps.validation.linked`, kept when re-judging).
- LMU replacing the lap time by a value <= 0 after the line (`game_last_lap_time_withdrawn`) is a
  review, not a rejection, until protocol (s) confirms what it means.

### Client and server decide by the same rules
- The client no longer rejects a lap for track-limits steps or penalties. It sends its **reason
  codes** (`lap_report.reasons`); a test pins the client's table to the server's.
- A reason the server checks itself from the file (LMU's flags, the step counter) is left to
  that check; the others (pit lane, no time, sectors) are taken over with the server's severity.
- A **0.9.3 client** rejected laps for steps without saying why. The server explains such a
  rejection from the client's game summary: only steps → **review** (`client_invalid_unexplained`,
  a pit-lane visit cannot be ruled out); LMU's flags or sectors → invalid; nothing → invalid as before.
- Uploader: steps down one metadata level at a time for older servers (0.9.3.1 → 0.9.3 → 0.9.2).

### Stored laps can be judged again (no new upload)
- `laps.check_inputs` (migration 0014) keeps what the verdict depends on beyond the telemetry
  file: client verdict and reasons, game summary, tail samples, late window, LMU's ratios. For
  existing laps the migration recovers what is still there (the 0.9.3 client's summary from the
  stored verdict); nothing changes validity.
- `scripts/revalidate_laps.py` reports per transition — valid → invalid, **invalid → valid
  (restorable)**, kept (admin/report decision), valid → review, admin decided. `--apply` stores
  verdicts only; `--apply --restore` lets restorable laps count again (they go to the review
  queue); `--apply --update-validity` applies rejections. Laps an admin decided and laps rejected
  by an admin or a report are never changed.

### Car identity
- **Cut model names:** `mVehicleModel` is 30 bytes, LMU cuts names after 29 characters. Known
  cases are repaired (`MODEL_NAME_FIXES`: Aston Martin Vantage AMR LMGT3, Chevrolet Corvette Z06
  LMGT3.R, Lamborghini Huracan LMGT3 Evo2); any other name of that length is marked "…". The
  client marks the cut where it still can see it (before stripping — a name cut right after a
  space would otherwise look complete). Identity rules version 2: run
  `scripts/car_identity_report.py` (dry run, then `--apply`) for stored laps.
- **Safer alias learning:** a pair seen only in grid lists (matched by slot id, so a torn read
  or a reused slot could pair the wrong strings) is used after **two** separate uploads report
  it; the lap's own car is trusted at once. Stored aliases are repaired with today's rules.
- **What to check by hand:** report, PC probe and admin page list identities with a cut name,
  with veh_filenames of different models, or with laps in different classes. Nothing is merged
  or split automatically; `SPEC_RULES` stays empty until real data needs it.

### Theoretical best
- **Car model required** (`422` without it) and **track conditions are not mixed silently**:
  each lap stores its wet share (`lap_stats.conditions`, from `mSurfaceType` = wet); laps are
  grouped dry / partly wet / wet / unknown. By default the group of the fastest lap is used; the
  others are counted and named; `conditions=all` mixes on request and says so. Grip level and
  track temperature are reported as a spread. Labelled "calculated" everywhere.

### Tools, docs, tests
- `scripts/track_limits_probe.py` (PC): writes every frame of the relevant LMU signals to CSV
  and prints every change with the time since the finish line — the instrument for the open
  points (r)–(v) of the verification protocol.
- Versions may have four parts (`0.9.3.1`); the minimum-client check compares all four.
- 70 new tests (`tests/test_v0931_*.py`, plus jsdom checks in `tests/js/v093_analysis.test.mjs`), among them the regressions the review asked for: steps
  rise but the lap stays valid, gravel without gain, late reports, identical/cut model names.
- Measured on a synthetic 15,000-sample lap: the track-limits check takes ~3.3 ms instead of
  ~1.7 ms per upload (the file read dominates at ~20 ms); the client's late window costs ~3 µs
  per frame for 6 s per lap.

## V0.9.3 — A car is its model, track limits checked by the server, much more analysis

Server `0.9.3`; Desktop-Client `0.9.3`. **Database migration `0013`** (automatic at start,
reversible — see "Upgrade"). **No new dependency** anywhere (server, clients, web). Clients
0.8.x–0.9.2 keep working; client 0.9.3 also works against a 0.9.2 server.

### A car is its model, not its livery
- **The bug:** `laps.car_model` held LMU's raw `veh_filename`, which is per livery — a real
  recording shows `397_25_499P` for "499P Custom Team 2025 #397" (car number, season, model). So
  the Penske #6 and #3 were two different cars in every leaderboard, record and comparison.
- **Now** (`server/car_identity.py`): the identity is LMU's own **model name**
  (`TelemInfoV01.mVehicleModel`, "brand & model", recorded by client 0.9.3); otherwise a model
  name **learned from the game**; otherwise the `veh_filename` without the car number, season
  kept ("499P (2025)"). Different specifications stay apart whenever LMU names them differently
  (an "Evo", a new aero version) — that is what LMU's model name exists for.
- **Learned automatically, never entered by hand:** every 0.9.3 upload also reports the
  `(veh_filename, model)` of every car the client saw in the session (read every ~10 s, a few
  strings per car). The server stores these aliases (`car_aliases`) and moves laps of older
  clients to the model name on its own. If the game ever reports two models for one group, the
  group is marked as a conflict, never used for grouping, and anything it moved goes back.
- Nothing is lost: each lap keeps what LMU sent (`car_model_raw`, `car_vehicle_model`) and how
  its identity was decided (`car_identity_source`). `scripts/car_identity_report.py` shows the
  identity of every combination and what the current rules would make of it (dry run; `--apply`).
- `scripts/car_identity_probe.py` (on the PC, LMU running) lists every car of the session as LMU
  reports it and as Garage16 would group it — the evidence for any future specification rule
  (`SPEC_RULES`, deliberately empty until real data needs it).
- The record lock (PostgreSQL) is now keyed by the car model — two liveries of one model racing
  for the same record were not serialised before.

### Track limits: decided by the server, from the telemetry, with reasons
- **The bug:** validity came from one client bit, and `count_lap_flag` was read with the wrong
  meaning — 1 means "count the lap but **not** its time" (rFactor 2 header; normal laps show 2)
  and was accepted. A real track-limits lap was only rejected by accident (sector fallback).
- **Now** (`server/track_limits.py`) the server checks the uploaded file itself with independent
  signals and keeps every finding with **where** on the lap it happened and **which signal** said
  so (`laps.validation`):
  - **invalid:** LMU's `lap_invalidated` set during the lap · `count_lap_flag` ≠ 2 at the end of
    the lap · LMU track-limits points added · all four wheels on grass/dirt/gravel at the same
    time (≥ 3 samples) · the client rejected the lap (pit lane) — the old AND with the client stays
  - **review** (counts until an admin decides): the car's centre clearly beyond LMU's
    approximate track edge · the lap clearly shorter than the layout's usual driven distance
    (possible shortcut) · a penalty during the lap
  - **info:** two or three wheels off; an implausible signal (ignored for that lap, never used to
    strike a lap); a lap without such signals (old client: "not verifiable", the client decides)
- The edges of the lap are handled explicitly: the first second (LMU may clear last lap's flags
  late) and the samples recorded after the lap-number flip (`tail_samples`, now sent by the
  client) are left out.
- The client applies the same corrected rules for its own opinion (`client/telemetry/parser.py`).
- **Admin → Track-limits review**: the queue of review laps with every finding; "Lap counts" /
  "Invalidate lap" (`GET /admin/review`, `POST /admin/laps/{id}/review`).
- `scripts/revalidate_laps.py` re-checks stored laps (dry run by default; `--apply` stores
  verdicts and figures without changing validity — flagged laps go to the review queue;
  `--update-validity` only on request).
- The driver sees why: the lap list's `invalid_reason` names the finding and its distance; the
  analysis window **Track limits & events** lists all of them; the track map marks them.

### Much more analysis data
- **New derived channels** (`server/telemetry_channels.py`): damper velocity per wheel (time
  derivative of the travel, **bump = +**, the sign decided per lap from the braking dive),
  heave/roll/pitch from the suspension, rake, tyre tread **inner/middle/outer** per wheel (from
  LMU's left/centre/right by wheel side), carcass and inner-layer temperature, **wheel slip**
  (spin +, lock −; rolling radius estimated per lap, convention-free), wheels off track, wheels on
  a kerb, distance to the track edge, track-limits points, lap invalidated, combined G.
- **Histograms** (`GET /telemetry/analysis/distribution`): share of the lap's **time** per bin,
  both laps on the same bins. Window **Damper histograms** (bump/rebound and low/high-speed shares
  at 25 mm/s) and window **Histogram** for any channel (e.g. time per gear).
- **Lap events** (`GET /telemetry/analysis/events`): the track-limits findings plus measured
  **lock-ups and wheelspin** per wheel, each with its place on the lap.
- **Window "Tyres & brakes"**: lap averages (from the stored per-lap figures — no request) and
  values at the cursor (loaded on demand).
- **Window "Where to find time"**: the corners where Lap 1 loses most, with plain hints built from
  measured values — later braking, more minimum speed, earlier full throttle, faster exit,
  over-braking, wheel lock-ups. Every sentence names the two values it compares.
- Standard layout: + "Where to find time" and "Track limits & events" (appended, so every earlier
  window keeps its place). The heavier windows are one click away in "Customise view".

### Per-lap figures, theoretical best, sessions
- At upload the server stores compact figures per lap (`laps.lap_stats`, ~1–4 KB): fuel, virtual
  energy and tyre use, full-throttle/braking/coasting/trail-braking shares, gear changes, peak G,
  tyre temperatures/pressures, hottest brake, session grip level and fixed setup — and a **time
  profile** every 50 m of the game's lap distance.
- **Theoretical best** (`GET /telemetry/analysis/theoretical-best`): the fastest time over every
  50 m stretch of your valid laps, summed — and from which laps they come; plus the simpler sector
  ideal. Segment times of a lap add up exactly to its lap time, so the ideal is a sum of really
  driven stretches.
- **New page "My sessions"** (`GET /telemetry/analysis/sessions`): consecutive laps grouped into
  stints with best, median, spread of the clean laps (without laps > 107 % of the median), laps
  within 0.5 s, the lap-time trend over the stint, fuel/energy/wear per lap, a bar per lap (click =
  analyse) and the theoretical best on request. From the database only — no lap file is opened.

### Client 0.9.3
- Records four values per sample from the player's scoring struct (`path_lateral`, `track_edge`,
  `count_lap_flag`, `num_penalties`, appended after every existing column), LMU's model name, the
  session's grid every ~10 s, `tail_samples`, the game's own lap state summary and the session's
  grip level / fixed setup / track-limits settings (the 13 bytes LMU named in `ScoringInfoV01`'s
  reserve; struct size unchanged).
- Sends the new fields; a 0.9.2 server answers "Extra inputs are not permitted" and the client
  uploads the same lap once more without them (remembered for the run) — nothing is rejected.

### Efficiency (measured on a synthetic 15,000-sample lap, not real LMU data)
- The upload still reads the lap file **once** for all checks (52 of 200 columns):
  ~23 ms instead of ~7 ms (validation only) per upload.
- Damper histograms of four wheels from a cold file: ~23 ms; lap events: ~14 ms; theoretical best
  over 200 laps: ~4 ms (database only). Histogram/event answers are a few KB and cached like
  `/compare` (ETag; `ANALYSIS_CACHE_VERSION = 4`).

### Open source and code reuse
Reviewed (licences actually read, commits pinned): uPlot, Chart.js, SortableJS (MIT — not taken:
the existing engine/editor already do this, no measurable gain), Apache Arrow (already used),
pyLMUSharedMemory (MIT — **facts** about LMU's fields taken, no code), TinyPedal (GPL-3 — only
read), oasis4/LMU-telemetry and alelosbrigia/LMU-telemetry (**non-commercial only — excluded**),
cfcata03/LMU-Dash (MIT — nothing better than what exists). No third-party code was copied; no
dependency was added. Details: `docs/OPEN_SOURCE_REVIEW_V0_9_3.md`, `THIRD_PARTY_NOTICES.md`.

### Upgrade
Migration `0013` adds the identity, verdict and figures columns and the `car_aliases` table, and
rewrites existing `car_model` values to "model (season)" while keeping the raw value; the
downgrade restores them byte for byte. Step-by-step with the NAS paths: `docs/ADMIN_GUIDE.md`,
"Neu in V0.9.3". The server image now contains the two new scripts.

### Verified
- Rules, edges and defensive paths of the track-limits check; identity derivation, alias learning
  and conflict undo; per-lap figures, theoretical best and sessions; damper sign recovery for both
  raw signs; a wheel locked on purpose found at its place; tread zones by wheel side — unit tests.
- End to end through the real client Uploader and the real API: invalid / review / clean laps,
  admin review, two car numbers on one leaderboard, regrouping of old laps, the uploader's fallback
  for an old server, histogram/events/theoretical-best/sessions endpoints, the report and
  re-check scripts — `tests/test_v093_*.py`.
- Migration `0013` on real rows (upgrade regroups, downgrade restores) on SQLite; the new windows,
  the sessions page, layout rules and German texts in jsdom — `tests/js/v093_analysis.test.mjs`.

### Not verified (needs real LMU on Windows — `docs/LMU_VERIFICATION_PROTOCOL.md` (m)–(q))
What LMU actually reports in `lap_invalidated`, `track_limits_steps`, `surface_type`,
`path_lateral`/`track_edge` and `mVehicleModel` during real track-limits situations and for real
specification variants; the 13 named `ScoringInfoV01` bytes. Every one of these signals is
range-checked, and an implausible one is ignored rather than trusted.

## V0.9.2 — Analysis app on the PC: "Client" or "Client + analysis app"

Server `0.9.2`; Desktop-Client `0.9.2`. **No migration** (Alembic unchanged). New dependency
**only in the "Client + analysis app" edition**: `pywebview` (`client/requirements-analysis.txt`);
server and plain client have none. Everything is additive; clients 0.8.x–0.9.1 keep working.

### Two downloads, one app
- The desktop client is built in **two editions** from the same code:
  - **Client** — `Garage16-Client-Setup.exe` (+ `Garage16-Portable.zip`): records and uploads
    laps; "Open analysis" opens the web analysis **in the browser**.
  - **Client + analysis app** — `Garage16-Client-Analysis-Setup.exe`
    (+ `Garage16-Analysis-Portable.zip`): the same, plus the analysis **in its own window**.
- Both install as the **same app** (one AppId, one folder): installing the other download over an
  existing installation switches the edition; login and recorded laps stay (they live outside the
  program folder). The installer clears only its own `_internal` folder first, so nothing of the
  analysis window is left behind when switching back.
- **GitHub**: the `build-client` workflow builds both editions (matrix). Pushing a tag
  `client-v0.9.2` additionally creates a **GitHub release with all four files** and a description
  saying which download is which.
- **Website**: a new **Download** page (menu, and a button on the start page) shows both editions
  **side by side** — "Client" | "Client + analysis app" — with what each does and what the app
  needs. The links come from the server: `LMU_GARAGE_CLIENT_DOWNLOAD_URL` (existing) and the new
  `LMU_GARAGE_CLIENT_APP_DOWNLOAD_URL`; a missing one shows as "not available yet", never as a
  broken button. A too-old client is sent to the download of **its own** edition (`/health` and the
  426 answer gained `client_app_download_url` / `app_download_url`).

### The analysis app window
- Client window: **"Open analysis"** and **"Analyse last lap"** (enabled once a lap was uploaded
  in this session — the uploader now remembers the server's lap id). In the analysis edition a tick
  box **"Open the analysis in my browser instead"** (saved in
  `client_config.json` as `analysis_view`).
- The window (`client/analysis_window.py`) shows **the same web analysis** of the driver's own
  server — so it is always in sync with the website (laps, comparisons, layout options), and every
  improvement of the web analysis is in the app too. Nothing is computed or stored locally.
- It runs as **its own process** (`Garage16.exe --analysis-window [--lap N]`): recording and
  uploading never wait for it, its memory is returned when it closes, at most one is open; asked for
  another lap it reopens on that lap; quitting the client closes it.
- **No extra login.** When the page in the window is not signed in, it asks the app; the client asks
  the server with its own credential (`POST /accounts/app-session`, Bearer token only — a browser
  cookie cannot do this) for an ordinary web session, and the page turns it into the usual HttpOnly
  cookie (`POST /accounts/app-session/activate`, same-origin only). The session is listed (and can be
  ended) under Account → sessions as "Garage16 desktop app (analysis window)". The window keeps its
  cookie, language and layout, so this happens about once a month.
- The bridge between page and app only answers pages of the driver's own server, exposes no
  credential to the page, and opens links to other sites in the default browser.
- **Fallbacks**: no WebView2 runtime, a broken installation or a window that fails to start → the
  analysis opens in the browser, and the client says why once. `Garage16.exe --analysis-selftest`
  reports whether the window can be shown (also run by the build on every packaged exe).

### Verified
- Server: app-session endpoints (Bearer only, same-origin activation, revoked/locked/expired, rate
  limit), download links in `/health` and the 426 answer — `tests/test_v092_app_session.py`.
- Client: edition, preference, launcher, bridge, fallbacks, uploader, the real Tk window under Xvfb
  — `tests/test_v092_analysis_app.py`. Web: sign-in inside the app and the download page (jsdom) —
  `tests/js/desktop_app.test.mjs`; German texts complete.
- **The real window, end to end**: the real `client/analysis_window.py` with real pywebview (Qt
  WebEngine backend under Xvfb — the Linux engine, not WebView2) against the real stack (app + real
  Caddy, seeded laps): it opened on the lap, signed itself in without any input, showed the standard
  layout with drawn charts; the session cookie was not readable by scripts, no token was stored in
  the page, the page saw only the three bridge methods; a second start reused the cookie (one app
  session on the server in total).
- **Real PyInstaller builds of both editions** (Linux): the edition is baked in, the plain client
  contains no pywebview, the analysis edition's packaged engine imports (`--analysis-selftest`).
- Download page in a real Chromium, English and German, desktop and phone: both cards side by side
  (stacked on the phone), no sideways scrolling.

### Not verified
Implemented but requires a real Windows run: the Windows build of the analysis edition (pythonnet
+ WebView2 in the PyInstaller bundle — the CI build's `--analysis-selftest` is its first real check),
the window on WebView2 itself, the Inno Setup edition switch, and the GitHub release job (first run
on the first `client-v*` tag). The repository is private: friends cannot download release files from
it — put the two setup files on the webspace (`download.garage16.de`) and set the two `.env` links.

## V0.9.1 — Analysis: compare in two clicks, your own layout, much less data moved

Server `0.9.1`; Desktop-Client `0.9.1`. **No migration** (Alembic is unchanged), no new
dependency. Laps from clients 0.8.x and 0.9.0 keep working; clients older than 0.9.1 can still
upload. The V0.9.0 API is unchanged — everything below is additive.

### Analysis page (web)
- **"Compare lap"** (German: "Runde vergleichen") opens a dialog **"Compare with"**: *Garage16 Best*,
  *Team Best*, *My Best* or a *Lap ID*. The server decides what each means (`server/analysis_reference.py`);
  a missing reference is a plain message ("No Team Best available.", "Lap not found.",
  "This lap cannot be analysed.") and **never** a substitute lap. If you already
  are the best, the dialog says so and offers the next-fastest lap (one more click) — the same
  lap is never loaded twice. No raw error or stack trace is ever shown.
- **"WR" is called "Garage16 Best"** in the interface: Garage16 has no official world record,
  it is the fastest valid lap *on Garage16* for the same track layout and car — exactly the rule
  that produces the `WR` record events (`server/records.py`), so the two cannot disagree.
- **"You vs. Garage16 Best / Team Best / My Best"** on the analysis start page (and the circuit
  library's "Compare my lap with it") pair your best lap with the right reference in one step.
- **The Garage16 standard layout** is what everyone sees first: summary, delta, corners, speed,
  throttle, brake, track map (coloured by delta), values at the cursor, car physics. Nothing to set up.
  **"Customise view"** (quiet button) opens the editor: reorder windows (drag handle or ↑/↓),
  size (small/medium/large), which channel a window shows, colours (lap 1, lap 2, delta, and per
  window), add/remove windows, **"Restore standard layout"**. Stored in *this browser only*
  (`localStorage`, versioned, migratable) — no database field, no telemetry in the browser. A broken
  or hand-edited value falls back to the standard layout; colours can only be `#rrggbb`, so the
  defaults cannot be broken by a stored configuration. Users of V0.9.0 keep their channel selection
  (it becomes their first layout).
- **Windows can only be moved by their handle in edit mode**, so dragging a chart still pans it.
  The grid is pure CSS (12 columns; desktop / laptop / tablet / phone breakpoints), so windows
  cannot overlap or push the page sideways.
- **Delta**: biggest gain and biggest loss — the steepest change of the delta over a corner-sized
  stretch (1/40 of the lap, 100–400 m: ~340 m at Le Mans), with its position (click = move the cursor
  there) — and the net result (signs: see below). **Sectors**: per-sector difference, total, best and
  worst sector marked; "biggest gain" / "biggest loss" only where a sector really gained / lost.
- **Touch**: tap (on lifting the finger) = cursor, sideways drag = scrub, vertical drag = the page
  scrolls (the cursor stays put), hold = pin, double tap = reset zoom, two fingers = pinch zoom + pan.
- **Record names**: the server's record types are shown as words, never as codes ("WR" → *Garage16
  Best*, "TEAM_BEST" → *Team Best*, "PR" → *Personal best*), and the team feed no longer calls a
  Garage16 best a "world record" (spec §51).
- **Phones**: no page can be pushed sideways any more — wide tables scroll inside themselves (the
  dashboard, team and driver pages overflowed by up to ~200 px since before V0.9). Track search
  fields are wide enough to show their whole hint.
- **Zoom refinement**: the first load is a coarse overview of the whole lap. Zoom in far (at least 2x,
  fewer than 600 overview points inside the view) and, once the gesture has settled (300 ms), the page
  fetches just that stretch — the view plus a margin, at a finer step, never more than ~2,000 points —
  and swaps it in under the same view and cursor. Panning out of the stretch shows the overview *at
  once* (never a half-empty chart) and fetches the next stretch; zooming out returns to the overview.
  Only the **view** can trigger a request, never the cursor. Summary, sector table, "biggest gain/loss"
  and the track map always stay on the whole-lap overview. Stretches already fetched are remembered
  (6, in memory), a failed request leaves the overview in place without an error screen, and after a
  "too many requests" answer (429) the page stops asking for a minute. A "biggest gain/loss" click
  outside the visible stretch moves the view there first.
- **One cursor, no requests**: moving it (mouse, touch, arrow keys) never calls the server. Requests
  happen on open, when a window needs a channel that is not loaded yet, and when the laps change.
- Leaderboard "Analyse" now opens that lap as **Lap 1** (then "Compare lap"); the circuit
  card shows **My Best**.
- **One sign convention in the whole analysis: Lap 1 minus Lap 2.** Negative = Lap 1 faster (time
  gained, green), positive = Lap 1 slower (time lost, red) — the delta chart, the sector table, the lap
  difference, "Biggest gain" (shown as e.g. −0.182 s), "Biggest loss" (+0.317 s) and "Net" (the lap's
  real difference, so the same number as the end of the delta curve). Captions say so on screen.

### Language: English by default, German on request
- **First visit: a popup "Choose your language · Sprache wählen"** with *English* and *Deutsch* (the
  browser's language is pre-selected; there is no way to skip it). **Menu: an EN | DE switch** at any
  time. The choice is kept in this browser (`localStorage`, key `garage16_language`); changing it
  reloads the page, so no stale text stays on screen.
- **The whole web interface is translated** — menu, every page, the analysis, dialogs, notices,
  tooltips, confirm questions, dates (formatted for the chosen language). The English text in the code
  is the key (`t("Choose Lap 1")`, `tp("{0} lap", "{0} laps", n)`; `web/js/i18n.js`), the German texts
  are in `web/js/i18n/de.js`. A text without a German entry is shown in English, never as a key.
- **Not translated, on purpose:** error messages the *server* sends (they stay English), driver /
  track / car names, the channel names, units and groups the server defines for the analysis, lap
  times (m:ss.mmm in both languages). The Discord bot is unchanged.
- **Safety net:** `scripts/i18n_check.py` and `tests/test_i18n.py` fail the build on a text without a
  German entry, a stale entry, differing `{0}` placeholders, a file that calls `t()` without importing
  it, or a local variable called `t` that would hide it (the conversion found and fixed three of those).
  `tests/js/i18n_pages.test.mjs` renders the real router and pages in German, in English and on a
  first visit (with the popup) and fails on a leftover English phrase, "{0}", "undefined" or an error.

### Server: less data read, computed and sent
- `GET /telemetry/analysis/compare` — new optional parameters `max_points` (upper bound of points
  per series; the grid is widened to fit, the raw file is untouched), `start`/`end` (one stretch
  of track, for zoom-in at a finer `step`). Without them the response is what V0.9.0 returned;
  `analysis_version` is added. Only the requested channels' parquet columns are read (plus the
  ~22 calibration columns every lap needs).
- **Cache** (`server/analysis_cache.py`): in-process LRU, bounded in entries (128), bytes (48 MiB)
  and age (15 min), versioned (`ANALYSIS_CACHE_VERSION` is part of every key), keyed by the laps'
  content hashes **in order** (the delta flips sign when the laps swap). It is an optimisation, never
  a data source or an authorisation shortcut: permissions and file checks run before every lookup,
  the rate limit counts cache hits too, and nothing depends on a hit (several workers each have
  their own). No Redis.
- **HTTP caching**: `ETag` + `If-None-Match` → `304` on `/compare` (`private, no-cache`, `Vary:
  Authorization, Cookie`) and, publicly cacheable, on the channel catalog. User-specific responses
  are never public. Caddy's existing `encode gzip` compresses the JSON; nothing is gzipped twice.
- The default of the `analysis` rate limit is raised from 300 to **900 per hour** (per driver and per IP,
  `LMU_GARAGE_RATELIMIT_ANALYSIS_MAX` still overrides): zoom refinement adds one request per settled
  zoom/pan. Cache hits and `304` answers count like any other request.
- `GET /telemetry/analysis/reference?kind=wr|team_best|my_best&lap_id=` and
  `GET /telemetry/analysis/quick?mode=…[&track_name=&car_model=]` (see the README).

### Upload and storage
- Client: the sha256 is computed in 1 MiB chunks and the file is **streamed** to the server with an
  exact `Content-Length` — `requests`' own `files=` copies the whole file into the request body
  (measured: 85 MiB peak for a 40 MiB file; now < 3 MiB). No extra gzip around the parquet.
- Server: validation runs **from the file** and reads only the columns it checks — measured on a
  synthetic lap ~6 ms / ~1.6 MiB instead of ~45 ms / ~17.5 MiB (Python heap; the process grew by
  ~46 MiB → ~6 MiB). Trade-off: damage confined to an extended-channel column is no longer caught at
  upload; the analysis reports such a lap as unreadable instead of crashing
  (`validate_telemetry_file(..., full_check=True)` restores the old behaviour).
- The upload endpoint now also removes its temp file when the final move fails, and removes the
  stored file after a failed commit only if no committed lap points at it.
- zstd level is pinned to **3** (pyarrow's default was 1). Row groups were benchmarked and *not*
  adopted. Parquet + zstd + float32 + BYTE_STREAM_SPLIT unchanged; content-addressed storage unchanged.
- **Local retention** (client): the local `.parquet` of an *uploaded* lap is deleted after **14 days**
  (`LMU_GARAGE_LOCAL_RETENTION_DAYS` or `local_retention_days` in `client_config.json`; `0` = never).
  Pending and rejected laps are never touched; the `.json` stays, so nothing is uploaded twice.
  **Consequence for admins:** after that the server (and its backup) holds the only copy.

### More exact, less load (final review)
A second pass over how times are computed, what the charts show and what every part costs. Numbers
are from the synthetic 14.4 km lap of the review stack and `scripts/benchmark_pipeline.py`, measured
on the build machine — not on a real LMU session, a real server or a real phone.

*More exact*
- **New window "Corners"** (in the standard layout, after the delta): every corner of the lap with
  its apex position, the time Lap 1 gained or lost there, where Lap 1 braked compared with Lap 2
  ("12 m later"), minimum and exit speed of both laps. Measured on the **raw samples** (full sample
  rate), not on the chart grid: braking point = first sample with ≥ 10 % brake, exit = first sample
  after the apex with ≥ 95 % throttle. Corners are found on the two laps' average speed (a drop of
  ≥ 10 km/h before and after, ≥ 120 m apart; a corner right at the start or finish line is still
  found). Each corner runs from its braking zone to the next one, the first also holds the straight
  from the line — so the **corners add up exactly to the lap-time difference** (the table's total
  equals "Net"). Most lost / most gained corner are marked; click or Enter zooms onto braking zone,
  apex and exit and pins the cursor at the apex. Whole lap only, both laps need speed.
- **Nothing hides between grid points any more**: the overview has ~1,500 points, so a brake tap or a
  kerb strike shorter than a grid cell could vanish. The server now also sends, for each cell whose
  raw samples reach further than the drawn line (by more than 2 % of the channel's range), the raw
  minimum and maximum; the chart draws them as thin vertical strokes. Usually a handful per lap,
  nothing at all once the grid is as fine as the samples.
- **"Biggest gain / loss" is the same on every screen**: it was found on the chart's grid, whose step
  depends on the window width, so a phone and a desktop could differ by a millisecond and a few
  metres. The server now measures it on a fixed 0.5 m grid (`delta_facts` in the response; the browser
  still computes it itself for older responses).
- **Precision per unit instead of "3 decimals for everything"**: km/h 1 decimal, % 2, rpm and gears
  whole numbers, G 3, times and the delta 3 (= milliseconds), … (`DECIMALS_BY_UNIT` in
  `server/telemetry_analysis.py`). No displayed digit is lost; the JSON got a little smaller
  (V0.9.0-style request 987 → 882 KiB raw). Tests prove: delta at the line = lap-time difference to
  the millisecond, corner times = the true ones to the millisecond, braking points exact to the sample.

*Less load*
- **Browser — cursor**: a chart now keeps its grid, axes and lines in a cached layer; moving the
  cursor only copies that layer and draws the cursor line (script per cursor move 1.56 → 0.36 ms for
  the standard layout). During a zoom or pan gesture the charts draw directly (copying a layer every
  frame would cost as much again) and cache the layer once the view has been still for 120 ms.
- **Browser — zoom refinement**: finer data is swapped **into** the existing charts instead of
  rebuilding every window: a zoom step that brings in finer data went from ~1,020 ms of main-thread
  work to ~3.5 ms, a pan step from ~90 ms to ~2.4 ms. Cursor, view, pin and a drag in progress are
  kept; the y-axes keep the whole lap's range, so nothing jumps. No leaks over repeated zooming
  (DOM nodes, listeners and heap stay flat).
- **Server**: the lap file is opened memory-mapped and only the needed columns are decoded; decoded
  laps stay in a small LRU (32 MiB per process, keyed by file, size, modification time and columns —
  a replaced file is never served stale). A standard-layout request whose laps are in that cache
  takes ~21 ms instead of ~50 ms (cold, in-process), a zoom refinement ~19 instead of ~41 ms — which
  is what a zoom or a second comparison with the same lap usually hits. Corner detection was made 4x cheaper before release (~4 ms for a
  14 km lap with 12 corners). Corners, spikes and facts are computed only for whole-lap requests.
- **Client (desktop app)**: each frame (60 per second) it used to copy LMU's **whole** shared-memory
  block (~317 KiB) to read three small parts of it; now it copies just those three (player
  telemetry, scoring info, player's scoring entry — < 10 KB) and checks that the game did not write
  in between (copy twice, compare). 351 → 11 µs per frame, i.e. ~2 % → ~0.07 % of a CPU core,
  measured on a synthetic memory block. The extended values of each sample are kept as `float32`
  (as they are written to the file anyway) instead of Python floats: a 15,000-sample lap holds
  ~26 MiB instead of ~98 MiB while recording. The written file is byte-for-byte the same.
- `ANALYSIS_CACHE_VERSION` is now **3** (precision, spikes, corners, `delta_facts`): old cache
  entries are never reused. All response fields are additive.

### Tests and tooling
- New: `tests/test_v091_pipeline.py`, `test_v091_analysis_api.py`, `test_v091_upload_atomicity.py`,
  `test_v091_retention.py`, `tests/js/analysis_layout.test.mjs`, `analysis_layout_page.test.mjs`;
  `scripts/benchmark_pipeline.py` (reproducible storage / hash / validation / endpoint benchmark).
- `tests/js/analysis_page.test.mjs` was adapted in three places (the standard layout asks for three
  channels instead of eight; the channel chooser became the layout editor; the circuit button uses
  the quick pairing) — reasons are written at the top of that file. No test was deleted.
- `tests/conftest.py` clears the analysis cache and the lap cache around every test.
- Final review: `tests/test_v091_accuracy.py` (spikes, corners against a lap with known corners,
  corner totals = lap-time difference, delta at the line, fixed-grid gain/loss, per-unit precision,
  cache version) and `tests/test_v091_client_load.py` (the three-part shared-memory read gives
  exactly the same frame as the full copy, declines the same cases, copies < 10 KB, survives torn
  reads; float32 samples write an identical lap file). jsdom tests check that a cursor move strokes
  no chart line, that zoom frames draw directly and that finer data does not rebuild a window.
- Full suite: **780 passed, 1 skipped** (the opt-in PyInstaller build), 0 failed — with the real
  Caddy 2.8.4 and Tk available.
- `tests/test_reverse_proxy.py` gained three tests that run the **real Caddy (2.8.4, local binary)** in
  front of the real app: the analysis JSON is gzip-compressed exactly once, `If-None-Match` still gives
  `304` through the proxy (also for the public channel catalog and never without a session), and the
  frontend files carry `Cache-Control: no-cache` with a working validator. The deployed image is
  `caddy:2-alpine`, i.e. a newer Caddy than the one the test used — it was not run against that image.

### Not verified
Implemented but requires real LMU verification / a real deployment: file sizes and CPU/RAM of real
recordings (all numbers here are from a synthetic lap), the client's three-part shared-memory read
against the running game (same structures and offsets as before, tested against a synthetic memory
block only), the corner thresholds (10 km/h, 120 m, 10 % brake, 95 % throttle) on real laps, the Cloudflare tunnel path in front of Caddy,
touch on real phones (verified in a real headless Chromium 153 with emulated touch, see below — not
on iOS Safari or real devices), PostgreSQL
behaviour of the new upload cleanup under real concurrency, and the sign conventions of TC, ABS,
motor map and brake migration, steering/G signs and map mirroring (still decided from each lap's own
data, as in V0.9.0 — not proven against the game).

### Review in a real browser (before release)
The whole frontend was driven in a real headless Chromium 153 against the real stack — the app,
real Caddy with `docker/Caddyfile`, laps uploaded by the real client (three drivers, a 14 km lap,
one lap from a 0.8.6 client, a team) — in English and German, desktop and phone (touch emulated).
That found what jsdom could not, all fixed before release:
- **Touch was broken on every real phone/tablet**: `setTimeout` was stored as an object method and
  browsers throw "Illegal invocation" when it is called that way (Node does not check). Every touch
  on a chart threw; a long press could never pin. Fixed, with a regression test that makes
  `setTimeout` as strict as a browser.
- A tap pinned the cursor (the browser's emulated mouse click after a touch); a scroll starting on
  a chart moved the cursor first. The emulated click is now ignored right after a touch, and a tap
  counts when the finger lifts without moving.
- "Biggest gain / loss" reported "+4.5 s at 7.2 km" for a lap that loses time steadily — the largest
  rise between ANY two points is most of the lap. Now the steepest change over a corner-sized stretch.
- The sector table called the least-bad of three losing sectors "biggest gain" and tinted it green.
- A drag (pan) was cut off halfway when finer zoom data arrived or the overview came back, because
  the charts were rebuilt under the pointer. The data is now swapped *into* the existing charts
  (see "More exact, less load" above), so nothing is rebuilt and the drag simply continues.
- "You vs. Garage16 Best" without a track (it uses the track of the latest lap) said "you already
  drive the fastest lap" without saying where; it now names the track and offers that lap alone.
- Raw record codes and "world record" (above), page overflow on phones, cut-off search hints,
  "—" instead of 0:00.000 at the start of the lap in the values table.
- The test suite left a database and up to ~20 MB of test telemetry in the temp folder per run (since
  before V0.9); `tests/conftest.py` now removes them.
- Not changed, worth knowing: the web app loads its fonts from Google Fonts, i.e. every visitor's IP
  address goes to Google — a known GDPR topic for a site run in Germany. Self-hosting the two fonts
  (Oswald, IBM Plex Mono; both under the SIL Open Font License) would avoid it.

## V0.9.0 — Lap analysis: all channels, lap-vs-lap comparison, track map, car physics, circuit library

Server `0.9.0`; Desktop-Client `0.9.0`. **No new migration.** Old clients keep
uploading exactly as before (the basic columns are unchanged).

Idea and feature list inspired by the open-source project ApexDash (MIT) — no code
was taken from it; see `THIRD_PARTY_NOTICES.md`. Everything is built on Garage16's
own data (Shared Memory, uploaded to the server) and styled like the rest of the app.

### Client: records everything LMU offers
- `client/lmu/structs.py`: the structs now name the fields LMU 1.3+ added **inside
  the former reserve bytes** — driver aids (TC level/slip/cut, ABS, engine/motor map,
  brake migration, anti-roll bars, active-intervention flags), hybrid values, time gaps,
  track-limit steps, and per tyre `optimal_temp`/compound. Struct sizes are unchanged
  (1888 / 260 bytes); every new offset is pinned by `tests/test_lmu_structs.py`. The layout
  was cross-checked against two independent open-source readers (LMU Pitwall,
  TinyPedal's pyLMUSharedMemory) that both follow LMU's own `SharedMemoryInterface.hpp`.
- `client/telemetry/channels.py` (new): ~180 extra columns per sample — filtered inputs,
  body velocity/acceleration/angular velocity, orientation, engine/chassis/aero values,
  driver aids and, per wheel (FL, FR, RL, RR): suspension travel, ride height, pushrod
  force, brake pressure, wheel speed, patch/ground velocities (-> slip angle), camber,
  lateral/longitudinal force, load, sliding share, tyre temperatures (3 + carcass + 3 inner
  layer), wear, surface, deflection, toe. Raw units, float32. A test fills a REAL struct
  with a distinct value in every field and checks that every column holds the field it
  is named after.
- Recorder: columns are built vectorised, floats are stored byte-stream-split + zstd
  (a 4-minute lap is roughly 4-10 MB instead of ~14 MB with the default encoding).
- New `client/telemetry/async_saver.py`: finished laps are written on a background
  thread; the 60 Hz frame loop no longer stalls at the start/finish line (it would have
  lost the first metres of the next lap). Laps are saved in order and flushed on exit.

### Server
- `server/telemetry_channels.py`: the channel catalog (96 channels, grouped, with display
  units: km/h, °C, kPa, mm, G, °, ...) and the **orientation self-calibration** (see README).
- `server/telemetry_analysis.py`: reads only the needed parquet columns, makes the distance
  axis strictly increasing, computes channels at native rate and resamples to a common
  distance grid, delta time, sector distances, track map (with mirror detection).
- `server/routers/analysis.py`: `/telemetry/analysis/{channels,compare,laps/{id},
  drivers/{id}/laps,circuits,circuits/outline}`. Access rules and file-path safety are
  described in the module docstring; tests cover cross-driver access, invalid laps, vanished
  files, a `telemetry_path` outside the storage directory, different tracks, channel limits
  and the rate limit.
- Leaderboard rows (global and team) now carry `lap_id`, so any row can be opened.
- New rate-limit bucket `analysis` (default 300/hour).

### Web
- New pages **Analysis** and **Circuits**; "Analyse" links on leaderboard rows and in
  "My Laps"; pages in `web/js/analysis/` (charts, track map, physics view, shared view
  state) and `web/js/pages/{analysis,analysis_picker,circuits}.js`. Plain JavaScript and
  Canvas, no build step — `web/` is still served as-is.

### Older clients stay compatible — and the dashboard says what they lack
- No change for old clients on the wire: uploads from 0.8.x clients (and their basic
  file format) are accepted and validated exactly as before; covered by tests that upload
  through the real `Uploader` claiming client `0.8.6`.
- The analysis marks laps recorded without the extended channels: a **"Basic data · client
  0.8.6"** tag on the lap card and an explanatory notice above the charts (which lap, how
  many of the 96 channels exist, what is missing, what to do: update the client and drive
  the lap again). The server sends `recording` per lap
  (`{level: "full"|"basic", client_version, channels_available, channels_total}`) in
  `/telemetry/analysis/compare` and `/telemetry/analysis/laps/{id}`. The verdict is taken from
  the **file's actual columns**, not from the version label.
- Lists carry a cheap, version-based `data_level` (own laps, leaderboard rows, a driver's lap
  list) and show a "basic data" tag: in the lap picker (with a legend), and in the dashboard's
  "My Laps" (with a short explanation). Comparing two old-client laps works on the basic channels
  incl. delta and track map.

### Dashboard on garage16.de
- `.env.example` and the `docker-compose.yml` defaults now point at `https://garage16.de`
  (`LMU_GARAGE_CORS_ORIGINS`, `LMU_GARAGE_FRONTEND_URL`); a Gmail example block is included.
  The web app and the API share this one address, so `web/config.js` is unchanged.
- The client build bakes `https://garage16.de` in by default (`build_windows.ps1`, the GitHub
  workflow; `-ServerUrl ""` still builds a package that asks). The client code itself still
  contains no address — a test checks that.
- `docs/ADMIN_GUIDE.md` chapter 10 is rewritten for the apex domain: DNS records to remove (A/AAAA/
  CNAME of `@` and `www`, **not** MX/TXT), the tunnel's Public Hostname with an empty subdomain,
  moving the All-Inkl webspace to a subdomain, and a **client migration path**: the old Quick
  Tunnel keeps running while `LMU_GARAGE_MIGRATED_TO=https://garage16.de` makes running clients
  (0.8.6+, HTTPS both ends) switch on their own; a client already on the new address does nothing.
- `tests/test_domain_config.py` pins all of this: every variable and service name the guide uses
  exists in the stack, the addresses agree everywhere, and the migration behaviour is as described.

### Tests
605 passed, 1 skipped (the opt-in PyInstaller build) — run with a real Caddy 2.11
binary, so the reverse-proxy tests ran too (the new `/telemetry/analysis/*` paths are
covered by the existing `/telemetry/*` route). Includes jsdom tests of the pages and
interaction tests that were mutation-checked (a deliberately broken hover, zoom
direction, sector sign and drag/click handling each turn them red).

### Deployment notes
- Rebuild the server image: `docker compose --profile tunnel up -d --build`; hard-reload
  the browser (Ctrl+F5) once.
- Friends need the **V0.9.0 client** to record the new channels; their old laps stay valid
  and comparable on the basic channels.
- **Storage**: roughly 4-10 MB per lap from now on (estimate — measure after the first
  real sessions). Backups grow with it. See `docs/ADMIN_GUIDE.md`.
- New verification items (h)-(l) in `docs/LMU_VERIFICATION_PROTOCOL.md` — they need a real
  LMU session on Windows and have NOT been run yet.

### Known limits
- TC/ABS/engine-map levels need **LMU 1.3 or newer**; one source claims the live
  in-cockpit levels are not exposed, two others read them from shared memory — verification
  item (h) settles it for your game version. If they stay at 0 the charts simply show 0.
- Comparing needs two laps on the same track string (layout), and any signed-in driver can
  open any valid lap — there is no per-driver opt-out in 0.9.

---

Older versions (V0.5.1 – V0.8.10): [`docs/archive/CHANGELOG_BIS_V0_8.md`](docs/archive/CHANGELOG_BIS_V0_8.md).
