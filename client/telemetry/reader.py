"""
reader.py — Opens LMU's native shared memory ("LMU_Data") and yields one
Frame per tick for the player's own vehicle.

Corrected from an earlier version that targeted the classic third-party
rFactor2 plugin's two named files ($rFactor2SMMP_Telemetry$ /
$rFactor2SMMP_Scoring$) and a version-counter tearing check that doesn't
exist in this format. LMU exposes a single named mapping, "LMU_Data" (see
client/lmu/structs.py for the full provenance/confidence notes on the
struct layout), and signals updates via a companion named Windows Event,
"LMU_Data_Event" — not a per-buffer mVersionUpdateBegin/mVersionUpdateEnd
pair. There is nothing to spin-and-compare on these buffers.

V0.6.2 fix — CONFIRMED via real LMU verification (2026-09-25), reproduced
twice in clean, single-process runs: the previous approach
(`mmap.mmap(-1, size, tagname, access=mmap.ACCESS_READ)`) does NOT reliably
fail when LMU isn't running. It printed "Connected." and read 0 frames for
the entire run with LMU fully closed — the mapping was silently created
fresh (zero-filled) rather than raising, contradicting the assumption the
prior version of this module's docstring documented as unconfirmed. Fixed
by switching to the Win32 `OpenFileMappingW` API directly via ctypes (see
_MappedView below) — it can only OPEN an existing mapping, never create
one, so it has no such ambiguity. No new dependency (no pywin32 needed).

One thing this module still cannot verify without a real, running copy of
the game on Windows: whether waiting on the "LMU_Data_Event" object
(instead of this module's read-twice-and-compare fallback) is needed in
practice to avoid torn reads. Tracked as a follow-up in README.md rather
than guessed at here.
"""

from __future__ import annotations

import ctypes
import time
from dataclasses import dataclass
from typing import Iterator, Optional

from client.lmu.structs import (
    SHARED_MEMORY_NAME,
    ScoringInfoV01,
    SharedMemoryObjectOut,
    TelemInfoV01,
    VehicleScoringInfoV01,
    validate_scoring_info,
)

# FILE_MAP_READ — grants read access to an existing mapping, matching the
# read-only relationship this client has to LMU's shared memory. This
# client never requests FILE_MAP_WRITE, so it can never corrupt what LMU
# writes even if something in this module were buggy.
_FILE_MAP_READ = 0x0004


def _get_last_error() -> object:
    """ctypes.get_last_error() only exists on Windows (it reads the
    thread-local Win32 error set by use_last_error=True calls) — this
    thin wrapper keeps the module importable and its error-path logic
    testable on any platform. On real Windows this calls the genuine
    function exactly as before; elsewhere it just can't retrieve a Win32
    error code (there isn't one), so it returns None for the diagnostic
    message rather than crashing this already-failing path."""
    fn = getattr(ctypes, "get_last_error", None)
    return fn() if fn is not None else None


class SharedMemoryNotFound(Exception):
    """Raised when LMU's shared memory segment can't be opened — the game
    isn't running, hasn't reached a session yet, or shared memory output
    isn't enabled in its settings."""


@dataclass
class Frame:
    telemetry: TelemInfoV01
    scoring: VehicleScoringInfoV01
    session: ScoringInfoV01


class _MappedView:
    """A Win32 OpenFileMappingW + MapViewOfFile pair, wrapped for this
    module's use. Replaces the earlier mmap.mmap()-based approach — see
    this module's docstring for why.

    OpenFileMappingW ONLY opens a mapping that already exists; it returns
    NULL (no handle) rather than creating one when it doesn't. That is
    exactly the "is LMU actually running" signal this module needs, with
    none of mmap.mmap()'s ambiguity between "opened an existing mapping"
    and "silently created a fresh one".

    Uses ctypes directly against kernel32 — no pywin32 dependency needed.
    """

    def __init__(self, name: str, size: int):
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._kernel32.OpenFileMappingW.restype = ctypes.c_void_p
        self._kernel32.OpenFileMappingW.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_wchar_p]
        self._kernel32.MapViewOfFile.restype = ctypes.c_void_p
        self._kernel32.MapViewOfFile.argtypes = [
            ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_size_t,
        ]
        # BUGFIX (found via real Windows execution, 2026-09-26): without
        # explicit argtypes, ctypes falls back to its default marshalling
        # for a bare Python int argument — effectively a 32-bit int. A
        # real 64-bit process address/handle overflows that on every
        # call, so UnmapViewOfFile/CloseHandle crashed on every clean
        # shutdown (Ctrl+C, close()) with "int too long to convert" —
        # never surfaced before because nothing had exercised a real
        # close() against a real (large) address until this session's
        # actual Windows run.
        self._kernel32.UnmapViewOfFile.restype = ctypes.c_int
        self._kernel32.UnmapViewOfFile.argtypes = [ctypes.c_void_p]
        self._kernel32.CloseHandle.restype = ctypes.c_int
        self._kernel32.CloseHandle.argtypes = [ctypes.c_void_p]

        self._size = size
        self._handle = self._kernel32.OpenFileMappingW(_FILE_MAP_READ, False, name)
        if not self._handle:
            raise SharedMemoryNotFound(
                f"Could not open shared memory '{name}' (OpenFileMappingW returned NULL, "
                f"GetLastError={_get_last_error()}) — is LMU running with shared "
                "memory output enabled, and in an active session?"
            )

        self._address = self._kernel32.MapViewOfFile(self._handle, _FILE_MAP_READ, 0, 0, size)
        if not self._address:
            error = _get_last_error()
            self._kernel32.CloseHandle(self._handle)
            self._handle = None
            raise SharedMemoryNotFound(
                f"Could not map view of shared memory '{name}' (MapViewOfFile failed, "
                f"GetLastError={error})."
            )

    def read_bytes(self) -> bytes:
        return ctypes.string_at(self._address, self._size)

    def close(self) -> None:
        if getattr(self, "_address", None):
            self._kernel32.UnmapViewOfFile(self._address)
            self._address = None
        if getattr(self, "_handle", None):
            self._kernel32.CloseHandle(self._handle)
            self._handle = None


class LMUSharedMemoryReader:
    def __init__(self, shared_memory_name: str = SHARED_MEMORY_NAME):
        self._name = shared_memory_name
        self._view: Optional[_MappedView] = None
        self._size = ctypes.sizeof(SharedMemoryObjectOut)

    def open(self) -> None:
        if self._view is not None:
            return  # already open — stream() no longer redundantly reopens, see below
        self._view = self._open_map()

    def _open_map(self) -> _MappedView:
        """Opens the existing "LMU_Data" mapping. Raises SharedMemoryNotFound
        (from _MappedView) if it doesn't exist — see this module's and
        _MappedView's docstrings for why this no longer uses mmap.mmap()."""
        return _MappedView(self._name, self._size)

    def close(self) -> None:
        if self._view is not None:
            self._view.close()
            self._view = None

    def _read_object(self) -> SharedMemoryObjectOut:
        """Read the whole top-level struct, guarding against a torn read
        by reading twice and comparing. This format doesn't expose a
        version-counter pair to check cheaply (see module docstring), so
        this is a full second read+compare rather than a 4-byte counter
        check — more expensive, but the only tearing guard available
        without adding a Win32 Event-wait dependency.
        """
        assert self._view is not None
        first = self._view.read_bytes()
        second = self._view.read_bytes()
        if first != second:
            # Torn read — the game wrote to the buffer between our two
            # reads. Caller retries on the next tick; this one is skipped.
            raise _TornRead()
        return SharedMemoryObjectOut.from_buffer_copy(first)

    def read(self) -> Optional[Frame]:
        """Read one snapshot and return the player's Frame, or None if no
        player vehicle is currently active (e.g. at a menu between
        sessions)."""
        if self._view is None:
            raise RuntimeError("open() must be called before read().")

        try:
            obj = self._read_object()
        except _TornRead:
            return None

        scoring_info = obj.scoring.scoring_info
        if not validate_scoring_info(scoring_info):
            # The one region of this struct without an independently
            # confirmed byte layout (see structs.py) — if it looks wrong,
            # don't hand out a Frame built on top of a probably-misaligned
            # read. Logging is the caller's job; this just declines to
            # yield a frame for this tick.
            return None

        telemetry_data = obj.telemetry
        if not telemetry_data.player_has_vehicle:
            return None
        player_idx = telemetry_data.player_vehicle_idx
        if not (0 <= player_idx < telemetry_data.telem_info._length_):
            return None

        player_telemetry = telemetry_data.telem_info[player_idx]

        player_scoring = None
        num_vehicles = min(scoring_info.num_vehicles, obj.scoring.veh_scoring_info._length_)
        for i in range(max(num_vehicles, 0)):
            candidate = obj.scoring.veh_scoring_info[i]
            if candidate.id == player_telemetry.id:
                player_scoring = candidate
                break
        if player_scoring is None:
            return None

        return Frame(telemetry=player_telemetry, scoring=player_scoring, session=scoring_info)

    def stream(self, poll_hz: float = 20.0) -> Iterator[Frame]:
        """Poll at `poll_hz` and yield a Frame each time one is available.
        Does not call open() itself — the caller controls the open/retry
        lifecycle (see client/main.py's telemetry_loop) so a
        SharedMemoryNotFound from open() and one from a mid-stream game
        restart are handled by the same retry path instead of two
        different ones.
        """
        if self._view is None:
            raise RuntimeError("open() must be called before stream().")
        period = 1.0 / poll_hz
        while True:
            frame = self.read()
            if frame is not None:
                yield frame
            time.sleep(period)


class _TornRead(Exception):
    """Internal signal for "buffer changed between our two reads" — never
    escapes this module."""
