"""Free Claude Code — Desktop Client GUI.

A simple desktop tray client for the Free Claude Code proxy server.
Controls server start/stop and minimizes to system tray.

Requires optional gui dependencies::

    uv sync --extra gui

Or install manually::

    pip install pystray Pillow
"""

from __future__ import annotations

import contextlib
import os
import queue
import subprocess
import threading
import time
import tkinter as tk
import urllib.error
import urllib.request
import webbrowser
from collections.abc import Callable
from pathlib import Path
from tkinter import messagebox, scrolledtext, ttk

try:
    import pystray
    from PIL import Image, ImageDraw

    HAS_TRAY = True
except ImportError:
    HAS_TRAY = False

from config.settings import get_settings

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ICON_PATH = PROJECT_ROOT / "claude.ico"
HEALTH_CHECK_INTERVAL = 3.0
HEALTH_CHECK_TIMEOUT = 2.0
HEALTH_CHECK_START_TIMEOUT = 15.0
LOG_POLL_INTERVAL_MS = 100


# ---------------------------------------------------------------------------
# Server subprocess manager
# ---------------------------------------------------------------------------


class ServerProcess:
    """Manages the uvicorn server subprocess — start, stop, output capture."""

    def __init__(self, log_callback: Callable[[str], None]) -> None:
        self._process: subprocess.Popen[str] | None = None
        self._log_callback = log_callback
        self._reader_thread: threading.Thread | None = None
        self._stop_reader = threading.Event()

    @property
    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def start(self, host: str, port: int) -> None:
        """Launch uvicorn as a child process (no console window)."""
        if self.is_running:
            self._log("Server is already running")
            return

        self._log(f"Starting server on {host}:{port}...")
        self._stop_reader.clear()

        # Use the venv's uvicorn.exe directly — no cmd, no uv, no console window.
        uvicorn_exe = str(PROJECT_ROOT / ".venv" / "Scripts" / "uvicorn.exe")
        self._process = subprocess.Popen(
            [
                uvicorn_exe,
                "server:app",
                "--host",
                host,
                "--port",
                str(port),
            ],
            cwd=str(PROJECT_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            creationflags=subprocess.CREATE_NO_WINDOW,  # Windows: zero console
        )

        self._reader_thread = threading.Thread(
            target=self._read_output,
            name="server-output-reader",
            daemon=True,
        )
        self._reader_thread.start()

    def stop(self) -> None:
        """Terminate the server subprocess."""
        proc = self._process
        if proc is None:
            return

        self._log("Stopping server...")
        self._stop_reader.set()

        try:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                    check=False,
                )
            else:
                proc.terminate()
                proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        except Exception as exc:
            self._log(f"Error stopping server: {exc}")

        self._process = None
        self._log("Server stopped")

    def _read_output(self) -> None:
        """Read server stdout in a daemon thread and forward each line."""
        proc = self._process
        if proc is None or proc.stdout is None:
            return

        try:
            for line in proc.stdout:
                if self._stop_reader.is_set():
                    break
                if line:
                    self._log_callback(line.rstrip("\n\r"))
        finally:
            proc.stdout.close()

        # The process exited (read loop ended).
        self._process = None
        self._log_callback("__SERVER_PROCESS_EXITED__")

    def _log(self, message: str) -> None:
        self._log_callback(f"[INFO] {message}")


# ---------------------------------------------------------------------------
# Tray icon helpers
# ---------------------------------------------------------------------------


# Keep built tray images alive so pystray's HICON handle stays valid.
_tray_images: dict[str, Image.Image] = {}


def _build_tray_image(status: str) -> Image.Image:
    """Return a 64x64 RGBA icon with a coloured status dot in the corner.

    Images are cached in ``_tray_images`` so pystray's underlying HICON
    handle never gets invalidated — Windows would otherwise lose the tray
    icon the next time the user hovers over it.
    """
    if status in _tray_images:
        return _tray_images[status]

    if ICON_PATH.is_file():
        img = Image.open(ICON_PATH)
        img = img.resize((64, 64), Image.Resampling.LANCZOS).convert("RGBA")
    else:
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.ellipse([8, 8, 56, 56], fill=(100, 100, 255, 255))

    draw = ImageDraw.Draw(img)
    color_map = {
        "running": (0, 200, 0, 255),
        "starting": (255, 200, 0, 255),
        "stopped": (200, 0, 0, 255),
    }
    color = color_map.get(status, (128, 128, 128, 255))
    draw.ellipse([44, 44, 60, 60], fill=color)
    _tray_images[status] = img
    return img


# ---------------------------------------------------------------------------
# Main GUI
# ---------------------------------------------------------------------------


class ClientGUI:
    """Tkinter desktop window for the Free Claude Code server client.

    Lifecycle is decoupled from the server:
    - On startup, checks if a server is already running via ``/health``.
    - If yes: shows "Running", allows Stop (calls ``POST /exit``).
    - If no: allows Start (launches a managed subprocess).
    """

    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("Free Claude Code Client")
        self.root.geometry("520x480")
        self.root.minsize(400, 300)
        self.root.resizable(True, True)

        if ICON_PATH.is_file():
            with contextlib.suppress(tk.TclError):
                self.root.iconbitmap(str(ICON_PATH))

        self._center_window()

        # Server state machine: stopped | starting | running
        self.status: str = "stopped"
        self._server_started_by_us = False
        self._log_queue: queue.Queue[str] = queue.Queue()
        self._health_stop = threading.Event()
        self._settings = get_settings()
        self.server_proc = ServerProcess(log_callback=self._on_server_log_raw)

        self._hosts = ["127.0.0.1", "0.0.0.0", "::1", "localhost"]
        initial = self._settings.host
        if initial not in self._hosts:
            self._hosts = [initial] + [h for h in self._hosts if h != initial]
        self._host_var = tk.StringVar(value=initial)

        self._build_ui()

        # Intercept window close → clean shutdown.
        self.root.protocol("WM_DELETE_WINDOW", self.quit_app)

        # On next idle tick, probe whether the server is already running.
        self.root.after(10, self._check_existing_server)

    # -- UI construction ------------------------------------------------

    def _build_ui(self) -> None:
        # --- Server status ---
        status_frame = ttk.LabelFrame(self.root, text="Server Status", padding=10)
        status_frame.pack(fill="x", padx=10, pady=(10, 5))

        self.status_canvas = tk.Canvas(
            status_frame, width=16, height=16, highlightthickness=0
        )
        self.status_canvas.grid(row=0, column=0, padx=(0, 8))
        self._draw_status_dot("red")

        self.status_label = ttk.Label(
            status_frame, text="Stopped", font=("", 10, "bold")
        )
        self.status_label.grid(row=0, column=1, sticky="w")

        # --- Bind address selector ---
        host_frame = ttk.Frame(status_frame)
        host_frame.grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 0))
        ttk.Label(host_frame, text="Bind:").pack(side="left")
        self._host_combo = ttk.Combobox(
            host_frame,
            textvariable=self._host_var,
            values=self._hosts,
            state="readonly",
            width=16,
        )
        self._host_combo.pack(side="left", padx=(4, 0))

        host = self._browser_host()
        self.address_label = ttk.Label(
            host_frame,
            text=f"→  http://{host}:{self._settings.port}",
            foreground="#888",
        )
        self.address_label.pack(side="left", padx=(8, 0))

        self._host_var.trace_add("write", self._on_host_changed)

        # --- Control buttons ---
        btn_row = ttk.Frame(self.root)
        btn_row.pack(fill="x", padx=10, pady=5)

        self.start_stop_btn = ttk.Button(
            btn_row, text="▶  Start Server", command=self._toggle_server
        )
        self.start_stop_btn.pack(side="left", padx=(0, 5))

        ttk.Button(
            btn_row,
            text="Open Admin UI",
            command=lambda: webbrowser.open(
                f"http://{self._browser_host()}:{self._settings.port}/admin"
            ),
        ).pack(side="left", padx=5)

        # --- Server log ---
        log_frame = ttk.LabelFrame(self.root, text="Server Log", padding=5)
        log_frame.pack(fill="both", expand=True, padx=10, pady=5)

        self.log_text = scrolledtext.ScrolledText(
            log_frame,
            wrap="word",
            height=15,
            font=("Consolas", 9),
            state="disabled",
            bg="#1e1e1e",
            fg="#d4d4d4",
            insertbackground="#d4d4d4",
        )
        self.log_text.pack(fill="both", expand=True)

        # --- Bottom bar ---
        bottom = ttk.Frame(self.root)
        bottom.pack(fill="x", padx=10, pady=(0, 10))

        ttk.Button(bottom, text="About", command=self._show_about).pack(side="left")
        ttk.Label(bottom, text="v1.0.0").pack(side="right")

        # Periodic log drain
        self.root.after(LOG_POLL_INTERVAL_MS, self._drain_log_queue)

    def _center_window(self) -> None:
        self.root.update_idletasks()
        w, h = 520, 480
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        self.root.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 2}")

    def _check_existing_server(self) -> None:
        """Check if a server process is already reachable on the configured port.

        Sets status to ``running`` (with external flag) or ``stopped``.
        Also starts a health-check monitor so the GUI detects external shutdowns.
        """
        try:
            req = urllib.request.Request(
                f"http://127.0.0.1:{self._settings.port}/health", method="GET"
            )
            with urllib.request.urlopen(req, timeout=HEALTH_CHECK_TIMEOUT) as resp:
                if resp.status == 200:
                    self._server_started_by_us = False
                    self._append_log(
                        "[INFO] Detected existing server — lifecycle is decoupled"
                    )
                    self._set_status("running")
                    self._host_combo.config(state="disabled")

                    # Start monitoring so the UI detects external /exit shutdowns.
                    self._health_stop.clear()
                    url = f"http://127.0.0.1:{self._settings.port}/health"
                    thread = threading.Thread(
                        target=self._monitor_health_forever,
                        args=(url,),
                        name="health-monitor",
                        daemon=True,
                    )
                    thread.start()
                    return
        except OSError:
            pass

        self._set_status("stopped")

    def _on_host_changed(self, *_args: object) -> None:
        """Refresh the address label when the user picks a different bind host."""
        self._settings.host = self._host_var.get()
        host = self._browser_host()
        self.address_label.config(text=f"→  http://{host}:{self._settings.port}")

    def _browser_host(self) -> str:
        host = self._settings.host.strip() if self._settings.host else "127.0.0.1"
        if host in {"0.0.0.0", "::", "[::]"}:
            host = "127.0.0.1"
        return host

    # -- Status helpers -------------------------------------------------

    def _draw_status_dot(self, color: str) -> None:
        self.status_canvas.delete("dot")
        r = 5
        cx, cy = 8, 8
        self.status_canvas.create_oval(
            cx - r,
            cy - r,
            cx + r,
            cy + r,
            fill=color,
            outline=color,
            tags="dot",
        )

    def _set_status(self, status: str) -> None:
        self.status = status
        match status:
            case "stopped":
                self.status_label.config(text="Stopped")
                self._draw_status_dot("red")
                self.start_stop_btn.config(text="▶  Start Server", state="normal")
            case "starting":
                self.status_label.config(text="Starting...")
                self._draw_status_dot("orange")
                self.start_stop_btn.config(text="⏳  Starting...", state="disabled")
            case "running":
                self.status_label.config(text="Running")
                self._draw_status_dot("green")
                self.start_stop_btn.config(text="■  Stop Server", state="normal")
        self._update_tray()

    # -- Server lifecycle -----------------------------------------------

    def _toggle_server(self) -> None:
        if self.status == "stopped":
            self._start_server()
        elif self.status == "running":
            self._stop_server()

    def _start_server(self) -> None:
        self._set_status("starting")
        self._server_started_by_us = True
        self._host_combo.config(state="disabled")
        self.server_proc.start(self._settings.host, self._settings.port)

        self._health_stop.clear()
        thread = threading.Thread(
            target=self._health_check_loop,
            args=(self._settings.host, self._settings.port),
            name="health-check",
            daemon=True,
        )
        thread.start()

    def _stop_server(self) -> None:
        if self._server_started_by_us:
            # We launched the subprocess → kill it directly.
            self._health_stop.set()
            self.server_proc.stop()
            self._set_status("stopped")
        else:
            # Server was already running → ask it to exit gracefully via API.
            self._health_stop.set()
            self._call_exit_api()
            self._append_log("[INFO] Sent exit signal to server via /exit")
            self._set_status("stopped")

        self._host_combo.config(state="readonly")

    # -- Health check ---------------------------------------------------

    def _health_check_loop(self, host: str, port: int) -> None:
        url = f"http://127.0.0.1:{port}/health"
        start_time = time.monotonic()

        # Phase 1 — wait for the server to become reachable.
        while not self._health_stop.is_set():
            if self._try_health(url):
                self._schedule_set_status("running")
                break
            if time.monotonic() - start_time > HEALTH_CHECK_START_TIMEOUT:
                self._schedule_set_status("stopped")
                self._log_queue.put(
                    "[ERROR] Server failed to start within "
                    f"{HEALTH_CHECK_START_TIMEOUT:.0f} seconds"
                )
                self._schedule_error(
                    "Server Error",
                    "Server failed to start. Check the logs for details.",
                )
                return
            self._health_stop.wait(HEALTH_CHECK_INTERVAL)

        # Phase 2 — keep polling to detect unexpected shutdowns.
        self._monitor_health_forever(url)

    @staticmethod
    def _try_health(url: str) -> bool:
        """Return True when /health responds with HTTP 200."""
        try:
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=HEALTH_CHECK_TIMEOUT) as resp:
                return resp.status == 200
        except urllib.error.URLError, urllib.error.HTTPError, OSError:
            return False

    def _monitor_health_forever(self, url: str) -> None:
        """Poll /health indefinitely; set status to ``stopped`` when it dies."""
        while not self._health_stop.is_set():
            if not self._try_health(url):
                self._schedule_set_status("stopped")
                return
            self._health_stop.wait(HEALTH_CHECK_INTERVAL)

    def _schedule_set_status(self, status: str) -> None:
        self.root.after(0, lambda: self._set_status(status))

    def _call_exit_api(self) -> None:
        """POST ``/exit`` on the local server to request graceful shutdown."""
        try:
            req = urllib.request.Request(
                f"http://127.0.0.1:{self._settings.port}/exit",
                method="POST",
                data=b"{}",
                headers={"Content-Type": "application/json"},
            )
            urllib.request.urlopen(req, timeout=HEALTH_CHECK_TIMEOUT)
        except OSError:
            pass  # Server went down before responding — expected.

    def _schedule_error(self, title: str, msg: str) -> None:
        self.root.after(0, lambda: messagebox.showerror(title, msg))

    # -- Logging --------------------------------------------------------

    def _on_server_log_raw(self, message: str) -> None:
        """Called from the reader thread — forward to the log queue."""
        self._log_queue.put(message)

    def _drain_log_queue(self) -> None:
        """Periodic tkinter callback — drain the thread-safe log queue."""
        try:
            while True:
                msg = self._log_queue.get_nowait()
                if msg == "__SERVER_PROCESS_EXITED__":
                    self._on_process_exit()
                else:
                    self._append_log(msg)
        except queue.Empty:
            pass
        self.root.after(LOG_POLL_INTERVAL_MS, self._drain_log_queue)

    def _append_log(self, text: str) -> None:
        self.log_text.config(state="normal")
        self.log_text.insert("end", text + "\n")
        self.log_text.see("end")
        self.log_text.config(state="disabled")

    def _on_process_exit(self) -> None:
        """Handle unexpected server process exit."""
        if self.status == "running" and self._server_started_by_us:
            self._health_stop.set()
            self._set_status("stopped")
            self._append_log("[WARN] Server process exited unexpectedly")

    # -- Window management ----------------------------------------------

    def show_window(self) -> None:
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def _show_about(self) -> None:
        messagebox.showinfo(
            "About Free Claude Code Client",
            "Free Claude Code Proxy Client\n\n"
            "A desktop client for managing the Claude Code proxy server.\n"
            "Minimize to tray and control the server from there.",
        )

    def _update_tray(self) -> None:
        """Sync the tray tooltip to current status."""
        icon = getattr(self, "_tray_icon", None)
        if icon is None:
            return
        with contextlib.suppress(Exception):
            icon.title = f"Free Claude Code — {self.status.title()}"

    def quit_app(self) -> None:
        """Clean shutdown — stop server if we started it, destroy window, exit."""
        self._health_stop.set()
        if self._server_started_by_us:
            self.server_proc.stop()
        self.root.quit()
        self.root.destroy()

    def run(self) -> None:
        """Enter the tkinter main loop."""
        self.root.mainloop()


# ---------------------------------------------------------------------------
# System tray
# ---------------------------------------------------------------------------


def _build_tray_menu(gui: ClientGUI) -> pystray.Menu:
    """Build a dynamic pystray right-click menu tied to *gui*."""

    def on_show(_icon: pystray.Icon, _item: pystray.MenuItem) -> None:
        gui.show_window()

    def on_toggle(_icon: pystray.Icon, _item: pystray.MenuItem) -> None:
        gui._toggle_server()

    def on_quit(_icon: pystray.Icon, _item: pystray.MenuItem) -> None:
        _icon.stop()
        gui.root.after(0, gui.quit_app)

    return pystray.Menu(
        pystray.MenuItem("Show Window", on_show, default=True),
        pystray.MenuItem(
            "Start Server",
            on_toggle,
            visible=lambda: gui.status == "stopped",
        ),
        pystray.MenuItem(
            "Stop Server",
            on_toggle,
            visible=lambda: gui.status == "running",
        ),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit", on_quit),
    )


def _run_tray_thread(gui: ClientGUI) -> pystray.Icon | None:
    """Create the system tray icon and run its message loop.

    Returns the Icon or None if pystray is unavailable.
    """
    if not HAS_TRAY:
        return None

    image = _build_tray_image("stopped")
    menu = _build_tray_menu(gui)
    icon = pystray.Icon("free-claude-code", image, "Free Claude Code", menu)

    # Non-daemon so the hidden-window message loop is stable on Windows.
    t = threading.Thread(target=icon.run, daemon=False, name="pystray")
    t.start()

    return icon


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Create the GUI, (optionally) the tray icon, and enter the event loop."""
    gui = ClientGUI()

    if not HAS_TRAY:
        gui.root.after(
            0,
            lambda: messagebox.showerror(
                "Missing Dependency",
                "pystray is not installed — system tray unavailable.\n\n"
                "Please run:\n  uv sync --extra gui\n\n"
                "Then restart the client.",
            ),
        )
    else:
        gui._tray_icon = _run_tray_thread(gui)

    gui.run()


if __name__ == "__main__":
    main()
