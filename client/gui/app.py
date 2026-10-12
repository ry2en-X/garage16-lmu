"""
app.py — Minimal status window for the LMU Garage client.

Not meant to be a full dashboard (that's the website's job) — just enough
for the driver to confirm the client is connected, see the current
track/car/lap, and watch upload activity, while it runs in the background.
"""

from __future__ import annotations

import queue
import threading
import tkinter as tk
import webbrowser
from tkinter import ttk

from client.update_notice import is_safe_download_url
from client.gui.status import StatusUpdate, format_time, should_update_last_lap, should_update_telemetry_fields


class GarageApp:
    """Runs on the main thread; the telemetry/upload loops run in background
    threads and push StatusUpdate objects through a thread-safe queue."""

    def __init__(self, on_reconnect=None, on_open_analysis=None, analysis_choice=None):
        """on_open_analysis(lap_id or None): opens the analysis (V0.9.2, see
        client/main.py open_analysis). analysis_choice: None in the standard
        edition; in the "Client + analysis app" edition a pair
        (prefers_browser: bool, set_prefers_browser(bool)) for the checkbox."""
        self._on_reconnect = on_reconnect
        self._on_open_analysis = on_open_analysis
        self._analysis_choice = analysis_choice
        self._last_lap_id = None
        from client.icon import apply_to_tk, set_windows_app_id

        set_windows_app_id()          # V0.9.4: the taskbar shows the Garage16 icon, not Python's
        self.root = tk.Tk()
        apply_to_tk(self.root)
        self.root.title("Garage16 — Client")
        self.root.geometry("440x400" if analysis_choice is not None else "440x380")   # V0.9.4: three analysis buttons
        self.root.resizable(False, False)

        self._queue: "queue.Queue[StatusUpdate]" = queue.Queue()

        self._build_widgets()
        self.root.after(200, self._poll_queue)

    def _build_widgets(self) -> None:
        pad = {"padx": 12, "pady": 4}

        self.status_var = tk.StringVar(value="Disconnected")
        status_label = ttk.Label(self.root, textvariable=self.status_var, font=("Segoe UI", 12, "bold"))
        status_label.pack(anchor="w", **pad)

        # Prominent banner for things a friend must actually notice
        # (update required, server unreachable/moved) — hidden while empty.
        self.notice_var = tk.StringVar(value="")
        self.notice_label = tk.Label(
            self.root, textvariable=self.notice_var, fg="#8a4b00", bg="#fff4d6",
            wraplength=400, justify="left", anchor="w", padx=8, pady=6,
        )

        self._notice_url = ""
        self.notice_button = ttk.Button(self.root, text="Open download page", command=self._open_notice_url)

        info_frame = self.info_frame = ttk.Frame(self.root)
        info_frame.pack(fill="x", **pad)

        self.track_var = tk.StringVar(value="Track: —")
        self.car_var = tk.StringVar(value="Car: —")
        self.lap_var = tk.StringVar(value="Lap: —")
        self.last_lap_var = tk.StringVar(value="Last lap: —")

        for var in (self.track_var, self.car_var, self.lap_var):
            ttk.Label(info_frame, textvariable=var).pack(anchor="w")
        # Plain tk.Label (not ttk) so its text color can be set per-instance
        # (valid=green / invalid=red) — ttk labels need a named style for
        # that, more setup than this one line needs.
        self.last_lap_label = tk.Label(info_frame, textvariable=self.last_lap_var, anchor="w")
        self.last_lap_label.pack(anchor="w", fill="x")

        ttk.Separator(self.root, orient="horizontal").pack(fill="x", **pad)

        # V0.9.2: the analysis — in the browser, or (Client + analysis app) in its own window
        if self._on_open_analysis is not None:
            row = ttk.Frame(self.root)
            row.pack(fill="x", padx=12, pady=(0, 4))
            # V0.9.4: two dashboards — one lap in detail, or the whole session
            ttk.Button(row, text="LapTime Analyzer", command=lambda: self._on_open_analysis(None)).pack(side="left")
            ttk.Button(row, text="Stint Analyzer", command=lambda: self._on_open_analysis(None, view="stints")).pack(side="left", padx=(6, 0))
            self.last_lap_button = ttk.Button(row, text="Analyse last lap", state="disabled",
                                              command=self._open_last_lap)
            self.last_lap_button.pack(side="left", padx=(6, 0))
            if self._analysis_choice is not None:
                prefers_browser, setter = self._analysis_choice
                self.browser_var = tk.BooleanVar(value=bool(prefers_browser))
                ttk.Checkbutton(
                    self.root, text="Open the analysis in my browser instead",
                    variable=self.browser_var, command=lambda: setter(bool(self.browser_var.get())),
                ).pack(anchor="w", padx=12, pady=(0, 4))

        if self._on_reconnect is not None:
            ttk.Button(self.root, text="Reconnect account…", command=self._on_reconnect).pack(anchor="e", padx=12, pady=(0, 4))

        ttk.Label(self.root, text="Activity").pack(anchor="w", **pad)
        self.log_box = tk.Listbox(self.root, height=8)
        self.log_box.pack(fill="both", expand=True, padx=12, pady=(0, 12))

    def push(self, update: StatusUpdate) -> None:
        """Thread-safe: call this from the telemetry/uploader threads."""
        self._queue.put(update)

    def _poll_queue(self) -> None:
        try:
            while True:
                update = self._queue.get_nowait()
                self._apply(update)
        except queue.Empty:
            pass
        self.root.after(200, self._poll_queue)

    def _open_notice_url(self) -> None:
        # Re-validated at click time, not just when it was received.
        if is_safe_download_url(self._notice_url):
            webbrowser.open(self._notice_url)

    def _open_last_lap(self) -> None:
        if self._on_open_analysis is not None and self._last_lap_id is not None:
            self._on_open_analysis(self._last_lap_id)

    def _apply(self, update: StatusUpdate) -> None:
        if update.last_lap_id is not None and self._on_open_analysis is not None:
            self._last_lap_id = update.last_lap_id
            self.last_lap_button.configure(state="normal")
        if update.notice_url is not None:
            self._notice_url = update.notice_url if is_safe_download_url(update.notice_url) else ""
            if self._notice_url:
                self.notice_button.pack(anchor="e", padx=12, pady=(0, 4), before=self.info_frame)
            else:
                self.notice_button.pack_forget()
        if update.notice is not None:
            self.notice_var.set(update.notice)
            if update.notice:
                self.notice_label.pack(fill="x", padx=12, pady=(0, 4), before=self.info_frame)
            else:
                self.notice_label.pack_forget()
        if should_update_telemetry_fields(update):
            self.status_var.set("Connected" if update.connected else "Disconnected")
            self.track_var.set(f"Track: {update.track_name}")
            self.car_var.set(f"Car: {update.car_name}")
            self.lap_var.set(f"Lap: {update.lap_number}")
        if should_update_last_lap(update):
            suffix = ""
            color = "black"
            if update.last_lap_valid is True:
                suffix, color = " (valid)", "#1a7f37"
            elif update.last_lap_valid is False:
                suffix, color = " (invalid)", "#c0392b"
            self.last_lap_var.set(f"Last lap: {format_time(update.last_lap_time)}{suffix}")
            self.last_lap_label.configure(fg=color)
        if update.log_line:
            self.log_box.insert(tk.END, update.log_line)
            self.log_box.see(tk.END)

    def run(self) -> None:
        self.root.mainloop()


def run_in_background(target, *args, **kwargs) -> threading.Thread:
    t = threading.Thread(target=target, args=args, kwargs=kwargs, daemon=True)
    t.start()
    return t
