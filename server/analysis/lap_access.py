"""Who may open which lap, its telemetry file, and the small pieces every analysis endpoint shares (lap headers, channel lists, ETags)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import HTTPException, Request, status
from sqlalchemy.orm import Session, object_session

from server.analysis.telemetry_analysis import logical_columns, recording_level
from server.analysis.telemetry_channels import (
    CATALOG,
    DEFAULT_CHANNELS,
)
import logging
from server.core.models import Driver, Lap
from server.core.request_context import current_request_id
from server.laps import integrity
from server.laps.official import is_visible

logger = logging.getLogger("lmu_garage.analysis")


MAX_CHANNELS_PER_REQUEST = 24


# Channels that are not in a lap file but come from the PAIR of laps. They are listed
# in the catalog (marked "pair") and requested like any other channel.
PAIR_CHANNELS: Dict[str, dict] = {
    "line_offset": {
        "key": "line_offset", "label": "Line of Lap 2 vs Lap 1 (inside = +)", "unit": "m", "group": "Racing line",
        "discrete": False, "fixed_range": None, "pair": True,
    },
}


def _lap_for_viewer(db: Session, lap_id: int, viewer: Driver) -> Lap:
    lap = db.get(Lap, lap_id)
    if lap is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Lap {lap_id} not found.")
    if lap.driver_id == viewer.id:
        return lap
    if not is_visible(lap):
        # Same answer as a missing lap: no hint that an invalidated lap exists.
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Lap {lap_id} not found.")
    return lap


INTEGRITY_MESSAGE = ("INTEGRITY_FAILED: the telemetry file of this lap no longer matches what was uploaded. "
                     "The lap was set aside for an admin to check.")


def _telemetry_file(lap: Lap) -> Path:
    """The lap's parquet file, only if it really lives under the telemetry
    storage directory (the DB value is never trusted as a free-form path).

    And only if it is still exactly the file that was uploaded (server/laps/integrity.py:
    size, sha256 — cached per unchanged file — and the server's MAC). A file that is missing,
    changed or swapped puts the lap into quarantine (it leaves leaderboards and records) and the
    request fails without revealing paths: 404 for a missing file (as before), 422 with the code
    INTEGRITY_FAILED for a changed one (the status the analysis already uses for unusable files)."""
    result = integrity.verify(lap, use_cache=True)
    if result.ok:
        path, _ = integrity.resolve_inside_storage(lap.telemetry_path)
        return path
    request_id = current_request_id()
    lap_id = lap.id
    if result.status == integrity.OUTSIDE:
        logger.error("Lap %s has a telemetry_path outside the storage directory", lap_id)
    if result.quarantine:
        # end this read-only request's transaction first (SQLite: BEGIN IMMEDIATE holds the write
        # lock), then quarantine in a session of its own
        session = object_session(lap)
        if session is not None:
            session.rollback()
        integrity.quarantine_by_id(lap_id, result, request_id=request_id)
    if result.status in (integrity.MISSING, integrity.OUTSIDE):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "The telemetry of this lap is no longer stored."
                            if result.status == integrity.MISSING else "Telemetry file is not available.")
    raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, INTEGRITY_MESSAGE)


def _parse_channels(channels: Optional[str]) -> List[str]:
    if channels is None or not channels.strip():
        return list(DEFAULT_CHANNELS)
    keys = [part.strip() for part in channels.split(",") if part.strip()]
    keys = list(dict.fromkeys(keys))  # drop duplicates, keep order
    if len(keys) > MAX_CHANNELS_PER_REQUEST:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"At most {MAX_CHANNELS_PER_REQUEST} channels per request.")
    unknown = [key for key in keys if key not in CATALOG and key not in PAIR_CHANNELS]
    if unknown:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown channel(s): {', '.join(unknown[:5])}")
    return keys


def _recording(offered: set, lap: Lap) -> dict:
    """What the lap's FILE actually contains (authoritative — it is what the
    charts are drawn from): "full" for a V0.9.0+ recording, "basic" for a lap from
    an older client, which has the basic channels only."""
    available = sum(1 for channel in CATALOG.values() if all(column in offered for column in channel.needs))
    return {
        "level": recording_level(offered),
        "client_version": lap.client_version,
        "channels_available": available,
        "channels_total": len(CATALOG),
    }


def _lap_header(lap: Lap, driver_name: str) -> dict:
    return {
        "id": lap.id,
        "driver_id": lap.driver_id,
        "driver_name": driver_name,
        "track_name": lap.track_name,
        "car_name": lap.car_name,
        "car_class": lap.car_class,
        "car_model": lap.car_model,
        "lap_number": lap.lap_number,
        "lap_time": lap.lap_time,
        "sector_times": lap.sector_times,
        "is_valid": lap.is_valid,
        "ambient_temp": lap.ambient_temp,
        "track_temp": lap.track_temp,
        "uploaded_at": lap.uploaded_at.isoformat() if lap.uploaded_at else None,
        # The track-limits verdict with its findings (positions on the lap), the
        # review state, and the compact per-lap figures without the time profile
        "validation": lap.validation,
        "review_status": lap.review_status,
        "stats": _public_stats(lap.lap_stats),
    }


def _public_stats(stats) -> Optional[dict]:
    if not isinstance(stats, dict):
        return None
    return {key: value for key, value in stats.items() if key != "profile"}


def _available_keys(path: Path) -> List[str]:
    offered = logical_columns(path)
    return [key for key, channel in CATALOG.items() if all(column in offered for column in channel.needs)]


def _open_lap(db: Session, lap_id: int, viewer: Driver):
    """Authorisation and file checks for one lap — always BEFORE the cache is
    consulted, so a cache entry can never be reached for a lap the viewer may not
    see. Returns (lap row, parquet path, owner's display name)."""
    lap = _lap_for_viewer(db, lap_id, viewer)
    path = _telemetry_file(lap)
    owner = db.get(Driver, lap.driver_id)
    return lap, path, owner.display_name if owner else "?"


def _compact_json(value) -> bytes:
    return json.dumps(value, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _etag_matches(request: Request, etag: str) -> bool:
    """If-None-Match may list several validators and may mark them weak (W/...)."""
    header = request.headers.get("if-none-match")
    if not header:
        return False
    wanted = etag.strip('"')
    for token in header.split(","):
        token = token.strip()
        if token == "*" or token.removeprefix("W/").strip('"') == wanted:
            return True
    return False


# Authenticated responses: the browser may keep them but must revalidate (a 304 is
# cheap, and the permission check has already happened by then). Never "public".
PRIVATE_REVALIDATE = "private, no-cache"


VARY_AUTH = "Authorization, Cookie"
