# Changes in V0.5.2

Scope: the P1 items explicitly requested — server-side validity, upload
streaming, security hard-fail, uploader retry policy, recorder
collision-safety, and regression tests for all of it. No architecture
changes; V0.5.1's revert of the CurrentRecord table stands.

Note: items 1–4 (server validity, LapMetadata hardening, upload
streaming, the crypto/security hard-fail) turned out to already be
implemented on disk — from work done earlier in this session before a
context handoff, not before this request. This pass verified each
against the actual requirements below, then focused new work on items 5
and 6, which genuinely weren't done yet.

## 1) Server-side validity — verified already correct

`Lap.is_valid` is set from `validate_telemetry()`'s verdict
(`server/validation.py`), never from `metadata.is_valid` — that's stored
separately as `Lap.client_claimed_valid` for diagnostics only
(`routers/telemetry.py`, `models.py`). The validator parses the uploaded
parquet file and checks: sample_count vs. actual row count, monotonic
timestamps, telemetry duration vs. claimed lap_time, sector_times sum vs.
lap_time, speed/throttle/brake/steering/gear/rpm ranges, lap_dist backward
jumps, and NaN/Inf anywhere in the required columns. Missing/unparseable
telemetry → `server_valid=False`.

**Added:** 13 tests in `tests/test_validation.py`, covering exactly the
scenarios asked for (client claims valid but server disagrees,
manipulated lap_time, manipulated sector_times, NaN, Inf, missing
columns, empty file, out-of-range values, non-monotonic time, lap_dist
regression, sample_count mismatch). **13/13 passing** — pure pandas/numpy,
no pyarrow needed (see the module's own docstring on why the logic is
split from the parquet-parsing wrapper).

## 2) LapMetadata hardening — verified already correct

`extra="forbid"` (`ConfigDict`), explicit `math.isfinite()` checks on
every float (closing a real gap: a Field with only a one-sided bound, or
the old sector_times validator, could let NaN/Inf through silently — see
the class docstring for exactly which checks that would have missed), and
length/range limits on every field.

## 3) Upload streaming — verified already correct

`_stream_to_temp_file()` reads in 1 MiB chunks, hashes incrementally,
enforces `settings.max_upload_bytes` mid-stream (not after buffering the
whole file), writes to a temp file in the same directory as the final
destination, and `Path.replace()`s it into place atomically once the
content hash is known — never a full in-memory read, never a partial file
left at the final path.

**Added:** `tests/test_upload_streaming.py` (needs `fastapi`, not
runnable in this pass's sandbox — see "Not run in this pass" below):
oversized upload rejected with no leftover temp file, correct hash for an
in-limit upload, and a direct check that reads are actually chunked
(bounded read sizes) rather than a single unbounded `.read()`.

## 4) Security — verified already correct

`server/crypto.py`: `LMU_GARAGE_ENV=production` + missing
`LMU_GARAGE_SECRET_KEY` → `RuntimeError` at import time, no fallback.
Non-production keeps the loudly-logged dev-only key for zero-setup local
work. `server/config.py` carries the `environment`/`is_production` switch.
HMAC is documented (in `security.py`, `validation.py`, and
`uploader.py`'s docstrings) as authentication + integrity only, explicitly
not anti-cheat, throughout.

## 5) Uploader/Recorder — done this pass

- **`client/uploader/uploader.py`**: retries are now scoped to genuinely
  transient failures — network errors, HTTP 408, 429, and 5xx. A
  permanent 4xx (bad signature, invalid metadata, revoked token, 413 too
  large, ...) fails on the first attempt with no backoff loop, logs
  clearly, and is picked up again on the next scheduled `run_once()`
  cycle rather than retried five times immediately for no benefit.
- **`client/telemetry/recorder.py`**: filenames now include a
  `uuid4().hex[:8]` suffix — `lap_<ts>_<lap_number>` alone could collide
  when two laps complete within the same wall-clock second (same track,
  same lap number, different sessions — not actually rare). Both the
  initial metadata write and `mark_uploaded()`'s read-modify-write now go
  through a shared `_atomic_write_text()` (temp file in the same
  directory + `os.replace()`), so a crash mid-write can never leave a
  truncated/corrupt metadata file — the destination is always either the
  old complete content or the new complete content.

**Added:**
- `tests/test_uploader_retry.py` — **6/6 passing**: classification of
  transient vs. permanent status codes, a 400 gets exactly one attempt, a
  500/429 gets retried up to `MAX_RETRIES`, a transient failure followed
  by success still marks the lap uploaded, a clean 200 doesn't retry.
- `tests/test_recorder.py` — **5/5 passing**: two laps completed in the
  same second get distinct files, a lap with no samples isn't written, a
  simulated crash mid-write leaves the original metadata file intact and
  still marked not-uploaded, `mark_uploaded`/`pending_uploads` behave
  correctly.

## 6) Tests — summary

**43/43 passing** via `python -m tests.run_stdlib_tests` (no dependencies
beyond pandas/numpy/requests, already required elsewhere in this
project): `test_lmu_structs.py` (8), `test_parser.py` (11),
`test_validation.py` (13), `test_uploader_retry.py` (6),
`test_recorder.py` (5).

**Not run in this pass** (needs `fastapi`/`sqlalchemy`/`pydantic`, not
installed in this environment): `tests/test_upload_streaming.py`. Written
and syntax-checked, not executed — run it with `pytest` once the server's
real dependencies are installed, before trusting it fully.

All 8 originally-requested scenarios are covered:
(a) client claims valid, server disagrees — `test_validation.py`;
(b) manipulated lap_time — `test_validation.py`;
(c) manipulated sector_times — `test_validation.py`;
(d) NaN/Inf — `test_validation.py`;
(e) other invalid telemetry shapes — `test_validation.py`;
(f) upload over the size limit — `test_upload_streaming.py` (written, not
executed here — see above);
(g) retry on 500, no retry on 400 — `test_uploader_retry.py`;
(h) two laps within the same second — `test_recorder.py`.

## Explicitly not touched, per instruction

No `CurrentRecord` table, no team-membership historization, no new API
endpoints, no architecture changes. `BEGIN IMMEDIATE` from V0.5.1 is
unchanged.

## Still open

- `tests/test_upload_streaming.py` needs to actually be run once
  `pip install -r server/requirements.txt -r requirements-dev.txt`
  is done somewhere with those packages available.
- Priorities E (Discord per-channel delivery), F (Web/Auth: register as
  JSON body), G (Cleanup) from the V0.5.1 handoff are still untouched.
