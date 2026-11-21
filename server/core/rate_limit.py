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

import hashlib
import threading
import time
from typing import Dict, Tuple

from fastapi import HTTPException, Request, status

from server.core.client_ip import client_ip
from server.core.config import settings

# key -> (window_start_epoch, count, window_seconds)
_buckets: Dict[Tuple[str, str], Tuple[float, int, float]] = {}
_lock = threading.Lock()
# Expired windows are dropped now and then — before, every IP that ever called the server
# stayed in memory until the process restarted.
_PRUNE_EVERY = 1000
_calls_since_prune = 0


def _client_ip(request: Request) -> str:
    # X-Forwarded-For only from a trusted proxy, read from the right (server/core/client_ip.py) —
    # before, the first entry of a header the caller writes himself was taken as his address.
    return client_ip(request)


def _prune(now: float) -> None:
    """Drops every bucket whose window has run out (caller holds _lock)."""
    for key in [k for k, (start, _count, window) in _buckets.items() if now - start >= window]:
        del _buckets[key]


def _check(bucket: str, key: str, max_requests: int, window_seconds: float) -> bool:
    global _calls_since_prune
    now = time.monotonic()
    full_key = (bucket, key)
    with _lock:
        _calls_since_prune += 1
        if _calls_since_prune >= _PRUNE_EVERY:
            _calls_since_prune = 0
            _prune(now)
        window_start, count, _window = _buckets.get(full_key, (now, 0, window_seconds))
        if now - window_start >= window_seconds:
            # Window expired — start a fresh one.
            _buckets[full_key] = (now, 1, window_seconds)
            return True
        if count >= max_requests:
            return False
        _buckets[full_key] = (window_start, count + 1, window_seconds)
        return True


def _key_digest(key: str) -> str:
    return "key:" + hashlib.sha256(key.encode("utf-8", "replace")).hexdigest()[:32]


def key_blocked(bucket: str, key: str) -> bool:
    """True if `key` has used up `bucket` in the current window — without counting this call.
    With count_key() this limits FAILURES only (sign-in: a correct password never uses up the limit)."""
    max_requests, window_seconds = settings.rate_limits.get(bucket, (60, 60.0))
    now = time.monotonic()
    with _lock:
        start, count, _window = _buckets.get((bucket, _key_digest(key)), (now, 0, window_seconds))
        return now - start < window_seconds and count >= max_requests


def count_key(bucket: str, key: str) -> None:
    """Counts one use (a failure) of `bucket` for `key`."""
    max_requests, window_seconds = settings.rate_limits.get(bucket, (60, 60.0))
    _check(bucket, _key_digest(key), max_requests, window_seconds)


def limit_key(bucket: str, key: str) -> None:
    """Limits by anything else than IP or driver — the email address a sign-in, a password
    reset or a sign-up names. Brute force spread over many IP addresses against ONE account is then
    still capped. Raises 429 like the dependency. `key` is hashed so no address sits in memory."""
    max_requests, window_seconds = settings.rate_limits.get(bucket, (60, 60.0))
    if not _check(bucket, _key_digest(key), max_requests, window_seconds):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"Rate limit exceeded for '{bucket}'. Try again later.",
        )


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
    global _calls_since_prune
    with _lock:
        _buckets.clear()
        _calls_since_prune = 0
