from __future__ import annotations

import re
from pathlib import Path

from .config import settings


def _slugify(name: str) -> str:
    name = name.strip().lower()
    name = re.sub(r"[^a-z0-9_-]+", "_", name)
    return re.sub(r"_+", "_", name).strip("_") or "unknown"


def telemetry_storage_dir(driver_id: int, track_name: str, car_name: str) -> Path:
    """The directory a lap's telemetry file lives in — split out from
    telemetry_storage_path() so callers that need to stream an upload to a
    temp file before its content hash is known (see routers/telemetry.py)
    can put that temp file in the *same* directory as the eventual final
    path, which is what makes the final os.replace() an atomic same-
    filesystem rename rather than a cross-filesystem copy.
    """
    out_dir = (
        settings.telemetry_storage_dir
        / f"driver_{driver_id}"
        / _slugify(track_name)
        / _slugify(car_name)
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir


def telemetry_storage_path(
    driver_id: int, track_name: str, car_name: str, telemetry_hash: str, original_filename: str
) -> Path:
    """
    Content-addressed by telemetry_hash, so a retried upload with identical
    bytes lands on the same path instead of accumulating duplicates on disk.
    """
    suffix = Path(original_filename).suffix or ".parquet"
    out_dir = telemetry_storage_dir(driver_id, track_name, car_name)
    return out_dir / f"{telemetry_hash}{suffix}"
