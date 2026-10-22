"""
integrity.py — is a stored lap file still exactly what the server received? (V0.9.9)

What the server keeps per lap (models.Lap): the sha256 of the bytes as they arrived
(`telemetry_hash`, also the file name), their size (`telemetry_bytes`), the request id of the upload
(`upload_request_id`) and a MAC (`integrity_mac`) — an HMAC with the server's integrity key over the
lap id, the driver, the sha256, the size and the lap time.

What each piece proves, and what it does not
--------------------------------------------
* sha256 + size: the file on disk is byte for byte the file that was received. A changed, swapped
  or truncated file is detected — as long as the stored hash itself is trustworthy.
* MAC: the stored hash, size and lap time were written by this server. Somebody who can change the
  database and the files (a manipulated backup, direct database access) but does not hold the
  integrity key cannot make a swapped file or an edited lap time verify again.
* NEITHER proves that the file was authentic BEFORE the upload. The desktop client builds the file
  and its hash; a manipulated client can produce a forged lap and a correct hash for it. Whether
  the content is plausible is the job of the upload check (server/lap_decision.py), and even that
  only finds inconsistencies — not a consistently fabricated lap.
* Whoever holds the integrity key and can write the database can produce valid MACs.

Where it is checked
-------------------
  every analysis access    routers/analysis.py `_telemetry_file` -> verify_for_access (cached:
                           a file whose size, mtime, ctime and inode did not change is re-hashed
                           at most every CACHE_SECONDS; V0.9.9.1: ctime too — a program can put
                           the mtime back with utime(), but every write or utime() sets the ctime)
  audit / repair           scripts/integrity_check.py (all laps, full hash, --apply quarantines)
  re-judging               scripts/revalidate_laps.py (a file that fails is never re-judged)
  restore                  scripts/restore.sh validates every file's sha256 (V0.9.8)

A failure puts the lap into quarantine (review_status "quarantined"): it leaves leaderboards and
records at once, only its owner and admins still see it, and an audit event records what was found
with the request id. An admin releases it (Admin → Integrität) only after the file verifies again.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import stat as stat_mod
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .config import settings

logger = logging.getLogger("lmu_garage.integrity")

MAC_VERSION = "v1"
CACHE_SECONDS = 600.0
CACHE_MAX_ENTRIES = 4096
CHUNK = 1024 * 1024

OK = "ok"
MISSING = "missing"
OUTSIDE = "outside_storage"
NOT_REGULAR = "not_a_regular_file"
SIZE_MISMATCH = "size_mismatch"
HASH_MISMATCH = "hash_mismatch"
MAC_INVALID = "mac_invalid"
UNREADABLE = "unreadable"
# not failures: the lap predates V0.9.9 (no MAC yet) / its MAC was made with a key this server no
# longer has (reported, never quarantined — the file itself still matches its hash)
UNSIGNED = "unsigned"
UNKNOWN_KEY = "unknown_key"

QUARANTINE_ON = frozenset({MISSING, OUTSIDE, NOT_REGULAR, SIZE_MISMATCH, HASH_MISMATCH, MAC_INVALID, UNREADABLE})

_DEV_FALLBACK = b"garage16-dev-only-integrity-key-not-secret"


# ---------------------------------------------------------------------------------------- keys

def _derive(secret: bytes) -> bytes:
    """A purpose-bound key derived from a client-secret encryption key: the same secret never
    serves two purposes directly (HMAC-SHA256 as a key-derivation step, label below)."""
    return hmac.new(secret, b"garage16 integrity key v1", hashlib.sha256).digest()


def load_keys() -> List[bytes]:
    """The integrity keys: LMU_GARAGE_INTEGRITY_KEY (comma-separated; the FIRST signs, all verify —
    rotation like LMU_GARAGE_SECRET_KEY). Not set: derived from each LMU_GARAGE_SECRET_KEY key.
    Neither set: a fixed development key (never in production — config refuses to start there
    without LMU_GARAGE_SECRET_KEY, server/crypto.py)."""
    raw = os.environ.get("LMU_GARAGE_INTEGRITY_KEY", "")
    keys = [k.strip().encode() for k in raw.split(",") if k.strip()]
    if keys:
        return keys
    secrets = [k.strip().encode() for k in os.environ.get("LMU_GARAGE_SECRET_KEY", "").split(",") if k.strip()]
    if secrets:
        return [_derive(s) for s in secrets]
    return [_derive(_DEV_FALLBACK)]


def key_id(key: bytes) -> str:
    return hashlib.sha256(b"garage16-kid" + key).hexdigest()[:8]


def _keys_by_id() -> Dict[str, bytes]:
    return {key_id(k): k for k in load_keys()}


def sign(message: bytes) -> str:
    key = load_keys()[0]
    return f"{MAC_VERSION}:{key_id(key)}:{hmac.new(key, message, hashlib.sha256).hexdigest()}"


def check_mac(message: bytes, mac: Optional[str]) -> str:
    """OK / UNSIGNED / UNKNOWN_KEY / MAC_INVALID."""
    if not mac:
        return UNSIGNED
    parts = mac.split(":")
    if len(parts) != 3 or parts[0] != MAC_VERSION:
        return MAC_INVALID
    key = _keys_by_id().get(parts[1])
    if key is None:
        return UNKNOWN_KEY
    expected = hmac.new(key, message, hashlib.sha256).hexdigest()
    return OK if hmac.compare_digest(expected, parts[2]) else MAC_INVALID


def signed_with_current_key(mac: Optional[str]) -> bool:
    return bool(mac) and mac.split(":")[1:2] == [key_id(load_keys()[0])]


# ----------------------------------------------------------------------------------- lap MAC

def lap_message(lap_id: int, driver_id: int, sha256: str, size: int, lap_time: float) -> bytes:
    return f"garage16-lap|{MAC_VERSION}|{int(lap_id)}|{int(driver_id)}|{sha256}|{int(size)}|{float(lap_time)!r}".encode()


def lap_mac(lap) -> str:
    return sign(lap_message(lap.id, lap.driver_id, lap.telemetry_hash, lap.telemetry_bytes, lap.lap_time))


# ------------------------------------------------------------------------------ verification

@dataclass
class Result:
    status: str
    detail: Dict = field(default_factory=dict)
    mac: str = UNSIGNED

    @property
    def ok(self) -> bool:
        return self.status == OK

    @property
    def quarantine(self) -> bool:
        return self.status in QUARANTINE_ON

    def as_dict(self) -> dict:
        return {"status": self.status, "mac": self.mac, **({"detail": self.detail} if self.detail else {})}


def sha256_file(path: Path) -> Tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK), b""):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def resolve_inside_storage(stored: str) -> Tuple[Optional[Path], Optional[str]]:
    """(path, None) when `stored` names a regular file directly inside the storage folder, else
    (None, reason). A symlink (even one pointing inside) is refused: the stored path must be the
    file itself, never an indirection somebody could repoint."""
    root = settings.telemetry_storage_dir.resolve()
    try:
        raw = Path(stored)
        resolved = raw.resolve()
    except (OSError, RuntimeError, ValueError, TypeError):
        return None, OUTSIDE
    if resolved != root and root not in resolved.parents:
        return None, OUTSIDE
    try:
        st = os.lstat(raw)
    except FileNotFoundError:
        return None, MISSING
    except OSError:
        return None, UNREADABLE
    if not stat_mod.S_ISREG(st.st_mode):
        return None, NOT_REGULAR
    return resolved, None


_cache: Dict[str, Tuple[tuple, float]] = {}
_cache_lock = threading.Lock()


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


def _fingerprint(path: Path) -> Optional[tuple]:
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_size, st.st_mtime_ns, st.st_ctime_ns, st.st_ino, st.st_dev)


def verify(lap, *, use_cache: bool = False, path: Optional[Path] = None) -> Result:
    """Checks the lap's file against what was stored at upload. `path`: an already resolved path
    (scripts that map a restored volume); otherwise the stored path, which must lie inside the
    storage folder."""
    if path is None:
        path, problem = resolve_inside_storage(lap.telemetry_path)
        if problem:
            return Result(problem)
    elif not path.is_file():
        return Result(MISSING)
    expected_hash = lap.telemetry_hash
    expected_size = lap.telemetry_bytes
    fingerprint = _fingerprint(path)
    if fingerprint is None:
        return Result(MISSING)
    if expected_size is not None and fingerprint[0] != int(expected_size):
        return Result(SIZE_MISMATCH, {"expected_bytes": int(expected_size), "found_bytes": fingerprint[0]})

    cache_key = f"{path}|{expected_hash}"
    now = time.monotonic()
    cached = False
    if use_cache:
        with _cache_lock:
            hit = _cache.get(cache_key)
        cached = bool(hit and hit[0] == fingerprint and now - hit[1] < CACHE_SECONDS)
    if not cached:
        try:
            actual, size = sha256_file(path)
        except OSError as exc:
            return Result(UNREADABLE, {"error": type(exc).__name__})
        if actual != expected_hash:
            return Result(HASH_MISMATCH, {"found_sha256": actual, "found_bytes": size})
        if use_cache:
            with _cache_lock:
                if len(_cache) >= CACHE_MAX_ENTRIES:
                    _cache.clear()
                _cache[cache_key] = (fingerprint, now)

    mac = UNSIGNED
    if lap.integrity_mac:
        if expected_size is None:
            return Result(MAC_INVALID, {"why": "MAC without a stored size"})
        mac = check_mac(lap_message(lap.id, lap.driver_id, expected_hash, expected_size, lap.lap_time),
                        lap.integrity_mac)
        if mac == MAC_INVALID:
            return Result(MAC_INVALID, mac=MAC_INVALID)
    return Result(OK, mac=mac)


# -------------------------------------------------------------------------------- quarantine

HOLD_STATES = ("held", "quarantined")


def quarantine(db, lap, result: Result, *, actor: str = "system", request_id: Optional[str] = None) -> bool:
    """Puts the lap into quarantine in the caller's transaction (the caller commits). Idempotent:
    a lap already in quarantine only gets its finding refreshed. Returns True if it changed."""
    from . import audit

    validation = dict(lap.validation or {})
    already = lap.review_status == "quarantined"
    entry = dict(validation.get("quarantine") or {})
    if not already:
        entry = {"previous_review_status": lap.review_status,
                 "at": datetime.now(timezone.utc).isoformat()}
    entry.update({"reason": result.status, "detail": result.detail or None, "request_id": request_id})
    validation["quarantine"] = {k: v for k, v in entry.items() if v is not None or k == "previous_review_status"}
    lap.validation = validation
    lap.review_status = "quarantined"
    db.add(lap)
    if not already:
        audit.append(db, "lap.quarantined", actor=actor, lap_id=lap.id, driver_id=lap.driver_id,
                     request_id=request_id, detail={"reason": result.status, **(result.detail or {})})
        logger.error("Lap %s quarantined: %s %s (request %s)", lap.id, result.status, result.detail or "", request_id)
    return not already


def release(db, lap, *, actor: str = "admin", note: Optional[str] = None, request_id: Optional[str] = None) -> Result:
    """Releases a quarantined lap — only when its file verifies again. Returns the check result;
    the lap is changed (and audited) only when it is OK."""
    from . import audit

    result = verify(lap)
    if lap.review_status != "quarantined" or not result.ok:
        return result
    validation = dict(lap.validation or {})
    entry = validation.pop("quarantine", {}) or {}
    previous = entry.get("previous_review_status")
    history = list(validation.get("quarantine_history") or [])
    history.append({**entry, "released_at": datetime.now(timezone.utc).isoformat(), "note": note})
    validation["quarantine_history"] = history[-10:]
    lap.validation = validation
    lap.review_status = previous
    db.add(lap)
    audit.append(db, "lap.released", actor=actor, lap_id=lap.id, driver_id=lap.driver_id, request_id=request_id,
                 detail={"restored_review_status": previous, "note": note})
    return result


def quarantine_by_id(lap_id: int, result: Result, request_id: Optional[str] = None) -> None:
    """For read paths: quarantines in a session of its own (the reading request has no write
    transaction to piggy-back on). Never raises — the reader still answers with the error."""
    from .database import SessionLocal
    from .models import Lap

    try:
        with SessionLocal() as db:
            lap = db.get(Lap, lap_id)
            if lap is not None and lap.review_status != "quarantined":
                quarantine(db, lap, result, request_id=request_id)
                db.commit()
    except Exception:  # noqa: BLE001
        logger.exception("Could not quarantine lap %s", lap_id)
