"""client/gui/setup_dialog.py — GUI first-run / reconfigure dialog (V0.8.6).

Replaces the console `input()` wizard for the friend-facing app: a
packaged Windows build has no terminal at all, so setup has to happen in
a window. The validation logic (`validate_setup_input`) is deliberately a
plain function separate from the Tkinter widget code — it needs no
display, so it's directly unit-testable (tests/test_client_setup.py);
only `run_setup_dialog` itself touches Tkinter.

If the person building the package baked a server URL in
(client/config.py's garage16_server.txt), the URL field arrives
pre-filled and — since friends usually shouldn't touch it — the dialog
shows it as a normal editable field but leads with the two things a
friend actually has to provide: auth token and client secret.
"""

from __future__ import annotations

from typing import Optional, Tuple
from urllib.parse import urlparse

import requests

from client.config import ClientConfig


def normalize_server_url(raw: str) -> str:
    """Friendly input handling: trims whitespace and trailing slashes,
    and assumes https:// when a bare hostname is typed (the overwhelmingly
    likely intent for a public/tunnelled server; anyone deliberately
    running plain HTTP on a LAN has to type `http://` explicitly)."""
    url = raw.strip()
    if url and "://" not in url:
        url = "https://" + url
    # Strip trailing slashes only AFTER the scheme is settled — doing it
    # first turned a bare "https://" into "https:" and then into the
    # bogus-but-valid-looking "https://https:".
    return url.rstrip("/")


def validate_setup_input(api_url: str, auth_token: str, client_secret: str) -> Tuple[Optional[ClientConfig], Optional[str]]:
    """Returns (config, None) on success or (None, human-readable error).
    Errors are written for a non-technical reader — say what to fix, not
    what the parser choked on."""
    url = normalize_server_url(api_url)
    auth_token = auth_token.strip()
    client_secret = client_secret.strip()

    if not url:
        return None, "Please enter the server address you were given."
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None, "That server address doesn't look right. It should look like https://garage16.example.com"
    if not auth_token:
        return None, "Please paste your Auth token (shown once when you created your account)."
    if not client_secret:
        return None, "Please paste your Client secret (shown together with the Auth token)."
    return ClientConfig(api_url=url, auth_token=auth_token, client_secret=client_secret), None


def check_server_reachable(api_url: str, timeout: float = 5) -> bool:
    try:
        resp = requests.get(f"{api_url.rstrip('/')}/health", timeout=timeout)
        return resp.status_code == 200
    except requests.RequestException:
        return False


def run_setup_dialog(default_api_url: str, parent=None) -> Optional[ClientConfig]:
    """Blocks until the user connects or closes the window. Returns the
    ClientConfig, or None if they closed it without finishing. `parent`
    (an existing Tk window) makes this a modal Toplevel of it — used by
    the running app's 'Reconnect account' button; without one (first run,
    before any main window exists) it creates its own root."""
    import tkinter as tk
    from tkinter import ttk

    root = tk.Tk() if parent is None else tk.Toplevel(parent)
    root.title("Garage16 — Connect your account")
    root.geometry("440x360")
    root.resizable(False, False)

    result: dict = {"config": None}
    unreachable_warned = {"value": False}

    frame = ttk.Frame(root, padding=16)
    frame.pack(fill="both", expand=True)

    ttk.Label(frame, text="Connect Garage16 to your account", font=("Segoe UI", 13, "bold")).pack(anchor="w")
    ttk.Label(
        frame,
        text="Log in on the Garage16 website, open Account, and copy your\nAuth token and Client secret into the boxes below.",
        justify="left",
    ).pack(anchor="w", pady=(4, 12))

    def add_field(label: str, initial: str = "", secret: bool = False) -> tk.StringVar:
        ttk.Label(frame, text=label).pack(anchor="w")
        var = tk.StringVar(value=initial)
        ttk.Entry(frame, textvariable=var, show="•" if secret else "", width=52).pack(anchor="w", pady=(0, 8))
        return var

    url_var = add_field("Server address", default_api_url)
    token_var = add_field("Auth token", secret=True)
    secret_var = add_field("Client secret", secret=True)

    error_var = tk.StringVar(value="")
    tk.Label(frame, textvariable=error_var, fg="#c0392b", wraplength=400, justify="left").pack(anchor="w", pady=(0, 6))

    button_var = tk.StringVar(value="Connect")

    def on_connect() -> None:
        config, error = validate_setup_input(url_var.get(), token_var.get(), secret_var.get())
        if error:
            error_var.set(error)
            unreachable_warned["value"] = False
            button_var.set("Connect")
            return
        if not unreachable_warned["value"] and not check_server_reachable(config.api_url):
            error_var.set(
                "Couldn't reach that server. Check the address and your internet connection. "
                "Click again to save these settings anyway."
            )
            unreachable_warned["value"] = True
            button_var.set("Save anyway")
            return
        result["config"] = config
        root.destroy()

    ttk.Button(frame, textvariable=button_var, command=on_connect).pack(anchor="e")
    root.protocol("WM_DELETE_WINDOW", root.destroy)
    if parent is None:
        root.mainloop()
    else:
        root.transient(parent)
        root.grab_set()
        parent.wait_window(root)
    return result["config"]
