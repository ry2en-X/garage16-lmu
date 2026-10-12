"""client/analysis_launcher.py — what the client window's "LapTime Analyzer", "Stint Analyzer"
and "Analyse last lap" buttons do (V0.9.2; two dashboards since V0.9.4). Kept free of Tk so it
is testable.

  * standard edition, or the driver prefers the browser -> default browser, the web analysis
  * "Client + analysis app" -> the analysis window as its own process
    (`Garage16.exe --analysis-window`, client/analysis_window.py); at most one at a time.
    Asked for a specific lap while the window is open, the window is reopened on that lap.
  * the window cannot be shown here (e.g. WebView2 runtime missing) -> browser, and the
    driver is told why once.
"""

from __future__ import annotations

import logging
import os
import subprocess
import webbrowser
from typing import Callable, Optional

from client.edition import (
    VIEW_APP,
    VIEW_LAPTIME,
    analysis_url,
    analysis_window_command,
    analysis_window_problem,
    preferred_view,
)

logger = logging.getLogger("lmu_garage.analysis_launcher")


class AnalysisLauncher:
    def __init__(self, api_url: Callable[[], str], log: Callable[[str], None],
                 popen: Callable = subprocess.Popen, open_browser: Callable[[str], object] = webbrowser.open):
        self._api_url = api_url
        self._log = log
        self._popen = popen
        self._open_browser = open_browser
        self._process = None
        self._process_view = None
        self._told_problem = False

    def window_is_open(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def open(self, lap_id: Optional[int] = None, view: str = VIEW_LAPTIME) -> str:
        """Returns what it did: "window", "already-open" or "browser".
        view: "laptime" (LapTime Analyzer) or "stints" (Stint Analyzer)."""
        if preferred_view() == VIEW_APP:
            problem = analysis_window_problem()
            if problem is None:
                return self._open_window(lap_id, view)
            if not self._told_problem:
                self._told_problem = True
                self._log(f"{problem} Opening the analysis in your browser.")
        self._open_browser(analysis_url(self._api_url(), lap_id, view))
        return "browser"

    def _open_window(self, lap_id: Optional[int], view: str = VIEW_LAPTIME) -> str:
        if self.window_is_open():
            if lap_id is None and view == self._process_view:
                self._log("The analysis window is already open.")
                return "already-open"
            self._process.terminate()  # reopen it on the requested lap / dashboard
        kwargs = {"close_fds": True}
        if os.name == "nt":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            self._process = self._popen(analysis_window_command(lap_id, view), **kwargs)
            self._process_view = view
        except OSError as exc:
            logger.warning("Could not start the analysis window: %s", exc)
            self._log("The analysis window could not be started — opening the browser instead.")
            self._open_browser(analysis_url(self._api_url(), lap_id, view))
            return "browser"
        return "window"

    def close(self) -> None:
        """When the client quits, its analysis window goes with it."""
        if self.window_is_open():
            self._process.terminate()
