"""
config.py — Client credential loading.

Previously the client hard-required LMU_GARAGE_AUTH_TOKEN and
LMU_GARAGE_CLIENT_SECRET as environment variables, set manually in every
new shell before `python -m client.main` — easy to forget, and gone the
moment you close the terminal.

Resolution order (highest priority first):
  1. Environment variables (LMU_GARAGE_API_URL / _AUTH_TOKEN / _CLIENT_SECRET).
     Kept as the top priority on purpose: useful for CI, service accounts,
     or anyone who deliberately wants to override the saved config for one
     run without touching the file.
  2. A local config file at ~/.lmu_garage/client_config.json, written once
     by an interactive setup wizard and reused on every future run.
  3. If neither is present, run the wizard: prompt for the three values,
     save them, and continue.

Threat model note: the client_secret has always had to live in plaintext
somewhere reachable by the client process (it's used to HMAC-sign every
upload) — an env var isn't meaningfully safer than a file, and is easier
to accidentally leak (shell history, process listing, CI logs). This file
is written with owner-only permissions where the OS supports it (POSIX
chmod 600; Windows ACLs are not restricted here — this is still a
dev/beta-scale tool, not something protecting secrets on a shared machine).
Run with --reconfigure or --rotate-secret (server/routers/accounts.py) if
a secret needs to change.
"""

from __future__ import annotations

import json
import logging
import os
import stat
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger("lmu_garage.client_config")

CONFIG_DIR = Path(os.environ.get("LMU_GARAGE_CONFIG_DIR", Path.home() / ".lmu_garage"))
CONFIG_PATH = CONFIG_DIR / "client_config.json"

DEFAULT_API_URL = "http://localhost:8000"

# V0.8.6: the person who builds/distributes the friend-facing package can
# drop a one-line text file with their server's URL next to the
# executable (or, running from source, next to this file) — friends then
# never have to type or even see a server address during setup. See
# docs/CLIENT_BUILD.md. Resolution order for the pre-filled default:
# LMU_GARAGE_API_URL env var > this file > DEFAULT_API_URL (localhost,
# only sensible for developers).
BUNDLED_SERVER_FILENAME = "garage16_server.txt"


def _bundled_server_file_candidates() -> list:
    import sys
    candidates = []
    if getattr(sys, "frozen", False):  # PyInstaller
        candidates.append(Path(sys.executable).resolve().parent / BUNDLED_SERVER_FILENAME)
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates.append(Path(meipass) / BUNDLED_SERVER_FILENAME)
    candidates.append(Path(__file__).resolve().parent / BUNDLED_SERVER_FILENAME)
    return candidates


def default_api_url() -> str:
    env_url = os.environ.get("LMU_GARAGE_API_URL")
    if env_url:
        return env_url
    for candidate in _bundled_server_file_candidates():
        try:
            if candidate.is_file():
                value = candidate.read_text(encoding="utf-8").strip().splitlines()[0].strip()
                if value:
                    return value
        except (OSError, IndexError):
            continue
    return DEFAULT_API_URL


@dataclass
class ClientConfig:
    api_url: str
    auth_token: str
    client_secret: str


def _from_env() -> Optional[ClientConfig]:
    auth_token = os.environ.get("LMU_GARAGE_AUTH_TOKEN")
    client_secret = os.environ.get("LMU_GARAGE_CLIENT_SECRET")
    if not auth_token or not client_secret:
        return None
    return ClientConfig(
        api_url=os.environ.get("LMU_GARAGE_API_URL", DEFAULT_API_URL),
        auth_token=auth_token,
        client_secret=client_secret,
    )


def _from_file() -> Optional[ClientConfig]:
    if not CONFIG_PATH.exists():
        return None
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        return ClientConfig(
            api_url=data.get("api_url", DEFAULT_API_URL),
            auth_token=data["auth_token"],
            client_secret=data["client_secret"],
        )
    except (json.JSONDecodeError, KeyError, OSError):
        logger.warning("Could not read %s — re-running setup.", CONFIG_PATH)
        return None


def save_config(config: ClientConfig) -> None:
    """Atomic: writes to a sibling temp file, then os.replace()s it over
    the real one. A crash, power loss, or full disk mid-write leaves the
    PREVIOUS config fully intact instead of a truncated/corrupt file
    (V0.8.6 §8: a failed migration — or any failed save — must never
    destroy a working configuration). os.replace is atomic on both
    POSIX and Windows when source and destination are on the same
    filesystem, which a sibling file always is."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    tmp_path = CONFIG_PATH.with_name(CONFIG_PATH.name + ".tmp")
    tmp_path.write_text(json.dumps(asdict(config), indent=2), encoding="utf-8")
    try:
        # Owner read/write only. No-op on Windows (which ignores POSIX
        # chmod bits), but harmless there.
        os.chmod(tmp_path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
    os.replace(tmp_path, CONFIG_PATH)


def clear_config() -> None:
    CONFIG_PATH.unlink(missing_ok=True)


def _run_setup_wizard() -> ClientConfig:
    print()
    print("=== LMU Garage — first-time setup ===")
    print("Get an auth token and client secret from the web app's Account")
    print("page (register a new driver, or use an existing token).")
    print("These are saved to:", CONFIG_PATH)
    print()
    api_url = input(f"Server URL [{DEFAULT_API_URL}]: ").strip() or DEFAULT_API_URL
    auth_token = input("Auth token: ").strip()
    client_secret = input("Client secret: ").strip()
    while not auth_token or not client_secret:
        print("Both auth token and client secret are required.")
        auth_token = input("Auth token: ").strip()
        client_secret = input("Client secret: ").strip()

    config = ClientConfig(api_url=api_url, auth_token=auth_token, client_secret=client_secret)
    save_config(config)
    print("Saved. Re-run with --reconfigure any time to update these.\n")
    return config


def load_config(force_setup: bool = False, setup_ui: Optional[Callable[[str], Optional[ClientConfig]]] = None) -> ClientConfig:
    """Resolve client credentials: env vars > saved file > interactive setup.

    `force_setup=True` (from `python -m client.main --reconfigure`) skips
    straight to the wizard and overwrites the saved file — use this after
    rotating a token/secret via the server's /accounts/rotate-* endpoints.

    `setup_ui` (V0.8.6): an optional `(default_api_url: str) ->
    Optional[ClientConfig]` callable, used INSTEAD of the console
    `input()` wizard when interactive setup is actually needed. The
    packaged friend-facing app (client/main.py) passes a Tkinter dialog
    here — see client/gui/setup_dialog.py — so first-run setup never
    needs a terminal at all. Left as `None` for anything headless (tests,
    a developer running `python -m client.main` from a real terminal,
    CI): falls back to the original console wizard, unchanged. A `setup_ui`
    that returns `None` (user cancelled) re-raises as SystemExit — there's
    no sensible config to continue with.
    """
    if force_setup:
        return _run_setup_wizard() if setup_ui is None else _run_setup_ui(setup_ui)

    config = _from_env()
    if config is not None:
        return config

    config = _from_file()
    if config is not None:
        return config

    return _run_setup_wizard() if setup_ui is None else _run_setup_ui(setup_ui)


def _run_setup_ui(setup_ui) -> ClientConfig:
    result = setup_ui(default_api_url())
    if result is None:
        raise SystemExit("Setup cancelled — Garage16 needs a server URL, auth token, and client secret to run.")
    save_config(result)
    return result


def resolve_data_dir() -> Path:
    """Where recorded laps (and pending uploads) live. V0.8.6: the
    previous hardcoded `./lmu_garage_data` was relative to the CURRENT
    WORKING DIRECTORY — fine when a developer runs `python -m client.main`
    from the project folder, broken for an installed app started from a
    Start-menu shortcut (arbitrary cwd, possibly a read-only Program Files
    folder).

    Resolution order:
      1. LMU_GARAGE_DATA_DIR env var (explicit override)
      2. an existing ./lmu_garage_data in the cwd — keeps a developer's or
         early tester's already-recorded, possibly still-pending laps
         where the client will find them, instead of silently orphaning
         them behind a new default
      3. the per-user application data folder (%LOCALAPPDATA%\\Garage16 on
         Windows, ~/.lmu_garage otherwise) + /laps
    """
    override = os.environ.get("LMU_GARAGE_DATA_DIR")
    if override:
        return Path(override)
    legacy = Path("lmu_garage_data")
    if legacy.is_dir():
        return legacy.resolve()
    local_appdata = os.environ.get("LOCALAPPDATA")
    base = Path(local_appdata) / "Garage16" if local_appdata else Path.home() / ".lmu_garage"
    return base / "laps"


def log_file_path() -> Path:
    """Client log file — in windowed (no-console) builds there's nowhere
    else for log output to go, and it's what a friend sends the admin
    when something's wrong."""
    return resolve_data_dir().parent / "garage16-client.log"
