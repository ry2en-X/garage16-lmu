"""
rate_limit.py — A minimal in-process rate limiter.

What this is: a token-bucket limiter keyed by client IP (always) and,
where a driver dependency runs first in the chain, by driver_id too — so
one IP can't drown out registration for others, and one compromised
token can't hammer /upload past what one driver should ever legitimately
need.

What this is NOT: a distributed rate limiter. State lives in this
process's memory. That's fine for a single-worker deployment (the
default `uvicorn server.main:app` with no --workers flag), but with
multiple worker processes (gunicorn -w N, or multiple containers behind
a load balancer) each worker has its own counters — an attacker
distributed across workers effectively gets N times the limit. For real
production scale-out, replace this with a Redis-backed limiter (e.g.
`slowapi` + Redis, or nginx's own `limit_req`) — this module is
deliberately dependency-free so the project has *some* protection with
zero new infrastructure, not the final answer for a multi-worker fleet.
That tradeoff is a deliberate, documented choice for this stage of the
project, not an oversight.

Usage as a FastAPI dependency:

    @router.post("/register")
    def register(..., _rl: None = Depends(rate_limit("register"))):
        ...

Limits are configured via settings.rate_limits (see config.py) as
{bucket_name: (max_requests, window_seconds)}.
"""

from __future__ import annotations

import threading
import time
from typing import Dict, Optional, Tuple

from fastapi import Depends, HTTPException, Request, status

from .config import settings

# key -> (window_start_epoch, count)
_buckets: Dict[Tuple[str, str], Tuple[float, int]] = {}
_lock = threading.Lock()


def _client_ip(request: Request) -> str:
    # If running behind a reverse proxy (the expected production setup —
    # see require_https in main.py), the proxy should be configured to
    # pass the real client IP via X-Forwarded-For; trust it only because
    # main.py's require_https already assumes a trusted proxy sits in
    # front. If X-Forwarded-For is absent (direct connection, e.g. local
    # dev), fall back to the socket's own address.
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _check(bucket: str, key: str, max_requests: int, window_seconds: float) -> bool:
    now = time.monotonic()
    full_key = (bucket, key)
    with _lock:
        window_start, count = _buckets.get(full_key, (now, 0))
        if now - window_start >= window_seconds:
            # Window expired — start a fresh one.
            _buckets[full_key] = (now, 1)
            return True
        if count >= max_requests:
            return False
        _buckets[full_key] = (window_start, count + 1)
        return True


def rate_limit(bucket: str, per_driver: bool = False):
    """Returns a FastAPI dependency enforcing settings.rate_limits[bucket].

    Always limits by IP. If per_driver=True, ALSO limits by driver_id —
    for endpoints that already authenticate (upload, report), this stops
    one leaked/shared token from being hammered even from many IPs. The
    driver-scoped check only runs if get_current_driver has already
    populated request.state.driver_id (see auth.py), so declare this
    dependency alongside (not before) the driver dependency if per_driver
    matters — FastAPI resolves dependencies independently, so this reads
    from request.state rather than depending directly on get_current_driver
    to avoid running auth twice.
    """
    def _dependency(request: Request) -> None:
        max_requests, window_seconds = settings.rate_limits.get(bucket, (60, 60.0))
        ip = _client_ip(request)
        if not _check(bucket, f"ip:{ip}", max_requests, window_seconds):
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                f"Rate limit exceeded for '{bucket}'. Try again later.",
            )
        if per_driver:
            driver_id = getattr(request.state, "driver_id", None)
            if driver_id is not None:
                if not _check(bucket, f"driver:{driver_id}", max_requests, window_seconds):
                    raise HTTPException(
                        status.HTTP_429_TOO_MANY_REQUESTS,
                        f"Rate limit exceeded for '{bucket}'. Try again later.",
                    )

    return _dependency


def reset_all() -> None:
    """Test helper — clears all bucket state between test cases."""
    with _lock:
        _buckets.clear()
