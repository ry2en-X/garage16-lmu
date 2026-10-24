"""V1.0 — the desktop client connects with the email and password of the Garage16 account
(POST /accounts/desktop-login) instead of a pasted token and secret."""

from __future__ import annotations

import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
import requests

from client.gui.setup_dialog import desktop_login, validate_login_input, website_link
from tests.local_servers import FakeGarageServer, RealAppServer

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PASSWORD = "Correct-Horse-42-battery"
needs_xvfb = pytest.mark.skipif(shutil.which("xvfb-run") is None, reason="xvfb-run not available — GUI tests skipped, not faked")


@pytest.fixture()
def real_server(client):   # `client` gives a fresh database and drops it afterwards
    server = RealAppServer().start()
    try:
        yield server
    finally:
        server.stop()


def _signup(server, email="rita@example.com"):
    resp = requests.post(f"{server.url}/accounts/signup", timeout=10, json={
        "display_name": "Rita", "email": email, "password": PASSWORD, "accept_terms": True, "confirm_min_age": True})
    assert resp.status_code == 200, resp.text


@pytest.mark.parametrize("url,email,password,ok", [
    ("garage16.de", "a@b.de", "x", True),
    ("", "a@b.de", "x", False),
    ("ftp://garage16.de", "a@b.de", "x", False),
    ("https://garage16.de", "no-at-sign", "x", False),
    ("https://garage16.de", "a@b.de", "", False),
])
def test_validate_login_input(url, email, password, ok):
    normalized, error = validate_login_input(url, email, password)
    assert (error is None) is ok
    if ok:
        assert normalized == "https://garage16.de"


def test_website_links_only_for_plain_http_addresses():
    assert website_link("https://garage16.de/", "legal?tab=privacy") == "https://garage16.de/#/legal?tab=privacy"
    assert website_link("javascript:alert(1)", "setup") is None
    assert website_link("", "setup") is None


def test_desktop_login_against_the_real_server(real_server):
    _signup(real_server)
    config, error = desktop_login(real_server.url, "  Rita@Example.com ", PASSWORD)
    assert error is None and config.api_url == real_server.url
    who = requests.get(f"{real_server.url}/accounts/me", headers={"Authorization": f"Bearer {config.auth_token}"}, timeout=10)
    assert who.status_code == 200 and who.json()["display_name"] == "Rita"
    config, error = desktop_login(real_server.url, "rita@example.com", "wrong-password-123")
    assert config is None and "wrong" in error


def test_desktop_login_explains_unreachable_and_old_servers():
    config, error = desktop_login("http://127.0.0.1:9", "a@b.de", "x", timeout=2)
    assert config is None and "Couldn't reach" in error
    old = FakeGarageServer({"status": "ok"}).start()   # no /accounts/desktop-login: a pre-1.0 server
    try:
        config, error = desktop_login(old.url, "a@b.de", "x")
    finally:
        old.stop()
    assert config is None and "older than Garage16 1.0" in error and "auth token" in error


@needs_xvfb
def test_real_setup_dialog_signs_in_with_email_and_password(real_server):
    _signup(real_server, "gui@example.com")
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
            entries[1].insert(0, "gui@example.com"); entries[2].insert(0, "{PASSWORD}")
            links = [w.cget("text") for w in walk(self) if isinstance(w, tk.Label) and w.cget("cursor") == "hand2"]
            print("LINKS", "|".join(links))
            [w for w in walk(self) if isinstance(w, ttk.Button)][0].invoke()

        tk.Tk.mainloop = fake_mainloop
        from client.gui.setup_dialog import run_setup_dialog
        cfg = run_setup_dialog("https://prefill.example")
        print("RESULT", cfg.api_url, len(cfg.auth_token) > 20, len(cfg.client_secret) > 20)
        """
    )
    result = subprocess.run(["xvfb-run", "-a", sys.executable, "-c", script], cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=60)
    assert f"RESULT {real_server.url} True True" in result.stdout, result.stdout + result.stderr
    assert "LINKS Create an account|Setup guide|Privacy & legal" in result.stdout
