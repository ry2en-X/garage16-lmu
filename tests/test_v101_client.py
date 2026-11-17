"""V1.0.1 — desktop client: credentials encrypted on Windows (DPAPI, simulated here), the server's
new sign-in answers, the http:// warning, the computer name sent, the optional-update notice."""

from __future__ import annotations

import base64
import json

import pytest

import client.config as client_config
from client import secret_store
from client.config import ClientConfig, load_config, save_config
from client.gui.setup_dialog import LoginError, desktop_login, insecure_http, this_pc_name
from client.server_check import run_server_check
from tests.local_servers import FakeGarageServer, RealAppServer

PASSWORD = "Correct-Horse-42-battery"


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    config_dir = tmp_path / ".lmu_garage"
    monkeypatch.setattr(client_config, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(client_config, "CONFIG_PATH", config_dir / "client_config.json")
    for name in ("LMU_GARAGE_AUTH_TOKEN", "LMU_GARAGE_CLIENT_SECRET", "LMU_GARAGE_API_URL"):
        monkeypatch.delenv(name, raising=False)
    return config_dir / "client_config.json"


@pytest.fixture
def fake_windows(monkeypatch):
    """DPAPI stand-in: "encrypts" by reversing and tagging the bytes — enough to see what is written
    to the file and that reading goes through unprotect()."""
    monkeypatch.setattr(secret_store, "available", lambda: True)
    monkeypatch.setattr(secret_store, "_dpapi_encrypt", lambda data: b"DP" + data[::-1])

    def decrypt(data):
        if not data.startswith(b"DP"):
            raise OSError("not ours")
        return data[2:][::-1]

    monkeypatch.setattr(secret_store, "_dpapi_decrypt", decrypt)


def test_on_windows_the_credentials_are_stored_encrypted(isolated_config, fake_windows):
    save_config(ClientConfig(api_url="https://g16.example", auth_token="tok-123", client_secret="sec-456"))
    raw = json.loads(isolated_config.read_text(encoding="utf-8"))
    assert raw["auth_token"].startswith("dpapi:") and raw["client_secret"].startswith("dpapi:")
    assert "tok-123" not in isolated_config.read_text(encoding="utf-8")
    assert base64.b64decode(raw["auth_token"][6:]) == b"DP" + b"tok-123"[::-1]
    cfg = load_config()
    assert (cfg.auth_token, cfg.client_secret) == ("tok-123", "sec-456")


def test_a_plain_file_from_1_0_0_is_rewritten_encrypted(isolated_config, fake_windows):
    isolated_config.parent.mkdir(parents=True)
    isolated_config.write_text(json.dumps({"api_url": "https://g16.example", "auth_token": "tok", "client_secret": "sec",
                                           "local_retention_days": 0}), encoding="utf-8")
    cfg = load_config()
    assert (cfg.auth_token, cfg.client_secret) == ("tok", "sec")
    raw = json.loads(isolated_config.read_text(encoding="utf-8"))
    assert raw["auth_token"].startswith("dpapi:") and raw["local_retention_days"] == 0


def test_credentials_from_another_pc_count_as_missing(isolated_config, fake_windows):
    isolated_config.parent.mkdir(parents=True)
    isolated_config.write_text(json.dumps({"api_url": "https://g16.example",
                                           "auth_token": "dpapi:" + base64.b64encode(b"xx").decode(),
                                           "client_secret": "dpapi:" + base64.b64encode(b"yy").decode()}), encoding="utf-8")
    assert client_config._from_file() is None


def test_other_systems_keep_the_plain_file(isolated_config, monkeypatch):
    monkeypatch.setattr(secret_store, "available", lambda: False)
    save_config(ClientConfig(api_url="https://g16.example", auth_token="tok", client_secret="sec"))
    raw = json.loads(isolated_config.read_text(encoding="utf-8"))
    assert raw["auth_token"] == "tok"
    assert secret_store.protect("tok") == "tok" and secret_store.unprotect("tok") == "tok"
    with pytest.raises(secret_store.SecretUnavailable):
        secret_store.unprotect("dpapi:AAAA")


def test_a_failing_dpapi_never_breaks_saving(isolated_config, monkeypatch):
    monkeypatch.setattr(secret_store, "available", lambda: True)

    def broken(_data):
        raise OSError("no")

    monkeypatch.setattr(secret_store, "_dpapi_encrypt", broken)
    save_config(ClientConfig(api_url="https://g16.example", auth_token="tok", client_secret="sec"))
    assert json.loads(isolated_config.read_text(encoding="utf-8"))["auth_token"] == "tok"


# --------------------------------------------------------------- sign-in

@pytest.mark.parametrize("url,insecure", [
    ("http://192.168.178.9:8000", True), ("http://garage16.example", True),
    ("http://localhost:8000", False), ("http://127.0.0.1:8000", False),
    ("https://garage16.example", False), ("garage16.example", False),
])
def test_http_to_another_computer_is_flagged(url, insecure):
    assert insecure_http(url) is insecure


def test_the_pc_name_is_short_and_never_empty():
    name = this_pc_name()
    assert 0 < len(name) <= 80


@pytest.fixture()
def real_server(client):
    server = RealAppServer().start()
    try:
        yield server
    finally:
        server.stop()


def test_the_client_explains_an_unconfirmed_email_and_sends_its_name(real_server):
    import requests

    from server.database import SessionLocal
    from server.models import DeviceToken
    from tests.test_v100_signup import _verify

    assert requests.post(f"{real_server.url}/accounts/signup", timeout=10, json={
        "display_name": "Nina", "email": "nina@example.com", "password": PASSWORD,
        "accept_terms": True, "confirm_min_age": True}).status_code == 202
    config, error = desktop_login(real_server.url, "nina@example.com", PASSWORD)
    assert config is None and isinstance(error, LoginError) and error.code == "email_not_verified"
    assert "confirm your email" in error
    _verify("nina@example.com")
    config, error = desktop_login(real_server.url, "nina@example.com", PASSWORD, device_name="RIG-01")
    assert error is None and config.auth_token
    db = SessionLocal()
    try:
        assert [d.name for d in db.query(DeviceToken).all()] == ["RIG-01"]
    finally:
        db.close()


def test_the_client_asks_for_changed_terms(real_server, monkeypatch):
    from server.routers import legal
    from tests.test_v100_signup import _verify
    import requests

    requests.post(f"{real_server.url}/accounts/signup", timeout=10, json={
        "display_name": "Tom", "email": "tom@example.com", "password": PASSWORD, "accept_terms": True, "confirm_min_age": True})
    _verify("tom@example.com")
    monkeypatch.setattr(legal, "TERMS_VERSION", "2099-01-01")
    config, error = desktop_login(real_server.url, "tom@example.com", PASSWORD)
    assert config is None and error.code == "terms_required" and "I accept" in error
    config, error = desktop_login(real_server.url, "tom@example.com", PASSWORD, accept_terms=True)
    assert error is None and config is not None


# --------------------------------------------------------------- optional update

def _cfg(url):
    return ClientConfig(api_url=url, auth_token="t", client_secret="s")


def test_an_optional_update_is_shown_but_nothing_is_blocked():
    server = FakeGarageServer({"status": "ok", "version": "1.0.1", "min_client_version": "0.5.3",
                               "latest_client_version": "1.0.1",
                               "client_download_url": "https://example.com/Garage16-Client-Setup.exe"}).start()
    try:
        result = run_server_check(_cfg(server.url), "1.0.0")
        current = run_server_check(_cfg(server.url), "1.0.1")
    finally:
        server.stop()
    assert result.update_required is False and result.update_available is True
    assert "v1.0.1 is available" in result.notice and "keeps working" in result.notice
    assert result.notice_url == "https://example.com/Garage16-Client-Setup.exe"
    assert current.update_available is False and current.notice == ""


def test_a_required_update_is_not_announced_twice():
    server = FakeGarageServer({"status": "ok", "min_client_version": "9.0.0", "latest_client_version": "9.0.0"}).start()
    try:
        result = run_server_check(_cfg(server.url), "1.0.0")
    finally:
        server.stop()
    assert result.update_required is True and result.update_available is False
    assert "is available" not in result.notice


needs_xvfb = pytest.mark.skipif(__import__("shutil").which("xvfb-run") is None or __import__("importlib.util").util.find_spec("tkinter") is None,
                                reason="xvfb-run or tkinter not available — GUI tests skipped, not faked")


@needs_xvfb
def test_the_real_window_shows_the_terms_box_when_the_server_asks(real_server):
    """Drives the real Tkinter window: the account never accepted the current terms (a token account
    with email and password, like one from before V1.0) — the first Connect shows the box, ticking it
    connects."""
    import subprocess
    import sys
    import textwrap
    from pathlib import Path

    import requests

    from tests.test_v100_signup import _verify

    account = requests.post(f"{real_server.url}/accounts/register", params={"display_name": "Old Timer"}, timeout=10).json()
    assert requests.post(f"{real_server.url}/accounts/set-password", timeout=10, json={"email": "old@example.com", "new_password": PASSWORD},
                         headers={"Authorization": f"Bearer {account['auth_token']}"}).status_code == 200
    _verify("old@example.com")
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
            entries[0].delete(0, "end"); entries[0].insert(0, "{real_server.url}")
            entries[1].insert(0, "old@example.com"); entries[2].insert(0, "{PASSWORD}")
            button = [w for w in walk(self) if isinstance(w, ttk.Button)][0]
            terms = [w for w in walk(self) if isinstance(w, ttk.Checkbutton) and "I accept" in str(w.cget("text"))][0]
            print("BOX-BEFORE", terms.winfo_ismapped() or bool(terms.winfo_manager()))
            button.invoke()
            self.update_idletasks()
            print("BOX-AFTER", bool(terms.winfo_manager()))
            terms.invoke()
            button.invoke()

        tk.Tk.mainloop = fake_mainloop
        from client.gui.setup_dialog import run_setup_dialog
        cfg = run_setup_dialog("https://prefill.example")
        print("RESULT", cfg is not None and len(cfg.auth_token) > 20)
        """
    )
    root = Path(__file__).resolve().parent.parent
    result = subprocess.run(["xvfb-run", "-a", sys.executable, "-c", script], cwd=root, capture_output=True, text=True, timeout=60)
    out = result.stdout + result.stderr
    assert "BOX-BEFORE False" in out and "BOX-AFTER True" in out and "RESULT True" in out, out


def test_env_credentials_without_a_url_use_the_bundled_server(monkeypatch, tmp_path):
    """V1.0.1 (17.11.): token and secret from the environment without LMU_GARAGE_API_URL use the
    server baked into the package, like the setup window — not http://localhost:8000."""
    from client import config as cfg
    monkeypatch.setenv("LMU_GARAGE_AUTH_TOKEN", "tok")
    monkeypatch.setenv("LMU_GARAGE_CLIENT_SECRET", "sec")
    monkeypatch.delenv("LMU_GARAGE_API_URL", raising=False)
    bundled = tmp_path / "garage16_server.txt"
    bundled.write_text("https://garage16.example\n", encoding="utf-8")
    monkeypatch.setattr(cfg, "_bundled_server_file_candidates", lambda: [bundled])
    assert cfg._from_env().api_url == "https://garage16.example"
    monkeypatch.setenv("LMU_GARAGE_API_URL", "https://other.example")
    assert cfg._from_env().api_url == "https://other.example"
