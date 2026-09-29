"""Friend-facing client setup & configuration (V0.8.6 §2, §6, §8):
GUI-based first-run setup instead of console prompts, a bundled default
server URL, per-user data directory, and the notice/log plumbing the GUI
relies on. Logic is tested headlessly; the real Tkinter dialog and window
are exercised under Xvfb when it's available (skipped, not faked, when
it isn't)."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

import client.config as client_config
from client.config import ClientConfig, default_api_url, load_config, resolve_data_dir
from client.gui.setup_dialog import normalize_server_url, validate_setup_input
from client.gui.status import StatusUpdate, should_update_last_lap, should_update_telemetry_fields
from client.update_notice import format_update_required_message
from tests.local_servers import FakeGarageServer

PROJECT_ROOT = Path(__file__).resolve().parent.parent
needs_xvfb = pytest.mark.skipif(shutil.which("xvfb-run") is None, reason="xvfb-run not available — GUI tests skipped, not faked")


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    config_dir = tmp_path / ".lmu_garage"
    monkeypatch.setattr(client_config, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(client_config, "CONFIG_PATH", config_dir / "client_config.json")
    for var in ("LMU_GARAGE_AUTH_TOKEN", "LMU_GARAGE_CLIENT_SECRET", "LMU_GARAGE_API_URL", "LMU_GARAGE_DATA_DIR"):
        monkeypatch.delenv(var, raising=False)
    return config_dir


# ------------------------------------------------- input normalization

@pytest.mark.parametrize(
    "raw,expected",
    [
        ("garage16.example.com", "https://garage16.example.com"),
        ("  https://garage16.example.com/  ", "https://garage16.example.com"),
        ("http://192.168.1.5:8000", "http://192.168.1.5:8000"),
        ("", ""),
    ],
)
def test_normalize_server_url(raw, expected):
    assert normalize_server_url(raw) == expected


def test_validate_accepts_good_input_and_normalizes():
    config, error = validate_setup_input("garage16.example.com/", "  tok  ", " sec ")
    assert error is None
    assert config == ClientConfig(api_url="https://garage16.example.com", auth_token="tok", client_secret="sec")


@pytest.mark.parametrize(
    "url,token,secret,fragment",
    [
        ("", "t", "s", "server address"),
        ("ftp://x.example", "t", "s", "doesn't look right"),
        ("https://", "t", "s", "doesn't look right"),
        ("https://x.example", "", "s", "Auth token"),
        ("https://x.example", "t", "  ", "Client secret"),
    ],
)
def test_validate_rejects_bad_input_with_a_plain_message(url, token, secret, fragment):
    config, error = validate_setup_input(url, token, secret)
    assert config is None
    assert fragment in error


# ----------------------------------------------- load_config with a GUI

def test_load_config_uses_setup_ui_instead_of_console_and_saves(isolated_config, monkeypatch):
    def no_console(*_a, **_k):
        raise AssertionError("console input() must not be used when a setup_ui is supplied")

    monkeypatch.setattr("builtins.input", no_console)
    seen = {}

    def fake_ui(default_url):
        seen["default"] = default_url
        return ClientConfig(api_url="https://s.example", auth_token="t", client_secret="c")

    config = load_config(setup_ui=fake_ui)

    assert config.api_url == "https://s.example"
    assert json.loads((isolated_config / "client_config.json").read_text(encoding="utf-8"))["auth_token"] == "t"
    assert seen["default"]  # the dialog was given a pre-fill


def test_load_config_skips_setup_ui_when_a_saved_config_exists(isolated_config):
    client_config.save_config(ClientConfig("https://saved.example", "t", "c"))

    def must_not_run(_default):
        raise AssertionError("setup UI must not appear when already configured")

    assert load_config(setup_ui=must_not_run).api_url == "https://saved.example"


def test_load_config_force_setup_reopens_the_ui_and_overwrites(isolated_config):
    client_config.save_config(ClientConfig("https://saved.example", "old", "old"))
    load_config(force_setup=True, setup_ui=lambda _d: ClientConfig("https://new.example", "new", "new"))
    assert json.loads((isolated_config / "client_config.json").read_text(encoding="utf-8"))["auth_token"] == "new"


def test_cancelled_setup_exits_cleanly_and_saves_nothing(isolated_config):
    with pytest.raises(SystemExit):
        load_config(setup_ui=lambda _d: None)
    assert not (isolated_config / "client_config.json").exists()


# ------------------------------------------- server URL centralization

def test_default_api_url_prefers_env_then_bundled_file_then_localhost(monkeypatch, tmp_path):
    bundled = tmp_path / client_config.BUNDLED_SERVER_FILENAME
    monkeypatch.setattr(client_config, "_bundled_server_file_candidates", lambda: [bundled])
    monkeypatch.delenv("LMU_GARAGE_API_URL", raising=False)

    assert default_api_url() == client_config.DEFAULT_API_URL  # nothing configured

    bundled.write_text("https://baked-in.example\n# comment line ignored\n", encoding="utf-8")
    assert default_api_url() == "https://baked-in.example"

    monkeypatch.setenv("LMU_GARAGE_API_URL", "https://from-env.example")
    assert default_api_url() == "https://from-env.example"


def test_no_hardcoded_server_addresses_anywhere_in_the_client():
    """§6: the server address lives in exactly one place
    (client/config.py). No LAN IPs, NAS names, or real domains scattered
    through the client's CODE. Inspects actual string literals via the AST
    (docstrings and comments — which legitimately contain example URLs —
    are excluded). The only literal URLs allowed are localhost/example/
    `.invalid` placeholders."""
    import ast
    import re

    pattern = re.compile(r"https?://[^\s\"')]+")
    allowed_fragments = ("localhost", "127.0.0.1", "example", ".invalid")
    offenders = []
    for path in (PROJECT_ROOT / "client").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docstring_nodes = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                body = node.body
                if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant):
                    docstring_nodes.add(id(body[0].value))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstring_nodes:
                for match in pattern.findall(node.value):
                    if not any(fragment in match for fragment in allowed_fragments):
                        offenders.append(f"{path.relative_to(PROJECT_ROOT)}:{node.lineno}: {match}")
                if "192.168." in node.value:
                    offenders.append(f"{path.relative_to(PROJECT_ROOT)}:{node.lineno}: LAN address")
    assert offenders == []


def test_uploader_placeholder_default_is_never_used_by_the_app():
    """uploader.py's DEFAULT_BACKEND_URL is an intentionally-invalid
    placeholder (a `.invalid` TLD can never resolve) — main.py always
    passes the configured URL explicitly."""
    from client.uploader.uploader import DEFAULT_BACKEND_URL

    assert DEFAULT_BACKEND_URL.endswith(".invalid")
    main_source = (PROJECT_ROOT / "client" / "main.py").read_text(encoding="utf-8")
    assert "backend_url=config.api_url" in main_source


# ------------------------------------------------------- data directory

def test_resolve_data_dir_precedence(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("LMU_GARAGE_DATA_DIR", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "AppData"))

    # 3. per-user default when nothing else applies
    assert resolve_data_dir() == tmp_path / "AppData" / "Garage16" / "laps"

    # 2. an existing legacy ./lmu_garage_data wins over the default, so
    #    already-recorded pending laps aren't orphaned
    (tmp_path / "lmu_garage_data").mkdir()
    assert resolve_data_dir() == (tmp_path / "lmu_garage_data").resolve()

    # 1. explicit override beats everything
    monkeypatch.setenv("LMU_GARAGE_DATA_DIR", str(tmp_path / "custom"))
    assert resolve_data_dir() == tmp_path / "custom"


def test_data_dir_is_no_longer_cwd_relative_by_default(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("LMU_GARAGE_DATA_DIR", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "AppData"))
    assert resolve_data_dir().is_absolute()


# ------------------------------------------------------ messages / GUI

def test_update_message_is_plain_language():
    msg = format_update_required_message("0.9.0", "https://example.com/dl")
    assert "new version of Garage16" in msg and "v0.9.0" in msg
    assert "https://example.com/dl" in msg
    assert "laps are kept" in msg
    assert "python" not in msg.lower() and "pip" not in msg.lower() and "terminal" not in msg.lower()


def test_update_message_without_link():
    assert "Ask whoever runs your Garage16 server" in format_update_required_message(None, None)


def test_log_only_updates_do_not_touch_telemetry_fields():
    assert should_update_telemetry_fields(StatusUpdate(connected=True)) is True
    assert should_update_telemetry_fields(StatusUpdate(log_only=True, log_line="x")) is False
    assert should_update_last_lap(StatusUpdate(log_only=True, log_line="x")) is False


@needs_xvfb
def test_real_window_log_only_updates_and_notice_banner():
    script = textwrap.dedent(
        """
        import queue
        from client.gui.app import GarageApp
        from client.gui.status import StatusUpdate

        app = GarageApp(on_reconnect=lambda: None)
        def drain():
            while True:
                try: app._apply(app._queue.get_nowait())
                except queue.Empty: break
        app.push(StatusUpdate(connected=True, track_name="Le Mans", car_name="499P", lap_number=3)); drain()
        app.push(StatusUpdate(log_only=True, log_line="Uploaded 1 lap(s).")); drain()
        assert app.status_var.get() == "Connected" and app.track_var.get() == "Track: Le Mans", "log-only update reset the display"
        app.push(StatusUpdate(log_only=True, notice="A new version of Garage16 is required.")); drain(); app.root.update()
        assert app.notice_label.winfo_ismapped()
        app.push(StatusUpdate(log_only=True, notice="")); drain(); app.root.update()
        assert not app.notice_label.winfo_ismapped()
        print("GUI-OK")
        """
    )
    result = subprocess.run(["xvfb-run", "-a", sys.executable, "-c", script], cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=60)
    assert "GUI-OK" in result.stdout, result.stdout + result.stderr


@needs_xvfb
def test_real_setup_dialog_collects_credentials_without_a_terminal(tmp_path):
    """Drives the REAL Tkinter dialog: fills the three fields, presses
    Connect, and checks the returned ClientConfig — against a real local
    server so the reachability check inside the dialog genuinely runs."""
    server = FakeGarageServer({"status": "ok"}).start()  # plain http, real socket
    try:
        script = textwrap.dedent(
            f"""
            import tkinter as tk
            from tkinter import ttk

            def walk(w):
                for c in w.winfo_children():
                    yield c
                    yield from walk(c)

            def fake_mainloop(self):
                entries = [w for w in walk(self) if isinstance(w, ttk.Entry)]
                assert len(entries) == 3, len(entries)
                entries[0].delete(0, "end"); entries[0].insert(0, "{server.url}")
                entries[1].insert(0, "my-token"); entries[2].insert(0, "my-secret")
                [w for w in walk(self) if isinstance(w, ttk.Button)][0].invoke()

            tk.Tk.mainloop = fake_mainloop
            from client.gui.setup_dialog import run_setup_dialog
            cfg = run_setup_dialog("https://prefill.example")
            print("RESULT", cfg.api_url, cfg.auth_token, cfg.client_secret)
            """
        )
        result = subprocess.run(["xvfb-run", "-a", sys.executable, "-c", script], cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=60)
    finally:
        server.stop()
    assert f"RESULT {server.url} my-token my-secret" in result.stdout, result.stdout + result.stderr


# ------------------------------------------- update link handling (§12)

@pytest.mark.parametrize(
    "url,safe",
    [
        ("https://example.com/download", True),
        ("http://nas.local/garage16.exe", True),
        ("javascript:alert(1)", False),
        ("file:///C:/Windows/System32/cmd.exe", False),
        ("ftp://example.com/x", False),
        ("https://", False),
        ("", False),
        (None, False),
        ("https://example.com/" + "a" * 3000, False),
    ],
)
def test_download_url_safety_check(url, safe):
    from client.update_notice import is_safe_download_url

    assert is_safe_download_url(url) is safe


def test_server_check_only_offers_a_link_that_passes_validation(tmp_path, monkeypatch):
    from client.server_check import run_server_check

    good = FakeGarageServer({"status": "ok", "min_client_version": "9.0.0", "client_download_url": "https://example.com/dl"}).start()
    bad = FakeGarageServer({"status": "ok", "min_client_version": "9.0.0", "client_download_url": "javascript:alert(1)"}).start()
    try:
        ok = run_server_check(ClientConfig(good.url, "t", "s"), "0.8.6")
        evil = run_server_check(ClientConfig(bad.url, "t", "s"), "0.8.6")
    finally:
        good.stop()
        bad.stop()
    assert ok.notice_url == "https://example.com/dl"
    assert evil.update_required is True and evil.notice_url is None  # still told to update, but no button


@needs_xvfb
def test_real_window_download_button_only_appears_for_safe_links():
    script = textwrap.dedent(
        """
        import queue
        from client.gui.app import GarageApp
        from client.gui.status import StatusUpdate

        app = GarageApp()
        def drain():
            while True:
                try: app._apply(app._queue.get_nowait())
                except queue.Empty: break
        app.push(StatusUpdate(log_only=True, notice="Update needed", notice_url="https://example.com/dl")); drain(); app.root.update()
        assert app.notice_button.winfo_ismapped(), "safe link should show the button"
        app.push(StatusUpdate(log_only=True, notice="Update needed", notice_url="javascript:alert(1)")); drain(); app.root.update()
        assert not app.notice_button.winfo_ismapped(), "unsafe link must NOT show the button"
        app.push(StatusUpdate(log_only=True, notice="Update needed", notice_url="https://example.com/dl")); drain(); app.root.update()
        app.push(StatusUpdate(log_only=True, notice="", notice_url="")); drain(); app.root.update()
        assert not app.notice_button.winfo_ismapped() and not app.notice_label.winfo_ismapped()
        print("BUTTON-OK")
        """
    )
    result = subprocess.run(["xvfb-run", "-a", sys.executable, "-c", script], cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=60)
    assert "BUTTON-OK" in result.stdout, result.stdout + result.stderr
