# Changelog

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

## V0.8.10 — Windows-Fix: Text-Kodierung (Testsuite brach mit UTF-8-Mojibake ab)

Server `0.8.10`; Desktop-Client unverändert `0.8.6`. Keine neue Migration.
Anlass: Beim ersten echten `git`-Vorbereiten und `pytest`-Lauf auf einem
**echten Windows-Rechner mit deutscher Spracheinstellung** (bisher lief hier
nur Linux) schlugen 8 Tests fehl — zwei verschiedene, unabhängige Ursachen,
beide reine Windows-Fallen, keine Logikfehler:

### 1. `.read_text()` ohne Kodierung — Mojibake auf Windows
Mehrere Testdateien lasen Projektdateien (README, CHANGELOG, Caddyfile,
docker-compose.yml, .gitattributes, Quellcode-Dateien mit "—" in Kommentaren
usw.) mit `Path.read_text()` **ohne explizite Kodierung**. Python nimmt dann
die Standardkodierung des Betriebssystems: auf Linux meist UTF-8 (deshalb lief
es hier immer fehlerfrei), auf deutschem Windows aber **cp1252**. Dieselben
UTF-8-Bytes wurden dadurch falsch interpretiert ("—" wurde zu "â€""), was u.a.
`test_docs_consistency.py` (Vergleich mit erwarteten Textbausteinen aus
`docs/FRIENDS_GUIDE.md`) zum Scheitern brachte — die Quelldatei selbst war die
ganze Zeit korrektes UTF-8, nur das Lesen war falsch.

Fix: **jede** `.read_text()`/`.write_text()`-Stelle im gesamten Projekt
(29 Dateien, automatisiert mit einem Skript, das Klammer-Balance statt
naivem Textersatz nutzt, damit mehrzeilige Aufrufe nicht kaputtgehen) bekommt
jetzt `encoding="utf-8"` explizit. Das betrifft auch drei Stellen im
**Desktop-Client** (`client/config.py`, `client/telemetry/recorder.py`,
`client/uploader/uploader.py`), die Konfigurations- bzw. Metadaten-JSON lesen
und schreiben: dort war es bisher nur **zufällig unschädlich**, weil
`json.dumps()` ohne `ensure_ascii=False` nicht-ASCII-Zeichen (z. B. Akzente in
einem Fahrernamen) automatisch als `\uXXXX` escaped — die Datei auf der
Platte war also immer reines ASCII. Trotzdem jetzt explizit korrigiert, damit
sich das nicht stillschweigend auf einen zukünftigen Codepfad verlässt, der
das nicht mehr tut.

Mit einer simulierten cp1252-Locale nachgestellt und bestätigt, dass die
explizite Kodierung das Problem behebt (siehe Testkommentar); der exakte
Windows-Absturz selbst wurde vom Betreiber auf echtem Windows gefunden und
hier nachvollzogen, nicht in der Entwicklungsumgebung reproduziert (die läuft
unter Linux mit UTF-8-Standardkodierung).

### 2. Migrations-Tests: hartkodierter Linux-`PATH` bricht Windows-Subprozesse
`tests/test_migrations.py` und `tests/test_readable_classes.py` starteten
`alembic` als **Subprozess** mit einer komplett ERSETZTEN Umgebung
(`env={"PATH": "/usr/bin:/bin", ...}`) statt sie nur zu ergänzen. Auf Linux
unschädlich (irgendein `PATH` mit `python`/`alembic` reicht), auf Windows
verschwindet dadurch der komplette normale Suchpfad — und Pythons eigenes
`asyncio` (das SQLAlchemy beim Import lädt) braucht Windows' Systempfad, um
seine Netzwerk-Erweiterung `_overlapped` zu laden. Ergebnis:
`OSError: [WinError 10106] Der angeforderte Dienstanbieter konnte nicht
geladen oder initialisiert werden`, bevor Alembic überhaupt startet — 5 Tests
betroffen, darunter der Migrationstest für die neue Klassen-Migration 0012
aus V0.8.9.

Fix: Start von der **echten** Umgebung (`os.environ`) plus die zwei
tatsächlich benötigten Variablen, statt sie komplett zu ersetzen.

### Ehrlich: was hier bestätigt ist und was nicht
Beide Fehler wurden vom Betreiber auf einem **echten Windows-11-Rechner**
gefunden (nicht hier in der Linux-Entwicklungsumgebung). Die Diagnose (Ursache
in beiden Fällen) ist zweifelsfrei anhand der Fehlermeldungen und des
Quellcodes; der **Fix selbst wurde nicht erneut auf echtem Windows
verifiziert** — das steht noch aus (siehe unten).

### Tests: 531 grün, 3 übersprungen auf SQLite — 530 grün, 4 übersprungen auf
echtem PostgreSQL (unverändert zu V0.8.9; dieser Fix ändert kein Verhalten,
nur die Kodierung beim Lesen/Schreiben von Text). Kein Docker/Caddy auf
Windows verfügbar gewesen, daher auch keine Regression dort zu erwarten.

### BLOCKED – USER TEST REQUIRED
- **Bestätigung auf dem echten Windows-Rechner, auf dem der Fehler auftrat:**
  `python -m pytest -q` sollte jetzt ohne die 8 vorherigen Fehlschläge
  durchlaufen (weiterhin einige "skipped" sind normal — Caddy/Node/PostgreSQL
  fehlen dort).

---

## V0.8.9 — Lesbare Klassen, alle Strecken mit Varianten & Suche, Anzeigename änderbar

Server `0.8.9`; Desktop-Client unverändert `0.8.6`. **Neue Migration 0012.**
Anlass: (1) im Web hieß der Fahrer noch "Test", weil das der bei der
Registrierung eingetippte Name war — und kein Ort ihn ändern ließ; (2) es gab
nur die Klasse "Hyper" (LMUs interner Name), weil Klassen, Strecken und Autos
ausschließlich aus bereits hochgeladenen Runden entstanden; (3) Strecken
waren rohe LMU-Strings ohne Suche und ohne Zusammenhang zwischen Varianten.
Ziel: benutzerfreundlich, und **der Admin muss nichts von Hand eintragen oder
zuordnen**.

### 1. Anzeigename ändern
`PATCH /accounts/me` (Bearer-Token oder Web-Session) + Karte **"Driver name"**
auf der Account-Seite. Der Name wird überall zur Anzeigezeit aus der
Fahrer-Zeile gelesen — Leaderboards, Profil, Teams, Einladungen, Discord —
deshalb erscheint der neue Name sofort überall, auch bei alten Runden.
Leerzeichen werden getrimmt/zusammengefasst; leer → 400, > 60 Zeichen → 422.

### 2. Lesbare Klassen (`server/catalog.py`, Migration 0012)
LMUs Rohwerte werden auf feste, lesbare Namen abgebildet — unabhängig von
Groß-/Kleinschreibung, Leer- und Unterstrichen: `Hyper` → **Hypercar**,
`LMP2_WEC` → **LMP2 WEC**, `LMP2_ELMS` → **LMP2 ELMS**, `LMP3`, `GTE`
(`LMGTE`), `GT3` (`LMGT3`). Eine **unbekannte Klasse bleibt exakt wie gesendet**
und erscheint automatisch (kein Datenverlust, keine Pflege).
`laps.car_class` speichert den lesbaren Namen, der Original-String liegt in
der neuen Spalte `laps.car_class_raw` — ein Zuordnungsfehler lässt sich also
jederzeit aus der Quelle korrigieren. Migration 0012 schreibt vorhandene
Runden um (`Hyper` → `Hypercar`), sichert den Rohwert und ist per Downgrade
verlustfrei umkehrbar (Test mit echten Daten in beide Richtungen; die
Alias-Tabelle der Migration ist eine Kopie und wird per Test an
`server/catalog.py` gepinnt; zusätzlich von Hand auf **echtem PostgreSQL** mit
Daten hoch und wieder runter geprüft: `Hyper → Hypercar`, `LMP2_ELMS → LMP2 ELMS`,
unbekannte Klasse und NULL unverändert, nach dem Downgrade wieder die Originalwerte). Alte Links/Bot-Aufrufe mit `Hyper` funktionieren
weiter (URL-Parameter werden normalisiert, auch beim Team-Board).

### 3. Alle Klassen, alle Strecken, Varianten, Suche
- `GET /leaderboard/catalog/class-options[?track_name=]`: **immer alle sechs
  Klassen** — auch bevor eine Runde gefahren wurde — plus jede weitere, die LMU
  je gesendet hat, mit der Anzahl gültiger Runden ("GT3 · 12 laps" /
  "no laps yet").
- `GET /leaderboard/catalog/venues[?q=]`: **jede bekannte Strecke** (gesetzte
  Liste mit 12 Strecken, Suchbegriffen inkl. Akzente/Schreibvarianten), auch ohne
  Runden; **alle Varianten** einer Strecke sind genau die Track-Strings, die LMU
  tatsächlich gesendet hat — sie erscheinen von selbst bei der ersten Runde;
  eine völlig unbekannte Strecke wird ihr eigener Eintrag. Die Suche ("fuji")
  findet die **ganze Strecke mit allen Varianten**; kurze Begriffe passen am
  Wortanfang (`spa` findet Spa-Francorchamps, nicht "Espanya"), ab 5 Zeichen
  auch mitten im Namen.
- Leaderboard-Seite: Suchfeld → Strecke → (bei mehreren Varianten) Layout, die
  meistgefahrene vorausgewählt → Klasse (alle) → Auto. Team-Dashboard: dieselbe
  Suche, Strecken nach Venue gruppiert, alle Klassen.
- "Meine Runden" (`/telemetry/laps?track_name=`) ist jetzt eine Suche: ohne
  Groß-/Kleinschreibung und streckenbewusst ("fuji" findet jede Fuji-Variante);
  ein voller Name findet weiterhin sich selbst.
- **Autos** werden bewusst nicht vorab gepflegt: ein Auto ist an jeder Runde
  immer mit der von LMU gemeldeten Klasse verknüpft — Autos und ihre
  Klassenzuordnung entstehen damit automatisch aus den Uploads.

### Ehrlich: was daran Annahme ist
Die gesetzten Listen (12 Strecken, 6 Klassen und deren Schreibweisen) sind mein
bestes Wissen über LMU, **nicht** gegen LMUs echte Strings geprüft — die kenne
ich nur aus wenigen echten Uploads. Ein falscher/fehlender Eintrag ist
harmlos (unpassende Strecke bleibt leer; unbekannte Strecke/Klasse wird
trotzdem automatisch angelegt) und eine Zeile in `server/catalog.py`. Um die
Tabellen an die Realität anzupassen: die echten Werte der eigenen Datenbank
abfragen (README, "Tracks, classes and cars").

### Tests: 533 grün, 1 übersprungen auf SQLite — 532 grün, 2 übersprungen auf echtem PostgreSQL
(mit Caddy und `npm install` in `tests/js`)
(vorher 468; Details unten). Neu: `test_readable_classes.py` (Zuordnung,
Rohwert, alte Links, Klassenoptionen, **Migration 0012 auf echten Daten hin und
zurück**), `test_catalog_venues.py` (Gruppierung, Auto-Lernen, Suche, "Meine
Runden"), `test_display_name.py`, und ein **echter DOM-Test** des neuen
Leaderboards (`tests/js/leaderboard_picker.test.mjs`: "fuji" tippen → ganze
Strecke, beide Varianten, meistgefahrene vorausgewählt, alle sechs Klassen,
Strecke ohne Runden wählbar, Suche ohne Treffer) — mutationsgeprüft. Die
drei bestehenden Katalog-Tests erwarten jetzt "Hypercar" statt "Hyper" (die
Uploads bleiben absichtlich bei "Hyper", dem echten LMU-Wert).

### BLOCKED – USER TEST REQUIRED
- Die Anzeige im **echten Browser** (getestet im simulierten Browser).
- Ob LMUs echte Track-/Klassen-/Auto-Strings zu den gesetzten Listen passen
  (siehe oben) — mit einer Datenbankabfrage prüfbar.
- Update auf der NAS: Migration 0012 läuft beim Serverstart automatisch.
  **Der Rückweg auf V0.8.8 ist damit nicht mehr "nur Container tauschen":**
  der alte Code läuft zwar mit der neuen Datenbank (er ignoriert die Spalte und
  zeigt "Hypercar" einfach an), zum vollständigen Zurückdrehen aber vorher
  `docker compose exec server alembic downgrade -1` (nach Backup).

---

## V0.8.8 — Betriebs-Fixes: Caddy, Backup, Restore, GitHub-Hygiene

Server `0.8.8`; der Desktop-Client bleibt `0.8.6` (nicht angefasst). Keine
neue Migration. Anlass: Beim Schreiben der Admin-Anleitungen (NAS, Domain,
GitHub) habe ich **jeden Befehl gegen das echte Projekt geprüft** statt ihn
aus dem Gedächtnis zu schreiben — dabei kamen fünf echte Betriebsfehler
heraus, die kein bisheriger Test finden konnte, weil kein Test je durch
Caddy, das Backup-Skript oder ein Restore lief.

### 1. Caddyfile war ungültig — Caddy startet nicht (kritisch)
`handle /teams /teams/* {` (zwei Pfade hinter `handle`) ist keine gültige
Caddyfile-Syntax. Mit dem **echten Caddy 2.10** nachgestellt:
`Error: … wrong argument count or unexpected line ending after '/teams/*'`.
Der `reverse_proxy`-Container stürzt beim Start ab; jedes frische Deployment
aus dem ZIP wäre ohne Web/API-Zugang gewesen. (Der Fehler war in V0.6.8 auf
der NAS aufgetreten und dort von Hand mit einem benannten Matcher repariert
worden — im Projekt wurde er nie nachgezogen.)

### 2. `/drivers` und `/invitations` wurden nie an den Server geleitet
Das Caddyfile kannte nur `/accounts /telemetry /leaderboard /teams /admin
/health`. Seit V0.7.2 gibt es aber auch `/drivers/*` (Fahrerprofil,
Namenssuche) und `/invitations/*` (Team-Einladungen): Caddy beantwortete sie
mit der `index.html` der Web-App statt mit JSON — Fahrerprofile,
"Einladen per Name" und Team-Einladungen waren hinter dem Reverse Proxy
**still kaputt**, während alle Tests grün blieben (TestClient geht nie
durch Caddy).

Fix: **ein** benannter Matcher `@api path …` mit allen API-Präfixen.
Abgesichert durch `tests/test_reverse_proxy.py`: (a) statisch — jedes Präfix
aus dem OpenAPI-Schema der App **und** jedes Präfix, das das Frontend
aufruft, muss im Matcher stehen; (b) mit **echtem Caddy vor der echten App**
(läuft, wenn ein `caddy`-Binary da ist, sonst übersprungen): `/health`,
`/drivers/{id}/public`, unauthentifizierte `/invitations/mine`, `/teams/mine`
liefern JSON statt HTML; Frontend + SPA-Fallback; `X-Forwarded-Proto` (App mit
`require_https` akzeptiert die Anfrage nur über den Proxy); ein echter
Client-Upload durch den Proxy. Mutation geprüft: Ohne die beiden Routen
schlagen 3 Tests fehl.

### 3. `scripts/backup.sh` brach im Ordner "Garage16 - LMU" ab
Der Volume-Name wurde aus dem **Ordnernamen** gebildet
(`$(basename "$(pwd)")_telemetry_data`). Compose pinnt den Projektnamen aber
auf `garage16` (Volume `garage16_telemetry_data`) — im echten Ordner ergab
das `Garage16 - LMU_telemetry_data`, einen ungültigen Docker-Volume-Namen. Der
DB-Dump wurde noch geschrieben, dann brach das Skript ab: **jedes Backup war
unvollständig.** Bei einem zufällig gültigen, aber falschen Namen hätte
`docker run -v` stattdessen still ein neues, *leeres* Volume gesichert.
Fix: Volume-Name aus dem Compose-Projekt (`COMPOSE_PROJECT_NAME`, Default
`garage16`), Abbruch mit klarer Meldung, wenn das Volume fehlt, Abbruch bei
leerem DB-Dump, und das Skript wechselt selbst ins Projektverzeichnis
(Synology-Aufgabenplaner startet in `/root`). `tests/test_backup_script.py`
führt das **echte Skript** mit einem Fake-`docker` aus, der Dockers echte
Namensregel erzwingt (5 Tests; gegen das Original-Skript schlagen alle 5 fehl).

### 4. Restore-Anleitung war falsch — jetzt `scripts/restore.sh`
`docs/ROLLBACK.md` sagte `docker compose down` und danach
`docker compose exec db psql …` — bei gestopptem Stack unmöglich. Und ein
Restore per `psql < dump` in die **bestehende** Datenbank scheitert (`relation
"alembic_version" already exists`). Beides mit **echtem PostgreSQL 16**
nachgestellt; die richtige Prozedur (Datenbank mit `FORCE` löschen, neu
anlegen, mit `ON_ERROR_STOP` einspielen) stellt Daten und Migrationsstand
`0011` korrekt wieder her.

Neu: `scripts/restore.sh <db.sql.gz> <telemetry.tar.gz>` — prüft die
Dateien, verlangt die Eingabe `RESTORE`, stoppt Server/Bot, legt eine
Sicherheitskopie der aktuellen DB an, löscht/erstellt/lädt die DB, ersetzt die
Telemetrie, startet neu; unterstützt beide Backup-Arten. `ROLLBACK.md` und
README korrigiert (inkl. manueller Fallback). `tests/test_restore_script.py`
(9 Tests, echtes Skript + Fake-`docker`): Reihenfolge (Schreiber gestoppt →
DB gelöscht → Dump geladen → Telemetrie → Start), `WITH (FORCE)` und
`-d postgres`, Sicherheitskopie, Bestätigung, ungültige Eingaben ändern
nichts, fehlendes Volume stoppt vor dem Löschen, beliebiges Arbeitsverzeichnis.

### 5. Veraltete/fehlende Doku und GitHub-Hygiene
- README "Umstieg NAS → VPS" verlangte noch `--reconfigure` für jeden Fahrer
  (überholt seit V0.8.6) und nannte nicht die **Falle beim Umzug: derselbe
  `LMU_GARAGE_SECRET_KEY` auf dem neuen Server** (er verschlüsselt alle
  gespeicherten Client-Secrets; mit einem anderen Schlüssel schlägt jeder
  Upload fehl). Beides korrigiert.
- **Freunde brauchen beim Registrieren das "Registration secret"** (in
  Produktion Pflicht — der Server startet sonst gar nicht). Das stand nicht
  im Freunde-Leitfaden, und der Admin-Leitfaden behauptete, die Variable
  dürfe leer bleiben. Beides korrigiert.
- `.gitignore` und `package_release.bat` kannten die Build-Ordner des
  Client-Pakets nicht (`dist/`, `dist-installer/`, `build/`, `.venv-build/`,
  `*.log`) — sie wären auf GitHub gelandet. Ergänzt.
- Persönliche Namensnennung aus Code-Kommentar und CHANGELOG entfernt.
- **Das ZIP von V0.8.7 enthielt 12 Testdaten-Dateien** aus dem
  Arbeitsordner (`server_telemetry_storage/`). Aus V0.8.8 entfernt.
- `tests/test_repo_hygiene.py`: `.gitignore`/Packager-Ausschlüsse, keine
  privaten LAN-Adressen oder echten Tunnel-Hostnamen in ausgelieferten
  Dateien, keine echt aussehenden Secrets, Ops-Doku passt zu den Skripten.
- **`requirements-dev.txt` fehlte `pyyaml`**, das zwei Testdateien importieren:
  In einer frischen Umgebung (`pip install -r ...`, dann `pytest`) brach die
  Testsammlung ab. Ergänzt. Gefunden, indem der Entwickler-Weg in einem
  **frischen virtualenv** durchgespielt wurde (462 grün, 7 übersprungen:
  Caddy-/Node-/Frozen-Build-Tests ohne die jeweiligen Programme).
- Im selben Durchlauf: der Hygiene-Test durchsuchte auch ein `.venv` **im
  Projektordner** (genau das empfohlene Setup) und fand in fremden Paketen
  LAN-Adressen. Er überspringt jetzt Umgebungs-/Build-Ordner.
- `web/README.md` behauptete, die Web-App spreche standardmäßig mit
  `localhost:8000` — `config.js` setzt aber `""` (gleicher Origin), der lokale
  `python -m http.server` lief damit ins Leere. Korrigiert (eine Zeile in
  `web/config.js` für die lokale Entwicklung).

### 6. Telemetrie-Aufräumwerkzeug: im Docker-Betrieb unbenutzbar und riskant
Die README sagte "im Server-Container ausführen" — aber (a) `scripts/` war
gar nicht im Image, und (b) `docker compose exec` erbt **nicht** die
Datenbank-URL, die der Entrypoint für den Server baut; das Werkzeug hätte auf
die SQLite-Standarddatenbank gezeigt, und `--execute` hätte jede
Telemetrie-Datei für verwaist gehalten. Fix: das Skript liegt jetzt im Image;
es **zeigt an, welche Datenbank und welches Verzeichnis es benutzt** (ohne
Passwort); und `--execute` wird **verweigert**, wenn (1) Produktions-
einstellungen mit der SQLite-Standard-DB zusammentreffen oder (2) die DB 0
Runden hat, aber Dateien existieren — die typischen Zeichen der falschen
Datenbank. Der exakte funktionierende `docker compose exec … sh -c '…'`-Befehl
steht in der README (ein Test prüft, dass er nur Variablen benutzt, die der
Server-Container wirklich definiert). `tests/test_telemetry_cleanup_safety.py`.

### 7. Zeilenenden (CRLF) — Windows → NAS
Ein GitHub-Checkout unter Windows liefert standardmäßig CRLF
(`core.autocrlf=true`); ein CRLF-`entrypoint.sh` lässt den Container mit
"exec ./docker/entrypoint.sh: no such file or directory" sterben (der Kernel
sucht einen Interpreter namens `sh\r`). Neu: `.gitattributes` (LF für alles,
was unter Linux läuft; CRLF nur für die `.bat`), und beide Dockerfiles
entfernen CRLF aus ihrem Entrypoint, bevor er ausführbar gemacht wird. Ein
Test stellt den **echten Fehler** nach (CRLF-Skript startet nicht) und
zeigt, dass der Dockerfile-Befehl ihn behebt.

### 8. `restore.sh` für den Serverumzug
`RESTORE_START_SERVICES` (Standard `server discord_bot`): beim Wiederherstellen
auf einem **neuen** Server, während der alte noch läuft, darf nur die API
starten — zwei Bots mit demselben Token posten jeden Rekord doppelt.

### Tests: 468 grün, 1 übersprungen auf SQLite — 467 grün, 2 übersprungen auf echtem PostgreSQL
(vorher 426; mit Caddy-Binary und `npm install` in `tests/js`. Ohne Node/jsdom
kommt je ein Skip dazu — der DOM-Test der Discord-Karte —, ohne Caddy fallen
die 5 Reverse-Proxy-Integrationstests weg; der zusätzliche Skip auf PostgreSQL
ist der bewusst SQLite-only CLI-Test). Der andere Skip ist der opt-in Frozen-Build-Test. Die
Reverse-Proxy-Tests mit echtem Caddy liefen mit `CADDY_BIN` (Caddy 2.10.0);
ohne Binary werden nur diese Integrationstests übersprungen, die statischen
Guards laufen immer. Beim Schreiben fielen zwei meiner eigenen neuen Tests auf
PostgreSQL durch, weil sie SQLite voraussetzten — als Testfehler erkannt und
datenbankunabhängig gemacht (die Schutzlogik wird jetzt für beide Dialekte
direkt getestet).

### BLOCKED – USER TEST REQUIRED
- Das echte `docker compose` / `docker run` auf der NAS: Die Skripte wurden
  mit einem Fake-`docker` ausgeführt und ihr SQL-Teil gegen echtes PostgreSQL
  geprüft — der **Backup-/Restore-Durchlauf auf der NAS selbst** steht aus
  (Anleitung: Admin-Handbuch, "Restore-Probe").
- Das reparierte Caddyfile lief mit dem echten Caddy-Binary, aber nicht im
  `caddy:2-alpine`-Container auf der NAS.
- Unverändert: NAS, Windows-Build des Clients, Discord-Gateway, echter
  SMTP-Versand, Windows + LMU.

---

## V0.8.7 — Discord-Verknüpfung im Profil (Link-Token + Verknüpfungsmechanismus)

Server `0.8.7`; der Desktop-Client bleibt `0.8.6` (nicht angefasst). Keine
neue Migration.

### Audit zuerst — was es schon gab und was fehlte

Ein Grundgerüst existierte (Code im Web erzeugen → `/link` im Bot →
`drivers.discord_user_id`), aber mit echten Lücken:

1. **Kein Unlink.** Der Bot sagte "Unlink first if you want to switch" —
   nirgends gab es einen Unlink. Wer versehentlich das falsche
   Discord-Konto verknüpft hatte, kam nicht mehr heraus.
2. Die Web-Karte zeigte **keinen Status** (verknüpft oder nicht).
3. **Stilles Überschreiben:** Ein schon verknüpfter Fahrer konnte einen
   neuen Code erzeugen; der Bot überschrieb die bestehende Verknüpfung.
4. Mehrere Codes gleichzeitig gültig; neuer Code entwertete alte nicht.
5. Codes waren 6 Zeichen aus `A–Z0–9-_` (Verwechslungsgefahr 0/O, 1/I,
   Sonderzeichen), schlecht abzutippen.
6. **Kein einziger Test.** Die Regeln steckten direkt im Bot-Command.
7. Der Endpunkt lag unter `/teams/…`, obwohl es ein Account-Thema ist.

### Umgesetzt

**Account → "Link Discord"** (`web/js/pages/account.js`): Die Karte zeigt
den Status. Nicht verknüpft: Erklärung, **"Generate link code"** → der
fertige Befehl (`/link ABCD-EFGH`), **"Copy command"**, Countdown bis zum
Ablauf; sobald der Bot den Code einlöst, springt die Karte **von selbst**
auf "linked" (Status-Poll, endet bei Ablauf/Seitenwechsel). Verknüpft:
**"Unlink Discord"** mit Bestätigung. Hinweis, nur selbst erzeugte Codes zu
benutzen (ein fremder Code würde dein Discord mit *dessen* Profil
verbinden).

**API** (`server/routers/accounts.py`): `GET /accounts/discord` (nur ein
Flag — die Discord-ID geht nie an den Browser), `POST
/accounts/discord/link-code` (409 wenn schon verknüpft; rate-limitiert),
`DELETE /accounts/discord` (idempotent, storniert offene Codes). Der alte
`POST /teams/discord-link-code` bleibt als **deprecated Alias** mit
identischem Verhalten.

**Gemeinsame Logik** (`server/discord_link.py`), von API *und* Bot benutzt,
mit echter Datenbank testbar:
- Code einmalig, 10 Minuten gültig; ein neuer Code entwertet alle älteren
  unbenutzten; Unlink storniert offene Codes.
- 8 Zeichen aus eindeutigem Alphabet (kein 0/O/1/I/L/-/_), Anzeige
  `ABCD-EFGH`; Eingabe ignoriert Groß/Klein, Leerzeichen und Bindestriche.
  Alte 6-Zeichen-Codes lassen sich weiter einlösen.
- Eins-zu-eins in beide Richtungen; eine bestehende Verknüpfung wird **nie
  überschrieben** (auch nicht durch einen früher erzeugten Code).
- Falsche Codes werden pro Discord-Konto gedrosselt (5 Fehlversuche / 10
  Minuten; der Zähler liegt im Speicher des Bot-Prozesses — ein Neustart
  setzt ihn zurück).
- Race-sicher: `SELECT … FOR UPDATE` (PostgreSQL) — zwei gleichzeitige
  `/link` können denselben Code nicht beide einlösen.
- Abgelaufene Codes werden beim Erzeugen eines neuen aufgeräumt.

**Bot** (`discord_bot/bot.py`): `/link` ruft die gemeinsame Logik auf,
**`/unlink` ist neu**. Antworten bleiben ephemeral (nur der Nutzer sieht sie).

### Beim Testen gefunden: Account-Löschung scheiterte auf PostgreSQL

Der PostgreSQL-Lauf deckte einen **echten, schon länger bestehenden
Produktionsfehler** auf, der nichts mit Discord allein zu tun hat: Die
Lösch-Kaskade (`DELETE /accounts/me` und Admin-`DELETE /admin/drivers/{id}`)
räumte `link_codes`, `password_reset_tokens`, `sessions`,
`email_verification_tokens` und `team_invitations` **nicht** auf. SQLite
erzwingt Foreign Keys nicht, deshalb fiel es nie auf — auf PostgreSQL
(Produktion) endete die Löschung mit **HTTP 500 (Foreign-Key-Verletzung)** bei
jedem Fahrer mit Web-Login, gesetzter E-Mail (Verifizierungs-Token seit
V0.8.1), Team-Einladung, Passwort-Reset oder Discord-Code — also praktisch
jedem aktiven Fahrer. Ein Teil davon stammt aus meinen eigenen früheren
Phasen (V0.7.2 Sessions/Einladungen, V0.8.1 Verifizierung); die damaligen
Tests löschten nur Fahrer *ohne* solche Zeilen. Die Admin-Kopie räumte
außerdem die Discord-Kanäle nicht auf.

Fix: **eine** gemeinsame Kaskade in `server/driver_lifecycle.py`, von beiden
Endpunkten benutzt. Dazu ein **Guard-Test**, der die echten
SQLAlchemy-Metadaten nach Foreign Keys auf `drivers` durchsucht und
fehlschlägt, sobald eine neue Tabelle hinzukommt, die dort nicht
aufgeführt ist — die nächste Migration kann das nicht still wieder kaputt
machen.

### Tests: 426 grün, 1 übersprungen — auf SQLite **und** echtem PostgreSQL
(vorher 385).
- `tests/test_discord_link.py` (34): API, Einlösen, Einmaligkeit, Ablauf,
  Schreibweisen, Legacy-Codes, Team-Codes nicht als Account-Code einlösbar,
  Eins-zu-eins, kein Überschreiben, Unlink, Drosselung, Rate-Limit,
  Aufräumen, **Race zweier Discord-Nutzer um einen Code**, und die
  **echten Bot-Commands** `/link` und `/unlink` über ihre echten Callbacks
  (mit Fake-Interaction, gegen die echte Datenbank).
- `tests/test_driver_deletion.py` (4): Fahrer mit einer Zeile in **jeder**
  referenzierenden Tabelle wird per Self-Service und per Admin gelöscht,
  danach zeigt **nirgends** mehr eine Zeile auf ihn (aus den Live-Metadaten
  ermittelt, nicht aus einer Handliste). Per Mutation geprüft: entfernt man
  das Aufräumen, schlägt der Test auf SQLite (Leftover-Prüfung) und auf
  PostgreSQL (dieselbe FK-Verletzung wie in Produktion) fehl.
- `tests/js/discord_card.test.mjs` + `tests/test_web_discord_card.py`:
  **echter DOM-Test (jsdom)** der Karte gegen ein In-Memory-Backend — Code
  erzeugen, Befehl/Countdown/Copy-Button, Bot löst ein → Karte springt
  automatisch auf "linked", Unlink mit und ohne Bestätigung, 409-Fehler,
  Statusfehler. Per Mutation geprüft (kaputter Auto-Poll lässt ihn
  fehlschlagen). Läuft nur mit Node + `cd tests/js && npm install`, sonst
  wird er übersprungen, nicht vorgetäuscht.
- Doku-Drift-Tests: die im Freunde-Leitfaden zitierten Karten- und
  Bot-Texte müssen wörtlich im Code stehen. Der frühere Versionstest wurde
  durch die eigentlich gemeinte Regel ersetzt (README nennt Server- *und*
  Client-Version exakt wie im Code; neuester CHANGELOG-Eintrag = aktuelle
  Server-Version), weil Server und Client jetzt getrennt versioniert sind.

Doku: `FRIENDS_GUIDE.md` (neuer Abschnitt "Discord verknüpfen" plus
Fehlermeldungen des Bots), `ADMIN_GUIDE.md`, `discord_bot/DISCORD_INTEGRATION.md`.

### BLOCKED – USER TEST REQUIRED
- **Echter Discord-Gateway:** Registrierung der neuen Slash-Commands
  (`/unlink` muss beim Start synchronisiert werden — nach dem Update den Bot
  neu starten), echtes `/link`/`/unlink` in deinem Server, und ob die Antwort
  wirklich nur für dich sichtbar ist. Die Bot-Commands wurden über ihre
  echten Callbacks getestet, aber nie gegen Discord selbst.
- **Echter Browser:** die Karte lief im simulierten DOM (jsdom), nicht in
  Chrome/Firefox (Aussehen, Zwischenablage-Knopf `navigator.clipboard`
  braucht HTTPS oder localhost).
- Ob Slash-Commands in Direktnachrichten mit dem Bot funktionieren, ist
  ungeprüft — die Doku nennt deshalb nur "einen Kanal mit dem Bot".
- Unverändert: NAS, Docker-Daemon, Windows + LMU, echter SMTP-Versand,
  Backup/Restore, Windows-Build des Clients.

---

## V0.8.6 — Freund-taugliches Client-Paket + automatische Server-Migration

Server **und** Client `0.8.6` (der Client hat sich substanziell geändert).
Letzter technischer/UX-Feinschliff vor den echten Tests auf NAS + Windows +
LMU. Keine neuen Großfeatures.

### Audit (zuerst, wie verlangt)

- **Ersteinrichtung brauchte ein Terminal** (`input()` im Konsolen-Assistenten,
  zudem auf einem Hintergrund-Thread). Ein gepacktes Fenster-Programm hätte
  daran unabhängig von allem anderen nicht funktioniert.
- **Server-URL war bereits zentral** (`client/config.py`); jetzt zusätzlich
  per AST-Test abgesichert (kein Hardcoding im Code, keine LAN-Adressen).
- **Zu alter Client → HTTP 400 → jede Runde dauerhaft "rejected".** Freunde
  hätten nach dem Update ihre bisher gefahrenen Runden nie hochladen können.
- Datenordner `./lmu_garage_data` war **relativ zum Arbeitsverzeichnis** —
  für eine installierte App (Startmenü-Verknüpfung) unbrauchbar.
- `--follow-migration` war nur ein Terminal-Befehl.

### Umgesetzt

**Ersteinrichtung als Fenster** (`client/gui/setup_dialog.py`), Validierung
getrennt von Tkinter und ohne Display testbar. Button "Reconnect account…"
im Hauptfenster (kein `--reconfigure` mehr nötig). Server-Adresse kann ins
Paket eingebacken werden (`garage16_server.txt`) — Freunde tippen sie nie.
Datenordner pro Benutzer (`%LOCALAPPDATA%\Garage16`); ein vorhandenes
`./lmu_garage_data` bleibt vorrangig, damit bereits aufgezeichnete, noch
nicht hochgeladene Runden nicht verwaisen. Logdatei
(`garage16-client.log`) und `--version` als Support-Ausgabe (nichts Geheimes).

**Automatische, sichere Server-Migration** (`client/migration.py`,
`client/server_check.py`): Der Client prüft bei Start und danach alle ~10
Minuten mit *einer* Health-Anfrage, ob der Server erreichbar, der Client zu
alt oder der Server umgezogen ist. Kein Terminal-Befehl. Sicherheitsmodell
(bewusst ohne neue PKI):
- Nur bei **HTTPS auf beiden Seiten** wird automatisch gefolgt; die
  TLS-Zertifikatsprüfung von `requests` ist der Vertrauensanker. Über
  Klartext-HTTP könnte jeder im Netz den Umzug fälschen — dort wird **nicht**
  gefolgt (Hinweis an den Freund).
- Nie in ein `http://`-Ziel (kein Downgrade). Ungültige Ziele (`javascript:`,
  `file:`, ohne Host, …) werden verworfen.
- Das neue Ziel muss auf `/health` antworten, **bevor** irgendetwas
  gespeichert wird. Dabei gehen **keine Zugangsdaten** an die neue URL
  (durch Server-Log der Testserver belegt).
- `save_config` schreibt **atomar**; scheitert das Speichern, bleibt die alte
  Konfiguration in Datei *und* Speicher unverändert.
- Restrisiko, ehrlich: Nach dem Wechsel gehen Uploads mit denselben
  Zugangsdaten an die neue URL — das ist der Zweck einer Migration; der
  Vertrauensanker ist die TLS-Identität des alten Servers.

**UPDATE_REQUIRED**: Server antwortet mit **HTTP 426** und strukturiertem
Body (`code`, `message`, `min_client_version`, `download_url`) statt eines
generischen 400. Der Client lässt die Runden **liegen** (nicht mehr
"rejected"), zeigt ein Banner in Klartext und — wenn
`LMU_GARAGE_CLIENT_DOWNLOAD_URL` gesetzt ist — einen "Open download
page"-Button (Link wird als untrusted validiert: nur http(s) mit Host).
`GET /health?client_version=` liefert das Urteil des Servers vorab
(Versionsverhandlung; die "zu alt"-Regel lebt nur in `server/versioning.py`).

**GUI**: persistentes Meldungs-Banner (verschwindet nicht wie eine Log-Zeile).
**API-Änderung:** 400 → **426** bei zu altem Client (dokumentiert; ältere
Clients behandeln beides als permanente Ablehnung, sind aber ohnehin zu alt).

**Packaging** (`packaging/`): PyInstaller-Spec (windowed, one-dir), Inno-Setup-
Skript (`Garage16-Client-Setup.exe`; pro Benutzer, **keine Admin-Rechte**;
gleiche App-ID → Update an Ort und Stelle; **löscht bei Deinstallation nie
Nutzerdaten**), `build_windows.ps1`, `sync_version.py` (Installer-Version wird
aus `CLIENT_VERSION` erzeugt und kann nicht driften), GitHub-Actions-Workflow.
Windowed-Build ohne Konsole: `--version`/`--follow-migration` docken an die
Eltern-Konsole an (`AttachConsole`).

**Docker/Doku**: `LMU_GARAGE_CLIENT_DOWNLOAD_URL` in `docker-compose.yml` und
`.env.example` (ohne das wäre der Update-Link auf der NAS nicht setzbar).
Server warnt beim Start, wenn `LMU_GARAGE_MIGRATED_TO` /
`..._CLIENT_DOWNLOAD_URL` von Clients ignoriert würde. `FRIENDS_GUIDE.md`
komplett neu (Installer → Start → Account verbinden → LMU → Fahren; kein
Python/pip/Terminal), `CLIENT_INSTALL.md` jetzt reines Entwickler-Dokument,
neu `CLIENT_BUILD.md`, `ADMIN_GUIDE.md`/README angepasst.

### Behobene Fehler
1. Zu alter Client markierte Runden dauerhaft als rejected (s. o.).
2. Log-Meldungen des Upload-Threads setzten "Connected" und Track/Car/Lap in
   der GUI zurück (`StatusUpdate` hatte nur Defaults).
3. `normalize_server_url("https://")` ergab `https://https:` und galt als
   gültig (`rstrip` lief vor der Schema-Prüfung).
4. `sync_version.py` band einen Default-Parameter beim Import.
5. 401-Meldung verwies im Log auf ein Terminal-Kommando.
6. Verwaister, irreführender Kommentar in `server/config.py` (Rest einer
   fehlerhaften Editierung aus V0.8.1).

### Tests: 385 grün, 1 übersprungen — auf SQLite **und** echtem PostgreSQL
(vorher 275). Der eine Skip ist der opt-in Frozen-Build-Test
(`GARAGE16_TEST_FROZEN_BUILD=1`), der separat ausgeführt wurde und grün war.

- Migration Fälle A–F gegen **echte lokale HTTP/HTTPS-Server** (uvicorn,
  self-signed Zertifikat, Zertifikatsprüfung bleibt an): keine Migration;
  gültige Migration (URL + Zugangsdaten persistiert); ungültige/unerreichbare/
  kaputte Ziele und fehlgeschlagenes Speichern (alte Konfiguration bleibt);
  UPDATE_REQUIRED (Banner, Konfiguration unverändert, **Runden bleiben
  pending und laden nach dem Update hoch** — echter Uploader gegen die echte
  App); Neustart nach Migration; Auth nach Migration (**dasselbe Token lädt
  danach über HTTPS in die echte App hoch, gleicher Fahrer**); keine
  Zugangsdaten an die Zielserver; HTTP-Quelle/Downgrade werden abgelehnt.
- Die drei mock-basierten `follow_migration`-Tests aus V0.8.1 wurden durch
  echte-Server-Varianten ersetzt (gleiche Szenarien). Der Test für zu alte
  Clients erwartet jetzt 426 und prüft zusätzlich den strukturierten Body.
- Echte **GUI unter Xvfb**: Setup-Dialog (Felder ausfüllen, Connect,
  Erreichbarkeitsprüfung gegen echten Server), Banner, Download-Button nur
  bei sicherem Link, Log-only-Regression.
- **Echter PyInstaller-Build** (Linux) + Ausführung des gebündelten Binaries:
  Version, eingebackene Server-URL, **HTTPS-Migration durch das Binary**.
- Packaging/Docker/Doku-Konsistenz: Installer-Version = Client-Version,
  Spec (windowed/one-dir/ohne Server-Code), per-User-Install, keine
  Datenlöschung beim Deinstallieren, Workflow-YAML, Compose-Variablen ↔
  `.env.example`, Doku-Meldungen wörtlich im Code, alle Doc-Verweise
  existieren, keine Entwickler-Anweisungen im Freunde-Leitfaden.

### Abschließender Audit — die sieben Fragen

1. **Freund ohne Python/Terminal?** Im Code ja (Fenster-Einrichtung, keine
   Konsole, gebündelte Runtime; Linux-Bündel real ausgeführt). Ein
   **Windows-.exe/Installer wurde noch nie gebaut** → BLOCKED.
2. **Automatischer Wechsel NAS → Domain?** Ja — **bei HTTPS auf beiden
   Seiten** (Cloudflare Tunnel/Caddy erfüllen das). Eine reine LAN-`http://`-
   NAS wechselt aus Sicherheitsgründen nicht selbst; dort gibt der Admin die
   neue Adresse und der Freund nutzt "Reconnect account…".
3. **Neu installieren wegen Serverwechsel?** Nein. Nur bei zu altem Client
   ein Update (Installer aktualisiert an Ort und Stelle).
4. **Inkompatibler Client?** Klartext-Banner, optional Download-Button,
   Konfiguration unberührt, Runden bleiben erhalten.
5. **Auth, Laps, Teams bleiben?** Auth+Upload nach Migration real bewiesen;
   Teams/Laps hängen an `driver_id` bzw. am Account in der wiederhergestellten
   Datenbank.
6. **Server-URLs zentral?** Ja (`client/config.py`), per Test erzwungen.
7. **Alles Testbare grün?** Ja: 385/385 (1 opt-in separat grün) auf SQLite
   und PostgreSQL.

### BLOCKED – USER TEST REQUIRED (nicht vorgetäuscht)

- **Windows-Build**: `.\packaging\build_windows.ps1 -ServerUrl "https://…"`
  oder GitHub-Workflow `build-client` ausführen; Installer auf einem echten
  Windows-PC durchklicken (Checkliste in `docs/CLIENT_BUILD.md`, u. a.
  Update-in-place, Deinstallation behält Daten, `AttachConsole`-Ausgabe von
  `--version`). Nichts davon wurde ausgeführt: kein Windows, kein Inno Setup,
  kein GitHub-Lauf.
- **SmartScreen** warnt bei der unsignierten `.exe` (Freunde: "Weitere
  Informationen → Trotzdem ausführen"). Code-Signing nicht enthalten.
- Paketgröße entpackt ~320 MB (pandas/pyarrow).
- Unverändert: NAS, echter Docker-Daemon, Windows + LMU (Shared Memory),
  Discord-Gateway, echter SMTP-Versand, echter Backup/Restore.

---

## V0.8.5 — V0.8-NAS Release Gate & Freeze (§12)

**Server-Version `0.8.4` → `0.8.5`. Dies ist der eingefrorene V0.8-NAS-
Release-Stand.** Keine Code-Änderung außer der Versionszeile — dieser
Eintrag ist der abschließende, ehrliche Release-Gate-Report, wie in §12
verlangt: PASS/FAIL/BLOCKED/NOT TESTED pro Kategorie, nichts als PASS
markiert, das nicht tatsächlich ausgeführt wurde.

### Release-Gate-Report

| Kategorie | Status | Begründung |
|---|---|---|
| **Tests** | ✅ PASS | 275/275, wiederholt stabil auf SQLite (mehrfach 5×+ hintereinander über den ganzen Sprint) **und** auf echtem PostgreSQL (mehrfach bestätigt, zuletzt final in dieser Version erneut über `scripts/test_against_postgres.sh`) |
| **PostgreSQL** | ✅ PASS | Echter PostgreSQL-16-Server real installiert und betrieben (V0.8.3). Komplette Migrationskette (0001→0011) upgrade→downgrade→upgrade real durchlaufen. Schema inspiziert, funktionaler Index bestätigt korrekt. Postgres-spezifischer `pg_advisory_xact_lock`-Codepfad — vorher nie ausgeführt — gezielt 15× getestet |
| **Docker** | 🔶 TEILWEISE / BLOCKED | Kein Docker-Daemon in dieser Sandbox verfügbar — echtes `docker build`/`docker compose up` **nie ausgeführt**. Was real geprüft wurde: `docker-compose.yml` als YAML geparst (valide), jede referenzierte Umgebungsvariable gegen `.env.example` abgeglichen (keine Lücke), alle Shell-Skripte (`entrypoint.sh`, `backup.sh`, `test_against_postgres.sh`) mit `bash -n` syntaxgeprüft. **Echter Build/Start bleibt BLOCKED – USER TEST REQUIRED** |
| **NAS** | ⛔ BLOCKED – USER TEST REQUIRED | Keine echte NAS/DSM-Umgebung in dieser Sandbox. Nie getestet |
| **Backup/Restore** | ⛔ BLOCKED – USER TEST REQUIRED | Skripte vorhanden und syntaxgeprüft (`scripts/backup.sh`), Restore-Prozedur dokumentiert (`docs/ROLLBACK.md`, `docs/ADMIN_GUIDE.md`). Der explizit als Release-Gate geforderte **echte** Restore-Durchlauf (Datenbank zerstören → wiederherstellen → verifizieren) wurde **nie gegen echte Infrastruktur durchgeführt** |
| **Auth** | ✅ PASS (mit einer Einschränkung) | Registrierung, Login, Passwort-Reset, E-Mail-Verifizierung, Logout/Logout-all, Account-Löschung, Sessions, Rate-Limits — alles automatisiert getestet, auf SQLite **und** PostgreSQL grün. **Einschränkung**: reale E-Mail-Zustellung über einen echten SMTP-Server wurde nie durchgeführt (kein Netzwerkzugriff auf beliebige SMTP-Ports in dieser Sandbox) — E-Mail-Versand ist in allen Tests über den `DevelopmentEmailProvider`/eine Capture-Fake abgedeckt, nie über echtes SMTP. Als eigener Punkt: **SMTP-Realversand: NOT TESTED** |
| **Teams** | ✅ PASS | Owner/Admin/Member, Join/Leave, Ownership-Transfer, Team-Löschung ohne Waisen, case-insensitive Namen, echtes Invitation-Modell mit vollem Status-Lebenszyklus, Team-Dashboard/Leaderboard/Records/Activity, You-vs-Team — durchgehend automatisiert getestet, SQLite und PostgreSQL |
| **Telemetry** | ✅ PASS (Server-Pipeline) | Upload, Validierung, Speicherung, Path-Traversal-Schutz (empirisch mit echten Payloads getestet), Duplikat-Erkennung, Orphan-Cleanup — alles mit echten synthetischen Telemetriedaten getestet, SQLite und PostgreSQL. Bezieht sich nur auf die Server-seitige Pipeline ab Upload — siehe LMU/Windows für den Teil davor |
| **LMU/Windows** | ⛔ BLOCKED – USER TEST REQUIRED | Kein Windows, keine echte LMU-Installation in dieser Sandbox — war seit der allerersten Nachricht dieser gesamten Konversation bekannt und nie anders darstellbar. `docs/LMU_VERIFICATION_PROTOCOL.md` enthält eine konkrete Testprozedur, die real auf Windows+LMU durchgeführt werden muss |
| **Client** | 🔶 TEILWEISE | Client-**Logik** (Config-Handling, Uploader-Retry, HMAC-Signierung, Server-Migration-Following via `--follow-migration`, CLI-Flags) automatisiert getestet, inkl. echtem Serverprozess ohne jedes Mocking (V0.8.1). Echte Ausführung auf einem echten Windows-Rechner mit echtem LMU: **BLOCKED**. Gepackter Installer (.exe): **bewusst nicht gebaut** — dokumentierte, wiederholt bestätigte Scope-Entscheidung (siehe `docs/CLIENT_INSTALL.md`), kein technisches Blockade-Problem |
| **Discord** | 🔶 TEILWEISE / BLOCKED | Bot-Code, Slash-Command-Definitionen, Record-Announcement-Logik, Heartbeat-Mechanismus vorhanden und unit-getestet. Echtes Posten in einen echten Discord-Server mit echtem Bot-Token: **nie durchgeführt** (keine echte Gateway-Verbindung in dieser Sandbox möglich) — **BLOCKED – USER TEST REQUIRED** |
| **Server-Migration** | ✅ PASS | Einziger Infrastruktur-nahe Punkt mit echtem Ende-zu-Ende-Test **ohne** externe Abhängigkeit: echter Serverprozess mit `LMU_GARAGE_MIGRATED_TO` gestartet, extern per HTTP abgefragt, echter Client-CLI-Code (`follow_migration()`) ohne jedes Mocking dagegen ausgeführt — Konfigurationsdatei nachweislich korrekt aktualisiert (V0.8.1) |
| **Dokumentation** | ✅ PASS | `docs/ADMIN_GUIDE.md`, `docs/FRIENDS_GUIDE.md` (beide neu, V0.8.4), README, CHANGELOG, `docs/ROLLBACK.md`, `docs/CLIENT_INSTALL.md`, `docs/LMU_VERIFICATION_PROTOCOL.md`, `discord_bot/DISCORD_INTEGRATION.md` — vorhanden, gegenseitig verlinkt, UI-Beschriftungen und Befehle stichprobenartig gegen echten Code verifiziert (ein Fehler dabei gefunden und vor Auslieferung korrigiert: Backup-Dienst-Profil-Flag) |

**Zusammenfassung:** 6 von 13 Kategorien vollständig PASS mit echter
Verifikation, 2 weitere PASS mit einer klar benannten Einschränkung, 3
teilweise (Code/Logik getestet, reale Hardware-/Netzwerk-Ausführung
blockiert), 2 vollständig blockiert (NAS, Backup/Restore-Realtest).
**Kein einziger Punkt wurde als PASS markiert, ohne tatsächlich
ausgeführt worden zu sein** — wo eine reale Ausführung in dieser Sandbox
nicht möglich war, steht das explizit da, nicht als optimistische
Annahme verkleidet.

### Was während des gesamten V0.8-NAS-Durchgangs an echten Fehlern gefunden und behoben wurde

Zur Einordnung, was "Audit → Architektur prüfen → implementieren → Tests
→ Regression prüfen" in der Praxis bedeutet hat — nicht nur Features
hinzugefügt, sondern wiederholt echte, vorher unbekannte Bugs gefunden:

- **V0.8.1**: ein echter Deadlock im Upload-Endpunkt (`async def` mit
  blockierenden DB-Calls konnte unter echter Last den einzigen
  Event-Loop-Thread einfrieren) — durch beharrliches Nachforschen bei
  einem als "bekannter Flake" abgetanenen Test gefunden, nicht durch
  Zufall
- **V0.8.2**: sieben echte Input-Validation-Lücken, darunter
  unbegrenzte `current_password`-Felder, die direkt in Argon2 laufen —
  ein echter Kostenverstärkungs-Vektor
- **V0.8.4**: ein Fehler im eigenen ersten Entwurf des Admin-Leitfadens
  (Backup-Dienst fälschlich als automatisch aktiv beschrieben) — beim
  Gegenprüfen gegen den echten Code gefunden und vor Auslieferung
  korrigiert, nicht danach

### Versions-Freeze

**V0.8-NAS ist hiermit eingefroren bei Server-Version `0.8.5` /
Client-Version `0.8.1`.** Keine weiteren großen Features, wie in der
ursprünglichen Vorgabe verlangt. Alle noch offenen Punkte aus der
Tabelle oben sind explizit Aufgaben für den Betreiber, nicht
unerledigte Arbeit an der Software selbst — die Software ist für jeden
Punkt, den diese Sandbox real prüfen konnte, nachweislich fertig.

---

## V0.8.4 — Dokumentation Admin + Freunde (§11)

Server-Version `0.8.3` → `0.8.4`. Keine Code-Änderung an Server oder
Client außer den unten genannten Doku-Korrekturen.

### Zwei neue, kurze und praktische Leitfäden

Bisher war die Dokumentation umfangreich, aber nach Thema organisiert
(README.md, 400+ Zeilen) statt nach Zielgruppe — ein Freund ohne
Entwicklerkenntnisse hätte sich durch Docker-/Caddy-/PostgreSQL-Details
wühlen müssen, um herauszufinden, wie man einem Team beitritt.

**`docs/ADMIN_GUIDE.md`** (neu) — die exakt geforderten neun Punkte, in
der Reihenfolge, in der man sie tatsächlich braucht: NAS-Installation,
Docker, `.env`, PostgreSQL, Backup/Restore, Discord, SMTP, Updates,
Troubleshooting. Verweist für die volle technische Tiefe auf die
bestehenden README-Abschnitte, statt sie zu duplizieren — dupliziert
Inhalt veraltet doppelt so leicht.

**Zwei neue, vorher nirgends dokumentierte Themen darin:**
- **Discord-Bot-Einrichtung von Grund auf**: Bot im Discord Developer
  Portal anlegen, Token holen, mit den richtigen OAuth2-Scopes
  (`bot`, `applications.commands`) und Berechtigungen einladen. Vorher
  gab es nur `discord_bot/DISCORD_INTEGRATION.md` — eine
  Entwickler-orientierte "was wurde gebaut"-Dokumentation, keine
  Einrichtungsanleitung für einen Betreiber.
- **SMTP-Einrichtung mit echtem Anbieter**: `.env`-Werte, Neustart, und
  ein tatsächlicher Test ("Passwort vergessen" mit der eigenen Adresse
  auslösen und die Mail wirklich ankommen sehen) — vorher gab es dafür
  nur die reinen Variablennamen in `.env.example`, keine Anleitung, was
  man damit tut.

**`docs/FRIENDS_GUIDE.md`** (neu, komplett von Grund auf) — Installation,
Account, Client, LMU, Teams, Leaderboard, typische Fehler. Enthält eine
Tabelle mit den tatsächlichen Fehlermeldungen, die die Anwendung zeigt
(z. B. "This client (vX) is older than the server's minimum", "This
server has moved to ...") und was jeweils zu tun ist — jede Meldung
wortgleich mit dem, was der Code tatsächlich ausgibt, nicht paraphrasiert.

### Ein echter Fehler beim Schreiben gefunden und korrigiert

Erster Entwurf von `ADMIN_GUIDE.md` behauptete, der `backup`-Docker-
Dienst liefe automatisch mit. Beim Gegenprüfen gegen `docker-compose.yml`
festgestellt: er ist hinter `profiles: ["backup"]` versteckt und startet
mit einem einfachen `docker compose up -d` **nicht** — er braucht
explizit `docker compose --profile backup up -d`, als bewusste
Alternative zu einem NAS-nativen Cron-/Task-Scheduler-Aufruf von
`scripts/backup.sh`. Korrigiert, bevor der Leitfaden ausgeliefert wurde,
statt einen Admin im Vertrauen auf ein nicht laufendes Backup zu lassen.

### Jede UI-Beschriftung im Leitfaden gegen den echten Code verifiziert

Nicht nur geschrieben und angenommen — jede im Freunde-Leitfaden zitierte
Button-Beschriftung und Fehlermeldung direkt im Frontend-Code
nachgeschlagen: "Resend verification email", "Create a team"/"Join a
team", "Get Garage16"/"Go to your Garage", die `tunnel`-Profile-Angabe in
`docker-compose.yml`, sowie die Discord-Bot-Intents
(`discord.Intents.default()` — bestätigt: keine privilegierten Intents
nötig, wie im Leitfaden behauptet).

### README verlinkt

Neuer Hinweis-Block direkt am Anfang von `README.md`, der auf beide
neuen Leitfäden verweist, bevor die technische Referenz beginnt.

### Tests

275/275 unverändert grün (reine Dokumentationsänderung, keine
Code-Änderung außer den beiden README-Versionszeilen).

### Nicht Teil von V0.8.4

Der abschließende Release-Gate-Report (§12) mit PASS/FAIL/BLOCKED/NOT
TESTED für jede Kategorie folgt in V0.8.5 — dem letzten Schritt dieses
Release-Durchgangs.

---

## V0.8.3 — PostgreSQL-, Migration- und Docker-Verifikation (§10)

Server-Version `0.8.2` → `0.8.3`. Keine neue Schema-Migration.

### Der wichtigste Punkt dieser Version: echtes PostgreSQL, zum ersten Mal überhaupt

Jede bisherige Testrunde in der gesamten Projektgeschichte — inklusive
des SQLite-spezifischen Concurrency-Fixes in V0.8.1 und des
Security-Reviews in V0.8.2 — lief ausschließlich gegen SQLite.
PostgreSQL ist die tatsächliche Produktionsdatenbank und nimmt an
mindestens einer Stelle einen echt anderen Codepfad
(`server/database.py`s `acquire_record_lock` — auf SQLite ein No-Op, auf
Postgres ein echter `pg_advisory_xact_lock`), den reines SQLite-Testen
niemals prüfen kann.

**In dieser Sandbox einen echten PostgreSQL-16-Server installiert und
gestartet** (nicht simuliert, nicht angenommen) und damit:

1. **Komplette Migrationskette (0001→0011) real gegen PostgreSQL
   durchlaufen lassen** — Upgrade bis head, vollständiger Downgrade bis
   zur Basis, erneutes Upgrade. Alle drei Durchläufe fehlerfrei.
   Schema-Inspektion bestätigt: der kniffligste Fall (der
   case-insensitive Funktionsindex `ix_teams_name_lower` aus Migration
   0008) wurde exakt wie beabsichtigt als `UNIQUE, btree (lower(name::text))`
   angelegt.
2. **Komplette Pytest-Suite gegen echtes PostgreSQL laufen lassen** —
   271/271, danach nach den in dieser Version neu hinzugekommenen
   Migrationstests 275/275, jeweils mehrfach wiederholt zur
   Stabilitätsbestätigung.
3. **Die beiden Concurrency-Tests gezielt 15× gegen PostgreSQL
   wiederholt** — inklusive `test_two_drivers_racing_for_the_same_record_produce_exactly_one_wr`,
   der genau den zuvor nie ausgeführten `pg_advisory_xact_lock`-Codepfad
   auslöst. Durchgehend grün, durchgehend schnell (~1,4–1,5s), keine
   Überraschungen.

### Dauerhaft und automatisch statt einmalig manuell

Eine einmalige manuelle Verifikation in dieser Sandbox hilft nicht,
wenn niemand sie wiederholt. Deshalb:

**`tests/test_migrations.py`** (neu) — läuft ab sofort bei **jedem**
`pytest`-Aufruf automatisch mit: vollständige Migrationskette gegen eine
frische SQLite-DB (upgrade head, downgrade base, upgrade head erneut),
inklusive eines echten App-Boots plus einer echten Schreiboperation
gegen die zurück-migrierte DB danach — nicht nur "Alembic hat nicht
gemeckert", sondern "die resultierende DB funktioniert tatsächlich".
Zusätzlich ein Test, dass die Migrationskette keine Verzweigung hat
(`alembic heads` liefert genau einen Head).

**`scripts/test_against_postgres.sh`** (neu) — automatisiert genau die
oben beschriebene Verifikation, wiederholbar für jeden, der eine echte
PostgreSQL-Instanz erreichbar hat (inklusive kurzer Anleitung, wie man in
~2 Minuten eine lokale Testinstanz aufsetzt). Selbst real ausgeführt und
bestätigt funktionierend, nicht nur geschrieben und angenommen.

**README** aktualisiert: der bisherige Hinweis "PostgreSQL-Pfad nicht
verifiziert, vor Produktivbetrieb selbst testen" ist jetzt korrekt als
"verifiziert, hier ist das wiederholbare Skript dafür" formuliert.

### Docker — statisch geprüft, echter Build/Run bleibt BLOCKED

**Kein Docker-Daemon in dieser Sandbox verfügbar** — echtes `docker
build`/`docker compose up` kann ich hier nicht ausführen und behaupte
das auch nicht. Was tatsächlich geprüft wurde:

- `docker-compose.yml` als YAML geparst — syntaktisch valide, 6 Services,
  5 benannte Volumes für persistente Daten.
- **Jede** in `docker-compose.yml` referenzierte `LMU_GARAGE_*`-Umgebungs-
  variable automatisiert gegen `.env.example` abgeglichen — keine
  undokumentierte Variable gefunden.
- `docker/Dockerfile.server`, `docker/entrypoint.sh`, `scripts/backup.sh`
  und das neue `scripts/test_against_postgres.sh` mit `bash -n` auf
  Syntaxfehler geprüft — alle sauber.

**Echter Container-Build, echter NAS-Deploy, echter Server-/NAS-Restart
bleiben `BLOCKED – USER TEST REQUIRED`** — das kann nur auf echter
Docker-Infrastruktur passieren, die diese Sandbox nicht hat.

### Tests

275/275 grün auf SQLite (271 zuvor + 4 neue in
`tests/test_migrations.py`), **zusätzlich 275/275 grün auf echtem
PostgreSQL** über `scripts/test_against_postgres.sh` real bestätigt.

### Nicht Teil von V0.8.3

Vollständige Admin-/Freunde-Dokumentation (§11), abschließender
Release-Gate-Report (§12) folgen in V0.8.4/V0.8.5. Infrastrukturabhängige
Punkte, die eine echte Docker-Umgebung, ein echtes NAS oder echtes
Windows+LMU brauchen, bleiben `BLOCKED – USER TEST REQUIRED`.

---

## V0.8.2 — Security-Review-Nachschlag (§9)

Server-Version `0.8.1` → `0.8.2`. Client-Version bleibt `0.8.1` —
keine Client-Code-Änderung in dieser Version; Versionsnummern laufen ab
jetzt pro Komponente getrennt, nicht mehr zwangsweise im Gleichschritt.

Jeder Punkt unten wurde **konkret im echten Code geprüft**, nicht nur
behauptet — mit Fundstelle, Testfall oder beidem.

### SQL Injection — PASS

Durchsucht: jede Roh-SQL-Stelle (`sa.text(...)`) im gesamten Server und
allen Migrationen. Ausnahmslos entweder statisch (keine Nutzereingabe
beteiligt) oder korrekt mit gebundenen Parametern (`:lock_id`, `:id`).
Der Rest der Codebase nutzt durchgehend SQLAlchemys Query-Builder.

### XSS — PASS

`web/js/format.js`s `el()`-Helfer fügt jeden String-Kindknoten über
`document.createTextNode()` ein — niemals `innerHTML`. Durchsucht: jede
`innerHTML`-Zuweisung im gesamten Frontend (ausnahmslos nur `= ""` zum
Leeren), sowie `eval`/`document.write`/`new Function` (keine Treffer).
Selbst vom Server zurückgegebene Fehlertexte landen über `el()` als
Textknoten — auch reflektierte Nutzereingaben in Fehlermeldungen können
nicht als HTML ausgeführt werden.

### CSRF — PASS

Verifiziert statt nur behauptet: **jeder** `GET`-Endpunkt im gesamten
Server (vollständig aufgelistet und geprüft) ist eine reine Leseoperation
— keine zustandsändernde Aktion hängt an `GET`, was der einzige
Request-Typ wäre, den `SameSite=Lax` bei einer Cross-Site-Top-Level-
Navigation überhaupt noch mitschicken würde. In Kombination mit der in
Phase 2 gewählten `SameSite=Lax`-Cookie-Konfiguration bestätigt das die
damalige Design-Entscheidung tatsächlich, statt sie nur zu wiederholen.

### Path Traversal — PASS (empirisch getestet, nicht nur gelesen)

`server/storage.py`s `_slugify()` entfernt jedes Zeichen außer
`[a-z0-9_-]` vor dem Bau eines Verzeichnispfads aus client-geliefertem
`track_name`/`car_name`. Mit echten Payloads durchgespielt:
`../../etc/passwd` → `etc_passwd`, `..\\..\\windows\\system32` →
`windows_system32` — keine Punkte oder Slashes überleben in irgendeiner
getesteten Variante. Zusätzlich ein echter Ende-zu-Ende-Upload-Test mit
genau diesen Payloads als `track_name`/`car_name`: die gespeicherte
Datei landet nachweislich innerhalb des konfigurierten
Storage-Wurzelverzeichnisses.

### CORS — PASS

`cors_origins` ist umgebungsvariablen-konfiguriert, niemals ein
Wildcard per Default — in Kombination mit `allow_credentials=True`
korrekt (Browser lehnen Wildcard+Credentials ohnehin ab, aber diese App
setzt gar keinen Wildcard).

### Secrets in Logs — PASS

Durchsucht: jeder `logger.*(...)`-Aufruf in Server und Client auf Nähe zu
`password`, `secret`, `token`. Keine Treffer. Die zentrale
Request-Logging-Middleware (`server/main.py`) loggt ausschließlich
Methode, Pfad, Status-Code, Dauer und Request-ID — niemals Body,
Query-String oder Header.

### Upload Security — PASS

`POST /telemetry/upload` verifiziert die HMAC-Signatur gegen das
`client_secret` des über den Bearer-Token **authentifizierten** Fahrers
— die Fahrer-Identität wird an keiner Stelle aus client-gelieferten
Envelope-Daten übernommen. Kein Weg, "als" ein anderer Fahrer
hochzuladen.

### Authorization / IDOR — PASS

Stichprobenartig (da in früheren Phasen bereits umfangreich getestet)
erneut verifiziert: jeder team-bezogene Endpunkt (`team_stats`,
`team_members`, `list_invitations`, beide Team-Leaderboards,
`team_records`, `team_activity`) prüft `_get_membership()` vor
Datenrückgabe.

### Input Validation — 7 echte Lücken gefunden und behoben

Das war der einzige Bereich mit tatsächlichem Handlungsbedarf:

- `TeamCreateRequest.name` hatte **gar kein** Längenlimit — inkonsistent
  mit `TeamUpdateRequest.name` (`max_length=100`), die dieselbe Spalte
  beschreibt. Jetzt angeglichen.
- `TeamJoinRequest.invite_code` ebenfalls unbegrenzt — jetzt
  `max_length=32` (echter Code ist 8 Zeichen).
- **`ChangePasswordRequest.current_password`** und
  **`SetPasswordRequest.current_password`** hatten kein Limit — beide
  gehen direkt in Argon2s `verify_password()`. Argon2 ist absichtlich
  speicher- und rechenintensiv; eine unbegrenzte Eingabe hier hätte
  erlaubt, einen beliebig großen Payload teuer hashen zu lassen — ein
  echter Verstärkungs-Vektor, den ein einfacher schneller Hash (wie
  `hash_token`s SHA-256) nicht hat. Jetzt `max_length=200`, konsistent
  mit `new_password`.
- `PasswordResetConfirmRequest.token` / `EmailVerificationConfirmRequest.token`
  — geringes Risiko (landen nur in SHA-256, nicht Argon2), trotzdem aus
  Konsistenzgründen auf `max_length=512` begrenzt.
- `GET /admin/drivers`s `search`-Parameter — Admin-only (also ohnehin
  höhere Vertrauensstufe), trotzdem für Konsistenz auf `max_length=100`
  begrenzt (das bereits vorhandene `limit`-Parameter war schon korrekt
  über `min(limit, 200)` gedeckelt).

### Tests

271/271 grün (260 zuvor + 11 neue in
`tests/test_security_review_v0_8.py`): jede der sieben neuen Grenzen mit
einem überlangen Payload (422 erwartet) UND mit einem normal-langen
Payload (weiterhin 200 erwartet, um zu bestätigen, dass die Grenzen
nicht zu eng sind) getestet. Zusätzlich `_slugify()` direkt mit
Path-Traversal-Payloads sowie ein voller Ende-zu-Ende-Upload mit
bösartigem `track_name`/`car_name`, der die tatsächlich gespeicherte
Datei auf Verbleib innerhalb der Storage-Wurzel prüft. Volle Suite
**4 Mal hintereinander** komplett grün.

### Nicht Teil von V0.8.2

PostgreSQL-spezifische Tests, Docker/Migration-Tests unter realer
Last, vollständige Admin-/Freunde-Dokumentation, abschließender
Release-Gate-Report — folgen in V0.8.3–V0.8.5. Infrastrukturabhängige
Punkte bleiben `BLOCKED – USER TEST REQUIRED`.

---

## V0.8.1 — E-Mail-Verifizierung (§2) + echter Concurrency-Deadlock behoben (§10)

Erste echte Unterversion des V0.8-NAS-Release-Zyklus (Versionsnummer ab
jetzt konsequent hochgezählt, nicht mehr bis Sprint-Ende zurückgehalten).

### §2 — E-Mail-Verifizierung

Letzter offener funktionaler Auth-Gap aus der ursprünglichen Spec,
bestätigt fehlend über den gesamten bisherigen V0.7.2-Sprint hinweg.
Jetzt vollständig:

**Migration 0011** — `drivers.email_verified` (Default `false`),
`drivers.email_verified_at`, neue Tabelle `email_verification_tokens`
(gleiches gehashtes, einmal verwendbares, ablaufendes Muster wie
`password_reset_tokens`/`team_invitations`). Rückwärtskompatibel per
Konstruktion: bestehende Fahrer mit bereits gesetzter E-Mail (aus der
Zeit vor diesem Feature) werden nicht rückwirkend gesperrt — Login bleibt
bewusst **unabhängig** vom Verifizierungsstatus (siehe `login()`s
Docstring); unverifiziert bedeutet nur einen Hinweis in den
Account-Einstellungen, keine Zugriffsbeschränkung.

**Wichtige Design-Entscheidung im Token-Modell:** Der
Verifizierungs-Token speichert die E-Mail-Adresse, die er bestätigt,
zum Zeitpunkt der Anfrage — nicht zur Bestätigungszeit von
`driver.email` gelesen. Grund: Ändert ein Fahrer seine E-Mail ein
zweites Mal, bevor er den ersten Link angeklickt hat, darf dieser alte
Link **nichts mehr verifizieren**, insbesondere nicht die inzwischen
neue E-Mail-Adresse. Mit eigenem Test abgesichert.

**Endpunkte**: `POST /accounts/verify-email/resend` (authentifiziert,
rate-limitiert, no-op bei bereits verifiziert, 400 ohne gesetzte
E-Mail), `POST /accounts/verify-email/confirm` (öffentlich,
Token-basiert). `set_password` löst eine Verifizierungsmail nur aus,
wenn sich die E-Mail tatsächlich ändert — ein reiner Passwortwechsel bei
gleicher, bereits verifizierter E-Mail setzt den Status nicht
zurück. `GET /accounts/me` (whoami) liefert jetzt zusätzlich `email` und
`email_verified`.

**Web-Frontend**: Account-Seite zeigt E-Mail + Verifizierungsstatus mit
Resend-Button; neue Seite `#/verify-email?token=...` für den Link aus
der E-Mail.

### Ein echter, tiefsitzender Bug gefunden und behoben (nicht nur der Test)

Der User verlangte ausdrücklich "ALL PASS, keine Tests abschwächen" —
das habe ich zum Anlass genommen, den seit Phase 1 bekannten,
gelegentlich fehlschlagenden Concurrency-Test wirklich zu reparieren,
statt ihn weiter nur als "bekannter Flake" zu vermerken.

**Root Cause gefunden:** `POST /telemetry/upload` war als `async def`
deklariert, führte darin aber durchgehend blockierende, synchrone
SQLAlchemy-Aufrufe aus — der einzige `async def`-Endpunkt im gesamten
Projekt (alle anderen sind bewusst normale `def`, die FastAPI automatisch
in einen echten Thread-Pool auslagert). Unter Last konnte das zu einem
echten Deadlock führen: Coroutine A öffnet eine SQLite-Transaktion
(`BEGIN IMMEDIATE`, hält den exklusiven Schreib-Lock), gibt dann bei
einem `await`-Punkt (Datei-Streaming) die Kontrolle an den Event-Loop
ab — Coroutine B startet daraufhin ihre eigene `BEGIN IMMEDIATE`, die
synchron (nicht-awaitbar, auf C-Ebene) auf den von A gehaltenen Lock
wartet und dabei den einzigen Event-Loop-Thread komplett blockiert. A
bekommt dadurch nie wieder Rechenzeit, um zu committen und den Lock
freizugeben — B wartet bis zum Timeout und schlägt dann fehl. Bestätigt,
indem `PRAGMA busy_timeout` versuchsweise auf 5s und 15s gesetzt wurde:
die Fehlschlagsdauer folgte exakt dem jeweiligen Timeout, statt dass ein
längerer Timeout die Fehlerrate gesenkt hätte — der Lock wurde nie
freigegeben, es war kein reines Timing-Problem.

**Fix**: `upload_lap` und die interne `_stream_to_temp_file`-Hilfsfunktion
von `async def` auf normales `def` umgestellt — Dateizugriff jetzt über
das synchrone `upload.file` (das zugrunde liegende
`SpooledTemporaryFile`) statt `await upload.read(...)`. FastAPI führt
normale `def`-Endpunkte automatisch in einem echten Thread-Pool aus,
womit zwei gleichzeitige Requests echte, unabhängige Datenbank-
Transaktionen bekommen, die sich gegenseitig nicht mehr blockieren
können. Betrifft ausschließlich SQLite (PostgreSQL, die tatsächliche
Produktionsdatenbank, kennt diese Single-Writer-Falle gar nicht) — der
zugrunde liegende Fehler (blockierende Aufrufe in einer `async
def`-Funktion) war aber unabhängig von der Datenbank ein echter
Korrektheitsfehler.

**Zusätzlich**: `PRAGMA busy_timeout = 5000` beim Verbindungsaufbau
gesetzt (fehlte komplett) — ein sinnvolles zusätzliches Sicherheitsnetz
für echte, kurzzeitige Konkurrenz, unabhängig vom jetzt behobenen
Deadlock.

**Verifikation, nicht nur Behauptung:** Der vormals flakige Test lief
**30 Mal hintereinander fehlerfrei** (vorher: intermittierend
fehlschlagend, exakt timeout-lang hängend). Vollständige Suite **5 Mal
hintereinander** komplett grün. Zusätzlicher echter End-to-End-Upload-
Smoke-Test (echter Serverprozess, kein Mocking) bestätigt, dass der
Upload-Endpunkt nach der Async→Sync-Umstellung weiterhin korrekt
funktioniert.

`tests/test_upload_streaming.py` musste an die neue synchrone
Schnittstelle angepasst werden (Fake-Upload-Objekt nutzt jetzt
`.file.read()` statt `await .read()`) — Testabsicht (Streaming in
begrenzten Chunks, keine unbegrenzte Einzel-Read) vollständig erhalten,
keine Abschwächung.

### Tests

260/260 grün (246 zuvor + 14 neue in `tests/test_email_verification.py`).
Migrationskette 0001→0011 auf leerer **und** bestehender DB (mit echten
Daten aus früheren Phasen) real durchlaufen, inklusive
Downgrade→Re-Upgrade. Zusätzlicher Realtest: ein Fahrer-Account aus
einer viel früheren Phase loggt sich nach der Migration weiterhin
korrekt ein und zeigt erwartungsgemäß `email_verified: false` (kein
rückwirkendes Aussperren).

### Version

`0.7.0` → `0.8.1` (Server **und** Client-Version, synchron gehalten).
Ab jetzt wird die Versionsnummer nach jeder V0.8-NAS-Teilversion
angehoben, nicht erst am Ende des gesamten Durchgangs.

### Nicht Teil von V0.8.1

Der Rest der V0.8-NAS-Checkliste folgt in weiteren Unterversionen:
Security-Review-Nachschlag (CSRF/Path-Traversal/Input-Validation konkret
geprüft), PostgreSQL-spezifische Tests, Docker/Migration-Tests,
vollständige Admin- und Freunde-Dokumentation, abschließender
Release-Gate-Report. Infrastrukturabhängige Punkte (Windows+LMU-Realtest,
NAS-Restart, Docker auf echter Hardware, Backup/Restore-Realtest,
SMTP-Realtest, Installer/Packaging) bleiben `BLOCKED – USER TEST
REQUIRED`.

---

## V0.8-NAS — Phase 1: Server-Migrationsfähigkeit (§7) + Health-Fix (§6)

Auftakt zum V0.8-NAS-Release (Ziel: stabile private Version für NAS +
Freundeskreis, kein neues Großfeature-Sprint). Erster Block: §7
("SEHR WICHTIG" markiert, komplett neu) plus ein kleiner, direkt
angrenzender §6-Fix. Versionsnummer bleibt vorerst `0.7.0` — Bump folgt
am Ende des V0.8-NAS-Release-Durchgangs, nicht nach jedem Teilschritt.

### Audit zuerst

Konkret geprüft statt angenommen: Server-URL ist im Client bereits
zentral in `client/config.py`s `ClientConfig.api_url` verwaltet (Env-Var
> gespeicherte Datei > interaktiver Wizard) und wird korrekt überall
durchgereicht — **kein** Hardcoding-Problem an vielen Stellen, wie §7
befürchtet. Die eigentliche Lücke war etwas anderes: es gab keinen
Mechanismus, mit dem ein Client von einem Serverwechsel überhaupt
erfährt. Ebenfalls geprüft: §6s geforderte verständliche Fehlermeldungen
(Auth-Fehler, LMU nicht gestartet, Upload-Fehler) waren bereits klar und
gut — nur der "Server offline beim Start"-Fall wurde bisher still
verschluckt (`except requests.RequestException: pass`).

### §7 — Kontrollierte Server-Migration

**`GET /health`** liefert jetzt optional ein `migrated_to`-Feld, gesetzt
über die neue Server-Einstellung `LMU_GARAGE_MIGRATED_TO`. Der Server
funktioniert währenddessen unverändert normal weiter — keine harte
Umschaltung, genau wie gefordert ("möglichst nichts bemerken").

**Client**: neuer CLI-Befehl `python -m client.main --follow-migration`
(`client/main.py`) — liest den aktuell konfigurierten Server einmalig
aus, prüft auf `migrated_to`, und aktualisiert bei Bedarf die
gespeicherte Konfiguration auf die neue URL (Zugangsdaten bleiben dabei
unverändert erhalten). Bewusst ein expliziter, separater Schritt statt
automatischem Umschalten mitten im laufenden Betrieb — das Umbiegen der
Ziel-URL aus einer Server-Antwort heraus verdient einen bewussten
menschlichen Moment, keine stille Hintergrundaktion. Die laufende
App zeigt beim nächsten Start-Health-Check klar an, dass ein Wechsel
ansteht und welcher Befehl dafür nötig ist.

### §6-Fix: stiller "Server offline"-Fall behoben

Der bestehende Start-Health-Check verschluckte eine nicht erreichbare
Verbindung bisher komplett (`except: pass`) — für jemanden ohne
Entwicklerkenntnisse unsichtbar, obwohl der Uploader im Hintergrund
längst erfolglos weiterversucht. Zeigt jetzt klar: "Could not reach the
server at \<url\> (...). Will keep retrying in the background."

### Tests

246/246 grün (240 aus dem V0.7.2-Sprint + 3 neue `tests/test_health.py`
+ 3 neue `tests/test_client_migration.py`). Zusätzlich zwei echte
Ende-zu-Ende-Durchläufe **ohne jedes Mocking**: ein echter Server-Prozess
mit `LMU_GARAGE_MIGRATED_TO` gesetzt, extern per HTTP abgefragt
(`/health` liefert `migrated_to` korrekt); danach derselbe echte
Server-Prozess plus der echte `follow_migration()`-Client-Code
(kein Fake-Response) — Konfigurationsdatei wird nachweislich auf die
neue URL aktualisiert, Zugangsdaten bleiben erhalten.

**Umgebungsauffälligkeit dabei behoben:** `tkinter` fehlte in dieser
Sandbox (`client.main` importiert die GUI und damit `tkinter`) — bisher
kein Problem, da keine bestehenden Tests `client.main` direkt
importierten. Per `apt-get install python3-tk` nachinstalliert (Ubuntu-
Archiv ist ohnehin erlaubte Netzwerk-Domain in dieser Umgebung); auf dem
echten Windows-Client bereits vorhanden, betrifft also nur diese
Test-Sandbox, nicht das Produkt.

### Selbst-Korrektur während der Umsetzung

Beim ersten Versuch, `migrated_to_url` in `server/config.py` zu
ergänzen, habe ich versehentlich die direkt benachbarten Felder
`db_pool_size`/`db_max_overflow` mitgelöscht (falsch abgegrenzter
`str_replace`-Block). Sofort beim nächsten Testlauf aufgefallen (Suche
nach den Feldnamen ergab keinen Treffer mehr) und vor jeder weiteren
Änderung korrigiert — an dieser Stelle bewusst erwähnt, damit
nachvollziehbar bleibt, dass der Fehler nicht unbemerkt weiterlief.

### Offen aus dieser Sprint-Vorgabe (folgt in weiteren Phasen)

§2 Email-Verifizierung — im gesamten bisherigen V0.7.2-Sprint nie gebaut,
trotz Nennung schon in der ursprünglichen Spec. Echter, bestätigter
Gap, keine Neuigkeit dieser Phase. Alle infrastrukturabhängigen
Punkte (§5 Windows+LMU-Realtest, §8 NAS-Restart/Docker-Realtest,
Backup/Restore-Realtest, §6 Installer/Packaging, SMTP-Realtest) bleiben
`BLOCKED – USER TEST REQUIRED`, wie in §12 explizit verlangt — dazu am
Ende des V0.8-NAS-Durchgangs ein vollständiger, ehrlicher Release-Gate-
Report.

---

## V0.7.2 — Completion Sprint, Phase 10: Landing Page

Versionsnummer weiterhin `0.7.0` bis Sprint-Abschluss. Keine neue
Migration.

### Ausgangslage

Die App landete bisher direkt auf dem Leaderboard — funktional, aber
genau das, was die ursprüngliche Spezifikation explizit nicht wollte
("Nicht: Login form + Dashboard, sondern eine Racing-Plattform-
Landingpage"). Neue Startseite (`web/js/pages/landing.js`), Route `#/`
(leerer Hash) — `#/leaderboard` bleibt als eigene, direkt erreichbare
Seite bestehen, nichts wurde entfernt.

### Ein neuer, bewusst schmaler Backend-Baustein

**`GET /leaderboard/recent`** (neu, öffentlich, kein Login) — die
zeitlich neuesten gültigen Runden plattformweit. Bisher gab es "recent
activity" nur pro Team (`/teams/{id}/activity`) oder pro Fahrer
(`/drivers/{id}/public`), nie plattformweit — für eine echte Startseite
fehlte genau das. Gleiche Vertrauensstufe wie das Leaderboard selbst
(zeigt ohnehin schon Fahrernamen + Rundenzeiten öffentlich) — keine neue
Datenkategorie, nur ein anderer, zeitlich sortierter Ausschnitt derselben
Lap-Tabelle.

### Bewusst NICHT gebaut: "Top Drivers" / "Team Records" global

Die ursprüngliche Spec nennt auch globale "Top Drivers" und "Team
Records" als mögliche Startseiten-Abschnitte. Beides hätte einen
eigenen, nicht-trivialen Ranking-Begriff erfunden (wonach ist ein Fahrer
"top"? über wie viele Teams hinweg "Team Records"?), ohne dass die
bestehende Architektur dafür schon eine natürliche Antwort liefert.
Passend zur expliziten Vorgabe "nicht überladen" bewusst weggelassen,
statt eine willkürliche Kennzahl zu erfinden.

### Web-Frontend

Hero ("GARAGE16 — Your LMU Data. Your Records. Your Team.") mit zwei
CTAs, deren zweite sich am Anmeldestatus orientiert ("Get Garage16" vs.
"Go to your Garage"), Recent-Activity-Liste (mit Verlinkung auf
Fahrerprofile aus Phase 7), kompakte "Explore"-Links, statischer
"What Garage16 Does"-Abschnitt (drei Zeilen, beschreibt nur tatsächlich
Vorhandenes). Der bisher unverlinkte "LMU GARAGE"-Schriftzug oben links
ist jetzt ein Link zur Startseite — vorher führte er nirgendwohin.

### Tests

240/240 grün (236 aus Phase 9 + 4 neue in
`tests/test_recent_activity.py`): öffentlich ohne Login abrufbar, zeigt
hochgeladene Runden neueste zuerst, schließt explizit invalidierte Runden
aus, respektiert das `limit`-Argument.

### Nicht Teil von Phase 10

Alles, was echte Infrastruktur braucht, bleibt unverändert offen:
Backup/Restore-Realtest, VPS-Deployment, echter LMU-Regressionstest,
SMTP-Realtest. Mit dieser Phase sind alle im Rahmen dieser Sandbox
sinnvoll umsetzbaren Teile des V0.7.2-Completion-Sprints abgeschlossen.

---

## V0.7.2 — Completion Sprint, Phase 9: Admin-UI-Frontend (§15)

Versionsnummer weiterhin `0.7.0` bis Sprint-Abschluss. Keine neue
Migration.

### Ausgangslage

Alle Admin-Endpunkte (`server/routers/admin.py`) existierten bereits seit
früheren Versionen, waren aber nur per curl/Swagger bedienbar — kein
Web-UI. §15 verlangt explizit "kein riesiges CMS", also bewusst schlank:
drei Tabs (Reports, Drivers, System), jeweils ein dünner Blick auf
bestehende bzw. minimal ergänzte Endpunkte.

### Backend-Lücken geschlossen, die eine echte UI brauchte

- **`GET /admin/reports`** lieferte bisher nur `lap_id` — für eine
  UI, in der ein Admin tatsächlich beurteilen soll, ob ein Report
  berechtigt ist, fehlten Strecke/Auto/Fahrer/Zeit der gemeldeten Runde
  komplett. Jetzt angereichert mit vollständigem Lap- und Reporter-Detail
  (`AdminLapReportOut`, neu — siehe unten zur Trennung von `LapReportOut`).
- **Keine Fahrer-Auflistung existierte überhaupt** — für "Driver
  moderation: Lock/Unlock" muss ein Admin einen Fahrer erst finden
  können. Neu: `GET /admin/drivers?search=...` — Teilstring,
  case-insensitive (bewusst großzügiger als das öffentliche
  `/drivers/lookup` aus Phase 6, das exakte Übereinstimmung verlangt: das
  hier läuft über den Operator-Admin-Token, eine kategorisch andere
  Vertrauensstufe, und enthält bewusst auch die E-Mail-Adresse — nirgends
  sonst in der API, aber hier für Support-/Moderationszwecke sinnvoll).
- **Kein Lap-Detail-Endpunkt für Admins** — neu: `GET /admin/laps/{id}`
  (§15 "Lap moderation: View, Invalidate").

**Eigener Fehler unterwegs gefunden und korrigiert:** `LapReportOut` wird
nicht nur von `GET /admin/reports` genutzt, sondern auch vom
Driver-eigenen `POST /telemetry/laps/{id}/report` (Bestätigung der
eigenen Meldung). Die erste Version dieser Änderung hätte das
Driver-Facing-Schema um admin-spezifische Felder erweitert und damit
dessen Response gebrochen — durch einen fehlschlagenden Bestandstest
(`test_report_and_resolve_flow`) sofort aufgefallen, bevor es committed
wurde. Sauber getrennt: `LapReportOut` (Driver-eigene Bestätigung, unverändert)
und `AdminLapReportOut` (angereicherte Admin-Ansicht, neues Schema).

### Web-Frontend

Neue Seite `web/js/pages/admin.js`, Route `#/admin` — bewusst **nicht**
in der sichtbaren Hauptnavigation verlinkt (nur Admins sollen sie
überhaupt suchen). Eigener Zugangsweg über ein Admin-Token-Gate
(`sessionStorage`, nicht `localStorage` — ein geteiltes Hochprivileg-Secret
soll nicht unbegrenzt im Browser liegen bleiben), komplett getrennt vom
Fahrer-Session-Mechanismus aus Phase 2 (`api.js` bekommt einen eigenen
`adminToken`-Requestpfad, der nie mit `Authorization`/Session-Cookie
vermischt wird und nie versehentlich die Fahrer-Session abmeldet, falls
der Admin-Token mal ungültig ist).

- **Reports**: Karte pro offenem Report mit Strecke/Auto/Fahrer/Zeit/
  Grund, Buttons "Invalidate lap" (fragt nach einem Grund, invalidiert
  die Runde und markiert den Report als erledigt in einem Zug) und
  "Dismiss report".
- **Drivers**: Suche, Sperr-Status, "Lock"/"Unlock" mit Grund-Abfrage.
- **System**: Live-Status aus dem bestehenden `/health`-Endpunkt.

### Tests

236/236 grün (226 aus Phase 8 + 10 neue in
`tests/test_admin_ui_backend.py`): angereicherte Reports zeigen
vollständiges Lap- und Reporter-Detail, die Driver-eigene
Report-Bestätigung bleibt nachweislich unverändert im alten Format,
Fahrer-Suche (Teilstring, case-insensitive, inkl. E-Mail wenn gesetzt),
Lap-Detail-Abruf inkl. 404, alle neuen Endpunkte lehnen fehlenden
Admin-Token mit 403 ab.

Zusätzlich ein echter Ende-zu-Ende-Durchlauf gegen einen laufenden
Prozess (nicht nur Pytest): Runde hochladen → melden → als Admin mit
vollem Kontext sehen → invalidieren → Report auflösen → aus der
offenen Liste verschwunden; Fahrer sperren → Zugriff wird mit 403
abgelehnt → entsperren → Zugriff funktioniert wieder.

### Nicht Teil von Phase 9

Landing Page bleibt offen. Übrige Infrastruktur-Punkte unverändert wie in
Phase 1–8 vermerkt — Backup/Restore-Realtest, VPS-Deployment, echter
LMU-Regressionstest, SMTP-Realtest bleiben abhängig von echter
Ausführung außerhalb dieser Sandbox.

---

## V0.7.2 — Completion Sprint, Phase 8: Dashboard-Neugestaltung (§6) + You-vs-Team (§21)

Versionsnummer weiterhin `0.7.0` bis Sprint-Abschluss. Keine neue
Migration.

### §21 — "You vs Team" zuerst nachgezogen

Beim Entwerfen des Dashboards aufgefallen: §21 war trotz zweifacher
Erwähnung in der Spec (§18, §21) nirgends tatsächlich gebaut —
`GET /teams/{id}/records` zeigte nur den Rekordhalter, nie den eigenen
Abstand des Aufrufers dazu. Da das Dashboard genau diese Daten braucht,
zuerst dort ergänzt: `TeamRecordOut` bekommt `your_lap_time`, `gap`,
`you_hold_it`. Bewusst `None` statt `0.0`, wenn der Aufrufer keine
gültige Runde auf dieser Kombination hat — `0.0` läse sich fälschlich als
"Gleichstand mit dem Rekord". Team-Dashboard zeigt die neue Spalte direkt
in der bestehenden Records-Tabelle.

### §6 — Echtes "YOUR GARAGE"-Cockpit

Das alte Dashboard war exakt die im ursprünglichen Audit (ganz zu Beginn
dieses Projekts) kritisierte reine Lap-Tabelle. Jetzt: **Your Best**
(schnellste gültige Runde pro Strecke, über alle Autos hinweg), **Your
Team** (pro Team: wie viele Team-Bestzeiten hält der Fahrer, plus die
knappste Lücke zu einer Bestzeit, die er nicht hält — beides aus dem
neuen §21-Datenfeld), **Recent Activity** (PR/WR/TEAM_BEST, wiederverwendet
aus dem in Phase 7 gebauten `GET /drivers/{id}/public`). Die bestehende
filterbare "alle Runden"-Tabelle bleibt vollständig erhalten, nur weiter
unten als "My Laps"-Sektion — keine Funktionalität verloren, nur
ergänzt.

**Kein neuer Backend-Endpunkt für den Cockpit-Teil nötig** — Phase 7s
`GET /drivers/{id}/public` liefert praktisch alles bereits (Statistiken,
persönliche Bestzeiten, Aktivität); der Cockpit-Teil ruft einfach das
öffentliche Profil des eigenen Fahrers ab plus `GET /teams/{id}/records`
je Team.

### Nebenbei gefunden und behoben: keine Möglichkeit, die eigene `driver_id` aus einem reinen Token zu ermitteln

Für den Cockpit brauchte es die eigene `driver_id` — die gab es für den
Legacy-"Token einfügen"-Wiederherstellungspfad im Web-Frontend bisher
nirgends (dort wurde nur ein frei eingetippter Anzeigename gespeichert,
nie echt verifiziert). Neuer, bewusst minimaler Endpunkt `GET /accounts/me`
(`WhoAmIOut`: nur `driver_id` + `display_name`, funktioniert sowohl mit
Bearer-Token als auch mit Session-Cookie). Der Token-Wiederherstellungs-
Flow im Frontend nutzt ihn jetzt, statt den frei eingetippten Namen zu
übernehmen — das bisher überflüssige, potenziell falsche Namensfeld
wurde entfernt.

### Tests

226/226 grün (220 aus Phase 7 + 3 neue in `tests/test_you_vs_team.py`
+ 3 neue Whoami-Tests in `tests/test_web_sessions.py`): Rekordhalter
sieht `gap: 0.0` und `you_hold_it: true`, Nicht-Halter sieht den
korrekten positiven Abstand, ein Fahrer ganz ohne eigene Runde auf einer
Kombination sieht `null` statt `0.0`. Whoami funktioniert sowohl über
Bearer-Token als auch über Session-Cookie, lehnt unauthentifizierte
Anfragen ab.

Der Dashboard-Umbau selbst (reines Frontend, kein neuer Endpunkt) wurde
per Node-Syntax-Check geprüft, nicht in einem echten Browser — wie bei
den Frontend-Änderungen in Phase 2 bleibt das offen.

### Nicht Teil von Phase 8

Landing Page, Admin-UI-Frontend (§15), separate "My Garage"-Unterseiten
(Overview/My Laps/My Records/My Teams als eigene Routen — aktuell ist
das Dashboard eine einzige Seite, die alles zeigt) bleiben offen. Übrige
Infrastruktur-Punkte unverändert wie in Phase 1–7 vermerkt.

---

## V0.7.2 — Completion Sprint, Phase 7: Öffentliches Fahrerprofil (§4/§5)

Versionsnummer weiterhin `0.7.0` bis Sprint-Abschluss. Keine neue
Migration — reine Erweiterung um neue Endpunkte/Response-Felder, keine
Schema-Änderung an bestehenden Tabellen.

### §4 — Öffentliches Fahrerprofil

Neuer Endpunkt `GET /drivers/{id}/public` (`server/routers/drivers.py`,
öffentlich, kein Login nötig — gleiches Vertrauensniveau wie das
Leaderboard selbst, das denselben Fahrer ja bereits namentlich zeigt):
Statistiken (Laps/Tracks/Cars), persönliche Bestzeiten (gruppiert nach
`car_model` wie schon `records.py`s PR/WR-Logik — nicht `car_name`, damit
Livree-/Nummernwechsel nicht als separates Auto zählen), letzte Aktivität
(PR/WR/TEAM_BEST, wiederverwendet aus `RecordEvent` wie schon die
Team-Activity-Konsole), Teams. Explizit **nie** enthalten: E-Mail,
Passwort-Hash, Auth-Token, Client-Secret, Sessions, Lock-Status — keines
dieser Felder wird für diesen Endpunkt überhaupt abgefragt, nicht nur
"herausgefiltert".

Neue Web-Seite `web/js/pages/driver.js`, Route `#/driver/<id>`.

### §5 — Leaderboard mit Fahrerprofilen verbunden

`LeaderboardEntry` und `TeamLeaderboardEntry` bekommen ein neues Pflicht-
feld `driver_id` (additiv, alle bestehenden Felder unverändert).
Fahrernamen im globalen Leaderboard, im Team-Leaderboard und in der
Team-Mitgliederliste verlinken jetzt auf `#/driver/<id>`.

### Nebenbei gefunden und behoben: Status-Leiste zeigte Cookie-Logins als "nicht angemeldet"

Beim Anfassen von `router.js` aufgefallen: `updateStatus()` prüfte direkt
`session?.authToken`, nicht `store.isSignedIn()` — ein echter, aus Phase 2
stammender Bug. Nach einem E-Mail/Passwort-Login (Cookie-Session, kein
`authToken` im lokalen State, siehe Phase 2) zeigte die Status-Leiste
oben "not signed in", obwohl der Fahrer tatsächlich angemeldet war.
`state.js`s `isSignedIn()` wurde in Phase 2 korrekt für beide
Session-Arten angepasst — diese eine zusätzliche, unabhängige Prüfstelle
in `router.js` wurde dabei übersehen. Jetzt behoben (nutzt
`store.isSignedIn()`).

### Tests

220/220 grün (210 aus Phase 6 + 10 neue in
`tests/test_driver_profile.py`): Profil ohne Login abrufbar, 404 für
nicht existierenden Fahrer, Statistiken spiegeln echte Laps wider,
persönliche Bestzeiten korrekt nach Track+car_model gruppiert (schnellere
Runde ersetzt langsamere, unterschiedliche Autos bleiben getrennt),
Aktivität spiegelt echte RecordEvents, Team-Mitgliedschaften werden
gelistet, explizite Prüfung, dass keines der verbotenen Felder
(`email`, `password_hash`, `auth_token`, `client_secret`, `is_locked`,
`token_hash`) irgendwo in der Antwort vorkommt — inklusive Prüfung, dass
die tatsächlichen Token-Werte des Test-Accounts nirgends im Response-Text
auftauchen, nicht nur die Schlüsselnamen. Fahrer ohne Laps liefert Nullen
statt Fehler. Zusätzlich zwei Tests, dass `driver_id` jetzt tatsächlich
in beiden Leaderboard-Antworten (global und team-scoped) ankommt.

### Nicht Teil von Phase 7

Keine Fahrersuche/-verzeichnis (bewusst — siehe Phase 6s
`/drivers/lookup`-Entscheidung, dieselbe Begründung gilt hier). Übrige
offene Punkte unverändert wie in Phase 1–6 vermerkt — Dashboard-
Neugestaltung, Landing Page, Admin-UI-Frontend, Backup/Restore-Realtest,
VPS-Deployment, echter LMU-Regressionstest, Installer-Planung,
Doku-Vervollständigung bleiben die größten verbleibenden Blöcke.

---

## V0.7.2 — Completion Sprint, Phase 6: Echtes Team-Invitation-Modell (§9.2)

Versionsnummer weiterhin `0.7.0` bis Sprint-Abschluss.

### Umgesetzt

**Migration 0010** — neue Tabelle `team_invitations`: `team_id`,
`invited_driver_id`, `created_by_driver_id`, `status`
(pending/accepted/declined/expired/revoked), `created_at`, `expires_at`,
`accepted_at`, `declined_at`, `revoked_at`. Das bestehende
`Team.invite_code`-System bleibt unverändert bestehen — beide
Mechanismen existieren nebeneinander (Code = "jeder mit dem Link kann
beitreten", Invitation = "eine konkrete Person wird gezielt gefragt und
muss zustimmen"), genau wie im Auftrag vorgesehen.

**Bewusste Scope-Entscheidung:** Adressierung ausschließlich über
`invited_driver_id`, nicht zusätzlich per E-Mail (obwohl die Spec beides
als Möglichkeit nennt). E-Mail ist laut §3.11 privat und darf nirgends
öffentlich auftauchen — ein E-Mail-Einladungs-Flow für noch nicht
registrierte Personen bräuchte einen eigenen Token-und-Registrierungs-
Verknüpfungs-Mechanismus (ähnlich komplex wie Password-Reset), für eine
kleine Community-Plattform, auf der der bestehende Invite-Code genau
diesen Fall ("jemand ohne Account einladen") bereits abdeckt. Additiv
später nachrüstbar, falls nötig.

**Neues Problem dabei gefunden und gelöst:** Ein Owner kennt den
Anzeigenamen eines Teamkollegen, aber nirgends dessen `driver_id` — die
API hat bisher an keiner Stelle eine Fahrer-ID zu einem Anzeigenamen
offengelegt (Leaderboard-Einträge zeigen nur den Namen). Neuer, bewusst
minimaler Endpunkt `GET /drivers/lookup?display_name=...`
(`server/routers/drivers.py`, neuer Router — zugleich ein sinnvoller
künftiger Ort für die öffentlichen Driver-Profile aus §4): exakte,
case-insensitive Übereinstimmung, niemals unscharfe Suche (das wäre ein
De-facto-Fahrerverzeichnis — bewusst nicht gebaut), erfordert Login,
liefert nie mehr als `driver_id` + `display_name`. Da `display_name`
keine Eindeutigkeitsbeschränkung hat, liefert die Suche bei Namens-
kollisionen alle Treffer zurück statt stillschweigend den "ersten" zu
wählen.

**Endpunkte** (`server/routers/teams.py`, neuer `invitations_router` für
die nicht team-skopierten Aktionen):
- `POST /teams/{id}/invitations` (owner/admin) — lehnt ab, wenn der
  Fahrer nicht existiert, bereits Mitglied ist, oder bereits eine
  ausstehende Einladung hat (Eindeutigkeit app-seitig geprüft, nicht per
  DB-Constraint — siehe Migration-Kommentar für die Begründung)
- `GET /teams/{id}/invitations` (owner/admin) — komplette Historie,
  wendet verzögerte Ablauf-Prüfung beim Lesen an (kein Cronjob, gleiches
  Muster wie PasswordResetToken)
- `POST .../invitations/{id}/revoke`, `POST .../invitations/{id}/resend`
  (owner/admin) — Resend nur für pending/expired, **nicht** für
  declined/revoked (eine bereits getroffene Entscheidung wird nicht
  stillschweigend wiederbelebt — dafür eine neue Einladung schicken)
- `GET /invitations/mine`, `POST /invitations/{id}/accept`,
  `POST /invitations/{id}/decline` — für den eingeladenen Fahrer selbst.
  Accept ist idempotent gegenüber einer zwischenzeitlich per Invite-Code
  bereits erfolgten Mitgliedschaft (kein Crash, keine doppelte Zeile).
  404 statt 403, wenn eine fremde Einladungs-ID aufgerufen wird — keine
  Enumeration, welche IDs existieren.

**Rate-Limit**: neuer `team_invite`-Bucket (§13).

**Web-Frontend**: neue "Invitations"-Karte im Team-Dashboard (owner/admin
— Einladen per Namenssuche, Liste mit Resend/Revoke), neue "Invitations"-
Sektion auf der Teams-Übersichtsseite (Accept/Decline für an mich selbst
gerichtete Einladungen).

### Tests

210/210 grün (189 aus Phase 5 + 21 neue in
`tests/test_team_invitations.py`): Namenssuche (Treffer, keine Treffer,
mehrere Treffer bei Namenskollision, Login-Pflicht), Einladen (nur
owner/admin, nicht für bereits-Mitglieder, keine doppelte ausstehende
Einladung, 404 für nicht existierenden Fahrer), Accept/Decline (inkl.
Idempotenz bei zwischenzeitlichem Invite-Code-Beitritt, 404 statt 403 bei
falschem Empfänger, kein doppeltes Accept), Revoke (inkl. Ablehnung nach
bereits erfolgtem Accept), Ablauf (verzögerte Status-Transition beim
Lesen, Accept nach Ablauf abgelehnt), Resend (belebt eine abgelaufene
Einladung wieder, aber nicht eine abgelehnte), Listings (`/invitations/mine`
über mehrere Teams hinweg, Zugriffsschutz auf die Team-Liste für
Nicht-Admins).

Migrationskette 0001→0010 auf leerer und bestehender DB real
durchlaufen, inklusive Downgrade→Upgrade.

### Nicht Teil von Phase 6

E-Mail-basierte Einladungen (siehe Scope-Entscheidung oben). Übrige
offene Punkte unverändert wie in Phase 1–5 vermerkt — Driver-Profile,
Dashboard-Neugestaltung, Admin-UI, Backup/Restore-Realtest,
VPS-Deployment, echter LMU-Regressionstest, Installer-Planung,
Doku-Vervollständigung bleiben die größten verbleibenden Blöcke.

---

## V0.7.2 — Completion Sprint, Phase 5: Team-Zeitfilter + Leaderboard-Kaskade

§9.4 und §9.5 aus dem Team-System-Abschnitt. Versionsnummer weiterhin
`0.7.0` bis Sprint-Abschluss.

### §9.4 — Team-Zeitfilter (Today/7 Days/30 Days/All Time)

Neuer Endpunkt `GET /teams/{id}/stats?window=today|7d|30d|all` (Default
`all`) — liefert Laps/PBs/Active-Drivers/Records für den gewählten
Zeitraum. Bewusst ein **zusätzlicher** Endpunkt statt einer Änderung an
`GET /teams/{id}` selbst: dessen feste 14-/7-Tage-Felder
(`active_driver_count`, `pbs_this_week`) bleiben unverändert, für alles,
was bereits auf diese Response-Form vertraut.

"Today" ist ein rollierendes 24-Stunden-Fenster, kein Kalendertag — der
Server kennt die Zeitzone des Aufrufers nicht, ein rollierendes Fenster
ist eindeutiger als eines, das stillschweigend von einer beliebigen
Zeitzone abhinge. Alle Zeitfenster filtern auf `Lap.uploaded_at`
(serverseitiger Zeitstempel), konsistent mit der bereits bestehenden
"aktiv"-Logik im Member-Listing — nicht auf das client-gelieferte
`recorded_at`.

**Web-Frontend**: neue "Team Statistics"-Karte im Team-Dashboard mit
Zeitraum-Umschalter, lädt beim Öffnen automatisch "All Time".

### §9.5 — Team-Leaderboard: echte dreistufige Kaskade

**Audit-Befund:** Der Filter im Team-Dashboard war Track → Class — zwei
Stufen, keine drei. Ein Filter für "exaktes Auto" existierte zwar am
Backend (`GET /teams/{id}/leaderboard/car/...`) und sogar schon als
ungenutzter `api.js`-Wrapper, war aber nirgends in die Kaskaden-UI
eingebunden. Zusätzlich unterstützte `catalog/cars` nur eine Filterung
nach Track, nicht nach Klasse — ein Fahrer hätte bei Track=Le Mans,
Class=Hypercar im Auto-Dropdown trotzdem GT3-Fahrzeuge derselben Strecke
angezeigt bekommen. Genau das "nur gültige Kombinationen anzeigen" aus
der Spezifikation war also nicht erfüllt.

**Fix**: `GET /leaderboard/catalog/cars` bekommt einen optionalen
`car_class`-Parameter (rückwärtskompatibel — ohne ihn unverändertes
Verhalten, genutzt von der öffentlichen Leaderboard-Seite, die nicht
kaskadiert). Team-Dashboard: echte dritte Stufe Track → Class → Car, bei
der jede Stufe nur die tatsächlich in der vorherigen Kombination
gefahrenen Werte anbietet. Eine leere Car-Auswahl ("All cars in this
class") verhält sich wie bisher (Class-Leaderboard); eine konkrete
Auswahl wechselt auf das bereits vorhandene, bisher ungenutzte
Car-Leaderboard.

### Tests

189/189 grün (181 aus Phase 4 + 8 neue in
`tests/test_v0_7_2_team_stats_and_cascade.py`): alle vier Zeitfenster,
Default-Fenster, Ablehnung eines ungültigen Fensters (422),
Mitgliedschafts-Pflicht, PBs/Records spiegeln tatsächliche
RecordEvents wider (nicht nur Lap-Zählungen) — sowie für die Kaskade:
`catalog/cars` mit Klassenfilter schließt die jeweils andere Klasse an
derselben Strecke korrekt aus, ohne Klassenfilter weiterhin
rückwärtskompatibel.

### Nicht Teil von Phase 5

§9.2 (echtes Invitation-Modell mit pending/accepted/declined/expired/
revoked) — deutlich größerer Umfang (neue Tabelle, mehrere Endpunkte,
Frontend-UI), eigene Phase. Übrige offene Punkte unverändert wie in
Phase 1–4 vermerkt.

---

## V0.7.2 — Completion Sprint, Phase 4: Telemetry-Storage-Lifecycle

Kleiner, risikoarmer Block aus §12. Versionsnummer weiterhin `0.7.0` bis
Sprint-Abschluss.

### Umgesetzt

**`server/telemetry_lifecycle.py`** (neu, reine Lese-Logik) — zwei
Kategorien von Drift zwischen DB und Storage-Verzeichnis, bewusst
unterschiedlich behandelt:
- **DB-Zeile, Datei fehlt** (`find_missing_files`): wird **nie**
  automatisch aufgelöst — ein Lap, der seine Telemetrie-Datei verloren
  hat (schlechtes Restore, manueller Eingriff, Bug), ist ein
  Datenverlust-Ereignis, das ein Mensch sehen und entscheiden soll, kein
  Fall für stille Selbstheilung.
- **Datei vorhanden, keine DB-Zeile** (`find_orphaned_files`): echter
  Aufräum-Kandidat, aber nur wenn älter als ein konfigurierbares
  Mindestalter (Default 1 Stunde). Grund: `routers/telemetry.py` benennt
  die hochgeladene Datei auf ihren finalen inhaltsadressierten Pfad um,
  **bevor** die Lap-Zeile committet wird — eine Datei ohne passende
  Zeile könnte schlicht ein Upload sein, der gerade noch läuft. Das
  Mindestalter ist der eigentliche Sicherheitsmechanismus hier, keine
  beliebige Stellschraube.

**`scripts/telemetry_cleanup.py`** (neu, CLI) — Dry-Run per Default
(reine Berichterstattung, löscht nie etwas ohne `--execute`),
`--min-age-hours` zum Anheben der Sicherheitsspanne. Fehlende Dateien
werden unter jedem Flag nur gemeldet, nie automatisch behoben.

### Tests

181/181 grün (176 aus Phase 3 + 5 neue in `tests/test_telemetry_lifecycle.py`):
frische Datei ohne DB-Zeile wird korrekt NICHT als Waise erkannt
(Sicherheitsmechanismus), alte Datei ohne DB-Zeile wird erkannt, eine
alte Datei mit passender Lap-Zeile wird niemals als Waise behandelt
(Kernkorrektheit), Lap-Zeile mit fehlender Datei wird gemeldet,
Lap-Zeile mit vorhandener Datei wird nicht fälschlich gemeldet.

Zusätzlich das CLI-Skript selbst real durchgespielt (nicht nur die
Pytest-Suite): echte Verzeichnisstruktur mit einer realen und einer
künstlich gealterten verwaisten Datei angelegt — Dry-Run lässt beide
Dateien unangetastet, `--execute` löscht ausschließlich die Waise, die
reale Datei bleibt nachweislich erhalten.

### Nicht Teil von Phase 4

Kein automatischer Cron-Eintrag angelegt (nur in README dokumentiert, da
Aktivierung eine bewusste Betreiber-Entscheidung sein sollte, kein
automatisch mitgeliefertes Verhalten). Übrige offene Punkte unverändert
wie in Phase 1–3 vermerkt.

---

## V0.7.2 — Completion Sprint, Phase 3: Security-Header + Docs-Gating

Kleiner, risikoarmer Block aus §14 (Security Review), direkt im Anschluss
an Phase 2. Versionsnummer weiterhin `0.7.0` bis Sprint-Abschluss.

### Umgesetzt

**`server/main.py`**: neue `security_headers`-Middleware auf jeder
Response — `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`,
`Referrer-Policy: strict-origin-when-cross-origin` immer,
`Strict-Transport-Security` nur wenn `require_https` aktiv ist (sonst
würde ein lokaler Dev-Server ohne TLS dem Browser HSTS einprägen — aktiv
schädlich). Bewusst **keine** Content-Security-Policy hier: dieser
Prozess liefert eine JSON-API (plus optional Swagger-UI unter `/docs`),
nicht das Fahrer-Frontend selbst (das ist Caddys Job als separater
Static-File-Server) — eine CSP gehört dorthin, wo tatsächlich HTML/Fonts/
CDN-Skripte geladen werden.

**Swagger/ReDoc standardmäßig aus in Produktion**: `/docs`, `/redoc`,
`/openapi.json` dokumentieren die komplette API-Oberfläche kostenlos —
unnötige Recon-Fläche für eine öffentliche Production-Instanz. Neuer
Schalter `LMU_GARAGE_ENABLE_API_DOCS` (Default aus in Produktion, immer
an in Dev) für alle, die es trotzdem wollen (z. B. beim Bauen eines
alternativen Clients).

### Selbst gefundener und behobener Testfehler

Die ersten Versionen der Docs-Gating-Tests nutzten `importlib.reload()`
auf `server.main`/`server.config`, um Produktions-Settings zu simulieren.
Das hat einen bereits bestehenden, unabhängigen Test
(`test_upload_streaming.py`) zum Fehlschlagen gebracht — aber nur, wenn
er NACH dieser Testdatei lief. Ursache: `settings` ist ein einzelnes,
zur Importzeit gebundenes Singleton-Objekt; jedes andere Modul
(`routers/*.py`, `rate_limit.py`, ...) hält seine eigene `from .config
import settings`-Referenz auf die ursprüngliche Instanz. Ein Reload von
`server.config` ersetzt `config_module.settings` durch ein neues Objekt,
das diese anderen Module nie zu sehen bekommen — der globale
Test-Prozess driftet in zwei parallele Zustände auseinander. Behoben
durch Umbau: `main.py` bekommt eine reine `_docs_urls()`-Entscheidungs-
funktion (unabhängig testbar ohne App/Reload), die HSTS-Tests
mutieren stattdessen direkt ein Attribut auf dem geteilten
`settings`-Singleton (`monkeypatch.setattr`, automatisches Teardown) —
kein Reload mehr nötig, keine Kontamination. Vor dem Fix real
reproduziert (Test schlug fehl, wenn nach dieser Datei ausgeführt;
isoliert lief er durch), nach dem Fix mit voller Suite bestätigt.

### Tests

176/176 grün (166 aus Phase 2 + 10 neue in `tests/test_security_headers.py`).

### Nicht Teil von Phase 3

Content-Security-Policy fürs Frontend selbst (gehört in Caddyfile/HTML,
nicht in diesen Prozess), CSRF-Token-Mechanismus (bewusste Entscheidung
aus Phase 2: `SameSite=Lax` + JSON-only-Endpunkte gelten als
ausreichend für den aktuellen Rahmen, siehe dortiger Eintrag). Übrige
offene Punkte unverändert wie in Phase 1/2 vermerkt.

---

## V0.7.2 — Completion Sprint, Phase 2: Web-Session-Architektur

Fortsetzung des Completion Sprints aus Phase 1 (siehe Eintrag darunter für
den Gesamtkontext und die Prozessvorgabe). Versionsnummer bleibt weiterhin
bei `0.7.0`, bis der komplette Sprint-Umfang steht.

### Der Anlass: ein echter, selbst verursachter Bug

Bei der in §3.7 geforderten Prüfung, ob der Web-Login von
`localStorage`-Bearer-Token auf serverseitige Cookie-Sessions umgestellt
werden soll, fiel auf: `POST /accounts/login` (aus Phase V0.7.0) rief
`_issue_fresh_token()` auf — das überschreibt `drivers.token_hash`,
dieselbe Spalte, die der Desktop-Client als `auth_token` nutzt. Meldete
sich ein Fahrer im Web mit E-Mail+Passwort an, während sein LMU-Client
lief, wurde dessen Token dabei **stillschweigend ungültig** — die nächste
hochgeladene Runde wäre mit 401 fehlgeschlagen. Direkter Verstoß gegen
§3.2 ("darf NICHT vermischt werden").

Das war der eigentliche Auslöser für diese Phase, nicht nur die
"nach Möglichkeit"-Formulierung aus §3.7: die korrekte Behebung des Bugs
(eine wirklich getrennte Session-Domäne) ist ohnehin der Großteil der
Arbeit für eine Cookie-Session-Architektur — beides in einem Zug zu
machen vermeidet, dieselbe Stelle zweimal anzufassen.

### Entscheidung

Neue, vom Desktop-Client-Token vollständig unabhängige Web-Session:
- HttpOnly, `Secure` (aktiv wenn `require_https`), `SameSite=Lax`
- Login liefert **keinen** Token mehr im Response-Body — die Session lebt
  ausschließlich im Cookie
- `get_current_driver` akzeptiert weiterhin den Bearer-Header (Desktop-
  Client, bestehende Tests, Legacy-Token-Restore im Web) **oder** das
  Session-Cookie — eine Identitätsauflösung, zwei akzeptierte Nachweise,
  kein zweites paralleles Auth-System
- `SameSite=Lax` statt `None` bewusst gewählt: vermeidet den Bedarf an
  einem eigenen CSRF-Token-Mechanismus für diesen Sprint (Lax blockiert
  bereits Cookie-Versand bei Cross-Site-Fetch/XHR und bei
  Cross-Site-Formular-POST; die verbleibende Lücke — Cross-Site-GET bei
  Top-Level-Navigation — trifft eine reine JSON-API ohnehin nicht). Kehrseite:
  ein Frontend, das nicht von derselben Domain wie die API ausgeliefert
  wird (die im README dokumentierte separate-Cloudflare-Pages-Variante),
  bekommt das Cookie bei Cross-Origin-Fetches gar nicht zugestellt — für
  diese Deployment-Variante bleibt der Bearer-Token-Weg (Registrierung +
  Token einfügen) die einzig funktionierende Option. Dokumentiert, nicht
  stillschweigend kaputt.

### Umgesetzt

**Migration 0009** — neue `sessions`-Tabelle (`session_token_hash` nur
gehasht, `driver_id`, `created_at`, `expires_at`, `revoked_at`,
`last_used_at`, `user_agent` rein informativ). Rührt `drivers.token_hash`
an keiner Stelle an.

**`server/sessions.py`** (neu) — Session-Erzeugung, Auflösung (inkl. der
inzwischen dritten Instanz derselben naive-vs-aware-Datetime-Falle wie
beim Discord-Link-Code und beim Password-Reset-Token, gleich behoben),
Revoke, Revoke-All. Fixe 30-Tage-Gültigkeit, kein Sliding-Window.

**`server/auth.py`**: `get_current_driver` prüft zuerst den
Bearer-Header, dann das Session-Cookie.

**`server/routers/accounts.py`**: `login()` umgebaut (Session statt
Token-Rotation), neu: `POST /accounts/logout` (aktuelle Session),
`POST /accounts/logout-all` (alle Web-Sessions, Desktop-Token bleibt
unberührt), `GET /accounts/sessions` (Account-Settings-Tab aus §3.9),
`DELETE /accounts/sessions/{id}` (einzelne Session, auf den eigenen
Fahrer beschränkt).

**Frontend**: `api.js` schickt jetzt `credentials: 'include'` auf jedem
Request; `state.js` unterscheidet Cookie-Session (`viaCookie: true`,
kein Secret in `localStorage`) von der alten Bearer-Token-Session
(Legacy-Restore-Pfad, bleibt bestehen); `auth.js`s Login-Karte nutzt die
Cookie-Session, die Registrierung persistiert den zurückgegebenen
Desktop-Token nicht mehr als Dauer-Web-Session, sondern nur transient für
den unmittelbar folgenden "Passwort setzen"-Schritt; `account.js`
bekommt eine neue "Sessions"-Karte (Liste, "aktuelles Gerät"-Markierung,
Einzel-Revoke, Sign-out-everywhere) und trennt die bestehende
Token/Secret-Karte klar als "Desktop Client Credentials" von der
Browser-Session.

### Tests

166/166 grün (151 aus Phase 1 + 15 neue in `tests/test_web_sessions.py`).
Kernstück: ein direkter Regressionstest, dass ein Web-Login den
Desktop-Client-Token unverändert lässt — das ist der Test, der beim
V0.7.0-Login-Design gefehlt hat und den eigentlichen Bug erst
unsichtbar gemacht hätte. Dazu: Cookie ohne Token im Body, Zugriff rein
über Cookie ohne Authorization-Header, Ablehnung bei fehlendem/kaputtem
Cookie, Logout beendet nur die eigene Session, Logout-All beendet alle
Web-Sessions eines anderen simulierten Browsers gleichzeitig (über einen
zweiten, unabhängigen Test-Client), Sessions-Liste markiert die aktuelle
Session korrekt und exponiert nirgends Desktop-Credentials, Revoke einer
fremden Session-ID schlägt mit 404 fehl statt mit Erfolg. Zwei
bestehende Tests aus `test_password_auth.py` an das neue
Response-Format angepasst (kein `auth_token` mehr im Login-Body).

Migrationskette 0001→0009 auf leerer **und** auf der bestehenden
V0.7.0/Phase-1-DB real durchlaufen (inkl. Downgrade→Upgrade), plus ein
Ende-zu-Ende-Realtest gegen die migrierte DB: Login setzt tatsächlich ein
Cookie, der ursprüngliche Desktop-Token funktioniert danach nachweislich
weiter, Sessions-Liste zeigt die echte Session korrekt an.

### Nicht Teil von Phase 2

Frontend-Cookie-Flows wurden nur per Node-Syntax-Check geprüft, nicht in
einem echten Browser — das bleibt offen. Ebenfalls offen (wie in Phase 1
vermerkt): Email-Verifikation, echtes Invitation-Modell, Team-Zeitfilter,
kaskadierende Leaderboard-Filter, Security-Header (CSP/HSTS),
Driver-Profile, Dashboard-Neugestaltung, Admin-UI, Telemetry-Orphan-
Cleanup, Backup/Restore-Realtest, VPS-Deployment, echter
LMU-Regressionstest, Installer-Planung, vollständige Dokumentation.

---

## V0.7.2 — Completion Sprint, Phase 1: Team-Datenintegrität + Performance

**Noch keine vollständige V0.7.2** — dies ist Phase 1 eines mehrstufigen
Completion Sprints (26 Abschnitte, siehe Auftrag) nach dem vorgegebenen
Prozess Audit → Architektur prüfen → implementieren → Tests → Regression.
Versionsnummer bleibt bewusst bei den einzelnen Zwischenschritten auf
`0.7.0`, bis der komplette Sprint-Umfang steht (siehe Abschnitt "Version"
im Auftrag) — dieser Eintrag dokumentiert Phase 1, kein Release-Tag.

### Audit-Befunde (Auszug — echte Code-Prüfung, keine Annahmen)

- **§9.1 "Member-Displayname-Bug"**: existiert nicht. `m.driver.display_name`
  in `teams.py` löst korrekt über die Membership-eigene SQLAlchemy-
  Relationship auf, nicht über den eingeloggten Aufrufer. Bereits vor
  diesem Sprint durch einen Test abgesichert. Nicht "repariert" — stattdessen
  mit einem expliziten 3-Fahrer-Regressionstest zusätzlich verriegelt, wie
  im Auftrag als Prüfschritt vorgegeben.
- **§9.6 N+1-Queries**: bestätigt real. Pro Teammitglied liefen 3 separate
  Queries (Lap-Anzahl, PB-Anzahl, letzte Aktivität) — bei 20 Mitgliedern
  60 Queries für einen einzigen Endpunkt-Aufruf.
- **§10 Team-Name-Eindeutigkeit**: bestätigt real. `Team.name` war ein
  reiner `UNIQUE`-String ohne Normalisierung; "Garage16" und "garage16"
  hätten zwei Teams erzeugt.
- **§3.10 Account-Löschung + Team-Ownership**: bestätigt, kritisch. Ein
  Owner konnte sich selbst löschen und sein Team ohne Owner (oder als
  vollständig verwaistes Team mit invite_code, aber null Mitgliedern)
  zurücklassen.
- **Nebenbei gefunden**: `DELETE /teams/{id}` hat nie verknüpfte
  `DiscordChannel`-Zeilen bereinigt — auf SQLite unsichtbar (keine
  FK-Durchsetzung), auf PostgreSQL ein echter `IntegrityError` beim
  Löschen eines Teams mit registriertem Discord-Announcement-Kanal.

### Fixes

**Team-Namen case-insensitiv eindeutig** (Migration 0008): funktionaler
Unique-Index auf `lower(name)` statt einer reinen String-Spalte —
identisch in `server/models.py` (für Dev/Test via `create_all`) und der
Migration (Produktion), damit beide Umgebungen exakt dasselbe Schema
erzwingen, nicht nur der Python-Code (`_name_taken()`-Helper in
`teams.py`, jetzt in `create_team` und `update_team` verwendet). Die
Migration prüft vor dem Anlegen des Index auf bereits existierende
Groß-/Kleinschreibungs-Kollisionen und bricht mit einer konkreten
Fehlermeldung ab, statt eine kryptische DB-Exception zu werfen oder
Daten stillschweigend zu verwerfen.

**`server/team_lifecycle.py`** (neu) — ersetzt die bisher duplizierte
Team-Lösch-Logik in `teams.py`/`accounts.py`/`admin.py` durch eine
gemeinsame Implementierung:
- `delete_team_cascade()`: FK-sicheres Löschen (inkl. des oben gefundenen
  DiscordChannel-Gaps)
- `transfer_ownership_to_next_member()`: befördert das am längsten
  dabei-seiende verbleibende Mitglied (dieselbe Heuristik wie Migration
  0006s ursprüngliches Owner-Backfill)
- `resolve_owned_teams_before_driver_deletion()`: der neue
  Ownership-Guard — berechnet alle Konflikte, BEVOR irgendetwas
  geschrieben wird, damit ein blockierter Löschversuch die DB garantiert
  unverändert lässt

**Account-Löschung ist jetzt sicher** (`DELETE /accounts/me`,
`DELETE /admin/drivers/{id}`): Ist der Account Owner eines Teams mit
anderen Mitgliedern, wird die Löschung mit `409` abgelehnt und nennt das
betroffene Team — der Auftrag ist hier explizit: "Owner muss Team
übertragen oder Team löschen", kein stiller Auto-Transfer. War der
Account der einzige im Team, wird das jetzt leere Team automatisch mit
gelöscht (kein sinnvoller Grund, es verwaist stehen zu lassen). Der
Admin-Endpunkt bekommt zusätzlich `?force=true` als bewusste Eskalation
(automatischer Ownership-Transfer statt Blockade) — für Fälle wie einen
gesperrten Cheater, der selbst nicht mehr übertragen wird.

**N+1-Fix im Team-Member-Listing** (`GET /teams/{id}/members`): von
`3 × Mitgliederzahl` Queries auf 4 feste Queries unabhängig von der
Teamgröße — eine Membership-Abfrage mit eager-geladenem Driver (kein
Lazy-Load pro Zeile für `display_name`) plus je eine gruppierte
Aggregat-Query für Lap-Anzahl, PB-Anzahl und letzte Aktivität, in Python
zusammengeführt. Identisches Antwortformat, identische Werte.

### Tests

151/151 grün (142 aus V0.7.0 + 9 neue in `tests/test_v0_7_2_phase1.py`):
Team-Name-Kollision case-insensitiv bei Erstellung und Umbenennung
(inkl. Umbenennen auf den eigenen aktuellen Namen in anderer
Schreibweise — muss weiterhin erlaubt sein), 3-Fahrer-Korrektheitstest
fürs Member-Listing, Query-Count-Test (per SQLAlchemy-Event-Listener
gemessen, muss unter 12 bleiben unabhängig von 9 Mitgliedern),
Solo-Owner-Löschung räumt das Team mit auf, Multi-Member-Owner wird
blockiert (409, DB bleibt unverändert), Löschung nach explizitem
Ownership-Transfer funktioniert, Admin-`force`-Override transferiert
automatisch, und ein Regressionstest, dass der neue Guard
Nicht-Owner-Accounts überhaupt nicht betrifft.

Migrationskette 0001→0008 auf leerer DB **und** auf einer bestehenden
V0.7.0-DB mit echten Daten (Login, Team-Erstellung, Case-Kollision) real
durchlaufen — inklusive downgrade → upgrade erneut. Ein vorbestehender,
von diesen Änderungen unabhängiger Flake in
`test_concurrency.py::test_duplicate_identical_upload_race_keeps_exactly_one_lap`
beobachtet (SQLite-Lock-Contention unter Last, 3/3 grün isoliert
wiederholt) — nicht Teil dieses Sprints, da vor den hier gemachten
Änderungen bereits vorhanden und nicht reproduzierbar isoliert.

### Nicht Teil von Phase 1 (folgt in weiteren Phasen dieses Sprints)

Email-Verifikation, Web-Session-Architektur (localStorage vs.
HttpOnly-Cookie — bewusst noch keine Entscheidung getroffen, siehe unten),
Driver-Profile, Dashboard-Neugestaltung, echtes Invitation-Modell,
Team-Zeitfilter, kaskadierende Leaderboard-Filter, Security-Header
(CSP/HSTS), Admin-UI (Frontend), Telemetry-Orphan-Cleanup,
Backup/Restore-Realtest, VPS-Deployment, echter LMU-Regressionstest,
Installer-Planung, vollständige Dokumentation.

### Offene Entscheidung vor Phase 2

Abschnitt 3.7 verlangt, den Wechsel von `localStorage`-Bearer-Token zu
serverseitigen HttpOnly-Cookie-Sessions zu **prüfen** ("nach Möglichkeit
ersetzen") — das ist die einzige Änderung in der gesamten Spec, die die
bestehende Auth-Architektur grundlegend verändern würde (jeder
API-Aufruf, CORS-`credentials`-Verhalten, CSRF-Schutz, beide bestehenden
Login-Flows). Bevor ich das anfasse, sollte das explizit entschieden
werden statt nebenbei mitgezogen zu werden.

---

## V0.7.0 — E-Mail/Passwort-Login, Password-Reset, Account-Settings-UI

Umgesetzt nach der vollständigen Gap-Analyse gegen den Master-Entwicklungsplan
(V0.6.8 → weitere Phasen). Zwei unabhängige, einzeln testbare Schritte in
einer Version zusammengefasst, da beide klein und risikoarm sind.

### 1. Records-Fix: `car_name` → `car_model` (V0.6.9-Teil)

**Echter Daten-Inkonsistenz-Bug**, gefunden bei der Analyse: `server/records.py`
verglich PR/WR/TEAM_BEST bisher nach `Lap.car_name` (exakter Fahrzeugname
inkl. Team/Nummer/Livree), während Leaderboard (`routers/leaderboard.py`)
und Team-Dashboard (`routers/teams.py`) seit V0.6.3 nach `car_model`
gruppieren (exaktes Fahrzeugmodell, ohne Livree/Nummer). Zwei parallele,
sich widersprechende Auffassungen von "gleiches Auto" aus derselben
`Lap`-Tabelle — genau das, was der Master-Plan unter "Record System" (§24)
explizit ausschließt.

**Konkrete Auswirkung des Bugs:** Ein Fahrer, der die Livree/Startnummer
desselben Fahrzeugmodells wechselte, startete ungewollt eine neue PR-Kette.
Ein TEAM_BEST-Event konnte ausgelöst werden (und an Discord gemeldet
werden), obwohl die tatsächliche, im Team-Dashboard angezeigte Bestzeit
(car_model-basiert) sich gar nicht änderte — oder umgekehrt.

**Fix:** Neue `_car_identity_filter()`-Hilfsfunktion in `records.py` —
gruppiert nach `car_model`, fällt für Laps ohne `car_model` (Clients vor
V0.6.3) auf den alten `car_name`-Vergleich zurück, damit bestehende
PR/WR-Historie nicht rückwirkend bricht. `car_name` bleibt weiterhin das
Anzeige-Feld auf `RecordEvent` (Discord-Text), ist aber nicht mehr an der
Vergleichslogik beteiligt.

3 neue Tests (`tests/test_records_car_identity.py`): PB überlebt
Livree-Wechsel, TEAM_BEST stimmt mit der eigenen Team-Records-Abfrage
überein, Rückwärtskompatibilität für Pre-V0.6.3-Laps ohne `car_model`.

### 2. Account-Settings-UI nachgezogen

`GET /accounts/me/export` (DSGVO-Auskunft) und `DELETE /accounts/me`
(Account-Löschung) existierten im Backend bereits vor V0.6.8, waren aber
im Web-Frontend nirgends verlinkt — ein Nutzer konnte sein
Auskunfts-/Löschrecht faktisch nicht über die UI wahrnehmen. Neue Karte
"Data & Privacy" auf der Account-Seite: "Download my data" (lädt die
Exportdaten als JSON-Datei herunter) und "Delete account" (mit
Bestätigungsdialog, danach automatisches Abmelden). Neue `.btn--danger`-
Klasse im bestehenden Design-System (keine neue Farbe — nutzt die
vorhandene `--signal`/`--signal-dim`-Palette, dieselbe wie bei
Fehlermeldungen).

### 3. E-Mail/Passwort-Account-System (§3–8 des Master-Plans)

Die größte im Audit gefundene Lücke: Es gab keinen Weg, ein Driver-Konto
mit E-Mail+Passwort zu sichern oder wiederherzustellen — nur den bei der
Registrierung einmalig gezeigten `auth_token`. Ging der verloren, war der
Account laut `revoke`-Endpunkt-Dokumentation "terminal, keine Möglichkeit
zurück".

**Bewusst kein Bruch mit der bestehenden Architektur:** Der Desktop-Client
braucht weiterhin nie ein Passwort — er authentifiziert sich unverändert
über `auth_token`+`client_secret` (siehe `server/security.py`). E-Mail
+Passwort ist eine **zusätzliche** Anmeldeoption obendrauf, kein Ersatz.
Login gibt denselben Bearer-`auth_token` zurück, den es auch schon gibt —
keine zweite, parallele Session-Mechanik (Cookies/JWT) eingeführt.

**Neue Endpunkte** (`server/routers/accounts.py`):
- `POST /accounts/set-password` — E-Mail+Passwort auf einem bestehenden
  (bisher token-only) Driver hinterlegen, oder ändern. Ein Endpunkt für
  beide Fälle: bei bereits gesetztem Passwort ist `current_password`
  Pflicht (verhindert, dass jemand mit gestohlenem `auth_token` den
  echten Besitzer aussperrt), beim ersten Mal nicht.
- `POST /accounts/login` — E-Mail+Passwort → neuer `auth_token`.
  Generische Fehlermeldung ("Invalid email or password") unabhängig
  davon, ob die E-Mail existiert, kein Passwort gesetzt ist, oder das
  Passwort falsch ist — verhindert User-Enumeration. Stellt außerdem den
  Zugriff wieder her, falls der Token zuvor per `/accounts/revoke`
  widerrufen wurde (das war vorher der Punkt ohne Rückweg).
- `POST /accounts/change-password` — Passwort ändern (authentifiziert
  über `auth_token`, nicht über das alte Passwort selbst als Auth).
- `POST /accounts/password-reset/request` /
  `POST /accounts/password-reset/confirm` — klassischer Forgot-Password-
  Flow. Reset-Token: kryptografisch zufällig (`secrets.token_urlsafe`,
  dieselbe Funktion wie für `auth_token`), 30 Minuten gültig, einmalig
  verwendbar, **nur als Hash in der DB** (neue Tabelle
  `password_reset_tokens` — bewusst nicht in `LinkCode` untergebracht,
  da dessen `code`-Feld Klartext speichert, was für einen
  Account-Übernahme-fähigen Reset-Token nicht akzeptabel ist).
  `/request` antwortet immer mit 200, unabhängig davon, ob die E-Mail
  existiert (kein User-Enumeration-Leck). Setzt **nicht** den
  Desktop-Client-`auth_token`/`client_secret` zurück — Web-Passwort und
  Client-Credentials sind bewusst getrennte Domänen.

**Passwort-Hashing:** Argon2id über `argon2-cffi`, Standardparameter der
Library (kein selbstgebautes KDF, wie vom Master-Plan gefordert).
Passwort-Stärkeprüfung nach NIST SP 800-63B-Prinzip (Mindestlänge 10
Zeichen statt starrer Zusammensetzungsregeln, die nachweislich zu
vorhersehbaren Ersetzungen wie "Passwort1!" führen).

**E-Mail-Versand:** Abstrahiert über `server/email_provider.py` —
`DevelopmentEmailProvider` (Default, loggt nur — passend für NAS/
Solo-Betrieb ohne echte Mail-Infrastruktur) und `SmtpEmailProvider`
(echter SMTP-Versand, per `LMU_GARAGE_EMAIL_PROVIDER=smtp` + SMTP-Zugangs-
daten in `.env` aktivierbar). **Nicht gegen einen echten SMTP-Server
getestet** in dieser Entwicklungsumgebung (kein Netzwerkzugriff auf
beliebige SMTP-Ports) — auf Korrektheit geprüft, aber wie bei
Docker/PostgreSQL in `docker-compose.yml` gilt: der erste echte Versand
gegen einen echten SMTP-Server ist der eigentliche Test.

**Migration 0007:** `drivers.email` (unique, nullable), `drivers.
password_hash` (nullable), neue Tabelle `password_reset_tokens`. Beide
neuen Spalten nullable — bestehende token-only Driver bleiben ohne
Änderung gültig, exakt dasselbe Non-Breaking-Muster wie in 0005/0006.

**Web-Frontend:** Neue "Sign in"-Karte auf der Account-Seite (E-Mail
+Passwort, mit "Forgot password?"-Link), neue "Email & Password"-Karte
für signed-in Driver (Passwort setzen/ändern), neue Route
`#/reset-password?token=...` für den Link aus der Reset-E-Mail.

**Nebenbei gefunden und gefixt:** Dieselbe naive-vs-aware-Datetime-Falle
wie beim Discord-Link-Code-Bug aus V0.6.8 — `PasswordResetToken.expires_at`
kommt aus der DB naiv zurück (kein `timezone=True` auf der Spalte, unter
SQLite und PostgreSQL gleichermaßen), ein Vergleich mit einem
zeitzonenbewussten "jetzt" hätte mit `TypeError` abgebrochen. Gleicher
Fix wie dort: `.replace(tzinfo=None)` auf der Vergleichsseite.

### Tests

142/142 grün (125 aus V0.6.8 + 3 Records-Fix + 14 zuvor bereits neue
Team/Catalog-Tests aus der Baseline). Ergänzt um 17 neue Tests für den
kompletten Passwort-Flow (`tests/test_password_auth.py`): Set-Password
(inkl. Pflicht-Prüfung des aktuellen Passworts bei Änderung),
E-Mail-Normalisierung/-Eindeutigkeit, Login (inkl. Groß-/Kleinschreibung,
generische Fehlermeldungen, Wiederherstellung nach Revoke, gesperrte
Accounts), vollständiger Password-Reset-Flow inklusive Ablauf und
Einmal-Verwendbarkeit des Tokens.

### Nicht umgesetzt / bekannte Lücken (bewusst zurückgestellt)

- Restore-Test des Backups (real, mit DB-Zerstörung) — braucht echte
  NAS-Infrastruktur, nicht in dieser Sandbox durchführbar
- VPS-Deployment-Variante (`docker-compose.vps.yml`) real getestet
- Fehlende Frontend-Seiten aus der Gap-Analyse (Landing Page, öffentliches
  Fahrerprofil, "YOUR GARAGE"-Dashboard mit PB/Team-Best-Übersicht statt
  reiner Lap-Tabelle)
- Echtes Invitation-System mit pending/expired/cancelled (aktuell weiterhin
  nur der einfache Invite-Code)
- SMTP-Versand nie gegen einen echten Mailserver verifiziert

### Nächster sinnvoller Schritt

VPS-Deployment einmal real testen (Migration 0007 läuft dabei automatisch
mit, siehe `docker/entrypoint.sh`), danach die fehlenden Frontend-Seiten
(Landing Page, Fahrerprofil, echtes Dashboard) aus der Gap-Analyse.

---

## V0.6.8 — Team-Dashboard mit Rollen, Statistiken, Team-Leaderboard

Vollständiges Team-System, gebaut nach expliziter Vorgabe: bestehende
Architektur wiederverwenden, keine neuen Zähler-Spalten, keine
künstliche Abstraktion. Alle Statistiken werden live aus `Lap` und
`RecordEvent` berechnet — keine gespeicherten Zähler, die aus dem Ruder
laufen könnten.

### Neue Rollen (Migration 0006)

`TeamMembership.role` — `owner` / `admin` / `member`. Bestehende Teams:
das jeweils älteste Mitglied wird automatisch zum Owner (Datenmigration,
kein Team bleibt ohne). `Team.description` und
`Team.discord_announcements_enabled` (Default `true`, nicht-brechend)
neu dazu.

**Rechte** (serverseitig durchgesetzt, nicht nur im Frontend versteckt):
- **Owner**: alles, inkl. Team löschen, Ownership übertragen. Kann selbst
  nicht entfernt werden — erst Ownership abgeben oder Team löschen.
- **Admin**: Team bearbeiten, Mitglieder verwalten — **kein** Löschen,
  **kein** Owner-Ersatz, kann keine anderen Admins entfernen.
- **Member**: alles ansehen, sich selbst jederzeit aus dem Team entfernen
  ("Leave").

### Neue Endpunkte (`server/routers/teams.py`)

- `GET/PATCH/DELETE /teams/{id}` — Detail (inkl. live berechneter
  Statistiken), bearbeiten, löschen
- `GET /teams/{id}/members` — Mitgliederliste mit Rolle, Aktivitätsstatus
  (Upload in den letzten 14 Tagen), Lap-/PB-Zahl, letzte Aktivität
- `DELETE /teams/{id}/members/{driver_id}` — entfernen (oder selbst
  verlassen)
- `POST /teams/{id}/members/{driver_id}/role` — befördern/degradieren
- `POST /teams/{id}/transfer-ownership` — Owner-Wechsel
- `GET /teams/{id}/leaderboard/class|car/...` — Team-Leaderboard, reine
  Team-Mitglieder, inkl. `delta_to_leader`
- `GET /teams/{id}/records` — aktuelle Team-Bestzeit pro (Strecke, Auto),
  live aus `Lap` — keine gespeicherte "aktueller Rekord"-Tabelle
- `GET /teams/{id}/activity` — Aktivitäts-Konsole, letzte
  PR/WR/TEAM_BEST-Events der Team-Mitglieder — **wiederverwendet
  `RecordEvent`**, keine neue Activity-Log-Tabelle

`_ranked_leaderboard` aus `routers/leaderboard.py` nach
`server/leaderboard_ranking.py` ausgelagert — wird jetzt von zwei Routern
gebraucht, echte Wiederverwendung statt spekulativer Abstraktion.

### Discord-Bot-Steuerung (Punkt 3)

Team-Owner/Admin schalten im Dashboard "Team-Bestzeiten an Discord
posten" ein/aus (`Team.discord_announcements_enabled`). Der Bot
respektiert das jetzt in `_post_pending_events()` — kein separater
"Bot-Development"-Bereich, ein Schalter in den Team-Einstellungen reicht
für das, was der Bot aktuell überhaupt konfigurierbar hat.

### Web-Frontend

Neue Seite `#/team/<id>` — Übersicht, Mitgliederliste (mit
Rollen-Buttons), Team-Leaderboard (Track/Klassen-Dropdown, Gap zur
Führung), Team-Bestzeiten-Tabelle, Aktivitäts-Konsole. Team-Übersicht
(`#/teams`) verlinkt jetzt auf die Dashboards. Caddy-Routing-Bug für
`POST /teams` aus V0.6.7 bleibt behoben, unverändert relevant hier.

### Nebenbei gefundener, vorbestehender Bug

`discord_bot/bot.py`s Link-Code-Ablaufprüfung verglich eine
zeitzonenbewusste "jetzt"-Zeit mit einem aus der DB gelesenen,
naiven Zeitstempel (`DateTime`-Spalten ohne `timezone=True` liefern unter
SQLite **und** PostgreSQL naive Werte zurück) — hätte bei echter Nutzung
mit `TypeError` abgestürzt. Nie aufgefallen, weil kein Test je einen
echten Ablauf-Vergleich durchlaufen hat. Jetzt gefixt, an beiden
betroffenen Stellen.

### Tests

122/122 grün (104 aus V0.6.7 + 18 neue: Rollen/Rechte, Statistik-
Berechnung, Team-Leaderboard-Isolation, Team-Records, Activity-Konsole,
Discord-Schalter).

---

## V0.6.7 — Feedback aus dem ersten echten Nutzungstag

Drei konkrete Bugs behoben, ein Feature ergänzt — alle aus echtem
Feedback nach dem ersten kompletten Testtag.

### 1. Client-GUI: "Last lap" zeigte immer "—"

Der Telemetrie-Loop schickt ~60 Status-Updates/Sekunde ans GUI (für
Track/Auto/Rundenzähler), aber nur **eines pro Runde** trägt eine
`last_lap_time` — alle anderen setzen sie standardmäßig auf `None`.
`_apply()` hat bisher **jedes** Update bedingungslos übernommen, auch mit
diesem Standardwert — die echte Zeit wurde binnen Sekundenbruchteilen
vom nächsten der 60 Frames/Sekunde wieder auf "—" zurückgesetzt.
**Fix**: Das "Last lap"-Feld wird jetzt nur noch aktualisiert, wenn ein
Update tatsächlich eine abgeschlossene Runde trägt — sonst bleibt der
letzte Wert stehen. Gleichzeitig ergänzt: **gültig/ungültig wird jetzt
direkt angezeigt** (grün/rot), nicht nur im Log darunter.
Testbare Logik nach `client/gui/status.py` ausgelagert (kein `tkinter`
in dieser Entwicklungsumgebung verfügbar) — 4 neue Tests.

### 2. Web: Team erstellen hat nicht funktioniert

Echter Caddy-Routing-Bug: `handle /teams/*` verlangt zwingend einen
Schrägstrich danach. `POST /teams` (Team erstellen) ist der **einzige**
Endpunkt im gesamten API ohne irgendetwas nach dem Router-Präfix — alle
anderen Team-Endpunkte (`/teams/mine`, `/teams/join`,
`/teams/discord-link-code`) haben einen Suffix und funktionierten
deshalb bereits. Die reine `/teams`-Anfrage fiel durch zum
Static-File-Server statt den Server zu erreichen.
**Fix**: `handle /teams /teams/*` — beide Formen jetzt explizit erfasst.

**Weiterhin offen** (kein Bug, sondern fehlender Funktionsumfang): Was
man mit einem erstellten Team tun kann, ist noch sehr eingeschränkt
(kein Team-Leaderboard im Web, keine Mitgliederverwaltung). Braucht eine
bewusste Entscheidung, was genau gebraucht wird, bevor das gebaut wird.

### 4. Auto-Katalog statt manueller Pflege

Neue, schreibgeschützte Endpunkte: `GET /leaderboard/catalog/tracks`,
`/catalog/classes`, `/catalog/cars` — geben zurück, welche Werte
tatsächlich in hochgeladenen Runden vorkommen (per `SELECT DISTINCT`).
**Kein Code muss gepflegt werden**, wenn LMU neue Inhalte bekommt — ein
neues Auto taucht auf, sobald die erste Runde damit hochgeladen wurde.
`classes`/`cars` lassen sich optional auf eine Strecke eingrenzen
(`?track_name=...`) — genau das, was die Leaderboard-Seite braucht.

Web-Frontend (`leaderboard.js`) umgestellt: Freitextfelder → echte
Dropdowns, gefüllt aus diesen Endpunkten. Das behebt nebenbei auch das
Risiko aus dem vorherigen Fund (LMU liefert `"Hyper"`, nicht
`"Hypercar"`) — man tippt nichts mehr, man wählt aus, was wirklich
existiert.

5 neue Tests (u.a. Ausschluss von `NULL`-Werten bei Laps von Clients vor
V0.6.3, Streckenscoping, Deduplizierung über mehrere Fahrer).

### Tests

104/104 grün (99 aus V0.6.6 + 5 neue).

---

## V0.6.6 — P0-7a endgültig behoben (variabler Scoring-Sync-Versatz)

V0.6.5s Fix ging von einem **festen** 1-Frame-Versatz zwischen
`telemetry.lap_number`-Sprung und `scoring.lap_dist`-Reset aus. Ein
zweiter echter Testlauf (Michelin Raceway Road Atlanta, 5 Runden) hat
das widerlegt: gemessen wurden Versätze von **2, 2, 3, 3 und 4 Frames** —
nie fest. Zwei von drei gültigen Runden blieben mit dem V0.6.5-Fix
weiterhin fälschlich ungültig.

### Der endgültige Fix

`client/telemetry/parser.py` wartet nach einem `lap_number`-Sprung jetzt
nicht mehr eine feste Anzahl Frames, sondern beobachtet `scoring.lap_dist`
direkt: Frames werden so lange der **abschließenden** Runde zugeordnet,
bis `lap_dist` tatsächlich um mehr als 500m zurückspringt (ein echter
Reset — unmöglich durch normale Fahrt bei jeder realistischen
Geschwindigkeit/Poll-Rate zu erklären). Ein großzügiger Sicherheits-Deckel
(30 Frames) verhindert endloses Warten, falls `lap_dist` aus
unbekanntem Grund nie zurückspringt — dann wird trotzdem finalisiert,
mit einer Warnung im Log.

### Verifikation

- 4 neue/aktualisierte Parser-Tests mit den echten gemessenen Werten
  (2- und 4-Frame-Versatz, Sicherheits-Deckel).
- **End-zu-Ende-Simulation gegen die echte, hochgeladene CSV**: alle 5
  echten Runden aus der zweiten Testsession liefen durch den tatsächlichen
  `LapParser` — maximaler Rückwärtssprung über alle Runden: 2.2m (vorher:
  bis zu -4070m). Das ist der stärkste Beweis, den wir für diesen Fix
  haben — keine synthetischen Testdaten, sondern die echten Frames aus
  deiner Session.
- 95/95 Tests grün (93 aus V0.6.5 + 4 neue, 2 dabei ersetzt).

### Für dich zum Nachtesten

Nach dem Update auf V0.6.6: eine neue Session fahren, danach
`GET /telemetry/laps` prüfen — alle sauber gefahrenen Runden sollten
jetzt durchgängig `is_valid: true` zeigen, nicht nur gelegentlich.

---

## V0.6.5 — Fünf echte Bugs aus dem ersten Ende-zu-Ende-NAS-Test

Alle fünf Punkte wurden durch einen tatsächlichen Deployment-Testlauf
auf einer echten Synology-NAS (Cloudflare Quick Tunnel) sowie eine echte
LMU-Session gefunden — keiner davon war vorher in Unit-Tests sichtbar,
weil bisher entweder SQLite/TestClient oder nur Health-/Register-Aufrufe
gegen den echten Stack getestet wurden, nie ein vollständiger,
echter Telemetrie-Upload gegen PostgreSQL + Docker-Volumes + echte LMU-
Daten.

### 1. Caddy meldete jede Anfrage fälschlich als "kein HTTPS"

`server`s `LMU_GARAGE_REQUIRE_HTTPS`-Prüfung liest `X-Forwarded-Proto`.
Caddy setzt diesen Header aber anhand seiner **eigenen** eingehenden
Verbindung — die ist bei Variante A (Cloudflare Tunnel) immer reines
HTTP, da Cloudflares Edge die öffentliche HTTPS-Terminierung übernimmt,
nicht Caddy selbst. Jede Anfrage wurde deshalb mit "HTTPS required"
abgelehnt, obwohl der Zugriff real über `https://` lief.
**Fix**: `docker/Caddyfile` setzt jetzt explizit
`header_up X-Forwarded-Proto https` in jedem `reverse_proxy`-Block —
korrekt für beide Varianten (bei Variante B terminiert Caddy ohnehin
selbst echtes HTTPS).

### 2. Datei-Leserechte gingen beim Transfer ZIP → Windows → NAS verloren

Sowohl das Bot-Entrypoint-Skript als auch `discord_bot/bot.py` selbst
waren nach `ZIP → Windows-Download → File-Station-Entpacken` für den
nicht-root-Nutzer im Container unlesbar (`Permission denied`).
**Fix**: Beide Dockerfiles (`Dockerfile.server`, `Dockerfile.discord_bot`)
führen jetzt `chmod -R a+rX` auf alle kopierten Anwendungsdateien aus —
unabhängig davon, welche Rechte die Quelldateien mitbringen.

### 3. `server.config`-Import legte unnötig ein Verzeichnis an

`config.py` rief beim reinen **Modul-Import** `mkdir()` auf dem
Telemetrie-Speicherpfad auf. Das brach den Discord-Bot-Container, der
`server.config` transitiv importiert (für DB-Zugriff), aber nie
Telemetrie schreibt und dafür auch keine Schreibrechte hat.
**Fix**: Zeile ersatzlos entfernt — `storage.py`s `telemetry_storage_dir()`
legt das Verzeichnis ohnehin schon korrekt an, und zwar nur dann, wenn
tatsächlich eine Datei geschrieben wird.

### 4. Frisches Docker-Volume gehörte `root`, Server durfte nicht schreiben

Docker legt den Mount-Punkt eines neuen, benannten Volumes beim ersten
Gebrauch selbst an — als `root`. Der Server läuft aber bewusst als
nicht-root `garage`. Der allererste echte Upload gegen ein frisches
Volume schlug deshalb mit `PermissionError` fehl.
**Fix**: `Dockerfile.server` setzt kein `USER garage` mehr — der
Container startet als `root`, `docker/entrypoint.sh` korrigiert die
Eigentümerschaft des Speicherpfads einmalig bei jedem Start und gibt
die Rechte dann kontrolliert über `gosu` ab, bevor Migration und Server
laufen. Neue Abhängigkeit: `gosu` (Debian-Standardpaket, minimal).

### 5. Scoring-/Telemetrie-Sync bestätigt und behoben (P0-7a)

Mit echten Road-Atlanta-Daten (4 von 4 Rundenübergängen) bestätigt: der
Frame, in dem `telemetry.lap_number` erstmals die neue Rundennummer
zeigt, trägt `scoring.lap_dist` noch vom **Ende der alten** Runde — der
tatsächliche Reset auf ~0 passiert erst einen Frame später. Der Parser
hat diesen Übergangs-Frame bisher fälschlich als ersten Sample der
**neuen** Runde behandelt, was einen Rückwärtssprung erzeugte, den
`server/validation.py` zu Recht als unplausibel ablehnte — alle
hochgeladenen Runden wurden als ungültig markiert.
**Fix**: `client/telemetry/parser.py` ordnet den Übergangs-Frame jetzt
der **abschließenden** Runde zu (sein korrekter letzter Distanzwert),
nicht der neuen. `_lap_start_time` wird für die neue Runde erst beim
nächsten echten Frame gesetzt (leicht präziser als vorher). Mit einem
Regressionstest, der exakt die realen Beobachtungswerte nutzt.

### Nebenbei: Absturz beim sauberen Beenden (reader.py)

`UnmapViewOfFile`/`CloseHandle` hatten keine expliziten `argtypes` —
ctypes behandelte die echte 64-Bit-Adresse/den Handle standardmäßig als
32-Bit-Zahl, was bei jedem sauberen Beenden (Strg+C, `close()`) mit
`OverflowError` abstürzte. Nur beim tatsächlichen Beenden auf echtem
Windows sichtbar, da bisherige Tests nie einen echten `close()` gegen
eine echte (große) Adresse ausgeführt hatten. Mit Regressionstest, der
prüft, dass `argtypes` tatsächlich gesetzt sind.

### Tests

93/93 grün (91 aus V0.6.4 + 2 neue: Parser-Regressionstest mit echten
Straßenzahlen, Reader-Regressionstest für die argtypes-Prüfung).

---

## V0.6.4 — Deployment-Architektur (NAS/Cloudflare Tunnel + VPS, austauschbar)

Reine Infrastruktur-Arbeit, kein Client-Code geändert (Client bleibt bei
V0.6.3). Ziel: geschlossener Kreis auf einem Synology-NAS betreiben,
ohne dass ein späterer Umzug auf einen VPS Anwendungscode anfassen muss.

### Plattformneutraler Docker-Compose-Stack

- `docker-compose.yml`: Basis-Stack (PostgreSQL, Server, Discord-Bot,
  Caddy), keine NAS-spezifischen Pfade, keine Synology-Paket-Features —
  nur benannte Volumes.
- `docker-compose.vps.yml`: Override-Datei für Variante B, fügt
  ausschließlich die Port-Freigaben (80/443) auf `reverse_proxy` hinzu.
  Alles andere (Images, Volumes, Healthchecks) bleibt identisch.
- **Zwei Zugangsvarianten, nur per Env-Var umschaltbar, kein
  Datei-/Codewechsel:**
  - Variante A (NAS): Cloudflare Tunnel, kein offener Router-Port.
    `docker compose --profile tunnel up -d`
  - Variante B (VPS): direkter Reverse Proxy mit Caddys Auto-HTTPS.
    `docker compose -f docker-compose.yml -f docker-compose.vps.yml up -d`
  - Die Caddyfile selbst ist jetzt **eine einzige Datei** für beide
    Varianten — Umschaltung über `{$CADDY_SITE_ADDRESS:80}` (Caddy-native
    Env-Var-Platzhalter mit Default).

### Secrets: .env ODER Docker Secrets, wählbar pro Wert

- Neue `docker/entrypoint.sh` (Server) und `docker/entrypoint-discord-bot.sh`
  (Bot, bisher gar kein eigenes Entrypoint) lösen `<VAR>_FILE`-Konventionen
  auf und bauen `LMU_GARAGE_DB_URL` **im Container** aus den
  `POSTGRES_*`-Einzelteilen zusammen — notwendig, weil Compose's eigene
  `${VAR}`-Interpolation auf dem Host passiert und nie ein nur im
  Container gemountetes Secret-File sehen könnte. Kein Codeunterschied
  zwischen beiden Wegen auf Python-Seite.

### Healthchecks für alle vier Dienste

- `db`: `pg_isready` (unverändert)
- `server`: `GET /health` (prüft selbst schon die DB-Verbindung) via
  Python (kein zusätzliches `curl`/`wget` im schlanken Server-Image nötig)
- `discord_bot`: **neuer Heartbeat-Mechanismus** — ein Bot öffnet keinen
  Port, daher berührt `announce_loop()` jetzt jeden Zyklus eine
  Heartbeat-Datei; der Healthcheck prüft, ob sie in den letzten 2 Minuten
  aktualisiert wurde. 2 neue Tests.
- `reverse_proxy`: Caddys eigene Admin-API

### Frontend-Entkopplung (Cloudflare Pages)

- `web/config.js` (neu): eine Zeile, die pro Deployment gesetzt wird —
  leer für "gleiche Domain wie API" (Standard), oder die volle API-URL
  für ein separates Cloudflare-Pages-Deployment.
- **Echter kleiner Bugfix dabei gefunden**: `web/js/api.js`s
  Fallback-Logik behandelte einen bewusst gesetzten leeren String
  identisch zu "gar nicht gesetzt" (`window.X || default` — ein leerer
  String ist in JS falsy). Das hätte die Cloudflare-Pages-Trennung
  unterlaufen. Jetzt: `typeof window.X === "string" ? window.X : default`.

### Backups — beide Varianten dokumentiert

- NAS-nativ: Synology Task Scheduler ruft das bestehende
  `scripts/backup.sh` auf.
- Containerisiert (neu, `docker/backup-loop.sh`,
  `docker compose --profile backup up -d`): identisch auf NAS und VPS,
  kein Host-Cron nötig, überlebt einen Serverumzug unverändert.

### Monitoring

- Healthchecks oben. Für echte Ausfall-Benachrichtigung: externer
  Uptime-Ping (z.B. UptimeRobot kostenlos) auf `/health`, mit
  Discord-Webhook — bewusst kein Uptime-Kuma-Container vorgeschlagen,
  das wäre für die aktuelle Größe (Freundeskreis/Liga) mehr
  Infrastruktur als nötig.

### Dokumentation

- README: zwei vollständige Abschnitte "Synology NAS" und "VPS", plus
  expliziter "Umstieg NAS → VPS"-Abschnitt, Secrets-Erklärung,
  Backup-Optionen, Monitoring — und ein Abschnitt, der offen benennt,
  dass der **komplette Multi-Container-Stack-Test noch aussteht**
  (kein Docker in dieser Entwicklungsumgebung verfügbar).
- `.gitignore` / `package_release.bat` um `secrets/`-Ordner ergänzt.

### Tests

- 91/91 grün (89 aus V0.6.3 + 2 neue Heartbeat-Tests).
- **Weiterhin nicht verifizierbar in dieser Umgebung**: der komplette
  Stack via `docker compose up` auf echter Hardware. Einzelne Bausteine
  (PostgreSQL-Migrationen, Concurrency, CRUD) waren bereits in V0.6.2/3
  gegen echtes PostgreSQL geprüft — das Zusammenspiel aller vier
  Container inklusive Caddy/Tunnel ist der nächste konkrete
  Verifikationsschritt vor Produktivbetrieb.

---

## V0.6.3 — Leaderboard-Architektur (Klasse + exaktes Modell)

Feature-Arbeit, freigegeben nach Abschluss des LMU-Validation-Gates
(alle 7 Punkte PASS oder bewusst zurückgestellt — siehe V0.6.2-Eintrag).

### Neue Datenfelder

- `Lap.car_class` und `Lap.car_model` — aus LMU's `VehicleScoringInfoV01`
  (`vehicle_class` / `veh_filename`, beide bereits ABI-verifizierte
  Offsets, keine neue Struct-Arbeit nötig). Beide **nullable**: Laps von
  Clients vor V0.6.3 funktionieren unverändert weiter, tauchen nur in
  keinem der beiden Leaderboards auf.
- Migration 0005.
- Kompletter Datenfluss verdrahtet: `parser.py` (Erfassung aus dem
  Scoring-Struct) → `recorder.py` (lokale JSON) → `uploader.py`
  (Allowlist erweitert — **kritisch**, sonst würden die Felder exakt wie
  beim P0-1-Bug beim Upload stillschweigend verschwinden) →
  `schemas.py` (optional, `extra="forbid"`-sicher) → `models.py` →
  `routers/telemetry.py`.

### Neue Leaderboard-Struktur

- `GET /leaderboard/class/{track}/{car_class}` — **Hauptleaderboard**:
  jede Fahrzeugklasse (Hypercar/GT3/...) an einer Strecke, alle Modelle
  dieser Klasse zusammen.
- `GET /leaderboard/car/{track}/{car_model}` — **Sub-Leaderboard**:
  exaktes Fahrzeugmodell an einer Strecke, Team/Startnummer/Baujahr
  spielen keine Rolle (die stecken in `car_name`, nicht `car_model`).
- Der alte Endpoint `GET /leaderboard/{track}/{car_name}` wurde ersetzt
  (nicht parallel weitergeführt) — Web-Frontend, Discord-Bot und alle
  Tests sind entsprechend aktualisiert.
- Web-Frontend: Umschalter "By class" / "By exact car" auf der
  Leaderboard-Seite.
- Discord `/leaderboard`: filtert jetzt auf `car_model` statt `car_name`
  (gleiche Team/Nummer-Ignorierung wie im Web).

### Tests

- `tests/test_leaderboard_architecture.py` — 5 neue Tests, die exakt die
  angeforderte Trennung beweisen: zwei Modelle einer Klasse landen
  zusammen im Hauptleaderboard, aber getrennt im Sub-Leaderboard; zwei
  Teams im selben Modell landen zusammen im Sub-Leaderboard; Laps ohne
  Klasse/Modell (alte Clients) brechen nichts und tauchen einfach in
  keinem der beiden Leaderboards auf.
- Bestehende Tests (`test_integration_upload_flow.py`,
  `test_moderation.py`, `test_parser.py`) auf die neue Struktur
  angepasst.
- Testsuite: 89/89 grün (84 aus V0.6.2 + 5 neue).

### Sonstiges

- `scripts/dump_shared_memory.py` erfasst jetzt zusätzlich
  `vehicle_class`/`veh_filename` in der Diagnose-CSV — bisher nur aus dem
  Struct-Layout abgeleitet (ABI-verifiziert), inhaltlich gegen echte LMU-
  Daten noch nicht bestätigt. Bei Gelegenheit gegenprüfen.
- `LMU_GARAGE_MIN_CLIENT_VERSION` bewusst **nicht** angehoben — die neuen
  Felder sind optional, Clients ab V0.5.3 bleiben voll kompatibel.

---

## V0.6.2 — Bestätigter Bugfix aus echter LMU-Verifikation (Test d)

Gefunden und zweifach reproduziert (2026-09-24/25) auf Michelin Raceway
Road Atlanta: `dump_shared_memory.py` zeigte `Connected.` und schrieb
0 Frames, obwohl LMU nachweislich geschlossen war.

- **Ursache bestätigt**: `mmap.mmap(-1, size, tagname, access=mmap.ACCESS_READ)`
  verhindert auf diesem Windows/Python-Setup NICHT die Neuanlage einer
  frischen, nullgefüllten Speicherzuordnung — die im Modul-Docstring der
  Vorversion dokumentierte Annahme ("ACCESS_READ verhindert das") war
  falsch. Die Zuordnung wurde also stillschweigend neu angelegt statt mit
  einem echten Fehler abzubrechen.
- **Fix**: `client/telemetry/reader.py` nutzt jetzt direkt die
  Win32-API `OpenFileMappingW` + `MapViewOfFile` über `ctypes` (keine neue
  Abhängigkeit, kein `pywin32` nötig). `OpenFileMappingW` kann eine
  Zuordnung ausschließlich öffnen, niemals neu anlegen — liefert `NULL`
  zurück, wenn LMU nicht läuft. Keine Mehrdeutigkeit mehr möglich.
- **Regressionstest**: `tests/test_reader_shared_memory.py` — 4 neue
  Tests, die `ctypes.WinDLL` mocken (laufen plattformunabhängig,
  bestätigen die eigene Logik: Exception bei `NULL`-Handle, sauberes
  Aufräumen bei Teilfehler, echtes Lesen über die gemappte Adresse).
- Testsuite: 84/84 grün (80 aus V0.6.1 + 4 neue).

**Noch zu verifizieren (auf echtem Windows, nach diesem Fix):** Test (d)
aus `docs/LMU_VERIFICATION_PROTOCOL.md` erneut mit geschlossenem LMU
ausführen, um zu bestätigen, dass jetzt korrekt "Waiting for LMU to
start..." statt "Connected." erscheint.

**Priorisierungsentscheidungen (kein Code geändert):**
- Test (e) Weather-Drop: zurückgestellt — schnellste Rundenzeiten werden
  ohnehin nur im Trockenen gewertet, daher aktuell nicht relevant.
- Test (f) Car-Identity: Anforderung für die spätere Leaderboard-Arbeit
  festgehalten — Haupt-Leaderboard filtert nach Strecke + Klasse
  (Hypercar/GT3/...), Sub-Leaderboards nach Strecke + genaues
  Fahrzeugmodell (nicht Team/Nummer/Baujahr). Erfordert einen neuen
  Klassen-/Modell-Datenpunkt, der aktuell weder in `LapMetadata` noch im
  `Lap`-Modell existiert — vorgemerkt für nach Abschluss des Validation
  Gates, nicht jetzt umgesetzt.

---

## V0.6.1 — Bugfix aus echter LMU-Verifikation

Gefunden während Test (a) des Validation Gates (2026-09-24), reale
Session auf Michelin Raceway Road Atlanta, 6 Runden.

- **Fix**: `LMU_GARAGE_DEBUG=1` hatte keine Wirkung — `client/main.py`
  setzte das Root-Logging-Level fest auf `INFO`, unabhängig von der
  Env-Var. `parser.py`s `_DEBUG`-Flag entschied zwar, *ob* geloggt wird,
  aber das Logging-Framework verwarf die `DEBUG`-Zeilen trotzdem, weil
  der Logger-Level nie hochgesetzt wurde. Ergebnis: keine
  `BOUNDARY:`/`EMIT:`/`INIT:`/`STALE:`-Zeilen im Log, obwohl die Env-Var
  korrekt gesetzt war.
- **Umsetzung**: Neues, GUI-unabhängiges Modul `client/logging_config.py`
  mit `resolve_log_level()` als einzige Quelle der Wahrheit für das
  Env-Var→Level-Mapping. `client/main.py` nutzt das jetzt für sein
  `logging.basicConfig()`.
- **Regressionstest**: `tests/test_debug_logging.py` — 5 neue Tests,
  darunter ein End-to-End-Test, der exakt das reale Symptom nachstellt
  (Boundary-Event muss bei `LMU_GARAGE_DEBUG=1` im Log ankommen).
- **Kein funktionaler Bug in Parser/Validator selbst** — die
  Rundenerkennung, Sektorzeiten und `count_lap_flag`-Behandlung liefen in
  der Testsession korrekt (`73.936s`, `72.615s`, `72.615s`, `71.555s` bei
  `count_lap_flag=2`, von `_derive_is_valid()` bereits korrekt als
  "gültig möglich" behandelt).
- Testsuite: 80/80 grün (75 aus V0.6.0 + 5 neue).

---

## V0.6.0 — Production-Readiness-Pass

Schwerpunkt: alles, was ohne Zugriff auf ein echtes LMU auf Windows
umsetzbar war, aus dem externen Audit und dem P1/Produktions-Arbeitsplan.

### P1-Härtung

- **Client-Queue**: `lap_time ≤ 0` wird nicht mehr aufgezeichnet (statt
  endlos vom Server abgelehnt zu werden). Permanent abgelehnte Uploads
  werden als `rejected` markiert statt jede 15s erneut versucht.
  Exceptions werden pro Lap isoliert — eine kaputte Datei blockiert nicht
  mehr die restliche Queue. `401` bricht den gesamten Upload-Zyklus ab
  statt mit totem Token alle Laps durchzuprobieren.
- **Architektur-Fix Allowlist**: `_build_payload()` nutzt jetzt eine
  Allowlist der Server-Felder statt einer Blockliste lokaler Felder —
  verhindert strukturell, dass ein künftiges lokales Feld denselben
  P0-1-Bug reproduziert.
- **Validator verschärft**: Toleranz Dauer/Lap-Time von ±10% auf ±3%
  (min. 1s) reduziert. Sektorsummen-Check greift jetzt auch bei Summe=0
  (vorher stillschweigend übersprungen). Neuer Cross-Check: ∫speed·dt vs.
  `lap_dist`-Differenz.
- **Kürzere Transaktion (Teilfix)**: Telemetrie-Validierung läuft jetzt im
  Threadpool (`run_in_threadpool`) statt blockierend im Event-Loop.
- **Rate-Limiting**: In-Memory-Token-Bucket auf
  register/upload/team-join/link-code/report, pro IP und (wo
  authentifiziert) pro Fahrer. Dokumentierte Grenze: nur pro Prozess,
  nicht über mehrere Worker hinweg geteilt.
- **Moderation**: neuer `/admin`-Router (Lock/Unlock Fahrer, Lap
  invalidieren mit Begründung, Fahrer-Reports einsehen/auflösen, Fahrer
  inkl. aller Daten löschen), neuer Report-Endpoint für Fahrer
  (`POST /telemetry/laps/{id}/report`).

### Produktionsbetrieb

- PostgreSQL: `pool_pre_ping`, konfigurierbare Pool-Größe.
- Migrationen als Deploy-Schritt (`docker/entrypoint.sh` läuft
  `alembic upgrade head` vor jedem Serverstart).
- Docker: `Dockerfile.server`, `Dockerfile.discord_bot`,
  `docker-compose.yml` (Postgres+Server+Bot+Caddy), `.dockerignore`.
- Reverse Proxy: `docker/Caddyfile` mit automatischem HTTPS,
  Body-Size-Limit, Access-Log.
- `.env.example` mit allen Variablen und production-Hinweisen.
- `/health` prüft jetzt echte DB-Konnektivität und liefert Versions-Info.
- Request-ID-Middleware + strukturiertes Logging (Text/JSON umschaltbar).
- `scripts/backup.sh` (DB+Storage konsistent, Retention, Cron-Beispiel).

### Auth/Accounts

- `is_locked`/`locked_reason` auf `Driver` — separater Hebel von
  Token-Revoke, für Moderationsfälle.
- `min_client_version`-Check beim Upload — verhindert dass ein kaputter
  alter Client (wie V0.5.2) dauerhaft gegen die Wand läuft.
- Client meldet beim Start, wenn er älter als das Server-Minimum ist.

### Datenschutz

- `GET /accounts/me/export` — Selbstauskunft.
- `DELETE /accounts/me` — Selbst-Löschung, FK-sichere Kaskade.
- `docs/PRIVACY_TEMPLATE.md`, `docs/IMPRESSUM_TEMPLATE.md` — als Vorlage
  markiert, keine Rechtsberatung, mit offenen Fragen an den Betreiber.

### Tests

- `tests/conftest.py` — isolierte Test-DB/Storage pro Testlauf,
  Rate-Limit-Reset zwischen Tests.
- `tests/test_integration_upload_flow.py` — echter Client-Envelope über
  echten HTTP-Request bis in die DB und aufs Leaderboard.
- `tests/test_concurrency.py` — paralleler Record-Race und paralleler
  Duplicate-Upload-Race, echte Threads, echte SQLite-Serialisierung.
- `tests/test_moderation.py`, `tests/test_rate_limit.py`.
- **Dabei gefunden und gefixt**: Rate-Limits wurden beim Router-Import
  einmalig gecacht statt pro Request aus `settings.rate_limits` gelesen —
  Laufzeit-Konfiguration war wirkungslos.

### LMU-Diagnostics (P0-7, vorbereitet — Verifikation braucht Windows+LMU)

- `scripts/dump_shared_memory.py` — Read-only-Diagnosewerkzeug (von den
  Tests referenziert, aber bisher fehlend).
- `LMU_GARAGE_DEBUG=1` — Debug-Logging in `parser.py` für Lap-Grenzen,
  Scoring-Sync-Differenz, Staleness-Erkennung.
- `docs/LMU_VERIFICATION_PROTOCOL.md` — konkretes Testprotokoll für alle
  7 offenen Punkte (a)–(g).

### Release

- Versionsnummer zentral: Server `0.6.0`, Client `0.6.0`.
- `docs/ROLLBACK.md`, `docs/CLIENT_INSTALL.md`.

---

## V0.5.3

Siehe `CHANGES_V0.5.3.md` — P0-1 (Upload-Vertrag), P0-2 (Upload-Härtung
gegen 500er), P0-3 (Client-AND-Server-Validity), P0-4a/b (PG-Advisory-Lock,
IntegrityError-Dateilöschung entfernt), P0-5 (Discord-Outbox: Session vor
awaits geschlossen, Embed-Länge, WR-Spam-Schutz), P0-6 (Fail-open-Default
invertiert, psycopg, create_all nur Dev), P0-7 (Parser: Lap-Sprünge, Reset
bei Reconnect, Falsy-Zero-Fix).

## V0.5.2

Erste vollständige Audit-Baseline. Siehe `CHANGES_V0.5.2.md`.

## V0.5.1

Siehe `CHANGES_V0.5.1.md`.
