# Changes in V0.5.1

Scope for this pass, per priority: **A (Reader/Parser + Tests)** done and
tested; a small piece of **D (Leaderboard/Record bugs)** done; the rest of
B/C/E/F/G (Recorder/Uploader, Server validation/Security, Discord,
Web/Auth, Cleanup) are **not yet started** — see "Still open" below.

## A) LMU Shared Memory Reader + Parser — done, tested

**The foundational bug:** the reader targeted the classic third-party
rFactor2 plugin's shared memory (`$rFactor2SMMP_Telemetry$` /
`$rFactor2SMMP_Scoring$`, two separate named files, version-counter
tearing protection). **LMU does not use that interface.** It exposes its
own single named mapping, `"LMU_Data"`, with a different unified struct
and no per-buffer version counters at all. The reader could never have
received real data from the game.

- **New `client/lmu/structs.py`** — ctypes structures for LMU's actual
  shared memory layout (`SharedMemoryObjectOut` → `MGeneric`/`MPaths`/
  `MScoring`/`MTelemetry`), built from cross-referenced public sources
  (see the module's docstring for exact provenance and, importantly,
  confidence level per struct — one is independently byte-verified, one is
  mechanically derived and self-consistency-tested, one has a known
  unconfirmed region with a runtime plausibility check instead of blind
  trust).
- **ABI self-test** (`tests/test_lmu_structs.py`, pure `ctypes`, runs
  without the game or Windows): asserts the ctypes layout reproduces every
  independently-verified byte offset exactly. **8/8 passing.**
- **`client/telemetry/reader.py` rewritten**: correct shared memory name;
  fixed the open/"is the game running" detection (an earlier approach
  could silently succeed against a non-existent mapping instead of raising
  `SharedMemoryNotFound`); double-read-and-compare torn-read guard (this
  format has no version-counter pair to check cheaply); `open()` no longer
  silently double-opens when `stream()` is also called (a real handle
  leak in the previous version).
- **Sector-time formula fixed** in `client/telemetry/parser.py`:
  `S1 = last_sector1`, `S2 = last_sector2 - last_sector1`,
  `S3 = lap_time - last_sector2`. The previous formula computed
  `S3 = lap_time - S1 - last_sector2`, which double-subtracts S1 (once
  directly, once because `last_sector2` already includes it) —
  understating S3 by exactly S1 on every single lap.
- **Removed the fabricated `lap_valid` field** (not part of the real
  struct). `is_valid` is now derived from real signals: the game's own
  `mCountLapFlag`, a pit-lane visit during the lap, and sane/monotonic
  sector times.
- **11 new parser tests** (`tests/test_parser.py`, pure Python, no game or
  third-party deps) covering the sector-split fix (with an explicit
  regression test for the exact double-subtraction bug), lap-boundary
  detection, and `is_valid` derivation. **11/11 passing.**
- Fixed a signature mismatch in `client/main.py` introduced by the reader
  rewrite (`poll_hz` moved from the reader's constructor to `stream()`),
  and made the retry loop close the reader on error so the next attempt
  actually reopens instead of reusing a possibly-stale handle.

**Run these tests:** `python -m tests.run_stdlib_tests` (no dependencies
needed), or `pytest tests/test_lmu_structs.py tests/test_parser.py` once
the project's normal dependencies are installed.

**What's still uncertain, honestly:** the exact byte layout of
`TelemInfoV01`/`TelemWheelV01` (used for the raw per-sample telemetry —
speed, inputs, tire data) is *mechanically derived*, not independently
byte-verified like `VehicleScoringInfoV01` is — no public source publishes
confirmed offsets for it. The derivation method itself is proven correct
(the ABI test shows it exactly reproduces the one struct that *is*
independently verified), but the struct itself should be checked against
a real running copy of LMU before fully trusting recorded telemetry
samples (lap/sector times and validity, which come from
`VehicleScoringInfoV01`, rest on the verified struct). See
`client/lmu/structs.py`'s docstring for exactly which parts to double
check and against what.

## D) Leaderboard — one query bug fixed

`GET /leaderboard/{track}/{car}` picked each driver's best lap by joining
back on `(driver_id, lap_time)` — which isn't unique: two laps with the
*exact* same lap_time (a real, not particularly rare occurrence with
rounded times) could both match, returning more than one row for the same
driver. Rewritten using `ROW_NUMBER() OVER (PARTITION BY driver_id ORDER
BY lap_time, id)`, keeping only rank 1 — exactly one row per driver,
ties broken deterministically.

## Race condition in PR/WR/TEAM_BEST detection — fixed minimally

Two concurrent uploads could each read "current best lap time" before
either committed, and both conclude (independently, both "correctly"
given what they individually saw) that they'd set a new record.

**First attempt at fixing this over-engineered it** — introduced a
separate `CurrentRecord` table with a SQLite-specific atomic upsert. That
was reverted: it added a new table, a new query pattern, and a
dialect-specific dependency to solve a problem that has a much smaller
fix. **Actual fix:** `server/database.py` now opens every SQLite
transaction with `BEGIN IMMEDIATE` instead of pysqlite's default deferred
locking (a documented recipe for this exact pysqlite quirk — see the
comment in that file). This serializes the read-then-decide-then-write
sequence across concurrent requests at the transaction level, closing the
race without any schema or API change. Trade-off: every request,
including plain reads, now takes SQLite's write lock briefly — negligible
at this project's actual scale (SQLite is single-writer regardless); real
read concurrency would mean moving off SQLite entirely, not tuning this
further.

`server/models.py` gained one small, low-risk column:
`Lap.client_claimed_valid` (what the client itself believed, kept for
audit) alongside the existing `Lap.is_valid` (unchanged in name; wiring it
up to an actual server-side plausibility check instead of trusting the
client's value is priority C, not yet done — see below). Migration
`0003_client_claimed_valid.py`.

## Still open (not started this pass)

- **B) Recorder/Uploader**: filename collisions, atomic metadata writes,
  upload-queue resume-after-restart / permanent-vs-transient retry
  distinction.
- **C) Server validation & security**: `Lap.is_valid` still isn't
  server-verified against telemetry (the column exists, the check
  doesn't); streaming uploads; `extra="forbid"` / non-finite float
  rejection on `LapMetadata`; no-unset-dev-secret-in-production; rate
  limiting; enforced registration secret in production.
- **E) Discord**: per-channel delivery tracking so one failing channel
  can't cause duplicate or dropped announcements elsewhere.
- **F) Web/Auth**: register as JSON body instead of query params; token
  storage hardening beyond what V0.5 already added (rotation/revocation).
- **G) Cleanup**: README/`.env.example` updates for everything above.

Next pass should pick these up in the same order (B → C → E → F → G),
testing after each, rather than touching several at once.
