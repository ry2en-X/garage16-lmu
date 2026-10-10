#!/usr/bin/env python3
"""scripts/benchmark_pipeline.py — reproducible storage / read / hash benchmark (V0.9.1).

Uses ONE synthetic lap (~15,000 samples, ~196 columns, ~180 of them float32 —
tests/helpers/synthetic_lap.py), so the numbers are comparable between runs and
between code versions. They are NOT measurements of a real LMU session: real laps
have different noise, so real file sizes and CPU load will differ. What carries
over is the *relative* comparison (level A vs level B, before vs after).

Sections (each one prints a table and lands in the JSON output):
  storage   zstd level x row-group size: file size, write time, full-read time,
            3 / 10 / 24 column read time          (client/telemetry/recorder.py)
  hash      sha256 of the file: read_bytes() vs 1 MiB chunks — time and peak
            Python memory (tracemalloc)           (client/uploader/uploader.py)
  validate  server validation from bytes vs from a file path — time and peak
            Arrow memory                          (server/validation.py)
  analysis  server analysis of the lap (compare, 8 channels), JSON size and
            serialization time                    (server/telemetry_analysis.py)
  endpoint  GET /telemetry/analysis/compare through the real app (temporary database
            and storage): cold / cache hit / 304, wire size, and what the V0.9.0
            request (8 channels, 2 m grid, no downsampling) cost against the V0.9.1
            standard layout (3 channels, max_points)  (server/routers/analysis.py)

Usage:
  python scripts/benchmark_pipeline.py                  # all sections, table output
  python scripts/benchmark_pipeline.py --json out.json  # also write machine-readable results
  python scripts/benchmark_pipeline.py --only hash,validate --repeat 5
  python scripts/benchmark_pipeline.py --only endpoint
  python scripts/benchmark_pipeline.py --only endpoint --straight 5000 --radius 700   # a Le-Mans-sized lap (~14 km)
  python scripts/benchmark_pipeline.py --samples 15000 --seed 1

Timings are the MEDIAN of --repeat runs (default 3) after one warm-up.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import pyarrow as pa  # noqa: E402
import pyarrow.parquet as pq  # noqa: E402

from tests.helpers.synthetic_lap import make_lap  # noqa: E402

LEVELS = (None, 1, 3, 5, 7)          # None = uncompressed (comparison only, never shipped)
ROW_GROUPS = (None, 2048, 4096)      # None = pyarrow's default (one group for a lap this size)
HASH_CHUNK = 1024 * 1024


def median_time(fn, repeat: int) -> float:
    fn()  # warm-up
    runs = []
    for _ in range(repeat):
        start = time.perf_counter()
        fn()
        runs.append(time.perf_counter() - start)
    return statistics.median(runs)


def build_lap(samples: int, seed: int, noise: bool = True, straight: float = 1200.0, radius: float = 150.0) -> pd.DataFrame:
    """Roughly a 4-minute lap; the exact shape does not matter, the sample/column count does.

    The clean synthetic lap is far smoother than a real recording (many extended
    columns are constant or perfectly periodic), so it compresses to a fraction
    of a real file and would make every zstd-level / hashing comparison look
    better than it is. `noise` adds sensor-like noise (white noise plus a slow
    random walk, scaled to each column's own range) to the float32 extended
    columns only — time and distance (float64) stay untouched so the lap still
    validates. It is still synthetic: real files can be larger or smaller.
    """
    df = make_lap(n=samples, lap_time=240.0, straight=straight, radius=radius, seed=seed)
    if not noise:
        return df
    rng = np.random.default_rng(seed + 1)
    for column in df.columns:
        if str(df[column].dtype) != "float32":
            continue
        values = df[column].to_numpy(dtype=np.float64)
        span = float(np.nanmax(values) - np.nanmin(values)) if len(values) else 0.0
        scale = max(span * 0.004, 1e-3)
        walk = np.cumsum(rng.normal(0.0, scale * 0.05, len(values)))
        df[column] = (values + rng.normal(0.0, scale, len(values)) + walk).astype(np.float32)
    return df


def write_parquet(df: pd.DataFrame, path: Path, level, row_group) -> None:
    """Mirrors client/telemetry/recorder.py: float columns BYTE_STREAM_SPLIT, no dictionary."""
    float_columns = [c for c in df.columns if str(df[c].dtype) in ("float32", "float64")]
    kwargs = dict(index=False, use_dictionary=False, use_byte_stream_split=float_columns)
    if level is None:
        kwargs["compression"] = None
    else:
        kwargs["compression"] = "zstd"
        kwargs["compression_level"] = level
    if row_group:
        kwargs["row_group_size"] = row_group
    df.to_parquet(path, **kwargs)


def pick_columns(df: pd.DataFrame, count: int) -> list:
    preferred = [
        "speed", "throttle", "brake", "gear", "rpm", "steering", "lap_dist", "t", "accel_x", "accel_z",
        "susp_deflection_fl", "susp_deflection_fr", "susp_deflection_rl", "susp_deflection_rr",
        "tire_load_fl", "tire_load_fr", "tire_load_rl", "tire_load_rr",
        "pos_x", "pos_z", "ang_vel_y", "fuel", "clutch", "tc_level", "abs_level",
    ]
    cols = [c for c in preferred if c in df.columns]
    for c in df.columns:
        if len(cols) >= count:
            break
        if c not in cols and str(df[c].dtype) != "object":
            cols.append(c)
    return cols[:count]


def bench_storage(df: pd.DataFrame, tmp: Path, repeat: int) -> list:
    rows = []
    for row_group in ROW_GROUPS:
        for level in LEVELS:
            path = tmp / f"lap_{level}_{row_group}.parquet"
            write_s = median_time(lambda: write_parquet(df, path, level, row_group), repeat)
            size = path.stat().st_size
            full_s = median_time(lambda: pq.read_table(path), repeat)
            cols_s = {}
            for count in (3, 10, 24):
                cols = pick_columns(df, count)
                cols_s[count] = median_time(lambda cols=cols: pq.read_table(path, columns=cols), repeat)
            rows.append({
                "level": "none" if level is None else level,
                "row_group": row_group or "default",
                "size_bytes": size,
                "write_s": write_s,
                "read_all_s": full_s,
                "read_3_s": cols_s[3], "read_10_s": cols_s[10], "read_24_s": cols_s[24],
            })
    return rows


def _peak_python_bytes(fn) -> int:
    tracemalloc.start()
    tracemalloc.reset_peak()
    try:
        fn()
        return tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()


def bench_hash(path: Path, repeat: int) -> list:
    def whole() -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def chunked() -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(HASH_CHUNK), b""):
                digest.update(chunk)
        return digest.hexdigest()

    assert whole() == chunked(), "both strategies must produce the same digest"
    return [
        {"method": "read_bytes()", "time_s": median_time(whole, repeat), "peak_python_bytes": _peak_python_bytes(whole)},
        {"method": "1 MiB chunks", "time_s": median_time(chunked, repeat), "peak_python_bytes": _peak_python_bytes(chunked)},
    ]


class _Meta:
    """The three attributes validate_dataframe() reads from LapMetadata."""

    def __init__(self, df: pd.DataFrame, lap_time: float = 240.0):
        self.lap_time = lap_time
        self.sector_times = [lap_time / 3] * 3
        self.sample_count = len(df)


def bench_validate(df: pd.DataFrame, path: Path, repeat: int) -> list:
    from server import validation

    meta = _Meta(df)
    data = path.read_bytes()
    out = []
    out.append({
        "method": "from bytes (read_bytes + BytesIO)",
        "time_s": median_time(lambda: validation.validate_telemetry(path.read_bytes(), meta), repeat),
        "peak_python_bytes": _peak_python_bytes(lambda: validation.validate_telemetry(path.read_bytes(), meta)),
    })
    file_fn = getattr(validation, "validate_telemetry_file", None)
    if file_fn is not None:
        out.append({
            "method": "from file path",
            "time_s": median_time(lambda: file_fn(path, meta), repeat),
            "peak_python_bytes": _peak_python_bytes(lambda: file_fn(path, meta)),
        })
    else:
        out.append({"method": "from file path", "time_s": None, "peak_python_bytes": None,
                    "note": "validate_telemetry_file() not present in this version"})
    del data
    return out


def bench_analysis(path: Path, repeat: int) -> dict:
    from server.telemetry_analysis import load_lap, make_grid, resample, channel_values, json_series
    from server.telemetry_channels import CATALOG

    keys = [k for k in ("speed", "throttle", "brake", "gear", "steering", "rpm", "lat_g", "long_g") if k in CATALOG]

    def run():
        series = load_lap(path, keys)
        grid, _ = make_grid(float(series.dist[0]), float(series.dist[-1]), 2.0)
        out = {}
        for key in keys:
            values = channel_values(series, key)
            if values is not None:
                out[key] = json_series(resample(series, values, grid, CATALOG[key].discrete))
        return grid, out

    analysis_s = median_time(run, repeat)
    grid, payload = run()
    body = {"grid": {"count": int(len(grid))}, "channels": payload}
    serialize_s = median_time(lambda: json.dumps(body), repeat)
    return {
        "channels": len(payload),
        "grid_points": int(len(grid)),
        "analysis_s": analysis_s,
        "json_serialize_s": serialize_s,
        "json_bytes": len(json.dumps(body)),
        "peak_python_bytes": _peak_python_bytes(run),
    }




def bench_endpoint(df: pd.DataFrame, tmp: Path, repeat: int) -> dict:
    """Uploads two laps through the real client uploader into a temporary app, then times
    /compare. Everything runs in this process (TestClient), so numbers exclude the network,
    Caddy and its gzip — wire sizes are reported both raw and gzip-compressed (level 6, what
    Caddy's `encode gzip` uses by default) so the effect of compression is visible."""
    import gzip
    import io as _io

    os.environ["LMU_GARAGE_ENV"] = "development"
    os.environ["LMU_GARAGE_DB_URL"] = f"sqlite:///{tmp / 'bench.db'}"
    os.environ["LMU_GARAGE_STORAGE_DIR"] = str(tmp / "storage")
    os.environ["LMU_GARAGE_ADMIN_TOKEN"] = "bench-admin"
    os.environ.pop("LMU_GARAGE_REGISTRATION_SECRET", None)
    from fastapi.testclient import TestClient

    from client.telemetry.recorder import LapRecorder
    from client.uploader.uploader import Uploader
    from server.analysis_cache import analysis_cache
    from server.config import settings
    from server.main import app
    from server.rate_limit import reset_all

    settings.rate_limits["analysis"] = (10_000_000, 3600.0)
    ids = []
    with TestClient(app) as client:
        for index, (name, lap_time) in enumerate((("Bench A", 240.0), ("Bench B", 238.5))):
            account = client.post("/accounts/register", params={"display_name": name}).json()
            folder = tmp / f"lap{index}"
            folder.mkdir()
            frame = df.copy()
            frame["t"] = frame["t"] * (lap_time / float(frame["t"].iloc[-1]))
            write_parquet(frame, folder / "lap.parquet", 3, None)
            (folder / "lap.json").write_text(json.dumps({
                "track_name": "Bench Ring", "car_name": "Bench Car #1", "car_class": "Hypercar", "car_model": "Bench Car",
                "session_type": 10, "lap_number": 1, "lap_time": lap_time, "sector_times": [lap_time / 3] * 3,
                "is_valid": True, "started_at": 0.0, "recorded_at": 1700000000, "ambient_temp": 22.0, "track_temp": 30.0,
                "sample_count": len(frame), "telemetry_file": "lap.parquet", "uploaded": False,
            }), encoding="utf-8")
            uploader = Uploader(recorder=LapRecorder(data_dir=str(folder)), auth_token=account["auth_token"], client_secret=account["client_secret"])
            payload = uploader._build_payload(folder / "lap.json")
            resp = client.post(
                "/telemetry/upload",
                data={"envelope": json.dumps(payload["envelope"]), "signature": payload["signature"]},
                files={"telemetry": ("lap.parquet", _io.BytesIO(payload["telemetry_path"].read_bytes()))},
                headers={"Authorization": f"Bearer {account['auth_token']}"},
            )
            resp.raise_for_status()
            ids.append((resp.json()["lap_id"], account))
        headers = {"Authorization": f"Bearer {ids[0][1]['auth_token']}"}
        base = {"lap_a": ids[0][0], "lap_b": ids[1][0]}

        def request(params, extra=None):
            reset_all()
            return client.get("/telemetry/analysis/compare", params={**base, **params}, headers={**headers, **(extra or {})})

        from server.telemetry_analysis import clear_lap_cache

        def timed(params, cold: bool, keep_laps: bool = False) -> float:
            def run():
                if cold:
                    analysis_cache.clear()
                    if not keep_laps:
                        clear_lap_cache()   # a true first request: both lap files are read
                request(params)
            return median_time(run, repeat)

        v090 = {"channels": "speed,throttle,brake,gear,steering,rpm,lat_g,long_g", "step": 2}
        v091 = {"channels": "speed,throttle,brake", "step": 2, "max_points": 1500}
        zoom = {"channels": "speed,throttle,brake", "step": 0.5, "max_points": 3000, "start": 6000, "end": 6600}
        rows = {}
        for label, params in (("V0.9.0 request (8 channels, 2 m grid)", v090), ("V0.9.1 standard layout (3 channels, 1500 points)", v091),
                              ("V0.9.1 zoom refinement (600 m at 0.5 m)", zoom)):
            analysis_cache.clear()
            first = request(params)
            first.raise_for_status()
            etag = first.headers["etag"]
            body = first.content
            rows[label] = {
                "raw_bytes": len(body),
                "gzip_bytes": len(gzip.compress(body, 6)),
                "points": first.json()["grid"]["count"],
                "cold_s": timed(params, cold=True),
                "laps_in_memory_s": timed(params, cold=True, keep_laps=True),
                "cache_hit_s": median_time(lambda p=params: request(p), repeat),
                "not_modified_s": median_time(lambda p=params, e=etag: request(p, {"If-None-Match": e}), repeat),
            }
        return rows


def fmt_bytes(n) -> str:
    if n is None:
        return "-"
    for unit in ("B", "KiB", "MiB", "GiB"):
        if n < 1024 or unit == "GiB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.2f} {unit}"
        n /= 1024.0
    return str(n)


def fmt_ms(seconds) -> str:
    return "-" if seconds is None else f"{seconds * 1000:.1f} ms"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--samples", type=int, default=15000)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--only", default="storage,hash,validate,analysis,endpoint")
    parser.add_argument("--straight", type=float, default=1200.0, help="synthetic track: straight length in metres (lap = 2*straight + 2*pi*radius)")
    parser.add_argument("--radius", type=float, default=150.0, help="synthetic track: corner radius in metres")
    parser.add_argument("--no-noise", action="store_true",
                        help="use the perfectly clean synthetic lap (unrealistically small files)")
    parser.add_argument("--json", dest="json_path", default=None)
    args = parser.parse_args()
    sections = {s.strip() for s in args.only.split(",") if s.strip()}

    df = build_lap(args.samples, args.seed, noise=not args.no_noise, straight=args.straight, radius=args.radius)
    results: dict = {
        "meta": {
            "samples": len(df), "columns": int(df.shape[1]), "seed": args.seed, "repeat": args.repeat,
            "synthetic": True, "track_length_m": round(2 * args.straight + 2 * 3.141592653589793 * args.radius), "noise": not args.no_noise, "pyarrow": pa.__version__, "numpy": np.__version__, "pandas": pd.__version__,
            "cpu_count": os.cpu_count(),
        }
    }
    print(f"Synthetic lap: {len(df)} samples x {df.shape[1]} columns (NOT a real LMU recording)\n")

    with tempfile.TemporaryDirectory() as tmp_name:
        tmp = Path(tmp_name)
        reference = tmp / "reference.parquet"
        write_parquet(df, reference, 3, None)  # the level the client uses today

        if "storage" in sections:
            rows = bench_storage(df, tmp, args.repeat)
            results["storage"] = rows
            print("storage — zstd level x row group (write / read-all / read 3, 10, 24 columns)")
            print(f"{'level':>6} {'rowgrp':>8} {'size':>11} {'write':>10} {'read all':>10} {'3 col':>9} {'10 col':>9} {'24 col':>9}")
            for r in rows:
                print(f"{str(r['level']):>6} {str(r['row_group']):>8} {fmt_bytes(r['size_bytes']):>11} "
                      f"{fmt_ms(r['write_s']):>10} {fmt_ms(r['read_all_s']):>10} {fmt_ms(r['read_3_s']):>9} "
                      f"{fmt_ms(r['read_10_s']):>9} {fmt_ms(r['read_24_s']):>9}")
            print()

        if "hash" in sections:
            rows = bench_hash(reference, args.repeat)
            results["hash"] = {"file_bytes": reference.stat().st_size, "rows": rows}
            print(f"hash — sha256 of the {fmt_bytes(reference.stat().st_size)} file")
            for r in rows:
                print(f"  {r['method']:<16} {fmt_ms(r['time_s']):>10}   peak Python memory {fmt_bytes(r['peak_python_bytes'])}")
            print()

        if "validate" in sections:
            rows = bench_validate(df, reference, args.repeat)
            results["validate"] = rows
            print("validate — server plausibility check")
            for r in rows:
                note = f"   ({r['note']})" if r.get("note") else ""
                print(f"  {r['method']:<34} {fmt_ms(r['time_s']):>10}   peak Python memory {fmt_bytes(r['peak_python_bytes'])}{note}")
            print()

        if "analysis" in sections:
            row = bench_analysis(reference, args.repeat)
            results["analysis"] = row
            print("analysis — 8 channels, 2 m grid, one lap")
            print(f"  analysis {fmt_ms(row['analysis_s'])} · JSON serialize {fmt_ms(row['json_serialize_s'])} · "
                  f"JSON {fmt_bytes(row['json_bytes'])} · {row['grid_points']} points/series · "
                  f"peak Python memory {fmt_bytes(row['peak_python_bytes'])}")
            print()

        if "endpoint" in sections:
            rows = bench_endpoint(df, tmp, args.repeat)
            results["endpoint"] = rows
            print("endpoint — GET /telemetry/analysis/compare, two laps, in-process (no network, no Caddy)")
            print(f"  {'request':<50} {'points':>6} {'raw':>10} {'gzip':>10} {'cold':>9} {'laps in RAM':>12} {'cache hit':>10} {'304':>8}")
            for label, r in rows.items():
                print(f"  {label:<50} {r['points']:>6} {fmt_bytes(r['raw_bytes']):>10} {fmt_bytes(r['gzip_bytes']):>10} "
                      f"{fmt_ms(r['cold_s']):>9} {fmt_ms(r.get('laps_in_memory_s')):>12} {fmt_ms(r['cache_hit_s']):>10} {fmt_ms(r['not_modified_s']):>8}")
            print()

    if args.json_path:
        Path(args.json_path).write_text(json.dumps(results, indent=2), encoding="utf-8")
        print(f"JSON written to {args.json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
