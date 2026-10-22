"""
routers/admin.py — Moderation endpoints.

Auth model: a single shared bearer token (LMU_GARAGE_ADMIN_TOKEN), checked
via a dependency separate from get_current_driver (this is operator
access, not a driver identity). Deliberately simple for this project's
scale (one or a few trusted operators) — if that stops being true, this
is the place to replace the shared-token check with per-admin accounts
and an audit log, not the endpoints themselves.

If LMU_GARAGE_ADMIN_TOKEN is unset, every endpoint here 503s rather than
either being silently open or silently protected by a guessable default.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import audit, car_identity, integrity, track_limits
from ..official import HELD, QUARANTINED
from ..records import evaluate_and_record
from ..request_context import current_request_id
from ..config import settings
from ..write_lock import RETRY_AFTER_S, MaintenanceActive, writing
from ..database import acquire_record_lock, get_db
from ..driver_lifecycle import delete_driver_rows
from ..models import CarAlias, Driver, Lap, LapReport, RaceRuleSet
from ..schemas import (
    AdminDriverOut,
    AdminInvalidateRequest,
    AdminLapReportOut,
    AdminLapSummary,
    AdminLockRequest,
    AdminReviewDecision,
    RaceRuleSetIn,
    RaceRuleSetOut,
)
from ..team_lifecycle import TeamOwnershipConflict, resolve_owned_teams_before_driver_deletion

router = APIRouter(prefix="/admin", tags=["admin"])


def require_admin(x_admin_token: Optional[str] = Header(None)) -> None:
    if settings.admin_token is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Admin endpoints are disabled — LMU_GARAGE_ADMIN_TOKEN is not set.",
        )
    if not x_admin_token or x_admin_token != settings.admin_token:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Invalid or missing admin token.")


def _lap_summary(lap: Lap, driver_name: str) -> AdminLapSummary:
    return AdminLapSummary(
        id=lap.id, driver_id=lap.driver_id, driver_name=driver_name,
        track_name=lap.track_name, car_name=lap.car_name, car_class=lap.car_class, car_model=lap.car_model,
        lap_time=lap.lap_time, is_valid=lap.is_valid, invalid_reason=lap.invalid_reason,
        client_claimed_valid=lap.client_claimed_valid, uploaded_at=lap.uploaded_at.isoformat(),
        validation=lap.validation, review_status=lap.review_status,
        car_model_raw=lap.car_model_raw, car_identity_source=lap.car_identity_source,
    )


@router.get("/reports", response_model=List[AdminLapReportOut], dependencies=[Depends(require_admin)])
def list_reports(
    unresolved_only: bool = True,
    db: Session = Depends(get_db),
) -> List[AdminLapReportOut]:
    query = db.query(LapReport)
    if unresolved_only:
        query = query.filter(LapReport.resolved_at.is_(None))
    reports = query.order_by(LapReport.created_at.desc()).limit(200).all()

    out = []
    for r in reports:
        lap = db.query(Lap).filter(Lap.id == r.lap_id).one_or_none()
        reporter = db.query(Driver).filter(Driver.id == r.reported_by_driver_id).one_or_none()
        if lap is None or reporter is None:
            continue  # lap or reporter deleted since the report was filed — nothing sensible to show
        lap_driver = db.query(Driver).filter(Driver.id == lap.driver_id).one_or_none()
        out.append(AdminLapReportOut(
            id=r.id,
            lap=_lap_summary(lap, lap_driver.display_name if lap_driver else "(deleted driver)"),
            reported_by_driver_id=r.reported_by_driver_id,
            reported_by_display_name=reporter.display_name,
            reason=r.reason,
            created_at=r.created_at.isoformat(),
            resolved_at=r.resolved_at.isoformat() if r.resolved_at else None,
            resolution=r.resolution,
        ))
    return out


@router.post("/reports/{report_id}/resolve", dependencies=[Depends(require_admin)])
def resolve_report(
    report_id: int,
    resolution: str,
    db: Session = Depends(get_db),
) -> dict:
    report = db.query(LapReport).filter(LapReport.id == report_id).one_or_none()
    if report is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Report not found.")
    report.resolved_at = datetime.now(timezone.utc)
    report.resolution = resolution
    db.add(report)
    db.commit()
    return {"status": "ok"}


@router.get("/laps/{lap_id}", response_model=AdminLapSummary, dependencies=[Depends(require_admin)])
def get_lap(lap_id: int, db: Session = Depends(get_db)) -> AdminLapSummary:
    """The "View" action on a report, or standalone lap lookup for direct
    moderation outside of any report (§15's "Lap moderation: View,
    Invalidate")."""
    lap = db.query(Lap).filter(Lap.id == lap_id).one_or_none()
    if lap is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Lap not found.")
    driver = db.query(Driver).filter(Driver.id == lap.driver_id).one_or_none()
    return _lap_summary(lap, driver.display_name if driver else "(deleted driver)")


@router.patch("/laps/{lap_id}/invalidate", dependencies=[Depends(require_admin)])
def invalidate_lap(
    lap_id: int,
    body: AdminInvalidateRequest,
    db: Session = Depends(get_db),
) -> dict:
    """Soft-invalidate a lap: flips is_valid to False with a recorded
    reason. Deliberately does NOT delete the row or its RecordEvents —
    per the architecture decision (see records.py), leaderboards and
    records are derived live from Lap.is_valid, so this alone removes the
    lap from leaderboards/records on the next query. No CurrentRecord
    table to reconcile, no re-computation step needed."""
    lap = db.query(Lap).filter(Lap.id == lap_id).one_or_none()
    if lap is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Lap not found.")
    lap.is_valid = False
    lap.invalid_reason = [f"admin: {body.reason}"]
    db.add(lap)
    audit.append(db, "lap.invalidated", actor="admin", lap_id=lap.id, driver_id=lap.driver_id,
                 request_id=current_request_id(), detail={"reason": body.reason})
    db.commit()
    return {"status": "ok", "lap_id": lap_id}


# ---------------------------------------------------------------- V0.9.3: track-limits review

@router.get("/review", response_model=List[AdminLapSummary], dependencies=[Depends(require_admin)])
def review_queue(
    status_filter: str = Query(default="pending", alias="status",
                               pattern="^(pending|held|quarantined|cleared|invalidated)$"),
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
) -> List[AdminLapSummary]:
    """Laps the check (server/track_limits.py, server/lap_decision.py) flagged for a human look.
    Each entry carries the verdict with every finding (what, where on the lap, which signal).

    V0.9.9: "pending" lists every lap waiting for a decision — "held" (REVIEW: not official until
    cleared) and the older "pending" ones (counting provisionally, flagged before V0.9.9).
    "held" / "quarantined" (integrity failures, see GET /admin/integrity) list only those."""
    wanted = ("pending", HELD) if status_filter == "pending" else (status_filter,)
    laps = (
        db.query(Lap).filter(Lap.review_status.in_(wanted))
        .order_by(Lap.uploaded_at.desc()).limit(limit).all()
    )
    names = dict(db.query(Driver.id, Driver.display_name).filter(Driver.id.in_({lap.driver_id for lap in laps})).all()) if laps else {}
    return [_lap_summary(lap, names.get(lap.driver_id, "(deleted driver)")) for lap in laps]


@router.post("/laps/{lap_id}/review", dependencies=[Depends(require_admin)])
def review_lap(lap_id: int, body: AdminReviewDecision, db: Session = Depends(get_db)) -> dict:
    """clear: the lap stays valid and leaves the queue. invalidate: same effect as
    PATCH /laps/{id}/invalidate (leaderboards and records follow live), with the
    review's findings as the reason.

    V0.9.9: clearing a HELD lap makes it official — its record events (PR / WR / TEAM_BEST,
    Discord) are evaluated now, against the laps that count at this moment, under the same lock
    as an upload. A quarantined lap cannot be cleared here: its file must verify first
    (POST /admin/laps/{id}/integrity). Every decision is written to the audit log."""
    lap = db.query(Lap).filter(Lap.id == lap_id).one_or_none()
    if lap is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Lap not found.")
    if lap.review_status == QUARANTINED:
        raise HTTPException(status.HTTP_409_CONFLICT, {
            "code": "QUARANTINED", "message": "The lap's file failed its integrity check — verify and release it first."})
    note = (body.note or "").strip()
    previous = lap.review_status
    records = []
    if body.decision == "clear":
        lap.review_status = "cleared"
        if previous == HELD and lap.is_valid:
            db.flush()
            acquire_record_lock(db, lap.track_name, lap.car_model or lap.car_name)
            records = evaluate_and_record(db, lap)
    else:
        lap.review_status = "invalidated"
        lap.is_valid = False
        reasons = [f"admin review: {note}" if note else "admin review: track limits"]
        lap.invalid_reason = reasons
    verdict = dict(lap.validation or {})
    verdict["review"] = {"decision": body.decision, "note": note or None, "at": datetime.now(timezone.utc).isoformat()}
    lap.validation = verdict
    db.add(lap)
    audit.append(db, "lap.reviewed", actor="admin", lap_id=lap.id, driver_id=lap.driver_id,
                 request_id=current_request_id(),
                 detail={"decision": body.decision, "previous": previous, "note": note or None,
                         "records": [e.record_type for e in records]})
    db.commit()
    return {"status": "ok", "lap_id": lap_id, "review_status": lap.review_status,
            "records": [e.record_type for e in records]}


# ---------------------------------------------------------------- V0.9.9: integrity + audit

@router.get("/integrity", dependencies=[Depends(require_admin)])
def integrity_overview(limit: int = Query(default=200, ge=1, le=1000), db: Session = Depends(get_db)) -> dict:
    """Quarantined laps (what was found, when, with which request id), how many laps are signed
    with the current key, and the audit chain head. A full check of every file:
    scripts/integrity_check.py (docs/RUNBOOKS.md)."""
    rows = (db.query(Lap).filter(Lap.review_status == QUARANTINED)
            .order_by(Lap.id.desc()).limit(limit).all())
    names = dict(db.query(Driver.id, Driver.display_name).filter(Driver.id.in_({r.driver_id for r in rows})).all()) if rows else {}
    total = db.query(func.count(Lap.id)).scalar() or 0
    unsigned = db.query(func.count(Lap.id)).filter(Lap.integrity_mac.is_(None)).scalar() or 0
    return {
        "quarantined": [{"lap_id": r.id, "driver_id": r.driver_id, "driver_name": names.get(r.driver_id),
                         "track_name": r.track_name, "lap_time": r.lap_time,
                         "quarantine": (r.validation or {}).get("quarantine")} for r in rows],
        "laps": total, "unsigned": unsigned, "audit_head": audit.head(db),
    }


@router.post("/laps/{lap_id}/integrity", dependencies=[Depends(require_admin)])
def verify_lap_integrity(lap_id: int, release: bool = Query(False), note: Optional[str] = Query(None, max_length=500),
                         db: Session = Depends(get_db)) -> dict:
    """Checks one lap's file now (full hash, no cache). A failure quarantines the lap (if it is
    not yet); with release=true a quarantined lap whose file verifies again returns to the state it
    had before (review status kept from then)."""
    lap = db.query(Lap).filter(Lap.id == lap_id).one_or_none()
    if lap is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Lap not found.")
    result = integrity.verify(lap)
    request_id = current_request_id()
    released = False
    if result.quarantine:
        integrity.quarantine(db, lap, result, actor="admin", request_id=request_id)
    elif release and lap.review_status == QUARANTINED and result.ok:
        integrity.release(db, lap, actor="admin", note=note, request_id=request_id)
        released = True
    db.commit()
    return {"lap_id": lap_id, "result": result.as_dict(), "review_status": lap.review_status, "released": released}


@router.get("/audit", dependencies=[Depends(require_admin)])
def audit_events(lap_id: Optional[int] = Query(None), kind: Optional[str] = Query(None, max_length=40),
                 request_id: Optional[str] = Query(None, max_length=64),
                 limit: int = Query(default=200, ge=1, le=2000), db: Session = Depends(get_db)) -> dict:
    """The audit log, newest first, filtered by lap, kind or request id."""
    from ..models import AuditEvent

    query = db.query(AuditEvent)
    if lap_id is not None:
        query = query.filter(AuditEvent.lap_id == lap_id)
    if kind:
        query = query.filter(AuditEvent.kind == kind)
    if request_id:
        query = query.filter(AuditEvent.request_id == request_id)
    rows = query.order_by(AuditEvent.id.desc()).limit(limit).all()
    return {"events": [{"id": r.id, "at": r.created_at.isoformat() if r.created_at else None, "kind": r.kind,
                        "actor": r.actor, "lap_id": r.lap_id, "driver_id": r.driver_id, "request_id": r.request_id,
                        "detail": r.detail} for r in rows],
            "head": audit.head(db)}


@router.get("/audit/verify", dependencies=[Depends(require_admin)])
def audit_verify(db: Session = Depends(get_db)) -> dict:
    """Recomputes the whole audit chain (server/audit.py). ok=false names the first broken row."""
    return audit.verify_db(db)


@router.get("/track-limits/rules", dependencies=[Depends(require_admin)])
def track_limits_rules() -> dict:
    """V0.9.3.1: every track-limits rule with its tier (LMU verdict / Garage16 rule /
    heuristic), its effect and whether its meaning is confirmed — plus which heuristics this
    server lets invalidate (LMU_GARAGE_TRACK_LIMITS_INVALIDATING; default: none)."""
    from ..lap_check import promoted_codes

    promoted = promoted_codes()
    rules = track_limits.rules_table()
    for rule in rules:
        rule["effect"] = track_limits.severity_of(rule["code"], promoted)
    return {"version": track_limits.TRACK_LIMITS_VERSION, "rules": rules, "invalidating_heuristics": sorted(promoted)}


@router.get("/car-identity", dependencies=[Depends(require_admin)])
def car_identity_overview(db: Session = Depends(get_db)) -> dict:
    """What the server has learned about car models (server/car_identity.py): every
    alias with how often the game confirmed it, conflicts (two models reported for one
    veh_filename group — never used), and how the laps' identities were decided."""
    aliases = db.query(CarAlias).order_by(CarAlias.canonical, CarAlias.alias_key).all()
    sources = dict(db.query(Lap.car_identity_source, func.count()).group_by(Lap.car_identity_source).all())
    # V0.9.3.1: identities real data must decide on (cut names, two veh models or classes)
    combos = (
        db.query(Lap.car_model, Lap.car_model_raw, Lap.car_class, func.count())
        .filter(Lap.car_model.is_not(None))
        .group_by(Lap.car_model, Lap.car_model_raw, Lap.car_class).all()
    )
    suspects = car_identity.identity_suspects(
        {"identity": model, "veh_file": raw, "car_class": cls, "laps": count} for model, raw, cls, count in combos)
    return {
        "aliases": [
            {"key": a.alias_key, "model": a.canonical, "source": a.source, "seen": a.seen_count,
             "conflict": bool(a.conflict), "conflict_with": a.conflict_with,
             "used": car_identity.alias_usable(a),
             "last_seen": a.last_seen.isoformat() if a.last_seen else None}
            for a in aliases
        ],
        "laps_by_source": {str(k): v for k, v in sources.items()},
        "suspects": suspects,
        "rules_version": car_identity.IDENTITY_RULES_VERSION,
    }


# ---------------------------------------------------------------------------
# V0.9.4 — race rule sets for the Stint Analyzer's strategy calculator
# ---------------------------------------------------------------------------

MAX_RULE_SETS = 200


def _rule_set_or_404(db: Session, rule_set_id: int) -> RaceRuleSet:
    row = db.get(RaceRuleSet, rule_set_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Rule set not found.")
    return row


def _name_taken(db: Session, name: str, except_id: Optional[int] = None) -> bool:
    stmt = select(RaceRuleSet.id).where(func.lower(RaceRuleSet.name) == name.lower())
    if except_id is not None:
        stmt = stmt.where(RaceRuleSet.id != except_id)
    return db.execute(stmt).first() is not None


@router.get("/rule-sets", response_model=List[RaceRuleSetOut], dependencies=[Depends(require_admin)])
def list_rule_sets(db: Session = Depends(get_db)) -> list:
    return db.query(RaceRuleSet).order_by(RaceRuleSet.name).all()


@router.post("/rule-sets", response_model=RaceRuleSetOut, status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_admin)])
def create_rule_set(body: RaceRuleSetIn, db: Session = Depends(get_db)) -> RaceRuleSet:
    if db.query(func.count(RaceRuleSet.id)).scalar() >= MAX_RULE_SETS:
        raise HTTPException(status.HTTP_409_CONFLICT, f"At most {MAX_RULE_SETS} rule sets.")
    if _name_taken(db, body.name):
        raise HTTPException(status.HTTP_409_CONFLICT, "A rule set with this name exists already.")
    row = RaceRuleSet(name=body.name, description=body.description, track_name=body.track_name,
                      car_class=body.car_class, rules=body.rules.model_dump(exclude_none=True))
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.put("/rule-sets/{rule_set_id}", response_model=RaceRuleSetOut, dependencies=[Depends(require_admin)])
def update_rule_set(rule_set_id: int, body: RaceRuleSetIn, db: Session = Depends(get_db)) -> RaceRuleSet:
    row = _rule_set_or_404(db, rule_set_id)
    if _name_taken(db, body.name, except_id=row.id):
        raise HTTPException(status.HTTP_409_CONFLICT, "A rule set with this name exists already.")
    row.name, row.description, row.track_name, row.car_class = body.name, body.description, body.track_name, body.car_class
    row.rules = body.rules.model_dump(exclude_none=True)
    row.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(row)
    return row


@router.delete("/rule-sets/{rule_set_id}", dependencies=[Depends(require_admin)])
def delete_rule_set(rule_set_id: int, db: Session = Depends(get_db)) -> dict:
    row = _rule_set_or_404(db, rule_set_id)
    db.delete(row)
    db.commit()
    return {"status": "deleted", "id": rule_set_id}


@router.get("/drivers", response_model=List[AdminDriverOut], dependencies=[Depends(require_admin)])
def list_drivers(
    search: Optional[str] = Query(default=None, max_length=100),
    limit: int = 50,
    db: Session = Depends(get_db),
) -> List[AdminDriverOut]:
    """§15 driver moderation needs a way to find a driver at all — there
    was no listing/search endpoint anywhere in the API before this one.
    Substring, case-insensitive (unlike GET /drivers/lookup's exact-match
    requirement — that endpoint is reachable by any signed-in driver and
    deliberately can't become a searchable directory; this one is gated
    by the operator admin token, a categorically different trust level).
    Includes email — see AdminDriverOut's docstring for why that's fine
    here specifically."""
    query = db.query(Driver)
    if search:
        query = query.filter(Driver.display_name.ilike(f"%{search.strip()}%"))
    drivers = query.order_by(Driver.id).limit(min(limit, 200)).all()

    lap_counts = dict(
        db.execute(
            select(Lap.driver_id, func.count()).where(
                Lap.driver_id.in_([d.id for d in drivers])
            ).group_by(Lap.driver_id)
        ).all()
    ) if drivers else {}

    return [
        AdminDriverOut(
            driver_id=d.id, display_name=d.display_name, email=d.email,
            is_locked=d.is_locked, locked_reason=d.locked_reason,
            total_laps=lap_counts.get(d.id, 0),
        )
        for d in drivers
    ]


@router.post("/drivers/{driver_id}/lock", dependencies=[Depends(require_admin)])
def lock_driver(
    driver_id: int,
    body: AdminLockRequest,
    db: Session = Depends(get_db),
) -> dict:
    """Blocks all future authenticated requests from this driver
    (get_current_driver checks is_locked) without touching their existing
    data or revoking+losing the token the way self-service revoke does —
    reversible via /unlock."""
    driver = db.query(Driver).filter(Driver.id == driver_id).one_or_none()
    if driver is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Driver not found.")
    driver.is_locked = True
    driver.locked_reason = body.reason
    db.add(driver)
    db.commit()
    return {"status": "ok", "driver_id": driver_id}


@router.post("/drivers/{driver_id}/unlock", dependencies=[Depends(require_admin)])
def unlock_driver(driver_id: int, db: Session = Depends(get_db)) -> dict:
    driver = db.query(Driver).filter(Driver.id == driver_id).one_or_none()
    if driver is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Driver not found.")
    driver.is_locked = False
    driver.locked_reason = None
    db.add(driver)
    db.commit()
    return {"status": "ok", "driver_id": driver_id}


@router.delete("/drivers/{driver_id}", dependencies=[Depends(require_admin)])
def delete_driver(
    driver_id: int,
    force: bool = False,
    db: Session = Depends(get_db),
) -> dict:
    """Same cascade as the self-service DELETE /accounts/me — see that
    endpoint's docstring for the FK-ordering reasoning. This is the admin
    equivalent for e.g. GDPR/DSGVO erasure requests submitted outside the
    web UI (support email, etc.), or removing a banned cheater's data.

    V0.7.2: same team-ownership guard as the self-service endpoint —
    blocked (409) by default if this driver owns a team with other
    members. Unlike self-service, an admin can pass ?force=true to
    auto-transfer ownership to the next-earliest-joined member instead: a
    banned cheater won't come back to transfer it themselves, and an
    admin acting on a support/GDPR request needs a way to actually finish
    the deletion."""
    driver = db.query(Driver).filter(Driver.id == driver_id).one_or_none()
    if driver is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Driver not found.")

    try:
        resolve_owned_teams_before_driver_deletion(db, driver_id, force=force)
    except TeamOwnershipConflict as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This driver owns a team with other members — retry with ?force=true to "
            f"auto-transfer ownership, or resolve manually first: {', '.join(t.name for t in exc.teams)}",
        ) from exc

    # V0.9.8: rows and files go together under the shared write lock (server/write_lock.py) —
    # a backup running right now must not catch the rows deleted but the files still there
    try:
        with writing():
            telemetry_paths = delete_driver_rows(db, driver)

            for path_str in telemetry_paths:
                try:
                    Path(path_str).unlink(missing_ok=True)
                except OSError:
                    pass
    except MaintenanceActive:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, {"code": "MAINTENANCE", "message": "A backup is running — try again in a few minutes."},
                            headers={"Retry-After": str(RETRY_AFTER_S)})

    return {"status": "ok", "driver_id": driver_id}


# ---------------------------------------------------------------------------
# V0.9.7 — operations: upload outcomes and the database/file consistency
# ---------------------------------------------------------------------------

@router.get("/operations", dependencies=[Depends(require_admin)])
def operations(hours: int = Query(24, ge=1, le=24 * 30),
               client_version: Optional[str] = Query(None, max_length=32),
               outcome: Optional[str] = Query(None, pattern="^(accepted|duplicate|rejected|error)$"),
               request_id: Optional[str] = Query(None, max_length=64),
               db: Session = Depends(get_db)) -> dict:
    """Uploads of the last `hours`: per outcome, failure rate, the most common reasons, durations,
    per client version, the latest failures (each with its request id — the same id is in the
    server log and the client's X-Request-ID response header). Plus the analysis cache."""
    from ..analysis_cache import ANALYSIS_CACHE_VERSION, analysis_cache
    from ..lap_insights import INSIGHTS_VERSION
    from ..main import VERSION
    from ..upload_log import find, summary

    stats = analysis_cache.stats()
    return {
        "server_version": VERSION,
        # V0.9.8: drill-down by request id (the id in the client's error message / the log)
        **({"request": find(db, request_id)} if request_id else {}),
        "analysis_cache_version": ANALYSIS_CACHE_VERSION,
        "insights_version": INSIGHTS_VERSION,
        "uploads": summary(db, hours=hours, client_version=client_version, outcome=outcome),
        "analysis_cache": {"entries": stats.entries, "bytes": stats.bytes, "hits": stats.hits, "misses": stats.misses},
    }


@router.get("/consistency", dependencies=[Depends(require_admin)])
def consistency(deep: bool = Query(False), db: Session = Depends(get_db)) -> dict:
    """Database vs telemetry files (server/consistency.py). Read-only: the repairs are
    scripts/consistency_check.py's, behind explicit flags."""
    from ..consistency import check

    return check(db, deep=deep)
