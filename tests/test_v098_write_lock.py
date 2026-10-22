"""V0.9.8 — the write lock (server/write_lock.py) in-process: shared writers run together, a backup
waits for running writers and keeps new ones out, a stale marker never blocks uploads, a timeout
is a clear error. (The same lock across real processes and containers: test_v098_backup_restore.)"""

from __future__ import annotations

import os
import threading
import time

import pytest

from server import write_lock as wl


def test_writers_share_the_lock(tmp_path):
    with wl.writing(tmp_path):
        with wl.writing(tmp_path):            # a second writer at the same time
            pass
    assert (tmp_path / wl.LOCK_NAME).exists() and not (tmp_path / wl.MARKER_NAME).exists()


def test_a_marker_keeps_new_writers_out_at_once(tmp_path):
    (tmp_path / wl.MARKER_NAME).write_text("backup")
    assert wl.maintenance_active(tmp_path)
    with pytest.raises(wl.MaintenanceActive):
        with wl.writing(tmp_path):
            pass


def test_a_stale_marker_is_ignored(tmp_path, monkeypatch):
    marker = tmp_path / wl.MARKER_NAME
    marker.write_text("killed backup")
    old = time.time() - wl.MARKER_STALE_S - 10
    os.utime(marker, (old, old))
    assert not wl.maintenance_active(tmp_path)
    with wl.writing(tmp_path):
        pass


def test_the_backup_waits_for_running_writers_then_holds_new_ones_off(tmp_path):
    events = []
    writer_in = threading.Event()
    release_writer = threading.Event()

    def writer():
        with wl.writing(tmp_path):
            events.append("writer in")
            writer_in.set()
            release_writer.wait(5)
            events.append("writer out")

    t = threading.Thread(target=writer)
    t.start()
    writer_in.wait(5)

    def backup():
        with wl.exclusive(tmp_path, timeout_s=5):
            events.append("backup holds")
            # while the backup holds the lock nobody writes
            with pytest.raises(wl.MaintenanceActive):
                with wl.writing(tmp_path):
                    pass
            events.append("backup done")

    b = threading.Thread(target=backup)
    b.start()
    time.sleep(0.3)
    assert events == ["writer in"], "the backup waits for the running writer"
    assert wl.maintenance_active(tmp_path), "but new writers are refused from the announcement on"
    release_writer.set()
    t.join(5)
    b.join(5)
    assert events == ["writer in", "writer out", "backup holds", "backup done"]
    assert not wl.maintenance_active(tmp_path), "the marker goes with the backup"
    with wl.writing(tmp_path):                 # and writing works again
        pass


def test_a_writer_that_never_finishes_makes_the_backup_give_up_clearly(tmp_path):
    hold = threading.Event()
    inside = threading.Event()

    def writer():
        with wl.writing(tmp_path):
            inside.set()
            hold.wait(5)

    t = threading.Thread(target=writer)
    t.start()
    inside.wait(5)
    with pytest.raises(TimeoutError):
        with wl.exclusive(tmp_path, timeout_s=0.3):
            pass
    assert not wl.maintenance_active(tmp_path), "a failed backup leaves no marker behind"
    hold.set()
    t.join(5)


def test_a_writer_may_wait_a_moment_for_a_backup_to_finish(tmp_path):
    ready = threading.Event()

    def short_backup():
        # holds the lock exclusively without a marker (between the announcement's removal and release)
        import fcntl

        fd = os.open(tmp_path / wl.LOCK_NAME, os.O_RDWR | os.O_CREAT)
        fcntl.flock(fd, fcntl.LOCK_EX)
        ready.set()
        time.sleep(0.3)
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)

    t = threading.Thread(target=short_backup)
    t.start()
    ready.wait(5)
    with pytest.raises(wl.MaintenanceActive):
        with wl.writing(tmp_path, wait_s=0):
            pass
    with wl.writing(tmp_path, wait_s=3):        # waits until it is free
        pass
    t.join(5)


def test_without_a_root_the_configured_storage_is_used(monkeypatch, tmp_path):
    from server.config import settings

    monkeypatch.setattr(settings, "telemetry_storage_dir", tmp_path / "store")
    with wl.writing():
        pass
    assert (tmp_path / "store" / wl.LOCK_NAME).exists()
