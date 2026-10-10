"""V0.9.2: the two client editions and the analysis app window.

client/edition.py (which edition, which view), client/analysis_launcher.py (what the
"Open analysis" buttons do), client/analysis_window.py (the window's bridge to the page),
the uploader remembering the last uploaded lap, and the real Tk window's new buttons.
The real WebView2 window can only be shown on Windows — see docs/CLIENT_BUILD.md.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import textwrap
import types
from pathlib import Path

import pytest

import client.config as client_config
from client import edition as ed
from client.analysis_launcher import AnalysisLauncher
from client.analysis_window import AppBridge
from client.config import ClientConfig

PROJECT_ROOT = Path(__file__).resolve().parent.parent
needs_xvfb = pytest.mark.skipif(shutil.which("xvfb-run") is None, reason="xvfb-run not available — GUI tests skipped, not faked")

CONFIG = ClientConfig(api_url="https://garage16.example", auth_token="desk-token", client_secret="s3cret")


@pytest.fixture()
def config_file(tmp_path, monkeypatch):
    path = tmp_path / "client_config.json"
    path.write_text(json.dumps({"api_url": CONFIG.api_url, "auth_token": "t", "client_secret": "s",
                                "local_retention_days": 0}), encoding="utf-8")
    monkeypatch.setattr(client_config, "CONFIG_PATH", path)
    return path


@pytest.fixture()
def analysis_edition(monkeypatch):
    monkeypatch.setenv("LMU_GARAGE_EDITION", "analysis")


@pytest.fixture(autouse=True)
def _no_edition_env(monkeypatch):
    monkeypatch.delenv("LMU_GARAGE_EDITION", raising=False)


# ---------------------------------------------------------------- edition

def test_the_edition_comes_from_the_file_baked_next_to_the_executable(tmp_path, monkeypatch):
    marker = tmp_path / "garage16_edition.txt"
    monkeypatch.setattr(ed, "bundled_file_candidates", lambda name: [tmp_path / "missing.txt", marker])
    assert ed.edition() == "standard", "no file: the plain client"
    marker.write_text("analysis\n", encoding="utf-8")
    assert ed.edition() == "analysis"
    assert ed.edition_label() == "Client + analysis app"
    marker.write_text("something else", encoding="utf-8")
    assert ed.edition() == "standard", "an unknown value is never trusted"
    monkeypatch.setenv("LMU_GARAGE_EDITION", "analysis")
    assert ed.edition() == "analysis", "source runs can choose with LMU_GARAGE_EDITION"


def test_the_standard_edition_always_uses_the_browser(config_file):
    client_config.write_setting("analysis_view", "app")
    assert ed.preferred_view() == "browser"


def test_the_analysis_edition_uses_its_window_unless_the_driver_prefers_the_browser(config_file, analysis_edition):
    assert ed.preferred_view() == "app"
    ed.set_preferred_view("browser")
    assert ed.preferred_view() == "browser"
    saved = json.loads(config_file.read_text(encoding="utf-8"))
    assert saved["analysis_view"] == "browser" and saved["local_retention_days"] == 0 and saved["auth_token"] == "t", \
        "the setting is saved next to everything else, nothing else changes"
    ed.set_preferred_view("app")
    assert ed.preferred_view() == "app"
    with pytest.raises(ValueError):
        ed.set_preferred_view("tab")


def test_settings_before_setup_are_ignored_not_crashing(tmp_path, monkeypatch):
    monkeypatch.setattr(client_config, "CONFIG_PATH", tmp_path / "nope.json")
    client_config.write_setting("analysis_view", "browser")
    assert not (tmp_path / "nope.json").exists()
    assert client_config.read_setting("analysis_view", "app") == "app"


def test_save_config_keeps_the_view_setting(config_file, analysis_edition):
    ed.set_preferred_view("browser")
    client_config.save_config(ClientConfig(api_url="https://new.example", auth_token="t2", client_secret="s2"))
    assert ed.preferred_view() == "browser", "reconnecting an account must not reset the choice"


def test_analysis_urls_point_at_the_drivers_own_server():
    assert ed.analysis_url("https://garage16.de/") == "https://garage16.de/#/analysis"
    assert ed.analysis_url("https://garage16.de", 42) == "https://garage16.de/#/analysis?a=42"
    with pytest.raises((TypeError, ValueError)):
        ed.analysis_url("https://garage16.de", "42; rm")


def test_same_origin():
    assert ed.same_origin("https://garage16.de/#/analysis", "https://garage16.de")
    assert ed.same_origin("https://GARAGE16.de/x", "https://garage16.de/")
    assert not ed.same_origin("https://garage16.de.evil.example/", "https://garage16.de")
    assert not ed.same_origin("http://garage16.de/", "https://garage16.de")
    assert not ed.same_origin("", "https://garage16.de")
    assert not ed.same_origin("about:blank", "https://garage16.de")


def test_the_window_runs_as_its_own_process(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", r"C:\Garage16\Garage16.exe")
    assert ed.analysis_window_command(7) == [r"C:\Garage16\Garage16.exe", "--analysis-window", "--lap", "7"]
    monkeypatch.setattr(sys, "frozen", False)
    assert ed.analysis_window_command()[1:] == ["-m", "client.main", "--analysis-window"]


def test_why_the_window_cannot_be_shown(monkeypatch, analysis_edition):
    monkeypatch.setattr(ed, "webview_bundled", lambda: False)
    assert "reinstall" in ed.analysis_window_problem()
    monkeypatch.setattr(ed, "webview_bundled", lambda: True)
    fake_os = types.SimpleNamespace(name="nt", environ={"LMU_GARAGE_EDITION": "analysis"})
    monkeypatch.setattr(ed, "os", fake_os)
    monkeypatch.setattr(ed, "webview2_runtime_version", lambda: None)
    problem = ed.analysis_window_problem()
    assert "Microsoft Edge WebView2 Runtime" in problem
    monkeypatch.setattr(ed, "webview2_runtime_version", lambda: "131.0.2903.70")
    assert ed.analysis_window_problem() is None
    fake_os.environ["LMU_GARAGE_EDITION"] = "standard"
    assert "without the analysis app" in ed.analysis_window_problem()


def test_an_outdated_client_is_sent_to_its_own_editions_download(monkeypatch):
    body = {"client_download_url": "https://dl/standard", "client_app_download_url": "https://dl/app"}
    assert ed.download_url_for_this_edition(body, "client_download_url", "client_app_download_url") == "https://dl/standard"
    monkeypatch.setenv("LMU_GARAGE_EDITION", "analysis")
    assert ed.download_url_for_this_edition(body, "client_download_url", "client_app_download_url") == "https://dl/app"
    body.pop("client_app_download_url")
    assert ed.download_url_for_this_edition(body, "client_download_url", "client_app_download_url") == "https://dl/standard"
    assert ed.download_url_for_this_edition(None, "a", "b") is None


# ---------------------------------------------------------------- launcher

class FakeProcess:
    def __init__(self, command, **kwargs):
        self.command, self.kwargs, self.alive, self.terminated = command, kwargs, True, False

    def poll(self):
        return None if self.alive else 0

    def terminate(self):
        self.terminated, self.alive = True, False


def _launcher(monkeypatch, view="app", problem=None):
    started, opened, logs = [], [], []
    monkeypatch.setattr("client.analysis_launcher.preferred_view", lambda: view)
    monkeypatch.setattr("client.analysis_launcher.analysis_window_problem", lambda: problem)

    def popen(command, **kwargs):
        started.append(FakeProcess(command, **kwargs))
        return started[-1]

    launcher = AnalysisLauncher(api_url=lambda: "https://garage16.example", log=logs.append,
                                popen=popen, open_browser=opened.append)
    return launcher, started, opened, logs


def test_the_browser_preference_opens_the_web_analysis(monkeypatch):
    launcher, started, opened, _ = _launcher(monkeypatch, view="browser")
    assert launcher.open(12) == "browser"
    assert opened == ["https://garage16.example/#/analysis?a=12"] and started == []


def test_the_app_preference_opens_one_window_process(monkeypatch):
    launcher, started, opened, logs = _launcher(monkeypatch)
    assert launcher.open() == "window"
    assert "--analysis-window" in started[0].command and opened == []
    assert launcher.open() == "already-open", "a second click does not open a second window"
    assert len(started) == 1 and "already open" in logs[-1]
    assert launcher.open(5) == "window", "asked for a specific lap, the window is reopened on it"
    assert started[0].terminated and started[1].command[-2:] == ["--lap", "5"]
    started[1].alive = False                                  # the driver closed it
    assert launcher.open() == "window" and len(started) == 3
    launcher.close()
    assert started[2].terminated, "quitting the client closes its analysis window"


def test_without_webview2_the_browser_opens_and_the_driver_is_told_once(monkeypatch):
    launcher, started, opened, logs = _launcher(monkeypatch, problem="The analysis window needs the Microsoft Edge WebView2 Runtime …")
    assert launcher.open() == "browser" and launcher.open(3) == "browser"
    assert started == [] and len(opened) == 2
    assert len(logs) == 1 and "WebView2" in logs[0]


def test_a_window_that_cannot_start_falls_back_to_the_browser(monkeypatch):
    launcher, _, opened, logs = _launcher(monkeypatch)

    def broken(command, **kwargs):
        raise OSError("not found")

    launcher._popen = broken
    assert launcher.open(9) == "browser"
    assert opened == ["https://garage16.example/#/analysis?a=9"] and "could not be started" in logs[-1]


# ---------------------------------------------------------------- bridge

class FakeResponse:
    def __init__(self, status, body=None):
        self.status_code, self._body = status, body

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


def _bridge(url="https://garage16.example/#/analysis", response=None, error=None):
    calls, opened = [], []

    def post(target, headers, timeout):
        calls.append((target, headers))
        if error:
            raise error
        return response

    return AppBridge(CONFIG, current_url=lambda: url, post=post, open_browser=opened.append), calls, opened


def test_the_bridge_signs_the_window_in_with_the_desktop_credential():
    bridge, calls, _ = _bridge(response=FakeResponse(200, {"session_token": "web-tok", "driver_id": 3,
                                                           "display_name": "Michael", "expires_in": 1}))
    assert bridge.app_session() == {"session_token": "web-tok", "driver_id": 3, "display_name": "Michael"}
    assert calls == [("https://garage16.example/accounts/app-session", {"Authorization": "Bearer desk-token"})]


def test_the_bridge_only_answers_the_drivers_own_server():
    for url in ("https://evil.example/", "https://garage16.example.evil.example/", "about:blank", None):
        bridge, calls, _ = _bridge(url=url, response=FakeResponse(200, {}))
        assert "error" in bridge.app_session() and calls == [], url


def test_the_bridge_exposes_no_credential_to_the_page():
    bridge, _, _ = _bridge()
    public = sorted(name for name in dir(bridge) if not name.startswith("_"))
    assert public == ["app_info", "app_session", "open_external"]
    assert "desk-token" not in json.dumps(bridge.app_info())


@pytest.mark.parametrize("response,error,expected", [
    (FakeResponse(401), None, "Reconnect account"),
    (FakeResponse(403), None, "Reconnect account"),
    (FakeResponse(429), None, "Too many"),
    (FakeResponse(502), None, "HTTP 502"),
    (FakeResponse(200, ValueError("no json")), None, "unexpected"),
    (FakeResponse(200, {"driver_id": 1}), None, "unexpected"),
    (None, __import__("requests").ConnectionError("down"), "could not be reached"),
])
def test_the_bridge_reports_problems_in_words(response, error, expected):
    bridge, _, _ = _bridge(response=response, error=error)
    result = bridge.app_session()
    assert set(result) == {"error"} and expected in result["error"]


def test_external_links_open_in_the_browser_only_when_they_are_http():
    bridge, _, opened = _bridge()
    assert bridge.open_external("https://discord.com/invite/x") is True
    for bad in ("javascript:alert(1)", "file:///C:/Windows", "ms-settings:", "", "https://" + "a" * 3000):
        assert bridge.open_external(bad) is False
    assert opened == ["https://discord.com/invite/x"]


# ---------------------------------------------------------------- window process

def test_without_pywebview_the_window_opens_the_browser(monkeypatch):
    from client import analysis_window

    opened = []
    monkeypatch.setitem(sys.modules, "webview", None)          # import webview -> ImportError
    monkeypatch.setattr(analysis_window.webbrowser, "open", opened.append)
    assert analysis_window.run(CONFIG, 4) == 0
    assert opened == ["https://garage16.example/#/analysis?a=4"]


def test_the_window_is_created_with_the_bridge_and_persistent_storage(monkeypatch, tmp_path):
    from client import analysis_window

    seen = {}
    fake = types.ModuleType("webview")
    fake.settings = {}

    class Window:
        def get_current_url(self):
            return "https://garage16.example/#/analysis"

    def create_window(title, url, **kwargs):
        seen.update(title=title, url=url, **kwargs)
        return Window()

    def start(**kwargs):
        seen["start"] = kwargs
        seen["bridge_answer_origin_ok"] = kwargs and seen["js_api"]._page_is_ours()

    fake.create_window, fake.start = create_window, start
    monkeypatch.setitem(sys.modules, "webview", fake)
    monkeypatch.setattr(analysis_window, "storage_dir", lambda: tmp_path / "analysis_window")
    assert analysis_window.run(CONFIG) == 0
    assert seen["url"] == "https://garage16.example/#/analysis" and seen["title"] == "Garage16 — Analysis"
    assert isinstance(seen["js_api"], AppBridge) and seen["bridge_answer_origin_ok"] is True
    assert seen["start"]["private_mode"] is False and seen["start"]["storage_path"] == str(tmp_path / "analysis_window")
    assert fake.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] is True
    assert (tmp_path / "analysis_window").is_dir()


def test_a_window_that_fails_to_start_falls_back_to_the_browser(monkeypatch, tmp_path):
    from client import analysis_window

    fake = types.ModuleType("webview")
    fake.settings = {}
    fake.create_window = lambda *a, **k: object()

    def start(**kwargs):
        raise RuntimeError("WebView2 failed")

    fake.start = start
    opened = []
    monkeypatch.setitem(sys.modules, "webview", fake)
    monkeypatch.setattr(analysis_window, "storage_dir", lambda: tmp_path / "w")
    monkeypatch.setattr(analysis_window.webbrowser, "open", opened.append)
    assert analysis_window.run(CONFIG, 2) == 0
    assert opened == ["https://garage16.example/#/analysis?a=2"]


# ---------------------------------------------------------------- uploader

def test_the_uploader_remembers_the_server_id_of_the_last_uploaded_lap(tmp_path, monkeypatch):
    from client.telemetry.recorder import LapRecorder
    from client.uploader import uploader as up
    from tests.test_integration_upload_flow import _write_recorded_lap

    meta = _write_recorded_lap(tmp_path)
    uploader = up.Uploader(recorder=LapRecorder(data_dir=str(tmp_path)), auth_token="t", client_secret="s")
    assert uploader.last_uploaded_lap_id is None

    def post(url, data, timeout, headers):
        for _ in data:  # consume the streamed body like a real transport would
            pass
        return FakeResponse(200, {"status": "ok", "lap_id": 1234, "duplicate": False})

    monkeypatch.setattr(up.requests, "post", post)
    assert uploader.upload_one(meta) is True
    assert uploader.last_uploaded_lap_id == 1234


def test_an_answer_without_a_lap_id_still_counts_as_uploaded(tmp_path, monkeypatch):
    from client.telemetry.recorder import LapRecorder
    from client.uploader import uploader as up
    from tests.test_integration_upload_flow import _write_recorded_lap

    meta = _write_recorded_lap(tmp_path)
    uploader = up.Uploader(recorder=LapRecorder(data_dir=str(tmp_path)), auth_token="t", client_secret="s")
    monkeypatch.setattr(up.requests, "post", lambda url, data, timeout, headers: FakeResponse(200, ValueError("html")))
    assert uploader.upload_one(meta) is True and uploader.last_uploaded_lap_id is None


def test_426_sends_the_analysis_edition_to_its_own_download(tmp_path, monkeypatch, analysis_edition):
    from client.telemetry.recorder import LapRecorder
    from client.uploader import uploader as up
    from tests.test_integration_upload_flow import _write_recorded_lap

    meta = _write_recorded_lap(tmp_path)
    uploader = up.Uploader(recorder=LapRecorder(data_dir=str(tmp_path)), auth_token="t", client_secret="s")
    detail = {"code": "UPDATE_REQUIRED", "min_client_version": "9.9.9",
              "download_url": "https://dl/standard", "app_download_url": "https://dl/app"}
    monkeypatch.setattr(up.requests, "post", lambda url, data, timeout, headers: FakeResponse(426, {"detail": detail}))
    uploader.run_once()
    assert uploader.update_required.download_url == "https://dl/app"


# ---------------------------------------------------------------- the real Tk window

@needs_xvfb
@pytest.mark.parametrize("with_choice", [False, True])
def test_real_window_offers_the_analysis_buttons(with_choice):
    script = textwrap.dedent(
        f"""
        import queue
        from client.gui.app import GarageApp
        from client.gui.status import StatusUpdate

        opened, chosen = [], []
        choice = (False, chosen.append) if {with_choice} else None
        app = GarageApp(on_reconnect=lambda: None, on_open_analysis=opened.append, analysis_choice=choice)
        def drain():
            while True:
                try: app._apply(app._queue.get_nowait())
                except queue.Empty: break
        app.root.update()
        assert str(app.last_lap_button["state"]) == "disabled", "no lap uploaded yet"
        app._open_last_lap()
        assert opened == []
        app.push(StatusUpdate(log_only=True, last_lap_id=77)); drain(); app.root.update()
        assert str(app.last_lap_button["state"]) == "normal"
        app.last_lap_button.invoke()
        assert opened == [77], opened
        assert app.track_var.get() == "Track: —", "a lap id update does not touch the telemetry fields"
        if {with_choice}:
            assert app.browser_var.get() is False
            app.browser_var.set(True)
            for child in app.root.winfo_children():
                if child.winfo_class() == "TCheckbutton":
                    child.invoke(); child.invoke()
            assert chosen and chosen[-1] in (True, False)
        else:
            assert not hasattr(app, "browser_var"), "the plain client has no app window to choose"
        print("ANALYSIS-BUTTONS-OK")
        """
    )
    result = subprocess.run(["xvfb-run", "-a", sys.executable, "-c", script], cwd=PROJECT_ROOT,
                            capture_output=True, text=True, timeout=60)
    assert "ANALYSIS-BUTTONS-OK" in result.stdout, result.stdout + result.stderr
