"""
Regression test for a real, confirmed bug found during LMU hardware
verification (2026-09-25, reproduced twice in clean single-process runs):
the previous mmap.mmap(-1, size, tagname, access=mmap.ACCESS_READ)
approach did NOT raise SharedMemoryNotFound when LMU wasn't running — it
printed "Connected." and silently read a freshly-created, zero-filled
mapping instead, and 0 real frames were ever produced for the whole run.

Fix: client/telemetry/reader.py now uses the Win32 OpenFileMappingW API
directly (via ctypes, no pywin32 dependency), which can only OPEN an
existing mapping and returns NULL when one doesn't exist — no ambiguity.

These tests mock ctypes.WinDLL itself, so they run on any platform
(including this Linux dev sandbox) without needing a real Windows kernel32
or a running copy of LMU — they verify this module's own logic (raise on
NULL handle/address, clean up on partial failure, read real bytes through
a mapped view) rather than the real Win32 API's behavior, which can only
be confirmed on actual Windows (tracked in
docs/LMU_VERIFICATION_PROTOCOL.md item (d) — re-verify there after this
fix ships).
"""

import ctypes
from unittest.mock import MagicMock

import pytest

from client.telemetry.reader import LMUSharedMemoryReader, SharedMemoryNotFound
from client.lmu.structs import SharedMemoryObjectOut


def _fake_kernel32(open_returns, map_returns=None):
    fake = MagicMock()
    fake.OpenFileMappingW.return_value = open_returns
    if map_returns is not None:
        fake.MapViewOfFile.return_value = map_returns
    return fake


def test_open_raises_not_found_when_mapping_does_not_exist(monkeypatch):
    """The core regression: OpenFileMappingW returning NULL (0) — the real
    signal that LMU isn't running — must raise SharedMemoryNotFound, not
    silently succeed the way the old mmap.mmap() approach did."""
    fake = _fake_kernel32(open_returns=0)
    monkeypatch.setattr(ctypes, "WinDLL", lambda *a, **kw: fake, raising=False)

    reader = LMUSharedMemoryReader()
    with pytest.raises(SharedMemoryNotFound):
        reader.open()

    # Must not even attempt to map a view if opening the handle failed.
    fake.MapViewOfFile.assert_not_called()


def test_open_raises_not_found_when_map_view_fails(monkeypatch):
    """A handle that opens but fails to map (e.g. size mismatch) must also
    raise SharedMemoryNotFound, and must clean up the handle it did open —
    not leak it."""
    fake = _fake_kernel32(open_returns=1234, map_returns=0)
    monkeypatch.setattr(ctypes, "WinDLL", lambda *a, **kw: fake, raising=False)

    reader = LMUSharedMemoryReader()
    with pytest.raises(SharedMemoryNotFound):
        reader.open()

    fake.CloseHandle.assert_called_once_with(1234)


def test_open_succeeds_and_reads_real_bytes_through_mapped_view(monkeypatch):
    """When both OpenFileMappingW and MapViewOfFile succeed, reading must
    actually go through the mapped address (via ctypes.string_at) and
    return the real bytes sitting there — not fabricate them."""
    size = ctypes.sizeof(SharedMemoryObjectOut)
    buf = ctypes.create_string_buffer(size)
    buf.raw = bytes([7]) * size  # distinctive, checkable content
    addr = ctypes.addressof(buf)

    fake = _fake_kernel32(open_returns=999, map_returns=addr)
    monkeypatch.setattr(ctypes, "WinDLL", lambda *a, **kw: fake, raising=False)

    reader = LMUSharedMemoryReader()
    reader.open()  # must not raise

    raw = reader._view.read_bytes()
    assert raw == bytes([7]) * size

    reader.close()
    fake.UnmapViewOfFile.assert_called_once_with(addr)
    fake.CloseHandle.assert_called_once_with(999)


def test_unmap_and_close_have_explicit_argtypes(monkeypatch):
    """Regression test for a real bug found on Windows (2026-09-26):
    UnmapViewOfFile/CloseHandle had no explicit argtypes, so ctypes fell
    back to default int marshalling (effectively 32-bit) for the
    address/handle — OverflowError on any real 64-bit address, crashing
    every clean shutdown. This doesn't call the real Win32 API (no real
    kernel32 on this platform), but it does verify the constructor
    actually sets .argtypes / .restype on these two functions rather than
    leaving ctypes to guess — the exact thing that regressed."""
    fake = _fake_kernel32(open_returns=1, map_returns=1234)
    monkeypatch.setattr(ctypes, "WinDLL", lambda *a, **kw: fake, raising=False)

    reader = LMUSharedMemoryReader()
    reader.open()

    assert fake.UnmapViewOfFile.argtypes == [ctypes.c_void_p]
    assert fake.CloseHandle.argtypes == [ctypes.c_void_p]


def test_double_read_tearing_check_uses_mapped_view(monkeypatch):
    """_read_object()'s tear-check reads the view twice — confirm it goes
    through the new _MappedView (not stale mmap-era state) and succeeds
    when the buffer is stable between the two reads."""
    size = ctypes.sizeof(SharedMemoryObjectOut)
    buf = ctypes.create_string_buffer(size)
    addr = ctypes.addressof(buf)

    fake = _fake_kernel32(open_returns=1, map_returns=addr)
    monkeypatch.setattr(ctypes, "WinDLL", lambda *a, **kw: fake, raising=False)

    reader = LMUSharedMemoryReader()
    reader.open()
    obj = reader._read_object()  # must not raise _TornRead, must return a real struct
    assert obj is not None
