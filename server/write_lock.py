"""
write_lock.py — a short "nobody writes now" window for consistent backups (V0.9.8).

The problem: a backup is a database dump PLUS an archive of the telemetry files. Taken one after
the other while laps are uploaded (or a driver is deleted), the two can describe different
moments — a lap row whose file is not in the archive, the worst kind of backup because it only
shows when it is restored.

The mechanism: one lock file in the telemetry volume (`.garage16-write.lock`), shared by every
process that sees the volume (server workers, the backup container, a one-off maintenance run —
all on the same host kernel, so `flock` works across containers).

  * Every operation that changes database rows AND files together — storing an upload, deleting a
    driver with his laps, removing orphaned files — holds the lock SHARED for its duration
    (`writing()`). Many of them can run at once.
  * A backup announces itself with the marker `.garage16-maintenance`, then takes the lock
    EXCLUSIVE. From the marker on, new writes are refused at once (`MaintenanceActive` → HTTP 503
    with Retry-After; the desktop client keeps the lap and retries — 503 is "transient" for it);
    writes already running finish first, because the exclusive lock waits for them. Then the dump
    and the archive are made, the lock released, the marker removed.

Reading is never blocked: the analysis, leaderboards and pages keep working during a backup.

Without fcntl (Windows dev machines) the lock is a no-op — the server only runs on Linux.

V0.9.9.3 — who owns the lock file: the first process that opens it creates it. On the NAS that was
the backup's helper container (root), so the file was root:root 0644 and the server (user garage)
could no longer open it for writing — EVERY upload failed with 500 PermissionError until the file
was chown'ed by hand. Three layers now: the entrypoint creates the file as garage before the server
starts; both backup scripts give an existing file the owner of the telemetry directory; and the
server opens a file it cannot write READ-ONLY (flock works on a read-only descriptor, shared and
exclusive alike), so a wrongly owned lock file can never block uploads again.
"""

from __future__ import annotations

import contextlib
import logging
import os
import time
from pathlib import Path
from typing import Iterator, Optional

try:
    import fcntl
except ImportError:  # pragma: no cover — Windows
    fcntl = None  # type: ignore[assignment]

LOCK_NAME = ".garage16-write.lock"
MARKER_NAME = ".garage16-maintenance"
RETRY_AFTER_S = 120
# A marker older than this is a leftover of a backup that was killed before it could clean up
# (the lock itself is released by the kernel when its holder dies; the marker is a plain file).
# It is ignored then — uploads must never stay blocked because a backup container vanished.
MARKER_STALE_S = float(os.environ.get("LMU_GARAGE_MAINTENANCE_STALE_S", 2 * 3600))

logger = logging.getLogger("lmu_garage.write_lock")


def _open_lock(base: Path) -> int:
    """The lock file's descriptor. Created if missing; opened read-only if it exists but belongs
    to someone else (flock does not need write access) — V0.9.9.3."""
    path = base / LOCK_NAME
    try:
        return os.open(path, os.O_RDWR | os.O_CREAT, 0o664)
    except PermissionError:
        fd = os.open(path, os.O_RDONLY)          # still PermissionError if not even readable
        logger.warning("The write lock file %s is not writable by this process (wrong owner?) — "
                       "using it read-only. Fix: chown it to the server user.", path)
        return fd


class MaintenanceActive(Exception):
    """A backup (or restore) is running — try again in a moment."""


def _dir(root: Optional[Path] = None) -> Path:
    if root is not None:
        return Path(root)
    from .config import settings

    return settings.telemetry_storage_dir


def maintenance_active(root: Optional[Path] = None) -> bool:
    marker = _dir(root) / MARKER_NAME
    try:
        age = time.time() - marker.stat().st_mtime
    except FileNotFoundError:
        return False
    if age > MARKER_STALE_S:
        logger.warning("Ignoring a stale maintenance marker (%.0f s old) — a backup was interrupted", age)
        return False
    return True


@contextlib.contextmanager
def writing(root: Optional[Path] = None, *, wait_s: float = 0.0) -> Iterator[None]:
    """Hold the lock shared while changing rows and files together. Raises MaintenanceActive
    when a backup announced itself or holds the lock (after waiting up to wait_s)."""
    if fcntl is None:
        yield
        return
    base = _dir(root)
    base.mkdir(parents=True, exist_ok=True)
    if maintenance_active(base):
        raise MaintenanceActive()
    fd = _open_lock(base)
    try:
        deadline = time.monotonic() + wait_s
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise MaintenanceActive() from None
                time.sleep(0.05)
        # the marker may have appeared between the check and the lock: back off then too
        if maintenance_active(base):
            raise MaintenanceActive()
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


@contextlib.contextmanager
def exclusive(root: Optional[Path] = None, *, timeout_s: float = 300.0) -> Iterator[None]:
    """The backup side: announce, wait for running writes, hold the lock exclusive."""
    base = _dir(root)
    base.mkdir(parents=True, exist_ok=True)
    marker = base / MARKER_NAME
    marker.write_text(str(os.getpid()), encoding="utf-8")
    fd = _open_lock(base)
    try:
        if fcntl is not None:
            deadline = time.monotonic() + timeout_s
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError(f"running writes did not finish within {timeout_s:.0f} s") from None
                    time.sleep(0.1)
        yield
    finally:
        try:
            if fcntl is not None:
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
            marker.unlink(missing_ok=True)
