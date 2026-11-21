"""client/gui/setup_dialog.py — GUI first-run / reconfigure dialog.

Replaces the console `input()` wizard for the friend-facing app: a
packaged Windows build has no terminal at all, so setup has to happen in
a window. The validation logic (`validate_setup_input`) is deliberately a
plain function separate from the Tkinter widget code — it needs no
display, so it's directly unit-testable (tests/client/test_client_setup.py);
only `run_setup_dialog` itself touches Tkinter.

If the person building the package baked a server URL in
(client/config.py's garage16_server.txt), the URL field arrives
pre-filled and — since friends usually shouldn't touch it — the dialog
shows it as a normal editable field.

The driver signs in with the email and password of their Garage16
account (POST /accounts/desktop-login hands the client its credentials) —
nothing to copy from the website any more. Pasting an auth token and client
secret is still there, folded away as "advanced" (accounts without a
password, servers older than 1.0). Links to the website's sign-up, setup
guide and privacy policy sit right in the window.

The client sends this PC's name (each PC gets its own token, so the rig and a laptop can
both stay connected), understands the server's two new answers — "confirm your email first" and
"accept the changed terms" (a checkbox appears) — and warns before a password would travel over
plain http:// to anything but this computer.
"""

from __future__ import annotations

import re
import socket
from typing import Optional, Tuple
from urllib.parse import urlparse

import requests

from client.config import ClientConfig

LOGIN_TIMEOUT_S = 15
_HOST_RE = re.compile(r"^[A-Za-z0-9.-]+(:\d{1,5})?$")


def validate_login_input(api_url: str, email: str, password: str) -> Tuple[Optional[str], Optional[str]]:
    """(normalized server url, None) or (None, error for a non-technical reader)."""
    url = normalize_server_url(api_url)
    if not url:
        return None, "Please enter the server address you were given."
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None, "That server address doesn't look right. It should look like https://garage16.example.com"
    if not email.strip() or "@" not in email:
        return None, "Please enter the email address of your Garage16 account."
    if not password:
        return None, "Please enter your password."
    return url, None


class LoginError(str):
    """The message for the driver (a str, as before) plus the server's code when it sent one
    ("email_not_verified", "terms_required") — the dialog reacts to the code."""

    code: Optional[str] = None

    def __new__(cls, message: str, code: Optional[str] = None):
        obj = super().__new__(cls, message)
        obj.code = code
        return obj


_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]"}


def insecure_http(api_url: str) -> bool:
    """True if the password would go unencrypted to another computer (http://, not this PC)."""
    parsed = urlparse(normalize_server_url(api_url))
    return parsed.scheme == "http" and (parsed.hostname or "") not in _LOCAL_HOSTS


def this_pc_name() -> str:
    """How this PC appears in Account → Connected PCs (the computer name; "PC" if unknown)."""
    try:
        name = socket.gethostname().strip()
    except OSError:
        name = ""
    return name[:80] or "PC"


def desktop_login(api_url: str, email: str, password: str, timeout: float = LOGIN_TIMEOUT_S,
                  accept_terms: bool = False, device_name: Optional[str] = None) -> Tuple[Optional[ClientConfig], Optional[str]]:
    """Signs in with email + password (POST /accounts/desktop-login, V1.0) and returns the client's
    configuration, or (None, what to tell the driver — a LoginError). Never raises for network or
    server errors."""
    from client.uploader.uploader import CLIENT_VERSION

    from client.api_paths import fall_back, url_for

    body = {"email": email.strip(), "password": password, "accept_terms": bool(accept_terms),
            "device_name": (device_name or this_pc_name())[:80], "client_version": CLIENT_VERSION}
    path = "/api/v1/accounts/desktop-login"
    try:
        resp = requests.post(url_for(api_url, path), json=body, timeout=timeout)
        if fall_back(api_url, path, resp):      # A server before 1.0.2 (client/api_paths.py)
            resp = requests.post(url_for(api_url, path), json=body, timeout=timeout)
    except requests.RequestException:
        return None, "Couldn't reach that server. Check the address and your internet connection, then try again."
    if resp.status_code == 200:
        try:
            body = resp.json()
            token, secret = body["auth_token"], body["client_secret"]
        except (ValueError, KeyError, TypeError):
            return None, "The server sent an unexpected answer. Please try again later."
        if not (isinstance(token, str) and token and isinstance(secret, str) and secret):
            return None, "The server sent an unexpected answer. Please try again later."
        return ClientConfig(api_url=api_url.rstrip("/"), auth_token=token, client_secret=secret), None
    if resp.status_code == 401:
        return None, LoginError("Email or password is wrong. Use the ones you signed up with on the Garage16 website.")
    if resp.status_code == 403:
        return None, LoginError("This account has been locked. Please contact whoever runs the Garage16 server.")
    if resp.status_code == 409:
        # {"detail": {"code": ..., "message": ...}}
        try:
            detail = resp.json().get("detail")
        except (ValueError, AttributeError):
            detail = None
        if isinstance(detail, dict):
            code = detail.get("code")
            if code == "email_not_verified":
                return None, LoginError(
                    "Please confirm your email address first: open the link in the email we sent you (check the "
                    "spam folder). On the website under Account you can send a new link. Then click Connect again.",
                    code)
            if code == "terms_required":
                return None, LoginError(
                    "The terms of use and the privacy policy have changed. Read them (link below), tick "
                    "\"I accept…\" and click Connect again.", code)
            if isinstance(detail.get("message"), str):
                return None, LoginError(detail["message"][:300], code)
    if resp.status_code == 429:
        return None, LoginError("Too many attempts. Please wait a few minutes and try again.")
    if resp.status_code in (404, 405):
        return None, LoginError("This server does not support signing in with email (it is older than Garage16 1.0). "
                                "Tick \"Use an auth token instead\" and paste your token and secret.")
    return None, LoginError(f"The server answered with an error ({resp.status_code}). Please try again later.")


def website_link(api_url: str, page: str) -> Optional[str]:
    """The website page for a link in the window (#/account, #/setup, #/legal?...), or None for an
    address that is not a plain http(s) URL — never hand anything else to the browser."""
    url = normalize_server_url(api_url)
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not _HOST_RE.match(parsed.netloc):
        return None
    return f"{url}/#/{page}"


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
    import webbrowser
    from tkinter import ttk

    root = tk.Tk() if parent is None else tk.Toplevel(parent)
    if parent is None:
        from client.icon import apply_to_tk

        apply_to_tk(root)   # V0.9.4
    root.title("Garage16 — Connect your account")
    root.resizable(False, False)

    result: dict = {"config": None}
    unreachable_warned = {"value": False}

    frame = ttk.Frame(root, padding=16)
    frame.pack(fill="both", expand=True)

    ttk.Label(frame, text="Connect Garage16 to your account", font=("Segoe UI", 13, "bold")).pack(anchor="w")
    ttk.Label(
        frame,
        text="Sign in with the email and password of your Garage16 account.\nNo account yet? Create one on the website first — it takes a minute.",
        justify="left",
    ).pack(anchor="w", pady=(4, 12))

    def add_field(parent_frame, label: str, initial: str = "", secret: bool = False) -> tk.StringVar:
        ttk.Label(parent_frame, text=label).pack(anchor="w")
        var = tk.StringVar(value=initial)
        ttk.Entry(parent_frame, textvariable=var, show="•" if secret else "", width=52).pack(anchor="w", pady=(0, 8))
        return var

    url_var = add_field(frame, "Server address", default_api_url)
    login_frame = ttk.Frame(frame)
    login_frame.pack(anchor="w", fill="x")
    email_var = add_field(login_frame, "Email")
    password_var = add_field(login_frame, "Password", secret=True)

    # The old way, folded away — paste the auth token and client secret
    token_frame = ttk.Frame(frame)
    token_var = add_field(token_frame, "Auth token", secret=True)
    secret_var = add_field(token_frame, "Client secret", secret=True)
    use_token = tk.BooleanVar(value=False)

    terms_var = tk.BooleanVar(value=False)
    insecure_warned = {"value": False}

    error_var = tk.StringVar(value="")
    button_var = tk.StringVar(value="Connect")

    def toggle_token() -> None:
        error_var.set("")
        unreachable_warned["value"] = False
        button_var.set("Connect")
        if use_token.get():
            login_frame.pack_forget()
            token_frame.pack(anchor="w", fill="x", before=advanced)
        else:
            token_frame.pack_forget()
            login_frame.pack(anchor="w", fill="x", before=advanced)

    advanced = ttk.Checkbutton(frame, text="Use an auth token instead (advanced)", variable=use_token, command=toggle_token)
    advanced.pack(anchor="w", pady=(0, 6))
    # Shown (below the password) only when the server says the terms changed
    # (LoginError code "terms_required")
    terms_box = ttk.Checkbutton(frame, text="I accept the terms of use and have read the privacy policy",
                                variable=terms_var)

    tk.Label(frame, textvariable=error_var, fg="#c0392b", wraplength=400, justify="left").pack(anchor="w", pady=(0, 6))

    links = ttk.Frame(frame)
    links.pack(anchor="w", fill="x", pady=(0, 10))

    def add_link(text: str, page: str) -> None:
        label = tk.Label(links, text=text, fg="#2a6fdb", cursor="hand2", font=("Segoe UI", 9, "underline"))
        label.pack(side="left", padx=(0, 14))

        def open_page(_event=None) -> None:
            url = website_link(url_var.get(), page)
            if url:
                webbrowser.open(url)

        label.bind("<Button-1>", open_page)

    add_link("Create an account", "account")
    add_link("Setup guide", "setup")
    add_link("Terms of use", "legal?tab=terms")
    add_link("Privacy", "legal?tab=privacy")

    def on_connect() -> None:
        if use_token.get():
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
            return
        url, error = validate_login_input(url_var.get(), email_var.get(), password_var.get())
        if error:
            error_var.set(error)
            return
        if insecure_http(url) and not insecure_warned["value"]:
            error_var.set("This address starts with http:// — your password would be sent unencrypted. "
                          "Use https:// if you can, or click Connect again to continue anyway.")
            insecure_warned["value"] = True
            return
        error_var.set("Connecting…")
        root.update_idletasks()
        config, error = desktop_login(url, email_var.get(), password_var.get(), accept_terms=terms_var.get())
        if error:
            error_var.set(error)
            if getattr(error, "code", None) == "terms_required" and not terms_box.winfo_ismapped():
                terms_box.pack(anchor="w", pady=(0, 8), after=login_frame)
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
