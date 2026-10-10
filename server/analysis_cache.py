"""
analysis_cache.py — a small in-process LRU cache for computed analysis results (V0.9.1).

What it is: an optimisation. Computing a comparison reads a parquet file and
resamples a few channels (~15 ms for a typical request); the same request is
repeated whenever someone reopens a link, reloads the page or two people look at
the same leaderboard lap. The cache answers those from memory.

What it is NOT, on purpose:
  * not a data source. Every entry can be recomputed from the stored parquet file
    at any time; evicting or losing the cache never changes a result.
  * not an authorisation shortcut. The endpoint checks that the viewer may see
    each lap BEFORE it asks the cache anything (routers/analysis.py), and the
    cache is only ever asked with the keys of laps the viewer already passed
    that check for. Whoever knows a key does not thereby get an entry.
  * not shared between worker processes. With several workers each has its own
    cache; nothing depends on a hit.
  * not a second copy of the telemetry on disk. RAM only, bounded, evictable.

Bounds (all three apply): a maximum number of entries, a maximum total size in
bytes, and a time-to-live. Least-recently-used entries go first. A single result
larger than a quarter of the byte budget is not cached at all, so one huge
request can never flush everything else.

Versioning: ANALYSIS_CACHE_VERSION is part of every key. Bump it whenever the
computation changes (a new channel formula, a different resampling, a changed
response shape) and old entries become unreachable and age out — they are never
served for the new computation.
"""

from __future__ import annotations

import hashlib
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Callable, Hashable, Optional, Tuple

# Bump when what the cache stores would come out different for the same inputs.
ANALYSIS_CACHE_VERSION = 3  # 2 (V0.9.1 review): per-channel precision, spikes, corners; 3: corners cover the whole lap, delta_facts

DEFAULT_MAX_ENTRIES = 128
DEFAULT_MAX_BYTES = 48 * 1024 * 1024
DEFAULT_TTL_SECONDS = 15 * 60


@dataclass
class CacheStats:
    hits: int = 0
    misses: int = 0
    evictions: int = 0
    expirations: int = 0
    rejected_too_large: int = 0
    entries: int = 0
    bytes: int = 0


class AnalysisCache:
    def __init__(
        self,
        max_entries: int = DEFAULT_MAX_ENTRIES,
        max_bytes: int = DEFAULT_MAX_BYTES,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ):
        if max_entries < 1 or max_bytes < 1 or ttl_seconds <= 0:
            raise ValueError("cache limits must be positive")
        self.max_entries = max_entries
        self.max_bytes = max_bytes
        self.ttl_seconds = ttl_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._items: "OrderedDict[Hashable, Tuple[bytes, float]]" = OrderedDict()
        self._bytes = 0
        self._stats = CacheStats()

    # -- keys ---------------------------------------------------------------

    @staticmethod
    def make_key(*parts: Hashable) -> Tuple:
        """The version always leads the key. Order of `parts` is significant:
        (lap_a, lap_b) and (lap_b, lap_a) are different entries, because the
        delta changes sign when the laps swap."""
        return (ANALYSIS_CACHE_VERSION, *parts)

    @staticmethod
    def etag_for(key: Tuple, *extra: str) -> str:
        """A strong validator derived from the key (and e.g. the lap headers)."""
        digest = hashlib.sha256(repr(key).encode("utf-8"))
        for item in extra:
            digest.update(b"\x00")
            digest.update(item.encode("utf-8"))
        return f'"{digest.hexdigest()[:32]}"'

    # -- operations ---------------------------------------------------------

    def get(self, key: Tuple) -> Optional[bytes]:
        with self._lock:
            entry = self._items.get(key)
            if entry is None:
                self._stats.misses += 1
                return None
            value, stored_at = entry
            if self._clock() - stored_at > self.ttl_seconds:
                self._drop(key)
                self._stats.expirations += 1
                self._stats.misses += 1
                return None
            self._items.move_to_end(key)
            self._stats.hits += 1
            return value

    def put(self, key: Tuple, value: bytes) -> bool:
        """Stores `value`; returns False when it was too large to cache."""
        size = len(value)
        with self._lock:
            if size > self.max_bytes // 4:
                self._stats.rejected_too_large += 1
                return False
            if key in self._items:
                self._drop(key)
            self._items[key] = (value, self._clock())
            self._bytes += size
            self._evict()
            return True

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            self._bytes = 0

    def stats(self) -> CacheStats:
        with self._lock:
            snapshot = CacheStats(**self._stats.__dict__)
            snapshot.entries = len(self._items)
            snapshot.bytes = self._bytes
            return snapshot

    # -- internals (lock held) ------------------------------------------------

    def _drop(self, key: Tuple) -> None:
        value, _ = self._items.pop(key)
        self._bytes -= len(value)

    def _evict(self) -> None:
        while self._items and (len(self._items) > self.max_entries or self._bytes > self.max_bytes):
            oldest = next(iter(self._items))
            self._drop(oldest)
            self._stats.evictions += 1


# One cache per process, shared by the analysis endpoints.
analysis_cache = AnalysisCache()
