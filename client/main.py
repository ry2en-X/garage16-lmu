"""
main.py — Client entry point.

Wires together:
    reader (shared memory) -> parser (lap/sector detection)
                            -> recorder (local disk, full-fidelity)
                            -> uploader (compressed, signed, background)
                            -> gui (status window)

Run with: python -m client.main
"""

from __future__ import annotations

import argparse
import atexit
import logging
import os
import queue
import sys
import time

from client.config import (
    ClientConfig,
    _from_file,
    default_api_url,
    load_config,
    local_retention_days,
    log_file_path,
    resolve_data_dir,
    save_config,
)
from client.analysis_launcher import AnalysisLauncher
from client.edition import ANALYSIS, VIEW_APP, VIEW_BROWSER, edition, edition_label, preferred_view, set_preferred_view
from client.migration import apply_migration_if_safe, check_for_migration
from client.server_check import run_server_check
from client.update_notice import format_update_required_message
from client.gui.app import GarageApp, StatusUpdate, run_in_background
from client.logging_config import resolve_log_level
from client.telemetry.async_saver import AsyncLapSaver
from client.telemetry.parser import LapParser
from client.telemetry.reader import LMUSharedMemoryReader, SharedMemoryNotFound
from client.telemetry.recorder import LapRecorder
from client.uploader.uploader import CLIENT_VERSION, Uploader

SERVER_CHECK_INTERVAL_SECONDS = 600  # re-check for migration / min-version changes every 10 min
SERVER_UNREACHABLE_RETRY_SECONDS = 60
UPLOAD_POLL_SECONDS = 15
LOCAL_CLEANUP_INTERVAL_SECONDS = 3600  # V0.9.1: drop old, already-uploaded local telemetry files hourly

logging.basicConfig(level=resolve_log_level(), format="%(asctime)s [%(name)s] %(message)s")
logger = logging.getLogger("lmu_garage.main")


def telemetry_loop(app: GarageApp, recorder: LapRecorder) -> None:
    reader = LMUSharedMemoryReader()
    parser = LapParser()

    def lap_saved(lap, meta_path) -> None:
        # Runs on the saver thread (see telemetry/async_saver.py) — the frame
        # loop below must never block on disk I/O.
        if meta_path is None:
            return
        app.push(
            StatusUpdate(
                connected=True,
                track_name=lap.track_name,
                car_name=lap.car_name,
                lap_number=lap.lap_number,
                last_lap_time=lap.lap_time,
                last_lap_valid=lap.is_valid,
                log_line=(
                    f"Lap {lap.lap_number} saved: "
                    f"{lap.lap_time:.3f}s "
                    f"({'valid' if lap.is_valid else 'invalid'})"
                ),
            )
        )

    saver = AsyncLapSaver(recorder, on_saved=lap_saved)
    atexit.register(saver.flush, 15.0)

    while True:
        try:
            reader.open()
            app.push(StatusUpdate(connected=True, log_line="Connected to LMU shared memory."))
            for frame in reader.stream(poll_hz=60.0):
                app.push(
                    StatusUpdate(
                        connected=True,
                        track_name=frame.session.track_name.decode(errors="ignore").strip("\x00"),
                        car_name=frame.telemetry.vehicle_name.decode(errors="ignore").strip("\x00"),
                        lap_number=frame.telemetry.lap_number,
                    )
                )
                completed_lap = parser.feed(frame)
                if completed_lap is not None:
                    saver.submit(completed_lap)
        except SharedMemoryNotFound:
            reader.close()
            parser.reset()  # P0-7(b): discard stale lap buffer from previous session
            app.push(StatusUpdate(connected=False, log_line="Waiting for LMU to start..."))
            time.sleep(5)
        except Exception:
            reader.close()
            parser.reset()  # P0-7(b): discard stale lap buffer
            logger.exception("Telemetry loop crashed, restarting in 5s")
            app.push(StatusUpdate(connected=False, log_line="Telemetry error — retrying..."))
            time.sleep(5)


def uploader_loop(app: GarageApp, recorder: LapRecorder, config: ClientConfig, reconfig_queue: "queue.Queue[ClientConfig]") -> None:
    """Runs in a background thread. `config` was already resolved on the
    MAIN thread (client/main.py's main()) — first-run setup is a GUI
    dialog now (V0.8.6), and Tkinter dialogs must run on the main thread,
    so config loading can no longer happen in here.

    Every SERVER_CHECK_INTERVAL_SECONDS (and immediately at startup) one
    health request answers three questions at once — see
    client/server_check.py: is the server reachable, is this client too
    old for it (UPDATE_REQUIRED), and has it moved (automatic, secure
    migration — client/migration.py). A successful migration updates the
    saved config AND this running loop's uploader in place, so nothing
    needs a restart; the new URL also survives one, since it was saved.
    """
    uploader = Uploader(
        recorder=recorder,
        backend_url=config.api_url,
        auth_token=config.auth_token,
        client_secret=config.client_secret,
    )
    next_check = 0.0
    next_cleanup = 0.0
    announced_lap_id = None

    def log(line: str) -> None:
        logger.info(line)
        app.push(StatusUpdate(log_only=True, log_line=line))

    while True:
        try:
            # Credentials changed via the GUI's "Reconnect account" button.
            try:
                while True:
                    config = reconfig_queue.get_nowait()
                    uploader.backend_url = config.api_url.rstrip("/")
                    uploader.auth_token = config.auth_token
                    uploader.client_secret = config.client_secret.encode()
                    uploader.auth_rejected = False
                    next_check = 0.0
                    log("Account reconnected.")
            except queue.Empty:
                pass

            now = time.monotonic()
            if now >= next_check:
                result = run_server_check(config, CLIENT_VERSION)
                for line in result.log_lines:
                    log(line)
                app.push(StatusUpdate(log_only=True, notice=result.notice, notice_url=result.notice_url or ""))
                if result.migrated:
                    config = result.config
                    uploader.backend_url = config.api_url.rstrip("/")
                    next_check = now  # verify the NEW server right away
                elif not result.reachable:
                    next_check = now + SERVER_UNREACHABLE_RETRY_SECONDS
                else:
                    next_check = now + SERVER_CHECK_INTERVAL_SECONDS

            count = uploader.run_once()
            if uploader.update_required is not None:
                app.push(StatusUpdate(
                    log_only=True,
                    notice=format_update_required_message(
                        uploader.update_required.min_version, uploader.update_required.download_url),
                    notice_url=uploader.update_required.download_url or "",
                ))
            elif uploader.auth_rejected:
                app.push(StatusUpdate(log_only=True, notice=(
                    "Garage16 rejected your login (it may have been reset or revoked). "
                    "Click \"Reconnect account…\" and enter your current Auth token and Client secret."),
                    notice_url=""))
            if count:
                log(f"Uploaded {count} lap(s).")
            if uploader.last_uploaded_lap_id is not None and uploader.last_uploaded_lap_id != announced_lap_id:
                announced_lap_id = uploader.last_uploaded_lap_id
                app.push(StatusUpdate(log_only=True, last_lap_id=announced_lap_id))

            # V0.9.1: local retention. Only laps the server already accepted, only after
            # the configured number of days (default 14, 0 = keep everything) — see
            # LapRecorder.cleanup_uploaded. A failure here must never stop uploads.
            if now >= next_cleanup:
                next_cleanup = now + LOCAL_CLEANUP_INTERVAL_SECONDS
                try:
                    removed = recorder.cleanup_uploaded(local_retention_days())
                    if removed:
                        log(f"Removed {len(removed)} old local telemetry file(s) that were uploaded more than "
                            f"{local_retention_days():g} days ago.")
                except Exception:
                    logger.exception("Local cleanup failed")
        except Exception:
            logger.exception("Uploader loop crashed, retrying in %ds", UPLOAD_POLL_SECONDS)
        time.sleep(UPLOAD_POLL_SECONDS)


def follow_migration() -> int:
    """`python -m client.main --follow-migration` — the manual,
    terminal-only fallback for developers/admins. Friends never need it:
    the running app follows a migration automatically (see
    client/migration.py, client/server_check.py). Kept because it's the
    one way to move a client whose OLD server is plain HTTP (auto-follow
    refuses that — see migration.py's security model): a person typing
    this command deliberately IS the consent the automatic path derives
    from HTTPS, hence allow_insecure=True. Everything else (well-formed
    URL, new server actually answering) is still enforced.
    """
    config = load_config()
    body = check_for_migration(config.api_url, timeout=10)
    if body is None:
        print(f"Could not reach {config.api_url} (or it returned an error) — cannot check for a migration.")
        return 1
    result = apply_migration_if_safe(config, body, allow_insecure=True)
    if result.message:
        print(result.message)
    else:
        print(f"No migration in progress — {config.api_url} is still the active server.")
    return 0


def _attach_parent_console() -> bool:
    """Windows-only, frozen builds only. The packaged app is built
    `console=False` (windowed: no terminal window may ever pop up for a
    friend), and a windowed executable has NO stdout — so `Garage16.exe
    --version` typed into cmd/PowerShell would print nothing at all.
    Attaching to the console of the process that launched us (if there
    is one) makes the developer/admin flags usable from a terminal
    without giving the app its own console window when double-clicked.
    Returns True if it attached. A no-op everywhere else (source runs,
    Linux/macOS, double-click launches).

    Untestable outside Windows — see docs/CLIENT_BUILD.md's checklist.
    """
    if os.name != "nt" or not getattr(sys, "frozen", False):
        return False
    try:
        import ctypes

        ATTACH_PARENT_PROCESS = -1
        if not ctypes.windll.kernel32.AttachConsole(ATTACH_PARENT_PROCESS):
            return False  # launched by double-click / Start menu: no console to attach to
        sys.stdout = open("CONOUT$", "w", encoding="utf-8")
        sys.stderr = open("CONOUT$", "w", encoding="utf-8")
        return True
    except (OSError, AttributeError):
        return False


def _attach_file_logging() -> None:
    from logging.handlers import RotatingFileHandler

    try:
        path = log_file_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(path, maxBytes=1_000_000, backupCount=2, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s [%(name)s] %(message)s"))
        logging.getLogger().addHandler(handler)
    except OSError:
        pass  # logging must never stop the app from starting


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--reconfigure",
        action="store_true",
        help="Re-run the credential setup dialog, overwriting the saved config "
        "(use after rotating a token/secret on the server). Friends can do the "
        "same with the 'Reconnect account' button in the app window.",
    )
    parser.add_argument(
        "--follow-migration",
        action="store_true",
        help="Developer/admin fallback: check the configured server for a "
        "migration signal and switch if present, then exit. The running app "
        "does this automatically for HTTPS servers.",
    )
    parser.add_argument("--version", action="store_true", help="Print the client version and exit.")
    # V0.9.2: started by the window's "Open analysis" button (Client + analysis app)
    parser.add_argument("--analysis-window", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--lap", type=int, default=None, help=argparse.SUPPRESS)
    parser.add_argument(
        "--analysis-selftest", action="store_true",
        help="Check that this installation can show the analysis app window (build smoke test, support).",
    )
    args = parser.parse_args()

    if args.analysis_selftest:
        _attach_parent_console()
        raise SystemExit(analysis_selftest())

    if args.analysis_window:
        raise SystemExit(run_analysis_window(args.lap))

    if args.version or args.follow_migration:
        _attach_parent_console()

    if args.version:
        # Doubles as the "what is this install pointed at" support command
        # a friend can run (or the admin can ask them to) — everything
        # here is non-secret; never prints the token or client secret.
        saved = _from_file()
        print(f"Garage16 client {CLIENT_VERSION} ({edition_label()})")
        print(f"Server:    {saved.api_url if saved else default_api_url() + ' (default, not yet set up)'}")
        print(f"Data:      {resolve_data_dir()}")
        print(f"Log file:  {log_file_path()}")
        return

    if args.follow_migration:
        raise SystemExit(follow_migration())

    _attach_file_logging()

    # Setup happens HERE, on the main thread, as a window (V0.8.6) —
    # never as console prompts, and never on a background thread (Tk
    # dialogs must run on the main thread). Headless/dev callers of
    # load_config() without a setup_ui still get the console wizard.
    from client.gui.setup_dialog import run_setup_dialog

    config = load_config(force_setup=args.reconfigure, setup_ui=run_setup_dialog)

    reconfig_queue: "queue.Queue[ClientConfig]" = queue.Queue()
    holder = {}

    def on_reconnect() -> None:
        current = _from_file() or config
        new_config = run_setup_dialog(current.api_url, parent=holder["app"].root)
        if new_config is not None:
            save_config(new_config)
            reconfig_queue.put(new_config)

    def log_to_window(line: str) -> None:
        logger.info(line)
        holder["app"].push(StatusUpdate(log_only=True, log_line=line))

    launcher = AnalysisLauncher(api_url=lambda: (_from_file() or config).api_url, log=log_to_window)
    analysis_choice = None
    if edition() == ANALYSIS:
        analysis_choice = (
            preferred_view() == VIEW_BROWSER,
            lambda prefers_browser: set_preferred_view(VIEW_BROWSER if prefers_browser else VIEW_APP),
        )

    app = holder["app"] = GarageApp(on_reconnect=on_reconnect, on_open_analysis=launcher.open,
                                    analysis_choice=analysis_choice)
    recorder = LapRecorder(data_dir=str(resolve_data_dir()))

    run_in_background(telemetry_loop, app, recorder)
    run_in_background(uploader_loop, app, recorder, config, reconfig_queue)

    try:
        app.run()
    finally:
        launcher.close()


def analysis_selftest() -> int:
    """`Garage16.exe --analysis-selftest`: can this installation show the analysis window?
    Imports the window's engine the way the window does (on Windows: pythonnet + the
    WebView2 .NET assemblies) without opening anything. Exit 0 = ready (or: this is the
    plain client, which opens the browser by design), 1 = the engine is broken/missing.
    The build runs it on the packaged exe, so a broken bundle never ships."""
    from client.edition import ANALYSIS as _ANALYSIS, analysis_window_problem, edition as _edition, webview2_runtime_version

    print(f"Garage16 client {CLIENT_VERSION} ({edition_label()})")
    if _edition() != _ANALYSIS:
        print("Analysis: opens in the browser (this edition has no app window).")
        return 0
    try:
        import webview  # noqa: F401
        if os.name == "nt":
            import webview.platforms.winforms  # noqa: F401  (loads pythonnet + WebView2 assemblies)
    except Exception as exc:  # noqa: BLE001
        print(f"Analysis window: BROKEN — {type(exc).__name__}: {exc}")
        return 1
    try:
        from importlib.metadata import version as _dist_version

        engine = f"pywebview {_dist_version('pywebview')}"
    except Exception:  # noqa: BLE001 — a frozen build may not carry the package metadata
        engine = "pywebview"
    print(f"Analysis window engine: {engine}")
    if os.name == "nt":
        runtime = webview2_runtime_version()
        print(f"WebView2 runtime: {runtime or 'not installed (the analysis opens in the browser until it is)'}")
    problem = analysis_window_problem()
    print("Analysis window: ready" if problem is None else f"Analysis window: not available here — {problem}")
    return 0


def run_analysis_window(lap_id=None) -> int:
    """`Garage16.exe --analysis-window [--lap N]` — the analysis app window, in its own
    process (see client/analysis_window.py). Logs to its own file next to the client's
    (two processes must not rotate one log file)."""
    from logging.handlers import RotatingFileHandler

    try:
        path = log_file_path().with_name("garage16-analysis-window.log")
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(path, maxBytes=500_000, backupCount=1, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s [%(name)s] %(message)s"))
        logging.getLogger().addHandler(handler)
    except OSError:
        pass
    config = _from_file()
    if config is None:
        # not set up yet: nothing to sign in with — the website's own login still works
        import webbrowser

        from client.edition import analysis_url

        webbrowser.open(analysis_url(default_api_url(), lap_id))
        return 0
    from client import analysis_window

    return analysis_window.run(config, lap_id)


if __name__ == "__main__":
    main()
