"""
async_saver.py — writes completed laps to disk on a background thread.

Why this exists (V0.9.0): the telemetry loop reads LMU's shared memory at 60 Hz
and calls `recorder.save()` the instant a lap is completed. With the extended
channels a lap is ~15,000 samples x ~180 columns; building the DataFrame and
compressing the parquet file takes a noticeable fraction of a second. Done
inline, the loop would stall right at the start/finish line and the first
metres of the NEXT lap would never be recorded. So the parser hands the finished
lap to this thread and carries on reading frames immediately.

The Lap object is safe to hand over: LapParser starts a brand-new buffer list
for the next lap and never touches the old one again.

Laps are saved strictly in the order they were completed (one worker thread,
FIFO queue). On a clean shutdown `flush()` waits for queued laps, so closing
the app right after a lap does not lose it.
"""

from __future__ import annotations

import logging
import queue
import threading
from pathlib import Path
from typing import Callable, Optional

from .parser import Lap
from .recorder import LapRecorder

logger = logging.getLogger("lmu_garage.async_saver")

_STOP = object()


class AsyncLapSaver:
    def __init__(
        self,
        recorder: LapRecorder,
        on_saved: Optional[Callable[[Lap, Optional[Path]], None]] = None,
    ):
        self._recorder = recorder
        self._on_saved = on_saved
        self._queue: "queue.Queue[object]" = queue.Queue()
        self._thread = threading.Thread(target=self._run, name="lap-saver", daemon=True)
        self._thread.start()

    def submit(self, lap: Lap) -> None:
        self._queue.put(lap)

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            try:
                if item is _STOP:
                    return
                lap: Lap = item  # type: ignore[assignment]
                try:
                    meta_path = self._recorder.save(lap)
                except Exception:  # noqa: BLE001 — one bad lap must never kill the saver
                    logger.exception("Saving lap %s failed", getattr(lap, "lap_number", "?"))
                    meta_path = None
                if self._on_saved is not None:
                    try:
                        self._on_saved(lap, meta_path)
                    except Exception:  # noqa: BLE001
                        logger.exception("on_saved callback failed")
            finally:
                self._queue.task_done()

    def flush(self, timeout: float = 15.0) -> bool:
        """Waits until every queued lap is written. True if the queue drained
        within `timeout` seconds."""
        done = threading.Event()

        def _wait() -> None:
            self._queue.join()
            done.set()

        waiter = threading.Thread(target=_wait, daemon=True)
        waiter.start()
        return done.wait(timeout)

    def close(self, timeout: float = 15.0) -> None:
        self.flush(timeout)
        self._queue.put(_STOP)
