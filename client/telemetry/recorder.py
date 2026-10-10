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
from dataclasses import fields
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from .channels import EXTENDED_COLUMNS
from .parser import Lap, TelemetrySample

logger = logging.getLogger("lmu_garage.recorder")

# zstd level for lap files (V0.9.1: pinned explicitly — before, the level was
# whatever pyarrow's default happened to be, which is 1). Measured with
# scripts/benchmark_pipeline.py on a synthetic 15,000-sample lap with sensor-like
# noise (NOT a real LMU recording): level 3 was ~1 % smaller than level 1 at no
# meaningful extra write time, levels 5 and 7 gained another ~1 % for 40-60 %
# more write time. Reading is unaffected by the level. The saver runs on a
# background thread, so the write time is not on the recording hot path either
# way. Real laps may compress differently — re-run the benchmark on real files
# before changing this again. Row groups were measured too and make files
# bigger and reads slower for this access pattern, so the default (one group) stays.
PARQUET_ZSTD_LEVEL = 3


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

        df = self._build_dataframe(lap)
        # Parquet write itself isn't wrapped in the tmp+replace dance like
        # the JSON metadata below: pending_uploads() only ever discovers a
        # lap via its *.json sidecar, and that file is written last (after
        # this succeeds), so a crash here just leaves an orphaned .parquet
        # with no matching .json — invisible to the uploader, not a
        # half-written file it could try to read.
        # BYTE_STREAM_SPLIT stores the n-th byte of every float together, which
        # zstd then compresses far better: a 4-minute lap with all extended
        # channels is ~6 MB instead of ~14 MB, and writing is faster too.
        # Only plain float columns are listed (the tuple columns are nested
        # lists, which this encoding does not apply to); dictionary encoding is
        # off because it would take precedence over the split encoding.
        float_columns = [c for c in df.columns if str(df[c].dtype) in ("float32", "float64")]
        df.to_parquet(
            parquet_path, compression="zstd", compression_level=PARQUET_ZSTD_LEVEL, index=False,
            use_dictionary=False, use_byte_stream_split=float_columns,
        )

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

    @staticmethod
    def _build_dataframe(lap: Lap) -> pd.DataFrame:
        """Core columns exactly as before V0.9.0 (same names and types, so
        older servers/tools read the file unchanged), plus — when the samples
        carry them — every extended channel as float32 columns.

        Built column by column instead of via dataclasses.asdict() per sample:
        asdict() deep-copies recursively, which at ~160 values x ~15,000
        samples would take seconds. This takes a fraction of that."""
        samples = lap.samples
        core_names = [f.name for f in fields(TelemetrySample) if f.name != "ext"]
        df = pd.DataFrame({name: [getattr(s, name) for s in samples] for name in core_names})

        if any(s.ext is not None for s in samples):
            width = len(EXTENDED_COLUMNS)
            # one preallocated float32 block, filled row by row (rows are float32 arrays since
            # V0.9.1; tuples from older code paths fill the same way) — no second full copy
            block = np.full((len(samples), width), np.nan, dtype=np.float32)
            for i, s in enumerate(samples):
                if s.ext is not None and len(s.ext) == width:
                    block[i] = s.ext
            ext = pd.DataFrame(block, columns=list(EXTENDED_COLUMNS), copy=False)
            df = pd.concat([df, ext], axis=1)
        return df

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
        meta["uploaded_at"] = int(time.time())  # when the retention clock starts (V0.9.1)
        _atomic_write_text(meta_path, json.dumps(meta, indent=2))

    def cleanup_uploaded(self, retention_days: float, now: Optional[float] = None) -> list:
        """Deletes the local telemetry FILE of laps that were uploaded more than
        `retention_days` ago (V0.9.1) and returns the paths it removed.

        Why it is safe to do at all: once the server has accepted a lap it keeps the
        one canonical copy, and the local JSON sidecar — which is what the upload
        queue reads — stays, so the lap is never offered again. Why it is
        conservative:
          * `retention_days <= 0` turns it off completely (keep everything forever);
          * only laps the server confirmed (`uploaded`) are touched — never a pending
            one, never a rejected one (those stay for a human to inspect);
          * a lap uploaded before V0.9.1 has no `uploaded_at`: the sidecar's own
            modification time (rewritten at upload) stands in for it;
          * only a plain `*.parquet` file name next to its own sidecar is ever
            deleted — whatever a (possibly hand-edited) sidecar says, it cannot make
            this delete something elsewhere.
        The JSON is rewritten with `telemetry_removed: true`; running it again is a no-op.
        """
        if not retention_days or retention_days <= 0:
            return []
        current = time.time() if now is None else now
        cutoff = current - retention_days * 86400.0
        removed = []
        for meta_path in self.data_dir.rglob("*.json"):
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                if not isinstance(meta, dict) or meta.get("uploaded") is not True or meta.get("rejected"):
                    continue
                if meta.get("telemetry_removed"):
                    continue
                name = meta.get("telemetry_file")
                if not isinstance(name, str) or not name.endswith(".parquet") or Path(name).name != name:
                    continue
                uploaded_at = meta.get("uploaded_at")
                if not isinstance(uploaded_at, (int, float)) or isinstance(uploaded_at, bool):
                    uploaded_at = meta_path.stat().st_mtime
                if uploaded_at > cutoff:
                    continue
                target = meta_path.parent / name
                if target.is_symlink():
                    continue  # never follow a link out of the data folder
                if target.exists():
                    target.unlink()
                    removed.append(target)
                meta["telemetry_removed"] = True
                meta["telemetry_removed_at"] = int(current)
                _atomic_write_text(meta_path, json.dumps(meta, indent=2))
            except (OSError, ValueError):
                logger.warning("Local cleanup skipped %s", meta_path.name, exc_info=True)
        return removed

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
