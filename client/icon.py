"""client/icon.py — the Garage16 icon (V0.9.4): an angular "16", every corner cut at 45°.

Shown in the title bar and the Windows taskbar while the client window is open (the packaged
Garage16.exe carries the same icon itself — packaging/garage16.spec — so Explorer, Alt+Tab and
the taskbar show it for the analysis window too). Never fails: without the files (a stripped
build) the window simply keeps the default icon."""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional

logger = logging.getLogger("lmu_garage.icon")

APP_USER_MODEL_ID = "Garage16.Client"


def asset(name: str) -> Optional[Path]:
    """The icon file, in a source checkout or inside the PyInstaller bundle."""
    roots = [Path(__file__).resolve().parent / "assets"]
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        roots.insert(0, Path(bundle) / "client" / "assets")
    for root in roots:
        path = root / name
        if path.is_file():
            return path
    return None


def set_windows_app_id() -> None:
    """Windows groups taskbar buttons by "AppUserModelID". Without one, a client started from
    source (python.exe) shows Python's icon in the taskbar instead of the window's."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    except Exception:  # noqa: BLE001 — cosmetic only
        pass


def apply_to_tk(root) -> bool:
    """Title bar + taskbar icon of a Tk window. True when an icon was set."""
    try:
        ico = asset("garage16.ico")
        if sys.platform == "win32" and ico is not None:
            root.iconbitmap(default=str(ico))
            return True
        png = asset("garage16.png")
        if png is not None:
            import tkinter as tk

            image = tk.PhotoImage(file=str(png))
            root.iconphoto(True, image)
            root._garage16_icon = image   # keep a reference, Tk does not
            return True
    except Exception as exc:  # noqa: BLE001 — cosmetic only
        logger.debug("Window icon not set: %s", exc)
    return False
