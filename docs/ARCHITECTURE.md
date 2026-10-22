# Garage16 — architecture (V0.9.9)

How the parts fit together, where data lives, and the decisions behind the design. Written so
that someone who has never seen the project can run, change and repair it.

## Components

```
 Windows PC (each driver)                       Server host (Synology NAS or VPS, Docker)
 ┌──────────────────────────┐                   ┌─────────────────────────────────────────────────────┐
 │ Le Mans Ultimate          │                  │  caddy (reverse proxy, TLS, web/ static files)       │
 │   shared memory           │                  │     │  X-Forwarded-For rewritten / appended          │
 │        │ 60 Hz            │   HTTPS          │     ▼                                                │
 │ Desktop client (0.9.4)    │ ───────────────▶ │  server (FastAPI, uvicorn, 1 worker)                 │
 │  reader → recorder →      │  upload: signed  │     │ SQL                  │ files                   │
 │  lap file (parquet) →     │  envelope + file │     ▼                      ▼                         │
 │  uploader (retries)       │                  │  db (PostgreSQL 16)    telemetry_data volume         │
 │  optional analysis window │ ◀── web pages ── │     ▲                      (one parquet file per lap) │
 └──────────────────────────┘                   │     │                      ▲                         │
                                                │  discord_bot (reads records, announces)              │
 Browser (any device) ── HTTPS ───────────────▶ │  backup (optional profile: consistent backups)       │
                                                │  cloudflared (optional: tunnel instead of open port) │
                                                └─────────────────────────────────────────────────────┘
```

| Part | Responsibility | Never does |
|---|---|---|
| Desktop client (`client/`) | read LMU's shared memory, cut laps, write one parquet file per lap with its metadata, sign and upload, retry transient failures, keep laps until accepted | judge validity on its own authority (the server decides, the client's verdict is one input) |
| Server (`server/`) | authenticate, verify the upload signature, validate the lap (plausibility, track limits), store file + row atomically, compute records, serve the analysis | trust client-supplied paths, names or headers without checking |
| Web frontend (`web/`) | the pages; every analysis is computed by the server, the browser only draws and filters | hold data the server does not have |
| Discord bot (`discord_bot/`) | announce new records | write anything but its own announcement marks |
| Caddy (`docker/Caddyfile`) | TLS, static files, compression, forward the API, set the forwarding header | — |
| Backup (`docker/backup-loop.sh`, `scripts/backup.sh`) | database dump + file archive from ONE moment, checked, with manifest | publish an unchecked or partial backup |

## Data

| Where | What | Kept |
|---|---|---|
| PostgreSQL | drivers, laps (metadata, verdict, figures), records, teams, sessions, invitations, upload log | until deleted; upload log 30 days |
| `telemetry_data` volume | one parquet file per lap, content-addressed (`<sha256>.parquet`) | as long as its lap |
| Client PC | laps not yet accepted, the client's config and log | until uploaded / by the driver |
| Backups | `garage16_<stamp>/` = dump + archive + manifest | 14 days (configurable) |

Details per field and the deletion rules: `docs/DATA_PROTECTION.md`.

## The upload lifecycle

1. Client: the lap file and a JSON envelope (metadata, client version, sha256 of the file); the
   envelope is signed with the driver's client secret (HMAC).
2. Server, in this order — each step refuses before anything is kept:
   envelope is a JSON object → client not too old (426 `UPDATE_REQUIRED`) → signature (401) →
   metadata schema (400) → file streamed to a temp file in the target folder with a size limit
   (413) → sha256 matches (400) → already stored? (idempotent: same answer, `duplicate: true`).
3. Under the **shared write lock** (`server/write_lock.py`; 503 `MAINTENANCE` while a backup runs):
   temp file renamed to its final content-addressed name (atomic, same folder) → one transaction:
   validity check, lap row, record evaluation (serialised per track and car model: advisory lock on
   PostgreSQL) → commit. Any failure: rollback, and the file is removed unless a committed lap
   points at it.
4. Every attempt is recorded in `upload_events` with its request id (`server/upload_log.py`).

V0.9.9 additions, in the same order: the temp file is created new-only and never through a symlink
(`O_EXCL | O_NOFOLLOW`, mode 0600) in a folder that must resolve inside the storage root; the file's
footer must announce ≤ 200,000 rows, ≤ 1,000 columns and ≤ 512 MiB uncompressed before anything is
decoded (`telemetry_analysis.check_limits`, also on every later read); the lap row stores the
received size, the upload's request id and the server's MAC; the decision may hold the lap
(`review_status = held`); an audit event `lap.accepted` is appended last in the transaction; after
the commit the original is made read-only (0440).

## The lap check: from file to decision (V0.9.9)

```
file ──► 1 input        schema, ranges, required fields          schemas.py, validation.py
     ──► 2 time axis    order, restarts, duplicates, gaps         validation.py, lap_decision.py
     ──► 3 position     distance vs speed, jumps, acceleration    validation.py, lap_decision.py
     ──► 4 track limits LMU's flags + Garage16's measurements     track_limits.py
     ──► 5 decision     VALID / INVALID / REVIEW, evidence count  lap_decision.py
     ──► 6 record       laps.validation (findings with place, source, tier, validator versions,
                        data quality, evidence), audit event      lap_check.py, audit.py
                 │
                 ├─ INVALID  is_valid = false: never counts
                 ├─ REVIEW   is_valid = true, review_status = held: stored, analysable, NOT official
                 │           until an admin clears it → then records are evaluated (admin.review_lap)
                 └─ VALID    official at once: records evaluated in the upload transaction
official = is_valid AND review_status NOT IN (held, quarantined)            server/official.py
```

One function judges both new uploads and stored laps (`lap_check.check_lap`, used by
`scripts/revalidate_laps.py` and `scripts/trackcheck_eval.py`): the same file, stored inputs and rule
versions give the same verdict. A lap already stored is never taken out of the leaderboards by a
later re-judgement or by a finding linked from its next lap — it stays `pending` (counting
provisionally) until an admin decides or `revalidate_laps --apply --hold` is run on purpose.

## Originals, derived data, integrity (V0.9.9)

| What | Where | Changes? |
|---|---|---|
| the received original | `telemetry_data/driver_<id>/<track>/<car>/<sha256>.parquet`, mode 0440 | never (content-addressed; a retry of the same bytes lands on the same name) |
| its fingerprint | `laps.telemetry_hash` (sha256 of the bytes as received), `telemetry_bytes`, `upload_request_id`, `integrity_mac` | only by `integrity_check --backfill/--resign` (audited) |
| derived data | `laps.validation`, `laps.lap_stats`, analysis cache (memory) | recomputed from the original at any time |
| history | `audit_events` (chained HMAC), `upload_events` (30 days) | append-only (trigger); pruning records its anchor |

Integrity is checked on every analysis access (size, sha256 — cached per unchanged file for at most
10 min, "unchanged" = same size, mtime, ctime, inode — and MAC), by `scripts/integrity_check.py`,
before every re-judgement, and when a backup is restored (`consistency_check --deep`: hash, size,
MAC, audit chain; V0.9.9.1 also the chain head against the manifest's `audit_head`, before the
switch). A failure quarantines the lap.

## Trust boundaries

| From → to | What is trusted | What is checked instead |
|---|---|---|
| desktop client → server | nothing in the content: `is_valid`, reasons, lap time, sector times, the file itself are claims | signature (who sent it), schema, plausibility, track-limits signals, data quality, motion physics, reuse of another driver's file |
| proxy → server | the client address only from configured proxies (ADR-8) | — |
| database ↔ files | neither alone | sha256 + size + MAC tie a lap row to its file and lap time |
| backup → restore | nothing | manifest sha256, every file's hash, every lap's MAC, the audit chain |
| admin → server | one shared token (no per-person accounts) | every admin decision is an audit event |

Not covered: a client that fabricates a consistent lap (the signature proves the sender, not the
game), and an attacker with the integrity key AND database write access (ADR-12).

## The analysis pipeline

`load_lap` (parquet, memory-mapped) → time must not run backwards (a restart is refused) →
**distance axis** at telemetry rate (`server/distance_axis.py`: LMU's `lap_dist` where it advances,
the samples in between placed by integrated speed; repeated polls dropped; short NaN gaps bridged
for the axis only) → resampling onto a common distance grid → channels, delta (time at equal
distance), corners (detected on the mean speed of both laps, measured on raw samples, each point
with its resolution `res`), racing line, track surface. Results are cached in memory by file hash
and `ANALYSIS_CACHE_VERSION`; the cache is never a data source (a restart recomputes the same).

The **reference axis is distance along the lap** for every comparison: two laps are compared at the
same metre. Definitions, units and tolerances of every metric: `docs/QUALITY.md`.

## Consistency and recovery

* `server/consistency.py` / `scripts/consistency_check.py`: database ↔ files, figures versions,
  metadata, dangling rows; dry run by default; deletions re-check right before deleting.
* Backup: write lock exclusive → dump + archive → checks → manifest → one rename publishes.
* Restore (`scripts/restore.sh`): check → stage in a new database and volume → validate there
  (migrations, every file's sha256) → swap the databases in one transaction → swap the files →
  verify the running server; any failure returns to the state before (`docs/ROLLBACK.md`).

## Architecture decisions

**ADR-1 One server process, state in memory.** Rate limits and the analysis cache live in the
process (`LMU_GARAGE_WORKERS=1`). For a circle of friends this is enough and needs no Redis; with
more workers each has its own counters (the server logs a warning). Revisit before running several
workers or hosts: a shared limiter (Redis or the proxy's) is then required.

**ADR-2 Telemetry as files, metadata in the database.** Parquet files are large and append-only;
the database holds what is queried. The two are kept together by content addressing, the
transactional upload, the write lock for backups and the consistency check.

**ADR-3 Content-addressed lap files.** The file name is its sha256: retries cannot duplicate files,
a changed file is detectable (`--deep`), restore validation can verify every file.

**ADR-4 The server decides validity.** The client's verdict is one input; LMU's own signals,
Garage16's heuristics and admin decisions are stored separately with their source and rule version
(`laps.validation`, `track_limits.py`). Garage16's own heuristics are tiered: one that is not yet
validated against real recordings sends a lap to the admin review queue instead of invalidating it
(V0.9.3.1; the tiers per rule: Admin → Track-limits review, `GET /admin/track-limits/rules`).

**ADR-5 Distance as the comparison axis.** Laps differ in time, not in track; comparing at equal
distance makes "where" meaningful. LMU refreshes `lap_dist` at a low rate, hence the speed-integrated
axis between its updates (V0.9.5), verified against simulated laps with known values (V0.9.7).

**ADR-6 Backups without stopping the stack.** A short exclusive write lock instead of downtime:
reading continues, uploads wait (the client retries 503), the lock gives up on its own after a
configurable time so a killed backup cannot block uploads.

**ADR-7 Restore by staging and renaming.** Never drop the live database: restore into a new one,
validate, then swap names in one transaction; the old state stays as rollback point until cleaned up.

**ADR-8 Trust forwarding headers only from configured proxies, read from the right.** A header the
client writes himself cannot change the address the rate limits count (`server/client_ip.py`).

**ADR-9 The tested tree is the published tree.** The release archive is built from the staged copy
the gate ran on; its hash is in the report (`scripts/release_check.py`).

**ADR-10 REVIEW holds, VALID needs evidence (V0.9.9).** A lap that cannot be checked is not valid by
default: missing or unreadable signals, gaps, implausible motion or another driver's file make it
REVIEW, and REVIEW keeps it out of leaderboards and records until an admin decides. Records are
evaluated at that moment, never retroactively announced for laps that were faster in the meantime.
Laps flagged before (`pending`) keep counting so that no stored record changes on an update.

**ADR-11 Integrity by hash + server MAC, not by client signatures (V0.9.9).** The sha256 is taken over
the bytes as received; the server signs (HMAC, its own key, `LMU_GARAGE_INTEGRITY_KEY`, rotatable) the
lap's id, driver, hash, size and lap time. A client cannot produce this proof. It detects any change
after storage and any edit of hash, size or lap time by somebody without the key — including a
manipulated backup. It says nothing about the lap before the upload.

**ADR-12 Audit log as a keyed hash chain (V0.9.9).** Each event's MAC covers the previous MAC and the
event; a trigger refuses updates. Whoever holds the key and the database can rewrite the whole chain:
the head is therefore copied into every backup manifest (kept off the server). The driver id is not
part of the chain so a deletion request can be honoured without breaking it.

