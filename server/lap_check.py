"""
server/lap_check.py — everything the server decides about an uploaded lap, in one
place (V0.9.3; inputs, neighbours and re-judging since V0.9.3.1): plausibility
(server/validation.py), track limits (server/track_limits.py), the stored per-lap figures
(server/lap_insights.py) and the car's model identity (server/car_identity.py).

The upload endpoint calls `check_lap()` once; scripts/revalidate_laps.py calls the same
function for laps already stored, so a lap is always judged by exactly one set of
rules, whether it arrives today or is re-checked later.

Check inputs (V0.9.3.1)
-----------------------
The verdict depends on more than the telemetry file: the client's yes/no and its reasons,
its game summary, the tail samples after the line, the late window, LMU's step/point
ratios. They are stored per lap (laps.check_inputs, `inputs_from_metadata`), so a stored
lap can be judged again with the current rules without a new upload. For laps stored
before 0.9.3.1, `legacy_inputs` recovers what is still there (alembic 0014 does the same).

Neighbours (V0.9.3.1)
---------------------
LMU may report a track-limits offence of the end of a lap only after the finish line.
When a lap's file shows such a report in its first seconds, the lap BEFORE it gets a
"late_track_limits" finding (review) — `link_neighbours`. These linked findings live in
laps.validation["linked"] and survive re-judging.
"""

from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from sqlalchemy.orm import Session

from . import car_identity, lap_insights, track_limits
from .config import settings
from .models import Lap
from .validation import PLAUSIBILITY_REASON_PREFIXES, ValidationResult, validate_and_read

logger = logging.getLogger("lmu_garage.lap_check")

# The "usual" driven distance of a layout: median over this many recent valid laps,
# and only once at least REFERENCE_MIN_LAPS exist.
REFERENCE_LAPS = 25
REFERENCE_MIN_LAPS = 3

INPUTS_VERSION = 1
CLIENT_INVALID_TEXT = "client flagged this lap as invalid (pit lane / track limits / etc.)"

# A neighbour lap: the previous lap's start + its time must meet this lap's start within
# this many seconds (same session clock), and both must have been recorded within an hour.
LINK_TOLERANCE_SECONDS = 2.0
LINK_MAX_RECORDED_GAP_SECONDS = 3600


def promoted_codes() -> frozenset:
    return track_limits.parse_promote(getattr(settings, "track_limits_invalidating", ""))


@dataclass
class LapCheck:
    validation: ValidationResult
    verdict: track_limits.Verdict
    stats: dict
    inputs: dict = field(default_factory=dict)

    @property
    def is_valid(self) -> bool:
        return self.validation.is_valid and self.verdict.status != track_limits.STATUS_INVALID

    @property
    def review_status(self) -> Optional[str]:
        return "pending" if self.is_valid and self.verdict.status == track_limits.STATUS_REVIEW else None

    def invalid_reason(self) -> Optional[list]:
        if self.is_valid:
            return None
        reasons = list(self.validation.reasons)
        for finding in self.verdict.invalidating:
            if finding.code == "client_invalid":
                reasons.append(CLIENT_INVALID_TEXT)
            else:
                reasons.append(track_limits.reason_text(finding.code, finding.at))
        return reasons or None


def is_automatic_reason(text) -> bool:
    """Was this laps.invalid_reason entry written by the automatic check (and not by an
    admin, a report or a moderation decision)? Only such laps may be re-judged valid."""
    if not isinstance(text, str):
        return False
    if text == CLIENT_INVALID_TEXT or text.startswith("track limits:"):
        return True
    # V0.9.4: the plausibility check's own reasons (server/validation.py) — e.g. the lap whose file
    # ended with the next lap's first metres, which the current check reads correctly
    return text.startswith(PLAUSIBILITY_REASON_PREFIXES)


def reference_distance(db: Session, track_name: str, exclude_lap_id: Optional[int] = None) -> Optional[float]:
    """Median driven distance of the most recent valid laps on this layout."""
    query = db.query(Lap.lap_stats).filter(Lap.track_name == track_name, Lap.is_valid.is_(True), Lap.lap_stats.is_not(None))
    if exclude_lap_id is not None:
        query = query.filter(Lap.id != exclude_lap_id)
    rows = query.order_by(Lap.id.desc()).limit(REFERENCE_LAPS).all()
    values = [stats.get("driven_m") for (stats,) in rows if isinstance(stats, dict)]
    values = [float(v) for v in values if isinstance(v, (int, float)) and v > 0]
    return statistics.median(values) if len(values) >= REFERENCE_MIN_LAPS else None


# ---------------------------------------------------------------------------
# inputs
# ---------------------------------------------------------------------------

def _dump(value):
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        return value.model_dump(exclude_none=True, by_alias=True)
    return value


def sectors_ok(sector_times) -> Optional[bool]:
    """Plausible sector times as the client reports them ([S1, S2, S3]; a client that found
    LMU's sectors implausible sends [S1, 0, 0])."""
    if not isinstance(sector_times, (list, tuple)) or len(sector_times) < 2:
        return None
    try:
        return float(sector_times[0]) > 0 and float(sector_times[1]) > 0
    except (TypeError, ValueError):
        return None


def inputs_from_metadata(metadata, client_valid: bool) -> dict:
    report = _dump(getattr(metadata, "lap_report", None)) or {}
    reasons = report.get("reasons") if isinstance(report, dict) and "reasons" in report else None
    return {
        "version": INPUTS_VERSION,
        "client_valid": bool(client_valid),
        "client_reasons": list(reasons) if reasons is not None else None,
        "client_state": _dump(getattr(metadata, "game_lap_state", None)) or None,
        "late": report.get("late") if isinstance(report, dict) else None,
        "tail_samples": getattr(metadata, "tail_samples", None),
        "steps_per_point": getattr(metadata, "track_limits_steps_per_point", None),
        "steps_per_penalty": getattr(metadata, "track_limits_steps_per_penalty", None),
        "sectors_ok": sectors_ok(getattr(metadata, "sector_times", None)),
        # V0.9.4: the client corrected LMU's lap time in the late window / could not confirm it
        **{key: report[key] for key in ("lap_time_corrected", "lap_time_unconfirmed")
           if isinstance(report, dict) and report.get(key)},
    }


def legacy_inputs(lap: Lap) -> dict:
    """What can be recovered for a lap stored before 0.9.3.1 (KEEP IN SYNC with alembic
    0014 inputs_for — enforced by a test)."""
    state = None
    verdict = lap.validation if isinstance(lap.validation, dict) else None
    if verdict:
        for finding in verdict.get("findings") or []:
            if isinstance(finding, dict) and finding.get("code") == "client_invalid":
                detail = finding.get("detail")
                if isinstance(detail, dict) and detail and "state" not in detail and "reasons" not in detail:
                    state = detail
    stats = lap.lap_stats if isinstance(lap.lap_stats, dict) else {}
    session = stats.get("session") if isinstance(stats.get("session"), dict) else {}
    return {
        "version": INPUTS_VERSION,
        "client_valid": bool(lap.client_claimed_valid) if lap.client_claimed_valid is not None else True,
        "client_reasons": None,
        "client_state": state,
        "late": None,
        "tail_samples": None,
        "steps_per_point": session.get("track_limits_steps_per_point"),
        "steps_per_penalty": session.get("track_limits_steps_per_penalty"),
        "sectors_ok": sectors_ok(lap.sector_times),
        "recovered": True,
    }


def inputs_for_lap(lap: Lap) -> dict:
    return lap.check_inputs if isinstance(lap.check_inputs, dict) and lap.check_inputs else legacy_inputs(lap)


# ---------------------------------------------------------------------------
# the check
# ---------------------------------------------------------------------------

def check_lap(db: Session, path: Path, metadata, *, client_valid: bool, exclude_lap_id: Optional[int] = None,
              inputs: Optional[dict] = None, promote: Optional[frozenset] = None) -> LapCheck:
    """`metadata`: LapMetadata (or anything with the same attributes). `inputs`: stored
    check inputs (re-judging); built from `metadata` when not given."""
    if inputs is None:
        inputs = inputs_from_metadata(metadata, client_valid)
    if promote is None:
        promote = promoted_codes()
    extra = tuple(dict.fromkeys([*track_limits.COLUMNS, *lap_insights.COLUMNS]))
    try:
        validation, columns = validate_and_read(path, metadata, extra)
    except Exception as exc:  # noqa: BLE001 — a crash in a check must never become a 500
        logger.warning("Lap check crashed for %s: %s", Path(path).name, exc)
        validation, columns = ValidationResult(is_valid=False, reasons=[f"validation error: {exc}"]), {}

    tail = int(inputs.get("tail_samples") or 0)
    # V0.9.4: the next lap's first samples at the end of the file (LMU reset the distance
    # before the client switched laps) are tail too — for the track limits and the figures
    tail = max(tail, int(getattr(validation, "reset_tail", 0) or 0))
    client_ok = bool(inputs.get("client_valid", client_valid))
    try:
        reference = reference_distance(db, metadata.track_name, exclude_lap_id) if columns else None
        verdict = track_limits.assess(
            columns, tail_samples=tail, client_valid=client_ok, client_state=inputs.get("client_state"),
            reference_distance=reference, client_reasons=inputs.get("client_reasons"), late=inputs.get("late"),
            steps_per_point=inputs.get("steps_per_point"), steps_per_penalty=inputs.get("steps_per_penalty"),
            sectors_ok=inputs.get("sectors_ok"), promote=promote,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Track-limits check crashed for %s: %s", Path(path).name, exc)
        verdict = track_limits.Verdict(
            status=track_limits.STATUS_VALID if client_ok else track_limits.STATUS_INVALID,
            findings=[] if client_ok else [track_limits.Finding("client_invalid", track_limits.INVALID, "client")],
            signals=["client"],
        )
    try:
        stats = lap_insights.compute(columns, lap_time=metadata.lap_time, tail_samples=tail,
                                      sector_times=getattr(metadata, "sector_times", None)) if columns else {}
    except Exception as exc:  # noqa: BLE001 — figures are a convenience, never a reason to fail
        logger.warning("Lap figures failed for %s: %s", Path(path).name, exc)
        stats = {}
    session = {key: getattr(metadata, key, None) for key in (
        "track_grip_level", "fixed_setup", "track_limits_steps_per_point", "track_limits_steps_per_penalty")}
    session = {k: v for k, v in session.items() if v is not None}
    if not session.get("track_limits_steps_per_point") and inputs.get("steps_per_point"):
        session["track_limits_steps_per_point"] = inputs["steps_per_point"]
    if not session.get("track_limits_steps_per_penalty") and inputs.get("steps_per_penalty"):
        session["track_limits_steps_per_penalty"] = inputs["steps_per_penalty"]
    if session:
        stats["session"] = session
    return LapCheck(validation=validation, verdict=verdict, stats=stats, inputs=inputs)


# ---------------------------------------------------------------------------
# stored verdict: own findings + findings linked from a neighbour lap
# ---------------------------------------------------------------------------

def stored_validation(verdict: track_limits.Verdict, previous: Optional[dict] = None) -> dict:
    """The verdict as stored in laps.validation, keeping what does not come from this lap's
    own check: findings linked from a neighbour and an admin's review decision."""
    out = verdict.as_dict()
    previous = previous if isinstance(previous, dict) else {}
    for key in ("linked", "review", "restored"):
        if previous.get(key):
            out[key] = previous[key]
    if out.get("linked"):
        out["status"] = combined_status(out)
    return out


def combined_status(validation: dict) -> str:
    severities = [f.get("severity") for f in validation.get("findings") or [] if isinstance(f, dict)]
    severities += [f.get("severity") for f in validation.get("linked") or [] if isinstance(f, dict)]
    return track_limits.status_of(s for s in severities if s)


def _early_reports(validation: Optional[dict]) -> List[dict]:
    """A lap's step rises in its first seconds that (may) belong to the lap before it."""
    out = []
    for f in (validation or {}).get("findings") or []:
        if not isinstance(f, dict) or f.get("source") != "track_limits_steps":
            continue
        detail = f.get("detail") or {}
        if f.get("code") == "track_limits_points_previous_lap" or (
                f.get("code") == "track_limits_points" and detail.get("attribution") == track_limits.ATTR_AMBIGUOUS):
            out.append(f)
    return out


def _has_client_late(validation: Optional[dict]) -> bool:
    return any(isinstance(f, dict) and f.get("code") == "late_track_limits" and f.get("source") == "late_window"
               for f in (validation or {}).get("findings") or [])


def _neighbour(db: Session, lap: Lap, offset: int) -> Optional[Lap]:
    candidates = (
        db.query(Lap)
        .filter(Lap.driver_id == lap.driver_id, Lap.track_name == lap.track_name,
                Lap.lap_number == lap.lap_number + offset, Lap.id != lap.id)
        .limit(20).all()
    )
    best, best_gap = None, None
    for other in candidates:
        if (other.car_model_raw or other.car_name) != (lap.car_model_raw or lap.car_name):
            continue
        before, after = (other, lap) if offset < 0 else (lap, other)
        gap = abs((before.started_at or 0.0) + (before.lap_time or 0.0) - (after.started_at or 0.0))
        if gap > LINK_TOLERANCE_SECONDS:
            continue
        if lap.recorded_at and other.recorded_at and abs(lap.recorded_at - other.recorded_at) > LINK_MAX_RECORDED_GAP_SECONDS:
            continue
        if best_gap is None or gap < best_gap:
            best, best_gap = other, gap
    return best


def _link(target: Lap, source: Lap, reports: List[dict]) -> bool:
    """Adds the late_track_limits findings `source`'s early reports imply to `target` (the
    lap before it). Idempotent; never if the client already observed the late window."""
    validation = dict(target.validation or {})
    if not validation or _has_client_late(validation):
        return False
    linked = [f for f in validation.get("linked") or [] if isinstance(f, dict)]
    if any((f.get("detail") or {}).get("next_lap_id") == source.id for f in linked):
        return False
    for report in reports:
        detail = report.get("detail") or {}
        attribution = track_limits.ATTR_THIS if report.get("code") == "track_limits_points_previous_lap" else track_limits.ATTR_AMBIGUOUS
        new = {"added": detail.get("added"), "seconds_after_line": report.get("t"), "attribution": attribution,
               "from": "next_lap", "next_lap_id": source.id}
        if detail.get("points") is not None:
            new["points"] = detail["points"]
        linked.append(track_limits.Finding(
            "late_track_limits", track_limits.severity_of("late_track_limits"), "next_lap", detail=new).as_dict())
    validation["linked"] = linked
    validation["status"] = combined_status(validation)
    target.validation = validation
    if target.is_valid and validation["status"] == track_limits.STATUS_REVIEW and not target.review_status:
        target.review_status = "pending"
    return True


def link_neighbours(db: Session, lap: Lap) -> int:
    """Attributes track-limits reports LMU sent after the finish line (see the module
    docstring). Call after the lap row is flushed; runs in the caller's transaction.
    Returns how many laps got a linked finding."""
    changed = 0
    try:
        reports = _early_reports(lap.validation)
        if reports:
            previous = _neighbour(db, lap, -1)
            if previous is not None and _link(previous, lap, reports):
                db.add(previous)
                changed += 1
        following = _neighbour(db, lap, +1)
        if following is not None:
            reports = _early_reports(following.validation)
            if reports and _link(lap, following, reports):
                db.add(lap)
                changed += 1
    except Exception as exc:  # noqa: BLE001 — attribution is a refinement, never a reason to fail an upload
        logger.warning("Linking lap %s to its neighbours failed: %s", getattr(lap, "id", "?"), exc)
    return changed


# ---------------------------------------------------------------------------
# car identity
# ---------------------------------------------------------------------------

def identity_fields(db: Session, metadata) -> dict:
    """The lap's car identity columns (server/car_identity.py), before the lap is added."""
    raw = getattr(metadata, "car_model", None)
    vehicle_model = getattr(metadata, "vehicle_model", None)
    identity = car_identity.resolve(vehicle_model, raw, lambda key: car_identity.lookup_alias(db, key))
    return {
        "car_model": identity.key,
        "car_model_raw": raw,
        "car_vehicle_model": car_identity.clean_text(vehicle_model),
        "car_identity_source": identity.source,
    }


def learn_from_upload(db: Session, metadata) -> dict:
    """Learns what the upload reports about the cars of the session (after the lap row was
    flushed). Runs inside the caller's transaction. Returns the alias-learning counts."""
    raw = getattr(metadata, "car_model", None)
    vehicle_model = getattr(metadata, "vehicle_model", None)
    grid = getattr(metadata, "grid_vehicles", None) or []
    grid = [g.model_dump(by_alias=True) if hasattr(g, "model_dump") else g for g in grid]
    pairs = car_identity.observations(raw, vehicle_model, grid)
    return car_identity.learn_aliases(db, pairs) if pairs else {}
