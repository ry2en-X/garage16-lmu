"""
scripts/load_test.py — how the server copes with many requests at once (V0.9.7).

    python -m scripts.load_test https://<adresse-der-seite> --token <desktop token> \
        --lap-a 123 --lap-b 456 --users 8 --seconds 30

Simulates `--users` people at the same time, each repeatedly opening the leaderboard, a lap
comparison (if --lap-a/--lap-b are given) and the analysis catalog, for `--seconds`. Reports
requests per second, the median / 95th percentile / worst response time and every error per
endpoint. Read-only: it never uploads or changes anything, so it is safe against the real server
— but run it when nobody is driving, it is meant to load the server.

Standard library only (urllib + threads): runs on any machine with Python, no install.
Exit code 0 when no request failed and the 95th percentile stayed under --p95-limit-ms.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Dict, List, Tuple

Fetch = Callable[[str], int]   # path -> HTTP status


def http_fetcher(base: str, token: str | None, timeout: float = 30.0) -> Fetch:
    if not base.startswith(("http://", "https://")):
        raise SystemExit("base URL must start with http:// or https://")
    def fetch(path: str) -> int:
        req = urllib.request.Request(base.rstrip("/") + path, headers={"Authorization": f"Bearer {token}"} if token else {})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310 — the operator passes an http(s) base URL
                resp.read()
                return resp.status
        except urllib.error.HTTPError as exc:
            return exc.code
        except Exception:  # noqa: BLE001 — a timeout / reset is an error of the run
            return 0
    return fetch


def scenario(lap_a: int | None, lap_b: int | None) -> List[Tuple[str, str]]:
    steps = [("leaderboard", "/leaderboard/recent"), ("catalog", "/telemetry/analysis/channels")]
    if lap_a and lap_b:
        steps.append(("compare", f"/telemetry/analysis/compare?lap_a={lap_a}&lap_b={lap_b}"))
        steps.append(("health", f"/telemetry/analysis/laps/{lap_a}/health"))
    return steps


def run(fetch: Fetch, steps: List[Tuple[str, str]], users: int, seconds: float) -> Dict[str, dict]:
    times: Dict[str, List[float]] = {name: [] for name, _ in steps}
    errors: Dict[str, Dict[int, int]] = {name: {} for name, _ in steps}
    lock = threading.Lock()
    stop_at = time.monotonic() + seconds

    def user() -> None:
        while time.monotonic() < stop_at:
            for name, path in steps:
                started = time.monotonic()
                status = fetch(path)
                elapsed = (time.monotonic() - started) * 1000.0
                with lock:
                    times[name].append(elapsed)
                    if not (200 <= status < 400):
                        errors[name][status] = errors[name].get(status, 0) + 1

    with ThreadPoolExecutor(max_workers=users) as pool:
        for _ in range(users):
            pool.submit(user)
    out = {}
    for name, values in times.items():
        values.sort()
        p95 = values[min(len(values) - 1, int(0.95 * (len(values) - 1) + 0.5))] if values else None
        out[name] = {"requests": len(values), "per_second": round(len(values) / seconds, 1),
                     "median_ms": round(statistics.median(values), 1) if values else None,
                     "p95_ms": round(p95, 1) if p95 is not None else None,
                     "max_ms": round(values[-1], 1) if values else None, "errors": errors[name]}
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Read-only load test against a Garage16 server.")
    parser.add_argument("base_url")
    parser.add_argument("--token")
    parser.add_argument("--lap-a", type=int)
    parser.add_argument("--lap-b", type=int)
    parser.add_argument("--users", type=int, default=8)
    parser.add_argument("--seconds", type=float, default=30.0)
    parser.add_argument("--p95-limit-ms", type=float, default=2000.0)
    args = parser.parse_args(argv)
    result = run(http_fetcher(args.base_url, args.token), scenario(args.lap_a, args.lap_b), args.users, args.seconds)
    print(json.dumps(result, indent=2))
    failed = any(r["errors"] for r in result.values())
    slow = any((r["p95_ms"] or 0) > args.p95_limit_ms for r in result.values())
    return 1 if failed or slow else 0


if __name__ == "__main__":
    sys.exit(main())
