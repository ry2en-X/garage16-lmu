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
import os
import tempfile
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

project_root = Path(SPECPATH).resolve().parent

datas = []
server_url = os.environ.get("GARAGE16_SERVER_URL", "").strip()
if server_url:
    baked = Path(tempfile.mkdtemp()) / "garage16_server.txt"
    baked.write_text(server_url + "\n", encoding="utf-8")
    datas.append((str(baked), "."))  # lands next to the executable (see client/config.py)

hiddenimports = (
    collect_submodules("client")          # everything imported lazily (e.g. setup_dialog)
    + collect_submodules("pyarrow.vendored")
    + ["tkinter", "tkinter.ttk", "webbrowser"]
)

a = Analysis(
    [str(project_root / "packaging" / "garage16_entry.py")],
    pathex=[str(project_root)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    # The client never touches the server code or the test tooling.
    excludes=["server", "discord_bot", "alembic", "tests", "pytest", "IPython", "matplotlib", "scipy"],
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
