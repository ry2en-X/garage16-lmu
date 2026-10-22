"""
scripts/revalidate_laps.py — judges laps that are already stored again, with the CURRENT
rules (server/lap_check.py: plausibility, track limits from the telemetry, per-lap figures).

Why (V0.9.3.1): the rules change — V0.9.3 rejected every lap whose track-limits step counter
rose, V0.9.3.1 sends such laps to review instead (server/track_limits.py). A lap is judged
from its stored telemetry file plus the stored check inputs (laps.check_inputs: the client's
verdict and reasons, its game summary, the late window). No new upload is needed.

Nothing changes unless you ask for it:

    python -m scripts.revalidate_laps
        Dry run (default). Judges every stored lap whose file still exists and reports what
        WOULD change, grouped by transition, each with its reason:
          valid -> invalid     the current rules reject a lap that counts today
          invalid -> valid     a lap rejected AUTOMATICALLY (track limits / client) that the
                               current rules accept ("restorable")
          invalid, kept        would be accepted, but was rejected by an admin, a report or
                               a moderation decision — never touched
          valid -> review      newly flagged for the admin review queue
          admin decided        cleared/invalidated in the review queue — validity kept
        Changes nothing.

    python -m scripts.revalidate_laps --apply
        Stores the new verdict (laps.validation), the figures (laps.lap_stats), the
        recovered check inputs, and puts newly flagged laps into the review queue as "pending"
        (they keep counting provisionally). Does NOT change whether any lap counts.

    --apply --hold              V0.9.9: laps the current rules send to REVIEW are "held" instead —
                                they leave leaderboards and records until an admin clears them.
                                The dry run lists how many of them hold a current record.

V0.9.9: every file is checked against its stored sha256 / size / MAC first (server/integrity.py).
A lap whose file fails is never re-judged; with --apply it is quarantined. Every change made with
--apply is written to the audit log (kind lap.revalidated, actor script) with the old and new state.
Re-judging is reproducible: the same file, the same stored inputs and the same rules give the same
verdict (tests/test_v099_decision.py); the one input that depends on other laps — the layout's
usual distance — is stored in the verdict (decision.reference_distance_m).

    --apply --update-validity   additionally: valid -> invalid is applied
    --apply --restore           additionally: invalid -> valid ("restorable") is applied; such
                                laps go to the review queue ("pending") so an admin sees them,
                                and no record announcement is sent for them
    Both may be combined. Leaderboards and records follow live (they read laps.is_valid).

    --track NAME / --driver ID / --limit N   narrow the run
    --only-missing                           only laps without a stored verdict
    --details N                              list at most N laps per transition (default 50)

On the NAS (Docker):

    docker compose exec -T server sh -c 'LMU_GARAGE_DB_URL="postgresql+psycopg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@${POSTGRES_HOST}:${POSTGRES_PORT}/${POSTGRES_DB}" python -m scripts.revalidate_laps'
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from server import audit, integrity, track_limits
from server.config import settings
from server.database import SessionLocal
from server.official import HELD, official_filter
from server.lap_check import (
    check_lap, inputs_for_lap, is_automatic_reason, link_neighbours, promoted_codes, stored_validation,
)
from server.models import Lap

T_INVALIDATE = "valid -> invalid"
T_RESTORE = "invalid -> valid (restorable)"
T_KEPT = "invalid, would be valid, kept (not an automatic rejection)"
T_REVIEW = "valid -> review"
T_ADMIN = "admin decided, validity kept"
T_SAME = "unchanged"
T_INTEGRITY = "file failed its integrity check (not re-judged)"
TRANSITIONS = (T_INVALIDATE, T_RESTORE, T_KEPT, T_REVIEW, T_ADMIN, T_SAME)


def _metadata_for(lap: Lap) -> SimpleNamespace:
    """What check_lap needs from the upload metadata, rebuilt from the stored row."""
    return SimpleNamespace(
        track_name=lap.track_name, lap_time=lap.lap_time, sector_times=lap.sector_times or [],
        sample_count=lap.sample_count, tail_samples=None, game_lap_state=None, lap_report=None,
        track_grip_level=None, fixed_setup=None, track_limits_steps_per_point=None,
        track_limits_steps_per_penalty=None,
    )


def _inside_storage(path: Path) -> bool:
    root = settings.telemetry_storage_dir.resolve()
    try:
        resolved = path.resolve()
    except (OSError, RuntimeError):
        return False
    return resolved == root or root in resolved.parents


def transition(lap: Lap, check) -> str:
    """Which of TRANSITIONS applies (pure: decides nothing on its own)."""
    if lap.review_status in ("cleared", "invalidated", "quarantined"):
        return T_ADMIN
    if lap.is_valid and not check.is_valid:
        return T_INVALIDATE
    if not lap.is_valid and check.is_valid:
        reasons = lap.invalid_reason if isinstance(lap.invalid_reason, list) else [lap.invalid_reason]
        if reasons and all(is_automatic_reason(r) for r in reasons):
            return T_RESTORE
        return T_KEPT
    if lap.is_valid and check.review_status:
        # newly flagged; with --hold also a lap flagged before V0.9.9 ("pending", still counting)
        if not lap.review_status or (lap.review_status == "pending" and _hold_requested):
            return T_REVIEW
    return T_SAME


_hold_requested = False


def holds_record(db, lap: Lap) -> bool:
    """Is this lap the current official best of its track and car model (a record that would
    disappear from the leaderboard if the lap were held)?"""
    from sqlalchemy import func

    best = db.query(func.min(Lap.lap_time)).filter(
        official_filter(), Lap.track_name == lap.track_name,
        (Lap.car_model == lap.car_model) if lap.car_model is not None else Lap.car_name == lap.car_name).scalar()
    return best is not None and lap.lap_time <= best


def _codes(check, severity=None) -> list:
    return [f.code for f in check.verdict.findings if severity is None or f.severity == severity]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--update-validity", action="store_true")
    parser.add_argument("--restore", action="store_true")
    parser.add_argument("--track")
    parser.add_argument("--driver", type=int)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--only-missing", action="store_true")
    parser.add_argument("--details", type=int, default=50)
    parser.add_argument("--hold", action="store_true")
    args = parser.parse_args(argv)
    if (args.update_validity or args.restore or args.hold) and not args.apply:
        # the dry run reports the effect of --hold too; only applying needs --apply
        if not (args.hold and not (args.update_validity or args.restore)):
            parser.error("--update-validity / --restore need --apply")
    global _hold_requested
    _hold_requested = bool(args.hold)

    promote = promoted_codes()
    counts = Counter()
    reasons = defaultdict(Counter)
    shown = Counter()
    touched = []
    touched_integrity = []
    now = datetime.now(timezone.utc).isoformat()
    print(f"Track-limits rules version {track_limits.TRACK_LIMITS_VERSION}; "
          f"heuristics allowed to invalidate: {', '.join(sorted(promote)) or 'none (review only)'}\n")
    with SessionLocal() as db:
        query = db.query(Lap).order_by(Lap.id.asc())
        if args.track:
            query = query.filter(Lap.track_name == args.track)
        if args.driver:
            query = query.filter(Lap.driver_id == args.driver)
        if args.only_missing:
            query = query.filter(Lap.validation.is_(None))
        if args.limit:
            query = query.limit(args.limit)
        for lap in query.all():
            path = Path(lap.telemetry_path)
            if not _inside_storage(path) or not path.is_file():
                counts["file missing"] += 1
                if args.apply:
                    integrity.quarantine(db, lap, integrity.verify(lap), actor="script")
                    touched_integrity.append(lap.id)
                continue
            # V0.9.9: never re-judge a file that is not the one that was uploaded
            checked = integrity.verify(lap)
            if checked.quarantine:
                counts[T_INTEGRITY] += 1
                if shown[T_INTEGRITY] < args.details:
                    shown[T_INTEGRITY] += 1
                    print(f"[{T_INTEGRITY}] lap {lap.id:>6}  {checked.status} {checked.detail or ''}")
                if args.apply:
                    integrity.quarantine(db, lap, checked, actor="script")
                    touched_integrity.append(lap.id)
                continue
            inputs = inputs_for_lap(lap)
            before = {"is_valid": bool(lap.is_valid), "review_status": lap.review_status,
                      "status": (lap.validation or {}).get("status") if isinstance(lap.validation, dict) else None}
            check = check_lap(db, path, _metadata_for(lap), client_valid=bool(inputs.get("client_valid", True)),
                              exclude_lap_id=lap.id, inputs=inputs, promote=promote,
                              client_version=lap.client_version)
            kind = transition(lap, check)
            if kind == T_REVIEW and holds_record(db, lap):
                counts["   of which hold a current record"] += 1
            counts[kind] += 1
            if kind == T_INVALIDATE:
                reasons[kind].update(_codes(check, track_limits.INVALID))
            elif kind in (T_RESTORE, T_KEPT):
                reasons[kind].update(_codes(check, track_limits.REVIEW) or ["(no findings)"])
            elif kind == T_REVIEW:
                reasons[kind].update(_codes(check, track_limits.REVIEW))
            if kind != T_SAME and shown[kind] < args.details:
                shown[kind] += 1
                was = "valid" if lap.is_valid else f"invalid ({'; '.join(map(str, lap.invalid_reason or []))})"
                now_text = "; ".join(check.invalid_reason() or []) or ", ".join(_codes(check, track_limits.REVIEW)) or "clean"
                print(f"[{kind}] lap {lap.id:>6}  {lap.track_name} / {lap.car_model}  {lap.lap_time:.3f}s\n"
                      f"      was: {was}\n      now: {now_text}")

            if not args.apply:
                continue
            lap.validation = stored_validation(check.verdict, lap.validation)
            if check.stats:
                lap.lap_stats = check.stats
            if not lap.check_inputs:
                lap.check_inputs = inputs
            if kind == T_INVALIDATE and args.update_validity:
                lap.is_valid = False
                lap.invalid_reason = check.invalid_reason()
                lap.review_status = None
            elif kind == T_INVALIDATE:
                lap.review_status = lap.review_status or "pending"
            elif kind == T_RESTORE and args.restore:
                lap.validation = {**lap.validation, "restored": {"at": now, "previous_reason": lap.invalid_reason}}
                lap.is_valid = True
                lap.invalid_reason = None
                lap.review_status = "pending"
            elif kind == T_REVIEW:
                lap.review_status = HELD if args.hold else "pending"
            elif not lap.is_valid and not check.is_valid and lap.review_status not in ("cleared", "invalidated"):
                old = lap.invalid_reason if isinstance(lap.invalid_reason, list) else [lap.invalid_reason]
                if old and all(is_automatic_reason(r) for r in old):
                    lap.invalid_reason = check.invalid_reason()   # same decision, current wording
            db.add(lap)
            touched.append(lap.id)
            after = {"is_valid": bool(lap.is_valid), "review_status": lap.review_status,
                     "status": (lap.validation or {}).get("status")}
            if after != before or kind != T_SAME:
                audit.append(db, "lap.revalidated", actor="script", lap_id=lap.id, driver_id=lap.driver_id,
                             detail={"transition": kind, "before": before, "after": after,
                                     "validators": ((lap.validation or {}).get("decision") or {}).get("validators")})
        if args.apply:
            db.flush()
            linked = 0
            for lap_id in touched:
                lap = db.get(Lap, lap_id)
                linked += link_neighbours(db, lap)
            counts["laps given a late track-limits finding by their next lap"] += linked
            db.commit()

    print("\nSummary:")
    for key in (*TRANSITIONS, "   of which hold a current record", T_INTEGRITY, "file missing",
                "laps given a late track-limits finding by their next lap"):
        if counts.get(key):
            print(f"  {key:<62} {counts[key]}")
            for code, count in reasons.get(key, Counter()).most_common():
                print(f"      {track_limits.MESSAGES.get(code, code):<58} {count}")
    if not args.apply:
        print("\nDry run — nothing changed. --apply stores verdicts and figures (validity unchanged); "
              "add --update-validity and/or --restore to apply the transitions above, --hold to hold "
              "the laps that need a review (they leave leaderboards until an admin clears them).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
