"""client/edition.py — which Garage16 client this is, and where its analysis opens (V0.9.2).

The desktop client ships in two editions, built from the same code:

  * "standard"  — Garage16-Client-Setup.exe: records and uploads laps. "Open analysis"
                  opens the web analysis in the default browser.
  * "analysis"  — Garage16-Client-Analysis-Setup.exe: the same, plus the analysis in its
                  own app window (client/analysis_window.py, Microsoft Edge WebView2).

The edition is a one-line file baked into the package next to the executable
(garage16_edition.txt, written by packaging/garage16.spec — the same mechanism as
garage16_server.txt). Running from source it can be set with LMU_GARAGE_EDITION.
Nothing about it is a security decision: both editions talk to the same server with
the same credentials; the edition only decides what the "Open analysis" button does.

In the analysis edition the driver can still prefer the browser (a checkbox in the
window, saved as "analysis_view" in client_config.json). If the window cannot be shown
(WebView2 runtime missing, pywebview not bundled) the analysis opens in the browser.
"""

from __future__ import annotations

import importlib.util
import logging
import os
import sys
from typing import List, Optional
from urllib.parse import urlparse

from client.config import bundled_file_candidates, read_setting, write_setting

logger = logging.getLogger("lmu_garage.edition")

EDITION_FILENAME = "garage16_edition.txt"
STANDARD = "standard"
ANALYSIS = "analysis"
EDITIONS = (STANDARD, ANALYSIS)

VIEW_APP = "app"
VIEW_BROWSER = "browser"
SETTING_KEY = "analysis_view"

# Microsoft's documented way to tell whether the Evergreen WebView2 Runtime is installed:
# the "pv" (version) value of this client key, per machine or per user.
# https://learn.microsoft.com/microsoft-edge/webview2/concepts/distribution#detect-if-a-webview2-runtime-is-already-installed
WEBVIEW2_CLIENT_GUID = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"


def edition() -> str:
    env = (os.environ.get("LMU_GARAGE_EDITION") or "").strip().lower()
    if env in EDITIONS:
        return env
    for candidate in bundled_file_candidates(EDITION_FILENAME):
        try:
            if candidate.is_file():
                value = candidate.read_text(encoding="utf-8").strip().lower()
                if value in EDITIONS:
                    return value
        except OSError:
            continue
    return STANDARD


def edition_label(name: Optional[str] = None) -> str:
    return "Client + analysis app" if (name or edition()) == ANALYSIS else "Client"


def webview_bundled() -> bool:
    return importlib.util.find_spec("webview") is not None


def webview2_runtime_version() -> Optional[str]:
    """The installed WebView2 runtime's version, or None (also on non-Windows systems,
    where the window uses another engine and this check does not apply)."""
    if os.name != "nt":
        return None
    try:
        import winreg
    except ImportError:
        return None
    keys = (
        (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_CLIENT_GUID}"),
        (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_CLIENT_GUID}"),
        (winreg.HKEY_CURRENT_USER, rf"Software\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_CLIENT_GUID}"),
    )
    for root, path in keys:
        try:
            with winreg.OpenKey(root, path) as key:
                value, _ = winreg.QueryValueEx(key, "pv")
        except OSError:
            continue
        if isinstance(value, str) and value and value != "0.0.0.0":
            return value
    return None


def analysis_window_problem() -> Optional[str]:
    """Why the app window cannot be used on this machine, in words for the driver —
    or None when it can."""
    if edition() != ANALYSIS:
        return "This is the Garage16 client without the analysis app."
    if not webview_bundled():
        return "The analysis app is missing from this installation — reinstall the \"Client + analysis app\" download."
    if os.name == "nt" and webview2_runtime_version() is None:
        # No link here on purpose (the client contains no web addresses except the
        # driver's own server — tests/test_client_setup.py); the name is enough to find it.
        return ("The analysis window needs the free \"Microsoft Edge WebView2 Runtime\" from Microsoft, "
                "which is not installed on this PC (Windows 11 and updated Windows 10 have it).")
    return None


def preferred_view() -> str:
    """"app" or "browser". The standard edition always uses the browser; the analysis
    edition uses its window unless the driver ticked "open in the browser instead"."""
    if edition() != ANALYSIS:
        return VIEW_BROWSER
    value = read_setting(SETTING_KEY, VIEW_APP)
    return VIEW_BROWSER if value == VIEW_BROWSER else VIEW_APP


def set_preferred_view(view: str) -> None:
    if view not in (VIEW_APP, VIEW_BROWSER):
        raise ValueError(view)
    write_setting(SETTING_KEY, view)


# V0.9.4: two dashboards — the LapTime Analyzer (one lap against another) and the Stint
# Analyzer (whole sessions: stints, tyres, fuel, pit stops, strategy)
VIEW_LAPTIME = "laptime"
VIEW_STINTS = "stints"
ANALYSIS_VIEWS = (VIEW_LAPTIME, VIEW_STINTS)


def analysis_url(api_url: str, lap_id: Optional[int] = None, view: str = VIEW_LAPTIME) -> str:
    """The web analysis on the driver's own server (the web app is served from the same
    address as the API — docker/Caddyfile), optionally with one lap open: as Lap 1 in the
    LapTime Analyzer, or — view "stints" — the session of that lap in the Stint Analyzer."""
    base = api_url.rstrip("/")
    if view == VIEW_STINTS:
        return f"{base}/#/stints?lap={int(lap_id)}" if lap_id is not None else f"{base}/#/stints"
    if lap_id is not None:
        return f"{base}/#/analysis?a={int(lap_id)}"
    return f"{base}/#/analysis"


def same_origin(url_a: str, url_b: str) -> bool:
    a, b = urlparse(url_a or ""), urlparse(url_b or "")
    return bool(a.scheme and a.netloc) and (a.scheme, a.netloc.lower()) == (b.scheme, b.netloc.lower())


def analysis_window_command(lap_id: Optional[int] = None, view: str = VIEW_LAPTIME) -> List[str]:
    """How to start the window as its own process. The packaged app re-runs its own
    executable; a source checkout runs the module."""
    if getattr(sys, "frozen", False):
        command = [sys.executable, "--analysis-window"]
    else:
        command = [sys.executable, "-m", "client.main", "--analysis-window"]
    if lap_id is not None:
        command += ["--lap", str(int(lap_id))]
    if view == VIEW_STINTS:
        command += ["--view", VIEW_STINTS]
    return command


def download_url_for_this_edition(body: dict, standard_key: str, app_key: str) -> Optional[str]:
    """A too-old client is sent to the download of its own edition (falls back to the
    standard download when the server has no app download configured)."""
    if not isinstance(body, dict):
        return None
    if edition() == ANALYSIS and body.get(app_key):
        return body.get(app_key)
    return body.get(standard_key)
