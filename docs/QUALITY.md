# Quality — what is checked, how, and what is not yet proven (V0.9.9)

## The release gate

`python -m scripts.release_check [--zip OUT.zip]` (CI: `.github/workflows/ci.yml`) decides whether a
state may be released. In this order:

| Step | Fails the release when | Not run (exit 6) when |
|---|---|---|
| Environment | — | a package, Node/jsdom, tkinter, xvfb, caddy, PostgreSQL superuser URL, the Alpine root file system or the namespace tools are missing |
| Versions | — | installed versions differ from `constraints.txt` |
| Staging (`--zip`) | the staged tree changes during the run | — |
| Linter (`ruff.toml`: syntax errors, pyflakes) | any finding | — |
| Static security analysis (bandit, medium + high) | any finding not justified in place (`# nosec <id> — reason`) | — |
| Known vulnerabilities (pip-audit over every pinned package) | any known vulnerability | the database cannot be reached |
| Whole test suite, strict (a skip is a failure) | any failure; any skip except documented opt-in checks | the session does not start |
| Coverage (lines and branches, per module, `tests/coverage_floors.json`) | a module below its floor | — |

The report directory holds `TEST_REPORT.md` (verdict, counts, every coverage figure),
`test-results.xml`, `coverage.json` and `environment.json`; the archive carries `release/TEST_REPORT.md`
and `release/environment.json` (the two others are large and only reproduce what the report states). With `--zip` the gate runs in a staged copy of exactly the released files and
the archive is built from that copy; its sha256 is written next to it (`.zip.sha256`), the hash of the
tested tree is in the report.

## Metric definitions

Comparison axis: **distance along the lap** (metres from the line), V0.9.5 telemetry-rate axis
(`server/distance_axis.py`). Every value is measured on the raw samples; the grid only finds
corners and draws charts. Reference: `tests/helpers/reference_lap.py` (2 kHz simulation, recorded
like LMU: 50 Hz frames, 60 Hz polls, `lap_dist` at 5 Hz) — `tests/test_v097_reference_laps.py`.

| Metric | Unit | Definition | Interpolation | Missing values | Tolerance (proven against the reference) |
|---|---|---|---|---|---|
| Delta | s | time of lap 1 − time of lap 2 at the same distance | linear in time over distance | gaps bridged ≤ 0.5 s for the axis | ≤ 2 ms over the whole lap |
| Corner time / delta | s | time over the corner's stretch (braking zone to next braking zone); corners add up to the lap delta | as delta | as delta | ≤ 2 ms |
| Braking point | m | first sample ≥ 10 % brake before the apex, crossing interpolated | linear between the two samples | point = None | ≤ one sample spacing (`res`, reported per point) |
| Throttle on / 50 % / full | m | first crossing of 5 / 50 / 95 % throttle after the lowest point / apex | linear | None | ≤ `res` |
| Apex | m | lowest raw speed sample in the corner | — | NaN samples ignored | ≤ `res` |
| Minimum speed | km/h | that sample's speed | — | — | 0 … deceleration × frame time above the truth |
| Exit speed | km/h | speed at the full-throttle point | linear | — | ≤ acceleration × frame time |
| Resolution `res` | m | distance between the two recorded samples around the point | — | widens over gaps | covers the true error (tested, including a 0.2 s gap) |
| Hints | — | a difference is a hint only above its threshold AND the sum of both points' `res` | — | no value → no hint | `tests/js/v097.test.mjs` |
| Degradation | s/lap | Theil–Sen slope of clean lap times over tyre age, Sen's 95 % range | — | ≥ 4 clean laps | — |
| Tyre / fuel effect | s/lap, s/l | least squares with both inputs, t-based 95 % ranges; refused when tyre age and fuel move together (|r| ≥ 0.9) or one stint only | — | — | `tests/js/v097.test.mjs` |
| Consistency | s | robust spread (1.4826 × MAD) of clean laps around the Theil–Sen trend | — | ≥ 4 clean laps | — |

Invalid input is never interpreted silently: a restart inside a file is refused, a corner without
speed data is not reported, the data-quality check (`server/telemetry_health.py`) states gaps,
backward steps, speed vs distance and the resolution per lap.

Results change only together with `ANALYSIS_CACHE_VERSION` / `INSIGHTS_VERSION`
(`tests/reference/v097_golden.json`).

## Track limits: VALID, INVALID, REVIEW (V0.9.9)

Every upload is judged by one pipeline (`server/lap_check.py`), each step a module of its own:
input (`schemas.LapMetadata`, `server/validation.py`) → time axis and position (`validation.py`,
`server/lap_decision.py`) → track limits (`server/track_limits.py`) → decision
(`server/lap_decision.py`) → record (laps.validation + audit log).

| Status | Meaning | Effect |
|---|---|---|
| **VALID** | every check could run and found nothing: the plausibility check passed, the track-limits signals of the client version are present, the recording is dense enough, motion is physical | counts at once (leaderboards, records, Discord) |
| **INVALID** | a documented rule proves it: LMU's own verdict (`mLapInvalidated`, `mCountLapFlag`), a decided Garage16 rule (pit lane, no lap time), the client's own rejection, or an implausible file (time backwards, cut off, distance jumping back, values outside physics) | never counts; an admin can still review it |
| **REVIEW** | contradictory, incomplete or not conclusive: a measurement (four wheels off, beyond the edge, steps, shorter than usual) or a data-quality finding of the decision layer | stored and analysable, **held**: not official until an admin clears it — then its record events are evaluated |

The public API keeps its fields: `is_valid` (False only for INVALID), `validation.status`
(valid / review / invalid) and `review_status` (new values `held`, `quarantined`; `pending` keeps
its V0.9.3 meaning — flagged before V0.9.9, counting provisionally — so no stored record changes on
the day of the update).

**Signals** (what decides): LMU's verdict flags, LMU's step counter, the surface under each wheel,
the car's lateral position against LMU's approximate track edge, the driven distance against the
layout's usual distance, the client's report. **Missing data is never read as "clean"**: a file
without any track-limits signal is REVIEW (`not_verifiable`), a signal the client version always
records that is missing, empty or (for LMU's verdict) unreadable is REVIEW (`signals_missing`), one
signal family alone is REVIEW (`insufficient_evidence`), a check that crashes is REVIEW
(`check_failed`). A single wrong sample never invalidates on its own — it is REVIEW.

**Evidence** in every verdict (`decision.evidence`): the number of independent signal families
that were checked (strong 4–5, moderate 2–3, weak 1). It is a count, **not a probability** — nothing
here is calibrated.

| Decision-layer rule | Limit | Where the number comes from |
|---|---|---|
| coverage_gap | no new frame for > 1.0 s | the "bad" limit of the data-quality check (V0.9.7) |
| low_frame_rate | < 10 frames/s | same |
| speed_distance_mismatch | speed integral vs lap distance off by > 5 % over the lap | same (validation.py rejects > 15 %) |
| position_jump | ≥ 50 m AND > 2× what the speed allows between two distance updates | physics: 50 m in one 0.2 s update = 900 km/h |
| implausible_acceleration | > 8 G over 0.1 s | physics, wide margin (also the data-quality check's limit) |
| frozen_samples | ≥ 0.5 s of new frames with identical speed, revs, inputs and distance at > 5 m/s | a real car's speed changes every physics frame |
| edge_exceeded (V0.9.3) | car centre ≥ 2.0 m beyond LMU's edge for ≥ 0.3 s | half a car width + LMU's path being approximate |
| four_wheels_off (V0.9.3) | all four on grass/dirt/gravel, ≥ 3 samples and ≥ 0.05 s | LMU's surface types |

**None of these limits has been checked against labelled REAL laps yet** — the empirical basis is
the synthetic set below plus the V0.9.7 reference laps. `scripts/trackcheck_eval.py --labels` runs
the same measurement on real laps (docs/V0_9_9_NAS_ANLEITUNG.md).

### Reference set and measured error rates

Truth by geometry (`tests/helpers/trackcheck.py`): a violation = all four wheels beyond the white
line for ≥ 0.25 s (FIA definition: no part of the car on the track; kerbs beyond the line are not
track). LMU is modelled in three ways (strict: invalidates; lenient: steps only; silent: nothing).
Positive = violation; "flagged" = REVIEW or INVALID; measured on laps that were not manipulated.

| Set | n | TP | FP | TN | FN | Precision | Recall | FPR | FNR |
|---|---|---|---|---|---|---|---|---|---|
| curated | 38 | 10 | 0 | 26 | 2 | 100 % | 83.3 % | 0 % | 16.7 % |
| generated grid | 480 | 180 | 0 | 240 | 60 | 100 % | 75.0 % | 0 % | 25.0 % |

Automatic invalidation (INVALID only): precision 100 %, recall 33.3 % (curated) / 25.0 % (grid) —
by design only LMU's own verdict invalidates. Manipulated or insufficient recordings: 13 of 14
not passed as VALID; the one passing is a forgery consistent in every signal.

What the rates mean: they depend on the mix of situations in the set. Every false negative is of
one kind — **all four wheels beyond the line but still on the kerb (or a few centimetres beyond
it), with LMU reporting nothing**: no wheel on grass, the car's centre less than 2 m beyond LMU's
approximate edge. Without LMU's verdict such a lap cannot be told from legal kerb use. Pinned in
`tests/reference/trackcheck_metrics.json`; `python -m scripts.trackcheck_eval` reproduces both tables.

### Limits of the detection

* A file forged consistently in every signal (flags, surfaces, position, timestamps) cannot be told
  from a real lap. The upload signature proves who sent a lap, not that the game produced it.
* Timestamps squeezed by less than 5 % (with the lap time claimed to match) are not detected.
* Cutting a corner while LMU stays silent is seen only through wheels off / the edge, or as a
  shorter lap once the layout has ≥ 3 earlier laps (`short_distance`).
* LMU's track edge is approximate; tracks where LMU reports no usable edge lose that signal.
* Overlapping laps of one driver are not cross-checked (session clocks restart).

## Integrity of the stored originals (V0.9.9)

What is guaranteed, and only that: a lap file changed, swapped, truncated or removed **after** the
server stored it is detected — by its size and sha256 (on every analysis access, cached per
unchanged file for at most 10 min — unchanged meaning same size, mtime, ctime and inode; V0.9.9.1
added the ctime, which a program cannot set back — and in full by `scripts/integrity_check.py`), and an edit of the
stored hash, size or lap time in the database by the server's MAC (key: `LMU_GARAGE_INTEGRITY_KEY`).
Such a lap goes to quarantine (not official, not visible to others) and an audit event records it.
**Not** guaranteed: that the file was authentic before the upload (the client computes its hash),
and protection against somebody holding the integrity key AND write access to the database.

## Coverage

Floors per critical module in `tests/coverage_floors.json` (rounded down from the level reached;
they may rise, never fall). Goal for critical logic: 90 % lines, 85 % branches.

V0.9.9 — the new security-critical modules, measured over the whole suite in the release gate
(lines / branches): `server/integrity.py` 99.0 / 98.0, `server/lap_decision.py` 97.8 / 93.8,
`server/audit.py` 97.2 / 93.8, `server/official.py` and `server/request_context.py` 100 / 100,
`server/lap_check.py` 96.9 / 92.9 (it had fallen to 89.0 % lines, below its floor of 90, when the
crash paths were new — the floor caught it; tests that make each step crash on purpose fixed it).
Floors set from these, rounded down. Coverage only says a line ran; the mutation check below says
whether a test would notice it being wrong.

Below the goal since V0.9.8:

| Module | Why lower | Covered elsewhere |
|---|---|---|
| `server/write_lock.py`, `server/consistency.py` (parts) | the cross-process paths run in subprocesses (backup containers, scripts) | `tests/test_v098_backup_restore.py` runs them for real |
| `server/security.py` (branches) | error branches of header parsing | — (to do) |
| `server/crypto.py` (branches) | the import-time fallback runs once per process | `tests/test_v098_crypto.py` (functions) |
| `client/main.py`, `discord_bot/bot.py` | GUI / Discord event loops | window tests under xvfb, `test_discord_*` |

## Mutation check

`python -m scripts.mutation_check` breaks the critical decisions one small change at a time
(comparison, arithmetic, boolean operator, constant) and runs the tests that guard them; every
change must make a test fail. Targets: the client address (`client_ip`), records, the write lock,
rate-limit windows, the distance axis, pedal crossings / point resolution / braking run. Mutants that
cannot change behaviour are listed with the reason (`EQUIVALENT`). V0.9.8: 159 mutants, **all killed**
(18 more listed as equivalent, each with its reason).

V0.9.9 adds the targets `lap_decision` (signal expectations, evidence, frame rate, gaps, jumps,
speed vs distance, acceleration, frozen frames, the decision record), `decision_status` (VALID /
INVALID / REVIEW → API fields, late findings), `integrity` (MAC, verify, path checks, quarantine,
release), `official` (which laps count), `upload_limits` (decoding limits) and `audit_chain`.
Full run over every target on the final code (V0.9.9.1, 2026-10-21, log in
`docs/mutation_v0991/`): **371 mutants generated, 28 listed as equivalent (one key each, each with
its reason), 343 run — all 343 killed.** The same run on the code of the first V0.9.9 archive: 340
of 340 killed — but there 27 keys skipped 31 mutants, because two identical changes on one line had
the same description (e.g. both `- 1` in `clip(x - 1, 0, n - 1)`); three of those skipped mutants
were not equivalent (run separately: all three killed). Since V0.9.9.1 every description is unique
("… #2") and `tests/test_v099_mutation_keys.py` fails on a stale key or a key that would skip more
than one mutant.
The first V0.9.9 run had 39 survivors in `lap_decision` — the tests checked outcomes, not exact
values and limits; `tests/test_v099_decision_units.py` pins them with hand-computed numbers.
Run it after changing one of these functions (~40 minutes on 2 vCPU; not part of the gate).

## Not yet proven (the 9.9/10 checklist, honestly)

| Item | State |
|---|---|
| Clean-room install + full CI run | done locally in a fresh Python 3.12 environment; the GitHub pipeline runs on the next push |
| Mutation tests for critical calculations | done for the listed targets (`scripts/mutation_check.py`), V0.9.9 including the track-limit decision, integrity, quarantine, upload limits, record release; `server/track_limits.py`'s rule functions and `server/validation.py` are not yet targets |
| False positives / negatives of the track-limit check on labelled data | done on **synthetic** labelled laps (truth from geometry); **open on real laps** — `docs/V0_9_9_NAS_ANLEITUNG.md` Teil D, `scripts/trackcheck_eval.py --labels` |
| Real LMU laps as versioned regression corpus | **open** — needs recorded laps from the NAS (reference values must not come from the same algorithm) |
| Load test with fixed data volume on the target hardware | **open** — `scripts/load_test.py` exists; to be run on the NAS with a defined data set; p95/p99 to be recorded per release |
| Monitoring with alerting | partial — readiness endpoint, Docker health, operations view; **no alert channel yet** |
| Migration and rollback in staging | partial — every migration on filled databases (SQLite) and on PostgreSQL; no staging instance |
| External penetration test | **open** |
| Shared rate limiter for several workers | not needed with one worker (ADR-1); required before scaling out |
| Accessibility audit (screen reader, contrast measurement) | partial — keyboard focus styles, labels, tables scroll; no audit |
