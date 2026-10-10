# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for the Garage16 desktop client (V0.8.6).
#
# Build (from the project root):   pyinstaller packaging/garage16.spec --noconfirm
# Result: dist/Garage16/Garage16.exe (Windows) — a self-contained folder
# with its own Python; friends need NO Python installation. The Windows
# installer (packaging/garage16.iss) wraps that folder into a single
# Garage16-Client-Setup.exe. See docs/CLIENT_BUILD.md.
#
# Deliberately one-DIR, not one-file: a one-file exe unpacks itself into a
# temp folder on every launch (slow start for a background tool) and is
# flagged by antivirus far more often. The installer hides the folder
# from the friend anyway.
#
# Environment variables (all optional):
#   GARAGE16_SERVER_URL   baked into the package as garage16_server.txt so
#                         friends never have to type a server address.
#   GARAGE16_EDITION      "standard" (default): the plain client, "Garage16-Client-Setup.exe".
#                         "analysis" (V0.9.2): the client + analysis app window
#                         (client/analysis_window.py, pywebview on WebView2) —
#                         needs `pip install -r client/requirements-analysis.txt` first.
#                         Baked in as garage16_edition.txt (see client/edition.py).
import os
import sys
import tempfile
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

project_root = Path(SPECPATH).resolve().parent

datas = []
server_url = os.environ.get("GARAGE16_SERVER_URL", "").strip()
if server_url:
    baked = Path(tempfile.mkdtemp()) / "garage16_server.txt"
    baked.write_text(server_url + "\n", encoding="utf-8")
    datas.append((str(baked), "."))  # lands next to the executable (see client/config.py)

edition = os.environ.get("GARAGE16_EDITION", "standard").strip().lower() or "standard"
if edition not in ("standard", "analysis"):
    raise SystemExit(f"GARAGE16_EDITION must be 'standard' or 'analysis', not {edition!r}")
baked_edition = Path(tempfile.mkdtemp()) / "garage16_edition.txt"
baked_edition.write_text(edition + "\n", encoding="utf-8")
datas.append((str(baked_edition), "."))  # read by client/edition.py

hiddenimports = (
    collect_submodules("client")          # everything imported lazily (e.g. setup_dialog)
    + collect_submodules("pyarrow.vendored")
    + ["tkinter", "tkinter.ttk", "webbrowser"]
)
# The plain client must never carry the window's engine, even if the build
# environment happens to have it installed.
edition_excludes = ["webview", "clr", "pythonnet", "clr_loader"]
if edition == "analysis":
    edition_excludes = []
    hiddenimports += ["webview", "webview.http", "webview.util", "webview.window"]
    if sys.platform == "win32":
        # WinForms host + the Edge WebView2 control (.NET via pythonnet)
        hiddenimports += ["webview.platforms.winforms", "webview.platforms.edgechromium", "clr"]
    datas += collect_data_files("webview")  # js helpers + the WebView2 .NET assemblies / loader DLLs

a = Analysis(
    [str(project_root / "packaging" / "garage16_entry.py")],
    pathex=[str(project_root)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    # The client never touches the server code or the test tooling.
    excludes=["server", "discord_bot", "alembic", "tests", "pytest", "IPython", "matplotlib", "scipy"] + edition_excludes,
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Garage16",
    console=False,   # windowed: no terminal window, ever (V0.8.6 §2)
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="Garage16",
)
