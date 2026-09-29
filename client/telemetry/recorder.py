"""
recorder.py — Persists completed Lap objects to local disk.

Design goal (per the architecture doc): keep full raw telemetry locally,
only compress for transport. This module just handles the "write it to
disk reliably" half; uploader/uploader.py handles compression + transport.

Layout on disk:

    <data_dir>/
        <track>/<car>/
            lap_<unix_ts>_<lap_number>_<uuid8>.parquet   # telemetry samples
            lap_<unix_ts>_<lap_number>_<uuid8>.json      # lap metadata

The uuid8 suffix exists because unix-second timestamp + lap_number alone
isn't actually collision-proof: two laps recorded in the same wall-clock
second (e.g. two very short laps, or two separate sessions on the same
track/car whose lap counters happen to line up) would otherwise silently
overwrite each other's files.

Parquet keeps file size and read/parse cost low for potentially
hundreds of thousands of laps over a driver's history, and pandas/pyarrow
are already common in this kind of pipeline (see xlsx/csv tooling docs).
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Optional

import pandas as pd

from .parser import Lap

logger = logging.getLogger("lmu_garage.recorder")


def _slugify(name: str) -> str:
    name = name.strip().lower()
    name = re.sub(r"[^a-z0-9_-]+", "_", name)
    return re.sub(r"_+", "_", name).strip("_") or "unknown"


def _atomic_write_text(path: Path, text: str) -> None:
    """Write `text` to `path` without ever leaving a truncated/partial file
    in its place if the process dies mid-write (crash, power loss, killed
    process): write to a temp file in the same directory first, then
    os.replace() it into place — an atomic rename on both POSIX and
    Windows, so `path` is either the old complete content or the new
    complete content, never something in between.
    """
    tmp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    tmp_path.write_text(text, encoding="utf-8")
    os.replace(tmp_path, path)


class LapRecorder:
    def __init__(self, data_dir: str = "./lmu_garage_data"):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def save(self, lap: Lap) -> Optional[Path]:
        """Writes the lap's telemetry + metadata to disk. Returns the metadata path,
        or None if the lap had no valid samples (e.g. an out-lap fragment) or a
        non-positive lap_time (a parser/game-state artifact, not a real lap —
        the server rejects these with 400 anyway; V0.6.0 stops recording them
        at all, so they don't sit in pending_uploads() forever)."""
        if not lap.samples:
            return None
        if lap.lap_time <= 0:
            logger.warning(
                "Discarding lap with non-positive lap_time (%.3f) for %s/%s — "
                "not a real completed lap (likely a session/lap-number artifact).",
                lap.lap_time, lap.track_name, lap.car_name,
            )
            return None

        track_slug = _slugify(lap.track_name)
        car_slug = _slugify(lap.car_name)
        out_dir = self.data_dir / track_slug / car_slug
        out_dir.mkdir(parents=True, exist_ok=True)

        stamp = int(time.time())
        # uuid suffix makes this collision-proof even when two laps finish
        # in the same wall-clock second with the same lap_number — see
        # module docstring.
        base_name = f"lap_{stamp}_{lap.lap_number}_{uuid.uuid4().hex[:8]}"
        parquet_path = out_dir / f"{base_name}.parquet"
        meta_path = out_dir / f"{base_name}.json"

        df = pd.DataFrame([asdict(s) for s in lap.samples])
        # Parquet write itself isn't wrapped in the tmp+replace dance like
        # the JSON metadata below: pending_uploads() only ever discovers a
        # lap via its *.json sidecar, and that file is written last (after
        # this succeeds), so a crash here just leaves an orphaned .parquet
        # with no matching .json — invisible to the uploader, not a
        # half-written file it could try to read.
        df.to_parquet(parquet_path, compression="zstd", index=False)

        metadata = {
            "track_name": lap.track_name,
            "car_name": lap.car_name,
            "session_type": lap.session_type,
            "lap_number": lap.lap_number,
            "lap_time": lap.lap_time,
            "sector_times": lap.sector_times,
            "is_valid": lap.is_valid,
            "started_at": lap.started_at,
            "recorded_at": stamp,
            "ambient_temp": lap.ambient_temp,
            "track_temp": lap.track_temp,
            "sample_count": len(lap.samples),
            "telemetry_file": parquet_path.name,
            "car_class": lap.car_class or None,
            "car_model": lap.car_model or None,
            "uploaded": False,
        }
        _atomic_write_text(meta_path, json.dumps(metadata, indent=2))

        return meta_path

    def pending_uploads(self):
        """Yields metadata paths for laps not yet marked uploaded or rejected."""
        for meta_path in self.data_dir.rglob("*.json"):
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            if meta.get("uploaded", False) or meta.get("rejected", False):
                continue
            yield meta_path

    def mark_uploaded(self, meta_path: Path) -> None:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["uploaded"] = True
        _atomic_write_text(meta_path, json.dumps(meta, indent=2))

    def mark_rejected(self, meta_path: Path, reason: str) -> None:
        """Marks a lap as permanently rejected by the server (a 4xx that
        will never succeed on retry: bad signature, invalid metadata,
        payload too large, client too old). V0.5.2/early V0.5.3 kept
        retrying these forever every 15s — this stops that, while still
        keeping the file on disk (not deleted) so a human can inspect why
        via the metadata JSON's `rejected_reason` field."""
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["rejected"] = True
        meta["rejected_reason"] = reason
        _atomic_write_text(meta_path, json.dumps(meta, indent=2))
