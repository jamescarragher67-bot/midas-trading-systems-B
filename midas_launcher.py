"""
midas_launcher.py — Midas Capital Launcher
A desktop app to launch Midas bots and view live logs/dashboard.

Run with: python midas_launcher.py
Package as .exe with: pyinstaller --onefile --windowed --name "Midas Launcher" midas_launcher.py
"""

import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
import subprocess
import threading
import os
import sys
import json
from datetime import datetime

# ══════════════════════════════════════════════════════════════════════════
# CONFIG — update these paths to match your folder structure
# ══════════════════════════════════════════════════════════════════════════

BASE_DIR    = os.path.dirname(os.path.abspath(__file__))       # MIDAS TRADING BOT folder
DESKTOP_DIR = os.path.dirname(BASE_DIR)                          # Desktop folder

BOTS = {
    "Midas B (Trading Bot)": {
        "path": os.path.join(BASE_DIR, "main.py"),
        "log_dir": os.path.join(BASE_DIR, "logs"),
    },
    "Trade Sync (Firebase)": {
        "path": os.path.join(BASE_DIR, "trade_sync.py"),
        "log_dir": os.path.join(BASE_DIR, "logs"),
    },
    "Firebase Push": {
        "path": os.path.join(BASE_DIR, "firebase_push.py"),
        "log_dir": os.path.join(BASE_DIR, "logs"),
    },
    "Open-Llama Midas": {
        "path": os.path.join(DESKTOP_DIR, "OPEN-LLAMA MIDAS", "decision_loop.py"),
        "log_dir": os.path.join(DESKTOP_DIR, "OPEN-LLAMA MIDAS", "logs"),
    },
}

# Colours — Midas Capital minimal luxury: pure black, gold, refined greys
BG       = "#000000"
BG2      = "#0C0C0C"
SURFACE  = "#121212"
BORDER   = "#1F1F1F"
BORDER2  = "#2A2A2A"
TEXT     = "#F5F5F0"
TEXT_DIM = "#6E6E6E"
TEXT_MID = "#9A9A95"
GOLD     = "#C9A84C"
GOLD_DIM = "#8A7333"
GREEN    = "#3DDC84"
RED      = "#E5484D"
AMBER    = "#E0A52F"

# Backwards-compat alias (rest of file references ACCENT)
ACCENT = GOLD


class MidasLauncher(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Midas Capital — Launcher")
        self.geometry("1000x650")
        self.configure(bg=BG)
        self.minsize(900, 600)

        self.processes = {}   # name -> subprocess.Popen
        self.log_threads = {} # name -> thread

        self._build_ui()
        self._refresh_status()

    # ── UI BUILD ──────────────────────────────────────────────────────────
    def _build_ui(self):
        # Header
        header = tk.Frame(self, bg=BG, height=72)
        header.pack(fill="x", padx=32, pady=(28, 4))

        title = tk.Label(header, text="MIDAS", font=("Georgia", 22, "bold"),
                          fg=GOLD, bg=BG)
        title.pack(side="left")
        subtitle = tk.Label(header, text="  CAPITAL",
                             font=("Georgia", 22), fg=TEXT, bg=BG)
        subtitle.pack(side="left")

        tagline = tk.Label(header, text="   ·   Operations Console",
                            font=("Segoe UI", 10), fg=TEXT_DIM, bg=BG)
        tagline.pack(side="left", padx=(0, 0), pady=(6, 0))

        self.clock_label = tk.Label(header, text="", font=("Consolas", 10),
                                     fg=TEXT_DIM, bg=BG)
        self.clock_label.pack(side="right", pady=(6, 0))
        self._tick_clock()

        # Thin gold divider under header
        divider = tk.Frame(self, bg=BORDER, height=1)
        divider.pack(fill="x", padx=32, pady=(8, 0))

        # Main split: left = bot list, right = logs
        main = tk.Frame(self, bg=BG)
        main.pack(fill="both", expand=True, padx=32, pady=(20, 24))

        left = tk.Frame(main, bg=BG, width=340)
        left.pack(side="left", fill="y", padx=(0, 28))

        right = tk.Frame(main, bg=BG)
        right.pack(side="left", fill="both", expand=True)

        # ── Left: bot launcher cards ──
        section_label = tk.Label(left, text="P R O C E S S E S", font=("Segoe UI", 9, "bold"),
                                  fg=GOLD_DIM, bg=BG, anchor="w")
        section_label.pack(fill="x", pady=(0, 12))

        self.status_labels = {}
        self.toggle_buttons = {}

        for name in BOTS:
            card = tk.Frame(left, bg=SURFACE)
            card.pack(fill="x", pady=5)

            # Subtle top accent line (full width, very thin)
            accent_line = tk.Frame(card, bg=BORDER, height=1)
            accent_line.pack(fill="x", side="top")

            inner = tk.Frame(card, bg=SURFACE)
            inner.pack(fill="x", padx=18, pady=14)

            top_row = tk.Frame(inner, bg=SURFACE)
            top_row.pack(fill="x")

            name_label = tk.Label(top_row, text=name, font=("Segoe UI", 11),
                                   fg=TEXT, bg=SURFACE, anchor="w")
            name_label.pack(side="left")

            status_dot = tk.Label(top_row, text="●", font=("Segoe UI", 10),
                                   fg=RED, bg=SURFACE)
            status_dot.pack(side="right")
            self.status_labels[name] = status_dot

            btn_row = tk.Frame(inner, bg=SURFACE)
            btn_row.pack(fill="x", pady=(12, 0))

            toggle_btn = tk.Button(
                btn_row, text="START", font=("Segoe UI", 9, "bold"),
                bg=SURFACE, fg=GOLD, activebackground=BORDER2, activeforeground=GOLD,
                relief="flat", padx=18, pady=7, cursor="hand2",
                highlightthickness=1, highlightbackground=GOLD_DIM, highlightcolor=GOLD_DIM,
                bd=0,
                command=lambda n=name: self.toggle_bot(n)
            )
            toggle_btn.pack(side="left")
            self.toggle_buttons[name] = toggle_btn

            view_btn = tk.Button(
                btn_row, text="View Logs", font=("Segoe UI", 9),
                bg=SURFACE, fg=TEXT_DIM, activebackground=SURFACE, activeforeground=TEXT_MID,
                relief="flat", padx=12, pady=7, cursor="hand2", bd=0,
                command=lambda n=name: self.show_logs(n)
            )
            view_btn.pack(side="left", padx=(14, 0))

            # Hover effects
            self._add_hover(card, SURFACE, "#161616")
            self._add_hover(inner, SURFACE, "#161616")

        # ── Quick links section ──
        link_label = tk.Label(left, text="D A S H B O A R D", font=("Segoe UI", 9, "bold"),
                               fg=GOLD_DIM, bg=BG, anchor="w")
        link_label.pack(fill="x", pady=(28, 12))

        dash_card = tk.Frame(left, bg=SURFACE)
        dash_card.pack(fill="x", pady=5)

        dash_accent = tk.Frame(dash_card, bg=BORDER, height=1)
        dash_accent.pack(fill="x", side="top")

        dash_inner = tk.Frame(dash_card, bg=SURFACE)
        dash_inner.pack(fill="x", padx=18, pady=14)

        tk.Label(dash_inner, text="Live Dashboard", font=("Segoe UI", 11),
                 fg=TEXT, bg=SURFACE, anchor="w").pack(fill="x")
        tk.Label(dash_inner, text="midas-dashboard-beta.vercel.app",
                 font=("Consolas", 8), fg=TEXT_DIM, bg=SURFACE, anchor="w").pack(fill="x", pady=(3, 12))

        open_btn = tk.Button(
            dash_inner, text="OPEN DASHBOARD", font=("Segoe UI", 9, "bold"),
            bg=SURFACE, fg=GOLD, activebackground=BORDER2, activeforeground=GOLD,
            relief="flat", padx=18, pady=7, cursor="hand2",
            highlightthickness=1, highlightbackground=GOLD_DIM, highlightcolor=GOLD_DIM,
            bd=0,
            command=self.open_dashboard
        )
        open_btn.pack(side="left")

        self._add_hover(dash_card, SURFACE, "#161616")
        self._add_hover(dash_inner, SURFACE, "#161616")

        # Stop all button
        stop_all = tk.Button(
            left, text="STOP ALL PROCESSES", font=("Segoe UI", 9, "bold"),
            bg=BG, fg=RED, activebackground=SURFACE, activeforeground=RED,
            relief="flat", padx=16, pady=12, cursor="hand2", bd=0,
            highlightthickness=1, highlightbackground=BORDER, highlightcolor=BORDER,
            command=self.stop_all
        )
        stop_all.pack(fill="x", pady=(28, 0))

        # ── Right: log viewer ──
        log_header = tk.Frame(right, bg=BG)
        log_header.pack(fill="x")

        self.log_title = tk.Label(log_header, text="L O G   O U T P U T   ·   Select a process",
                                   font=("Segoe UI", 9, "bold"), fg=GOLD_DIM, bg=BG, anchor="w")
        self.log_title.pack(side="left")

        clear_btn = tk.Button(
            log_header, text="Clear", font=("Segoe UI", 9),
            bg=BG, fg=TEXT_DIM, activebackground=BG, activeforeground=TEXT_MID,
            relief="flat", padx=10, pady=2, cursor="hand2", bd=0,
            command=lambda: self.log_box.delete("1.0", tk.END)
        )
        clear_btn.pack(side="right")

        self.log_box = scrolledtext.ScrolledText(
            right, bg=BG2, fg="#C8C8C8", font=("Consolas", 9),
            insertbackground=TEXT, relief="flat", wrap="word",
            padx=16, pady=16, highlightthickness=1,
            highlightbackground=BORDER, highlightcolor=BORDER
        )
        self.log_box.pack(fill="both", expand=True, pady=(12, 0))

        # Configure colour tags for log levels
        self.log_box.tag_config("info", foreground=TEXT_MID)
        self.log_box.tag_config("error", foreground=RED)
        self.log_box.tag_config("warning", foreground=AMBER)
        self.log_box.tag_config("success", foreground=GREEN)
        self.log_box.tag_config("accent", foreground=GOLD)

        self.log_box.insert("1.0",
            "Midas Launcher ready.\n"
            "Select a process and click Start to begin.\n"
            "Logs will stream here in real time.\n",
            "info"
        )

    # ── Hover helper ──────────────────────────────────────────────────────
    def _add_hover(self, widget, normal_bg, hover_bg):
        def on_enter(e):
            self._set_bg_recursive(widget, hover_bg)
        def on_leave(e):
            self._set_bg_recursive(widget, normal_bg)
        widget.bind("<Enter>", on_enter)
        widget.bind("<Leave>", on_leave)

    def _set_bg_recursive(self, widget, color):
        try:
            widget.config(bg=color)
        except tk.TclError:
            pass
        for child in widget.winfo_children():
            if isinstance(child, (tk.Frame, tk.Label)):
                self._set_bg_recursive(child, color)

    # ── Clock ─────────────────────────────────────────────────────────────
    def _tick_clock(self):
        now = datetime.now().strftime("%a %d %b — %H:%M:%S")
        self.clock_label.config(text=now)
        self.after(1000, self._tick_clock)

    # ── Process management ───────────────────────────────────────────────
    def toggle_bot(self, name):
        if name in self.processes and self.processes[name].poll() is None:
            self.stop_bot(name)
        else:
            self.start_bot(name)

    def start_bot(self, name):
        path = BOTS[name]["path"]
        if not os.path.exists(path):
            messagebox.showerror("File not found", f"Cannot find:\n{path}")
            return

        self._log(f"\n{'='*60}\n", "accent")
        self._log(f"Starting {name}...\n", "accent")
        self._log(f"{'='*60}\n", "accent")

        cwd = os.path.dirname(path)
        try:
            proc = subprocess.Popen(
                [sys.executable, path],
                cwd=cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            )
        except Exception as e:
            messagebox.showerror("Launch failed", str(e))
            return

        self.processes[name] = proc
        self.toggle_buttons[name].config(text="STOP", fg=RED,
                                          highlightbackground=RED, highlightcolor=RED)

        # Stream output in background thread
        t = threading.Thread(target=self._stream_output, args=(name, proc), daemon=True)
        t.start()
        self.log_threads[name] = t

        self.log_title.config(text=f"L O G   O U T P U T   ·   {name}")
        self._refresh_status()

    def stop_bot(self, name):
        proc = self.processes.get(name)
        if proc and proc.poll() is None:
            proc.terminate()
            self._log(f"\n[STOPPED] {name}\n", "warning")

        self.toggle_buttons[name].config(text="START", fg=GOLD,
                                          highlightbackground=GOLD_DIM, highlightcolor=GOLD_DIM)
        self._refresh_status()

    def stop_all(self):
        for name in list(self.processes.keys()):
            self.stop_bot(name)
        self._log("\n[STOPPED] All processes stopped.\n", "warning")

    def _stream_output(self, name, proc):
        for line in iter(proc.stdout.readline, ""):
            if not line:
                break
            self._log_threadsafe(f"[{name}] {line}")
        self.after(0, lambda: self._on_process_exit(name))

    def _on_process_exit(self, name):
        if name in self.toggle_buttons:
            self.toggle_buttons[name].config(text="START", fg=GOLD,
                                              highlightbackground=GOLD_DIM, highlightcolor=GOLD_DIM)
        self._refresh_status()
        self._log(f"\n[EXITED] {name} stopped or crashed.\n", "error")

    def _log_threadsafe(self, text):
        self.after(0, lambda: self._log(text))

    def _log(self, text, tag="info"):
        # Auto-tag based on content
        lower = text.lower()
        if tag == "info":
            if "error" in lower or "❌" in text or "failed" in lower:
                tag = "error"
            elif "warning" in lower or "⚠" in text:
                tag = "warning"
            elif "✅" in text or "connected" in lower or "success" in lower:
                tag = "success"

        self.log_box.insert(tk.END, text, tag)
        self.log_box.see(tk.END)

        # Cap log length to avoid memory bloat
        if int(self.log_box.index('end-1c').split('.')[0]) > 2000:
            self.log_box.delete("1.0", "500.0")

    def show_logs(self, name):
        self.log_title.config(text=f"L O G   O U T P U T   ·   {name}")
        log_dir = BOTS[name]["log_dir"]
        self.log_box.delete("1.0", tk.END)

        if not os.path.exists(log_dir):
            self._log(f"No log directory found at:\n{log_dir}\n", "warning")
            return

        # Find most recent log file
        log_files = sorted(
            [f for f in os.listdir(log_dir) if f.endswith((".log", ".jsonl", ".json"))],
            reverse=True
        )
        if not log_files:
            self._log(f"No log files found in:\n{log_dir}\n", "warning")
            return

        latest = os.path.join(log_dir, log_files[0])
        self._log(f"Showing last 100 lines of: {log_files[0]}\n{'='*60}\n", "accent")

        try:
            with open(latest, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()[-100:]
                for line in lines:
                    self._log(line)
        except Exception as e:
            self._log(f"Error reading log: {e}\n", "error")

    # ── Status refresh ────────────────────────────────────────────────────
    def _refresh_status(self):
        for name, dot in self.status_labels.items():
            proc = self.processes.get(name)
            if proc and proc.poll() is None:
                dot.config(fg=GREEN)
            else:
                dot.config(fg=RED)
        self.after(2000, self._refresh_status)

    # ── External links ────────────────────────────────────────────────────
    def open_dashboard(self):
        import webbrowser
        webbrowser.open("https://midas-dashboard-beta.vercel.app")

    # ── Cleanup on close ──────────────────────────────────────────────────
    def on_close(self):
        for name, proc in self.processes.items():
            if proc.poll() is None:
                proc.terminate()
        self.destroy()


if __name__ == "__main__":
    app = MidasLauncher()
    app.protocol("WM_DELETE_WINDOW", app.on_close)
    app.mainloop()