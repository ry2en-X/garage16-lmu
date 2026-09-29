"""
routers/telemetry.py — Receives the payload built by client/uploader.py's
Uploader._build_payload(): a multipart POST with a `telemetry` file plus
`envelope` (JSON string) and `signature` (hex HMAC) form fields.

Verification order matters here:
  1. Parse envelope JSON — must be a JSON object, not array/string/number.
  2. Recompute the HMAC over the envelope bytes and compare to `signature`,
     using *this driver's* client_secret. Reject before touching the file
     or DB, and before spending any effort on detailed content validation.
  3. Only once the signature checks out: validate envelope.metadata against
     LapMetadata (schemas.py).
  4. Stream the telemetry file to a temp path while computing its size and
     sha256 incrementally, enforcing settings.max_upload_bytes as we go.
  5. Persist: run telemetry through server/validation.py, AND the result
     with the client's own validity claim (see P0-3), insert the lap,
     evaluate records, and commit — all in one transaction.

Upload is idempotent on (driver_id, telemetry_hash).

Hardening (V0.5.3):
  - P0-1: The client now strips local-only fields (`uploaded`) before
    sending. The server rejects unknown fields via extra="forbid".
  - P0-2: Envelope type checked, signature validated as hex before
    compare_digest, file extension forced to .parquet, validator
    exceptions caught as is_valid=False (not 500).
  - P0-3: is_valid = server_valid AND client_valid.
  - P0-4a: pg_advisory_xact_lock serializes record evaluation per
    (track, car) on PostgreSQL.
  - P0-4b: IntegrityError branch does NOT delete the content-addressed
    file (belongs to the winning concurrent upload).
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..auth import get_current_driver
from ..config import settings
from ..crypto import decrypt_secret
from ..database import acquire_record_lock, get_db
from ..models import Driver, Lap, LapReport
from ..rate_limit import rate_limit
from ..records import evaluate_and_record
from ..catalog import canonical_class, track_matches_query
from ..versioning import is_client_outdated
from ..schemas import LapMetadata, LapReportOut, LapReportRequest, LapSummary, UploadResponse
from ..security import verify_hmac
from ..storage import telemetry_storage_dir
from ..validation import ValidationResult, validate_telemetry

router = APIRouter(prefix="/telemetry", tags=["telemetry"])
logger = logging.getLogger("lmu_garage.telemetry")

_UPLOAD_CHUNK_SIZE = 1024 * 1024  # 1 MiB


def _format_validation_error(exc: ValidationError) -> str:
    parts = []
    for err in exc.errors()[:3]:
        field = ".".join(str(loc) for loc in err["loc"])
        parts.append(f"{field}: {err['msg']}")
    return "; ".join(parts)


def _stream_to_temp_file(upload: UploadFile, dest_dir: Path) -> tuple[Path, str, int]:
    """Streams `upload` into a temp file inside `dest_dir`, computing its
    sha256 and total size incrementally. Enforces settings.max_upload_bytes
    while streaming. Returns (temp_path, sha256_hex, total_bytes).

    Plain `def`, not `async def` (V0.8-NAS fix — see upload_lap's
    docstring for the full story): reads via upload.file (the underlying
    SpooledTemporaryFile Starlette wraps), a genuinely synchronous
    file-like object, instead of `await upload.read(...)`."""
    tmp_path = dest_dir / f".upload-{uuid.uuid4().hex}.tmp"
    sha256 = hashlib.sha256()
    total = 0
    try:
        with tmp_path.open("wb") as out:
            while True:
                chunk = upload.file.read(_UPLOAD_CHUNK_SIZE)
                if not chunk:
                    break
                total += len(chunk)
                if total > settings.max_upload_bytes:
                    raise HTTPException(
                        status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Telemetry file too large."
                    )
                sha256.update(chunk)
                out.write(chunk)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise
    return tmp_path, sha256.hexdigest(), total


@router.post("/upload", response_model=UploadResponse)
def upload_lap(
    telemetry: UploadFile = File(...),
    envelope: str = Form(...),
    signature: str = Form(...),
    driver: Driver = Depends(get_current_driver),
    db: Session = Depends(get_db),
    _rl: None = Depends(rate_limit("upload", per_driver=True)),
) -> UploadResponse:
    """Plain `def`, not `async def` — a real V0.8-NAS fix, not a style
    choice. This endpoint does purely synchronous, blocking work
    throughout (SQLAlchemy calls, disk I/O) with no genuinely async
    operations of its own. As `async def`, FastAPI runs it directly on
    the single event-loop thread; the previous version's `await
    upload.read(...)` and `await run_in_threadpool(...)` calls gave the
    event loop points to switch tasks, but a SQLite transaction (BEGIN
    IMMEDIATE, see server/database.py) opened before such an await stays
    open across it — so a second concurrent request's SQLite lock wait
    (itself a blocking C-level call, not awaitable) could freeze the
    only event-loop thread before the first request ever got scheduled
    again to COMMIT and release that lock. A genuine, reproducible
    deadlock, not just contention: found via
    tests/test_concurrency.py's duplicate-upload race test, which failed
    intermittently in exactly this way regardless of how long
    PRAGMA busy_timeout was set to (confirmed by testing 5s and 15s —
    the failure duration tracked the timeout, meaning the lock was never
    released, not just slow).

    As a plain `def`, FastAPI runs this on its worker thread pool
    instead — genuine OS-thread concurrency, where one request blocking
    on SQLite's lock never prevents another's transaction from actually
    progressing and committing. This matches every other route handler
    in this project already (this was the only `async def` endpoint in
    routers/), and PostgreSQL (this project's real production database)
    was never subject to this specific failure mode in the first place —
    it doesn't single-thread transactions the way SQLite's file lock
    does — but the underlying bug (blocking calls on an async event
    loop) is a genuine correctness issue independent of which database
    is behind it.
    """

    # --- 1. Parse envelope JSON ---
    try:
        envelope_dict = json.loads(envelope)
    except json.JSONDecodeError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "envelope is not valid JSON.")

    # P0-2: envelope must be a JSON object — an array, string, or number
    # would cause AttributeError on `.get()` below, surfacing as 500.
    if not isinstance(envelope_dict, dict):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "envelope must be a JSON object.")

    # V0.6.0: reject clients older than settings.min_client_version before
    # doing any other work. Prevents a known-broken old client (like
    # V0.5.2's P0-1 bug) from silently piling up permanent failures.
    client_version = envelope_dict.get("client_version", "0.0.0")
    if is_client_outdated(client_version, settings.min_client_version):
        # V0.8.6: 426 Upgrade Required with a machine-readable body
        # instead of a generic 400 — the desktop client recognizes
        # code == "UPDATE_REQUIRED" and (crucially) does NOT mark the lap
        # as permanently rejected, so it uploads normally after the
        # friend updates. A plain 400 used to get every pending lap
        # rejected forever.
        raise HTTPException(
            status.HTTP_426_UPGRADE_REQUIRED,
            {
                "code": "UPDATE_REQUIRED",
                "message": f"Client version {client_version} is too old (minimum: "
                f"{settings.min_client_version}). Update the desktop client.",
                "min_client_version": settings.min_client_version,
                "download_url": settings.client_download_url,
            },
        )

    metadata_raw = envelope_dict.get("metadata")
    if not isinstance(metadata_raw, dict):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "envelope.metadata is missing or not an object.")

    # --- 2. HMAC signature check (before content validation) ---
    envelope_bytes = json.dumps(envelope_dict, sort_keys=True).encode()
    signing_key = decrypt_secret(driver.client_secret)

    # P0-2: Validate signature is pure hex — compare_digest raises
    # TypeError on non-ASCII, which would surface as an unhandled 500.
    if not signature or not all(c in '0123456789abcdefABCDEF' for c in signature):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Signature must be a hex string.")

    if not verify_hmac(signing_key.encode(), envelope_bytes, signature):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Signature verification failed.")

    # --- 3. Validate metadata schema ---
    try:
        metadata = LapMetadata.model_validate(metadata_raw)
    except ValidationError as exc:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"envelope.metadata is invalid: {_format_validation_error(exc)}",
        )

    # --- 4. Stream telemetry to temp file ---
    out_dir = telemetry_storage_dir(driver.id, metadata.track_name, metadata.car_name)
    tmp_path, actual_hash, _size = _stream_to_temp_file(telemetry, out_dir)

    if actual_hash != envelope_dict.get("telemetry_hash"):
        tmp_path.unlink(missing_ok=True)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "telemetry_hash does not match uploaded file.")

    # --- 5. Idempotency check ---
    existing = (
        db.query(Lap)
        .filter(Lap.driver_id == driver.id, Lap.telemetry_hash == actual_hash)
        .one_or_none()
    )
    if existing is not None:
        tmp_path.unlink(missing_ok=True)
        return UploadResponse(status="ok", lap_id=existing.id, duplicate=True)

    # --- 6. Move to final content-addressed path ---
    # P0-2: Always use .parquet — never trust the client-supplied filename
    # extension (could be .exe, .html, or pathologically long).
    dest_path = out_dir / f"{actual_hash}.parquet"
    tmp_path.replace(dest_path)

    # --- 7. Validate, insert, evaluate records (one transaction) ---
    try:
        # Catch validator exceptions as is_valid=False, not 500.
        # A corrupt parquet or unexpected dtype shouldn't crash the upload.
        #
        # V0.6.0 (P1-3): run in the threadpool, not inline on the event
        # loop. pd.read_parquet + numpy checks on up to max_upload_bytes
        # of data is CPU-bound; running it inline blocks every other
        # request this worker is handling for the duration. This doesn't
        # shorten the DB transaction itself (that needs a bigger endpoint
        # restructure — see README's "known limitations"), but it stops
        # validation from starving concurrent requests on the same worker.
        telemetry_bytes = dest_path.read_bytes()
        try:
            validation = validate_telemetry(telemetry_bytes, metadata)
        except Exception as exc:
            logger.warning("Telemetry validation crashed for %s: %s", actual_hash, exc)
            validation = ValidationResult(is_valid=False, reasons=[f"validation error: {exc}"])

        # P0-3: AND server plausibility with client validity. The server
        # checks physical plausibility; the client knows race-context
        # (pit lane, count_lap_flag, track limits). Both sides can reject.
        is_valid = validation.is_valid and metadata.is_valid
        # invalid_reason records WHY, for the driver's own /telemetry/laps
        # view and for admin moderation — previously validation.reasons
        # was computed and then discarded (audit finding: "the driver sees
        # only 'invalid'").
        invalid_reason = None if is_valid else (validation.reasons or (
            ["client flagged this lap as invalid (pit lane / track limits / etc.)"]
            if not metadata.is_valid else None
        ))

        lap = Lap(
            driver_id=driver.id,
            track_name=metadata.track_name,
            car_name=metadata.car_name,
            car_class=canonical_class(metadata.car_class),
            car_class_raw=metadata.car_class,
            car_model=metadata.car_model,
            session_type=metadata.session_type,
            lap_number=metadata.lap_number,
            lap_time=metadata.lap_time,
            sector_times=metadata.sector_times,
            is_valid=is_valid,
            client_claimed_valid=metadata.is_valid,
            invalid_reason=invalid_reason,
            started_at=metadata.started_at,
            recorded_at=metadata.recorded_at,
            ambient_temp=metadata.ambient_temp,
            track_temp=metadata.track_temp,
            sample_count=metadata.sample_count,
            telemetry_hash=actual_hash,
            telemetry_path=str(dest_path),
            client_version=envelope_dict.get("client_version"),
            sent_at=envelope_dict.get("sent_at"),
        )
        db.add(lap)
        try:
            db.flush()
        except IntegrityError:
            # P0-4b: Do NOT delete dest_path — it's content-addressed,
            # the winner's Lap row points at this exact file.
            db.rollback()
            existing = (
                db.query(Lap)
                .filter(Lap.driver_id == driver.id, Lap.telemetry_hash == actual_hash)
                .one()
            )
            return UploadResponse(status="ok", lap_id=existing.id, duplicate=True)

        # P0-4a: Advisory lock serializes record evaluation per (track, car)
        # on PostgreSQL; no-op on SQLite (BEGIN IMMEDIATE handles it).
        acquire_record_lock(db, metadata.track_name, metadata.car_name)
        evaluate_and_record(db, lap)
        db.commit()
    except Exception:
        db.rollback()
        dest_path.unlink(missing_ok=True)
        raise

    db.refresh(lap)
    return UploadResponse(status="ok", lap_id=lap.id, duplicate=False)


@router.get("/laps", response_model=List[LapSummary])
def list_my_laps(
    track_name: Optional[str] = None,
    car_name: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
    driver: Driver = Depends(get_current_driver),
    db: Session = Depends(get_db),
) -> List[LapSummary]:
    query = db.query(Lap).filter(Lap.driver_id == driver.id)
    if track_name:
        # V0.8.9: a search, not an exact match — case-insensitive, and
        # venue-aware ("fuji" finds every Fuji layout). An exact raw name
        # still matches itself, so callers passing a full name are unaffected.
        mine = [t for (t,) in db.query(Lap.track_name).filter(Lap.driver_id == driver.id).distinct().all()]
        wanted = [t for t in mine if t == track_name or track_matches_query(t, track_name)]
        query = query.filter(Lap.track_name.in_(wanted)) if wanted else query.filter(Lap.id < 0)
    if car_name:
        query = query.filter(Lap.car_name == car_name)
    laps = query.order_by(Lap.uploaded_at.desc()).offset(max(offset, 0)).limit(min(limit, 500)).all()
    return [
        LapSummary(
            id=lap.id,
            track_name=lap.track_name,
            car_name=lap.car_name,
            session_type=lap.session_type,
            lap_number=lap.lap_number,
            lap_time=lap.lap_time,
            sector_times=lap.sector_times,
            is_valid=lap.is_valid,
            invalid_reason=lap.invalid_reason,
            ambient_temp=lap.ambient_temp,
            track_temp=lap.track_temp,
            uploaded_at=lap.uploaded_at.isoformat(),
        )
        for lap in laps
    ]


@router.post("/laps/{lap_id}/report", response_model=LapReportOut, status_code=status.HTTP_201_CREATED)
def report_lap(
    lap_id: int,
    body: LapReportRequest,
    driver: Driver = Depends(get_current_driver),
    db: Session = Depends(get_db),
    _rl: None = Depends(rate_limit("report", per_driver=True)),
) -> LapReportOut:
    """Any authenticated driver can flag a lap as suspicious. Purely
    advisory: it does not change is_valid by itself — an admin reviews
    reports via GET /admin/reports and acts (invalidate the lap, or mark
    the report resolved with no action) through the admin router."""
    lap = db.query(Lap).filter(Lap.id == lap_id).one_or_none()
    if lap is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Lap not found.")

    report = LapReport(lap_id=lap_id, reported_by_driver_id=driver.id, reason=body.reason)
    db.add(report)
    db.commit()
    db.refresh(report)
    return LapReportOut(
        id=report.id,
        lap_id=report.lap_id,
        reported_by_driver_id=report.reported_by_driver_id,
        reason=report.reason,
        created_at=report.created_at.isoformat(),
        resolved_at=None,
        resolution=None,
    )
