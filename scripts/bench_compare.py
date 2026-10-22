#!/usr/bin/env python3
"""
scripts/bench_compare.py — the same measurements on any Garage16 version (V0.9.9).

Runs inside one source tree (V0.9.8 or V0.9.9 — only interfaces both have are used) against a
fresh temporary database and storage folder, with laps generated here (identical in every tree:
a ~14 km lap, 60 Hz polls of 50 Hz frames, every 0.9.4 column, sensor-like noise):

  hash        sha256 of a lap file (MB/s)
  check       the server's lap check per lap (median ms) and its peak Python memory, for a
              normal lap and a long one (9 min)
  upload      N uploads through the real app one after the other: laps/s, p50 / p95 ms,
              SQL statements per upload, time inside the write lock
  parallel    the same with T threads at once (laps/s; on SQLite every write is serialised)
  read        GET /telemetry/analysis/laps/{id}: first access (V0.9.9: includes the integrity
              hash) and repeated access, median ms
  size        database rows and bytes the uploads added per lap (what backups grow by)

    python scripts/bench_compare.py --json out.json [--laps 30] [--threads 4] [--repeat 5]
    LMU_GARAGE_DB_URL=postgresql+psycopg://... python scripts/bench_compare.py ...   (PostgreSQL)

Numbers are machine-dependent: compare two versions on the SAME machine, same arguments.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import sys
import tempfile
import threading
import time
import tracemalloc
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_TMP = tempfile.mkdtemp(prefix="garage16-bench-")
os.environ.setdefault("LMU_GARAGE_ENV", "development")
os.environ.setdefault("LMU_GARAGE_DB_URL", f"sqlite:///{_TMP}/bench.db")
os.environ["LMU_GARAGE_STORAGE_DIR"] = os.path.join(_TMP, "storage")
os.environ.setdefault("LMU_GARAGE_RATELIMIT_UPLOAD_MAX", "100000")
os.environ.setdefault("LMU_GARAGE_RATELIMIT_ANALYSIS_MAX", "100000")

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402


def make_lap(seconds: float, seed: int, length_m: float = 13600.0) -> pd.DataFrame:
    from client.telemetry.channels import EXTENDED_COLUMNS

    rng = np.random.default_rng(seed)
    frames = np.arange(0, seconds * 50.0) / 50.0
    polls = np.arange(0, seconds, 1 / 60.0)
    idx = np.clip(np.searchsorted(frames, polls, side="right") - 1, 0, len(frames) - 1)
    t = frames[idx]
    phase = np.linspace(0, 14 * np.pi, len(frames))
    v_frames = 60.0 + 20.0 * np.sin(phase)                    # 40..80 m/s, smooth
    d_frames = np.concatenate(([0.0], np.cumsum(0.5 * (v_frames[1:] + v_frames[:-1]) / 50.0)))
    d_frames *= length_m / d_frames[-1]
    v_frames = np.gradient(d_frames, frames)
    scoring = d_frames[(np.arange(len(d_frames)) // 10) * 10]
    n = len(t)
    df = pd.DataFrame({
        "t": t, "lap_dist": scoring[idx], "speed": v_frames[idx] + rng.normal(0, 0.01, n),
        "throttle": np.clip(0.6 + 0.4 * np.sin(phase[idx]), 0, 1), "brake": np.clip(-0.5 * np.sin(phase[idx]), 0, 1),
        "clutch": np.zeros(n), "steering": 0.2 * np.cos(phase[idx]), "gear": np.full(n, 5.0),
        "rpm": 7000.0 + 30.0 * v_frames[idx] + rng.normal(0, 3, n), "fuel": np.linspace(70.0, 66.0, n),
        "pos_x": np.cos(d_frames[idx] / 2000.0) * 2000.0, "pos_y": np.zeros(n), "pos_z": np.sin(d_frames[idx] / 2000.0) * 2000.0,
        "tire_pressures": [(180.0, 181.0, 182.0, 183.0)] * n, "tire_temps_outer": [(350.0, 351.0, 352.0, 353.0)] * n,
        "brake_temps": [(600.0, 610.0, 620.0, 630.0)] * n,
    })
    ext = {}
    for name in EXTENDED_COLUMNS:
        ext[name] = (rng.normal(0, 1.0, n) * 0.01 + np.cumsum(rng.normal(0, 0.001, n))).astype(np.float32)
    for name in ("lap_invalidated", "track_limits_steps", "num_penalties", "path_lateral",
                 "surface_type_fl", "surface_type_fr", "surface_type_rl", "surface_type_rr"):
        ext[name] = np.zeros(n, dtype=np.float32)
    ext["count_lap_flag"] = np.full(n, 2.0, dtype=np.float32)
    ext["track_edge"] = np.full(n, 7.0, dtype=np.float32)
    return pd.concat([df, pd.DataFrame(ext)], axis=1)


def write(df: pd.DataFrame, path: Path) -> Path:
    floats = [c for c in df.columns if str(df[c].dtype) in ("float32", "float64")]
    df.to_parquet(path, index=False, compression="zstd", compression_level=3, use_dictionary=False,
                  use_byte_stream_split=floats)
    return path


def payload(tmp: Path, df: pd.DataFrame, account: dict, lap_number: int, seconds: float):
    from client.telemetry.recorder import LapRecorder
    from client.uploader.uploader import Uploader

    lap_dir = tmp / f"lap{lap_number}"
    lap_dir.mkdir(parents=True, exist_ok=True)
    write(df, lap_dir / "lap.parquet")
    meta = {"track_name": "Bench Ring", "car_name": "Bench Car #1", "car_class": "Hyper", "car_model": "BENCH_MODEL",
            "session_type": 10, "lap_number": lap_number, "lap_time": seconds, "sector_times": [seconds / 3] * 3,
            "is_valid": True, "started_at": 100.0 + seconds * lap_number, "recorded_at": 1_700_000_000 + lap_number,
            "ambient_temp": 20.0, "track_temp": 28.0, "sample_count": len(df), "telemetry_file": "lap.parquet",
            "uploaded": False}
    (lap_dir / "lap.json").write_text(json.dumps(meta), encoding="utf-8")
    uploader = Uploader(recorder=LapRecorder(data_dir=str(lap_dir)), auth_token=account["auth_token"],
                        client_secret=account["client_secret"])
    built = uploader._build_payload(lap_dir / "lap.json")
    return ({"envelope": json.dumps(built["envelope"]), "signature": built["signature"]},
            built["telemetry_path"].read_bytes())


def pct(values, q):
    values = sorted(values)
    return values[min(len(values) - 1, int(round(q * (len(values) - 1))))]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--laps", type=int, default=30)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--repeat", type=int, default=5)
    parser.add_argument("--json")
    args = parser.parse_args(argv)

    import hashlib

    from fastapi.testclient import TestClient
    from sqlalchemy import event, func

    from server.database import Base, SessionLocal, engine
    from server.main import VERSION, app
    from server.models import Lap

    Base.metadata.create_all(bind=engine)
    out = {"version": VERSION, "python": platform.python_version(), "machine": platform.machine(),
           "processor": platform.processor() or platform.machine(), "cpus": os.cpu_count(),
           "database": engine.dialect.name, "laps": args.laps, "threads": args.threads, "repeat": args.repeat}
    tmp = Path(_TMP) / "work"
    tmp.mkdir(parents=True, exist_ok=True)

    # --- hash and check on one file
    normal = make_lap(210.0, seed=1)
    long = make_lap(540.0, seed=2, length_m=34000.0)
    files = {"normal": write(normal, tmp / "normal.parquet"), "long": write(long, tmp / "long.parquet")}
    out["file_bytes"] = {k: p.stat().st_size for k, p in files.items()}
    out["rows"] = {"normal": len(normal), "long": len(long)}
    data = files["normal"].read_bytes()
    runs = []
    for _ in range(args.repeat):
        t0 = time.perf_counter()
        hashlib.sha256(data).hexdigest()
        runs.append(time.perf_counter() - t0)
    out["hash_mb_s"] = round(len(data) / 1e6 / statistics.median(runs), 1)

    from types import SimpleNamespace

    from server.lap_check import check_lap

    out["check_ms"], out["check_peak_mb"] = {}, {}
    for key, df in (("normal", normal), ("long", long)):
        seconds = float(df["t"].iloc[-1] - df["t"].iloc[0])
        meta = SimpleNamespace(track_name="Bench Ring", lap_time=seconds, sector_times=[seconds / 3] * 3,
                               sample_count=len(df), tail_samples=None, game_lap_state=None, lap_report=None,
                               track_grip_level=None, fixed_setup=None, track_limits_steps_per_point=None,
                               track_limits_steps_per_penalty=None)
        times, peak = [], 0
        with SessionLocal() as db:
            for i in range(args.repeat + 1):
                tracemalloc.start()
                t0 = time.perf_counter()
                check_lap(db, files[key], meta, client_valid=True)
                elapsed = time.perf_counter() - t0
                peak = max(peak, tracemalloc.get_traced_memory()[1])
                tracemalloc.stop()
                if i:
                    times.append(elapsed)
        out["check_ms"][key] = round(1000 * statistics.median(times), 1)
        out["check_peak_mb"][key] = round(peak / 1e6, 1)

    # --- uploads through the app
    statements = {"n": 0}

    @event.listens_for(engine, "before_cursor_execute")
    def _count(*_a, **_k):
        statements["n"] += 1

    lock_times = []
    try:
        from server import write_lock
        from server.routers import telemetry as telemetry_router

        original = telemetry_router.writing

        class _Timed:
            def __init__(self, *a, **k):
                self.inner = original(*a, **k)

            def __enter__(self):
                self.t0 = time.perf_counter()
                return self.inner.__enter__()

            def __exit__(self, *exc):
                result = self.inner.__exit__(*exc)
                lock_times.append(time.perf_counter() - self.t0)
                return result
        telemetry_router.writing = _Timed
        _ = write_lock
    except Exception:  # noqa: BLE001 — a version without the write lock
        pass

    with TestClient(app) as client:
        account = client.post("/accounts/register", params={"display_name": "Bench"}).json()
        headers = {"Authorization": f"Bearer {account['auth_token']}"}
        prepared = []
        for i in range(args.laps + args.laps):
            df = make_lap(210.0, seed=100 + i)
            prepared.append(payload(tmp, df, account, i + 1, float(df["t"].iloc[-1] - df["t"].iloc[0])))
        sequential, per_upload_sql = [], []
        for fields, body in prepared[:args.laps]:
            before = statements["n"]
            t0 = time.perf_counter()
            resp = client.post("/telemetry/upload", data=fields, files={"telemetry": ("lap.parquet", body)}, headers=headers)
            sequential.append(time.perf_counter() - t0)
            per_upload_sql.append(statements["n"] - before)
            assert resp.status_code == 200, resp.text
        out["upload"] = {"laps_per_s": round(len(sequential) / sum(sequential), 2),
                         "p50_ms": round(1000 * pct(sequential, 0.5), 1), "p95_ms": round(1000 * pct(sequential, 0.95), 1),
                         "sql_per_upload": statistics.median(per_upload_sql),
                         "write_lock_p50_ms": round(1000 * pct(lock_times, 0.5), 1) if lock_times else None,
                         "write_lock_p95_ms": round(1000 * pct(lock_times, 0.95), 1) if lock_times else None}

        # parallel
        queue = list(prepared[args.laps:])
        lock = threading.Lock()
        errors = []

        def worker():
            while True:
                with lock:
                    if not queue:
                        return
                    fields, body = queue.pop()
                r = client.post("/telemetry/upload", data=fields, files={"telemetry": ("lap.parquet", body)}, headers=headers)
                if r.status_code != 200:
                    errors.append(r.status_code)
        t0 = time.perf_counter()
        threads = [threading.Thread(target=worker) for _ in range(args.threads)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        elapsed = time.perf_counter() - t0
        out["parallel"] = {"laps_per_s": round(args.laps / elapsed, 2), "errors": len(errors)}

        # reads
        with SessionLocal() as db:
            ids = [i for (i,) in db.query(Lap.id).order_by(Lap.id).limit(args.repeat * 2).all()]
        first, again = [], []
        for lap_id in ids[:args.repeat]:
            t0 = time.perf_counter()
            assert client.get(f"/telemetry/analysis/laps/{lap_id}", headers=headers).status_code == 200
            first.append(time.perf_counter() - t0)
            t0 = time.perf_counter()
            client.get(f"/telemetry/analysis/laps/{lap_id}", headers=headers)
            again.append(time.perf_counter() - t0)
        out["read_ms"] = {"first": round(1000 * statistics.median(first), 1), "again": round(1000 * statistics.median(again), 1)}

    # --- what the database holds per lap
    with SessionLocal() as db:
        laps = db.query(func.count(Lap.id)).scalar()
        sizes = {}
        if engine.dialect.name == "postgresql":
            from sqlalchemy import text
            for table in ("laps", "record_events", "upload_events", "audit_events"):
                try:
                    sizes[table] = db.execute(text("select pg_total_relation_size(:t)"), {"t": table}).scalar()
                except Exception:  # noqa: BLE001 — a table this version does not have
                    db.rollback()
        else:
            sizes["database_file"] = Path(engine.url.database).stat().st_size
    out["db"] = {"laps": laps, "bytes": sizes, "bytes_per_lap": {k: round(v / max(laps, 1)) for k, v in sizes.items()}}
    print(json.dumps(out, indent=2))
    if args.json:
        Path(args.json).write_text(json.dumps(out, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
