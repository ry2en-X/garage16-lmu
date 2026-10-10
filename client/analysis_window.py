"""client/analysis_window.py — the analysis in its own window (V0.9.2, "Client + analysis app").

What it is: the web analysis of the driver's own Garage16 server, shown in a desktop
window (pywebview on Microsoft Edge WebView2) instead of a browser tab. It is the SAME
web app — every number comes from the server, so the window is always in sync with the
website, and every improvement to the web analysis is in the window too. No telemetry is
read or kept locally for it.

Why its own process: started by the client window's "Open analysis" button as
`Garage16.exe --analysis-window` (client/main.py). The Tk status window and the recording
keep running in the client process; the analysis window has its own GUI loop, and when it
is closed all of its memory is returned. If it crashed, the recording would not notice.

Signing in, without asking the driver for anything: the web app asks this window's bridge
(window.pywebview.api.app_session(), web/js/app_bridge.js) for a session only when it is
not signed in yet. The bridge asks the server with the desktop client's own credential
(POST /accounts/app-session) and returns a web-session token, which the page immediately
turns into the usual HttpOnly cookie (POST /accounts/app-session/activate). The window's
storage persists (cookie, language, layout), so this happens about once a month.

The bridge only answers pages of the driver's own server: if the window were ever showing
another site, app_session() refuses. Links to other sites open in the default browser.
"""

from __future__ import annotations

import logging
import os
import webbrowser
from typing import Callable, Optional
from urllib.parse import urlparse

import requests

from client.config import ClientConfig, resolve_data_dir
from client.edition import analysis_url, edition, same_origin
from client.uploader.uploader import CLIENT_VERSION

logger = logging.getLogger("lmu_garage.analysis_window")

WINDOW_TITLE = "Garage16 — Analysis"
WINDOW_SIZE = (1440, 900)
WINDOW_MIN_SIZE = (900, 600)
BACKGROUND = "#14171A"  # --asphalt in web/css/styles.css: no white flash while loading
REQUEST_TIMEOUT_SECONDS = 15


class AppBridge:
    """Exposed to the page as window.pywebview.api. pywebview exposes public methods
    only — everything holding a credential is underscore-private on purpose."""

    def __init__(self, config: ClientConfig, current_url: Callable[[], Optional[str]],
                 post: Callable = requests.post, open_browser: Callable[[str], object] = webbrowser.open):
        self._config = config
        self._current_url = current_url
        self._post = post
        self._open_browser = open_browser

    def _page_is_ours(self) -> bool:
        try:
            url = self._current_url()
        except Exception:  # noqa: BLE001 — a window that is closing has no URL
            return False
        return same_origin(url or "", self._config.api_url)

    def app_info(self) -> dict:
        return {"app": "garage16-desktop", "edition": edition(), "version": CLIENT_VERSION}

    def app_session(self) -> dict:
        """A fresh web session for this window (see the module docstring)."""
        if not self._page_is_ours():
            logger.warning("app_session refused: the window is not showing %s", self._config.api_url)
            return {"error": "Refused: this page is not your Garage16 server."}
        url = f"{self._config.api_url.rstrip('/')}/accounts/app-session"
        try:
            resp = self._post(url, headers={"Authorization": f"Bearer {self._config.auth_token}"},
                              timeout=REQUEST_TIMEOUT_SECONDS)
        except requests.RequestException as exc:
            logger.warning("app_session: server not reachable: %s", exc)
            return {"error": "The Garage16 server could not be reached."}
        if resp.status_code == 200:
            try:
                body = resp.json()
                return {"session_token": str(body["session_token"]), "driver_id": int(body["driver_id"]),
                        "display_name": str(body["display_name"])}
            except (ValueError, KeyError, TypeError):
                return {"error": "The server sent an unexpected answer."}
        if resp.status_code in (401, 403):
            return {"error": "Garage16 did not accept this app's login. Use \"Reconnect account…\" in the Garage16 window."}
        if resp.status_code == 429:
            return {"error": "Too many sign-ins in a short time — try again later."}
        return {"error": f"The server answered HTTP {resp.status_code}."}

    def open_external(self, url: str) -> bool:
        """Opens an http(s) link in the default browser (the page uses this for links
        that leave the analysis, e.g. Discord)."""
        parsed = urlparse(url or "")
        if parsed.scheme not in ("http", "https") or not parsed.netloc or len(url) > 2048:
            return False
        self._open_browser(url)
        return True


def storage_dir():
    """Where the window keeps its cookies and local storage (sign-in, language, layout)."""
    return resolve_data_dir().parent / "analysis_window"


def run(config: ClientConfig, lap_id: Optional[int] = None) -> int:
    """Shows the window and returns when it is closed. Falls back to the browser when the
    window cannot be shown; returns 0 either way (the driver gets to the analysis)."""
    url = analysis_url(config.api_url, lap_id)
    try:
        import webview  # pywebview — bundled with the "Client + analysis app" edition only
    except ImportError:
        logger.warning("pywebview is not installed — opening the analysis in the browser")
        webbrowser.open(url)
        return 0

    holder = {}
    bridge = AppBridge(config, current_url=lambda: holder["window"].get_current_url() if "window" in holder else None)
    try:
        # Links with target="_blank" (other sites) open in the default browser.
        webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True
    except (AttributeError, TypeError):
        pass
    try:
        holder["window"] = webview.create_window(
            WINDOW_TITLE, url, js_api=bridge,
            width=WINDOW_SIZE[0], height=WINDOW_SIZE[1], min_size=WINDOW_MIN_SIZE,
            background_color=BACKGROUND, text_select=True,
        )
        path = storage_dir()
        path.mkdir(parents=True, exist_ok=True)
        webview.start(gui="edgechromium" if os.name == "nt" else None,
                      private_mode=False, storage_path=str(path))
    except Exception:  # noqa: BLE001 — never leave the driver without the analysis
        logger.exception("The analysis window could not be shown — opening the browser instead")
        webbrowser.open(url)
    return 0
