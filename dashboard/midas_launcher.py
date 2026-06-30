"""
midas_launcher.py — Midas Capital Operations Console
Rebuilt for launch. iOS 27 dark aesthetic.
"""

import tkinter as tk
from tkinter import scrolledtext, messagebox
import subprocess, threading, os, sys, json, time
from datetime import datetime, timezone, date

# ── Paths — BASE_DIR is the project root, one level up from dashboard/ ────────
BASE_DIR    = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_DIR     = os.path.join(BASE_DIR, "logs")
TRADES_FILE = os.path.join(BASE_DIR, "jasons", "trades.json")

PROCESSES = {
    "Trading Bot": os.path.join(BASE_DIR, "main_combined.py"),
    "Trade Sync":  os.path.join(BASE_DIR, "sync", "trade_sync.py"),
    "Firebase":    os.path.join(BASE_DIR, "sync", "firebase_push.py"),
}

# Mirrors config/settings.py — update here if settings change
_MAX_CONSECUTIVE_LOSSES = 4
_MAX_DAILY_LOSS_PCT     = 5.0
_MAX_TRADES_PER_DAY     = 15

# ── Palette — Black + Midas Gold ──────────────────────────────────────────────
BG       = "#000000"
GLASS    = "#0D0D0D"
GLASS_H  = "#141414"
BORDER   = "#1A1A1A"
BORDER_A = "#333333"
TEXT     = "#ECEEFF"
TEXT_2   = "#666666"
TEXT_3   = "#1A1A1A"
GOLD     = "#C9A84C"
GOLD_HI  = "#E8C46A"
GOLD_DIM = "#3E3018"
GREEN    = "#30D158"
RED      = "#FF453A"
ORANGE   = "#FF9F0A"
BLUE     = "#0A84FF"

# ── Fonts — Times New Roman ────────────────────────────────────────────────────
F_LABEL = ("Times New Roman", 8, "bold")
F_BODY  = ("Times New Roman", 10)
F_BOLD  = ("Times New Roman", 10, "bold")
F_SMALL = ("Times New Roman", 9)
F_MONO  = ("Consolas", 9)
F_XL    = ("Times New Roman", 32)
F_L     = ("Times New Roman", 22)
F_M     = ("Times New Roman", 15)


# ══════════════════════════════════════════════════════════════════════════════
# PRIMITIVES
# ══════════════════════════════════════════════════════════════════════════════

class GlassCard(tk.Frame):
    """
    Card with a Canvas-drawn rounded-rectangle background.
    Add content to self.body (packed as a child of self).
    The Canvas covers the card via place() and is lowered behind body.
    """
    R = 14

    def __init__(self, parent, fill=GLASS, **kwargs):
        bg = parent.cget("bg")
        super().__init__(parent, bg=bg, **kwargs)
        self._fill = fill

        # Canvas sits behind everything, covers full card area
        self._cv = tk.Canvas(self, bg=bg, highlightthickness=0)
        self._cv.place(relx=0, rely=0, relwidth=1, relheight=1)

        # body is a direct child of self (not the canvas) — pack drives card height
        self.body = tk.Frame(self, bg=fill, padx=18, pady=14)
        self.body.pack(fill="x", padx=self.R, pady=self.R)

        # Raise body above the canvas so it's visible and receives mouse events
        self.body.lift()
        self.bind("<Configure>", self._redraw)

    def _redraw(self, event):
        w, h = event.width, event.height
        r, f = self.R, self._fill
        if w < 2 * r or h < 2 * r:
            return
        self._cv.delete("bg_")
        kw = dict(fill=f, outline=f, tags="bg_")
        self._cv.create_arc(0,     0,     2*r,   2*r,   start=90,  extent=90, **kw)
        self._cv.create_arc(w-2*r, 0,     w,     2*r,   start=0,   extent=90, **kw)
        self._cv.create_arc(0,     h-2*r, 2*r,   h,     start=180, extent=90, **kw)
        self._cv.create_arc(w-2*r, h-2*r, w,     h,     start=270, extent=90, **kw)
        self._cv.create_rectangle(r, 0,   w-r,   h,     fill=f, outline=f, tags="bg_")
        self._cv.create_rectangle(0, r,   w,     h-r,   fill=f, outline=f, tags="bg_")
        self._cv.tag_lower("bg_")


def _dot(parent, color, size=8):
    """Colored status dot as a Canvas oval."""
    bg = parent.cget("bg")
    c  = tk.Canvas(parent, width=size, height=size, bg=bg, highlightthickness=0)
    c.create_oval(0, 0, size, size, fill=color, outline="", tags="dot")
    return c


def _pill(parent, text, accent, command, fg=None, small=False):
    """Flat button styled as a bordered pill."""
    fg   = fg or accent
    font = ("Times New Roman", 8, "bold") if small else ("Times New Roman", 9, "bold")
    pady = 5 if small else 7
    return tk.Button(
        parent, text=text, font=font, fg=fg, bg=GLASS,
        activebackground=GLASS_H, activeforeground=fg,
        relief="flat", cursor="hand2", bd=0, padx=14, pady=pady,
        highlightthickness=1, highlightbackground=accent, highlightcolor=accent,
        command=command,
    )


def _section(parent, label, top=0):
    tk.Label(parent, text=label, font=F_LABEL, fg=TEXT_2, bg=BG, anchor="w"
             ).pack(fill="x", pady=(top, 10))


def _rule(parent, color=BORDER):
    tk.Frame(parent, bg=color, height=1).pack(fill="x", pady=(0, 12))


# ══════════════════════════════════════════════════════════════════════════════
# LAUNCHER
# ══════════════════════════════════════════════════════════════════════════════

class MidasLauncher(tk.Tk):

    def __init__(self):
        super().__init__()
        self.title("Midas Capital")
        self.geometry("1200x740")
        self.minsize(1060, 660)
        self.configure(bg=BG)

        self.processes:  dict[str, subprocess.Popen] = {}
        self._proc_rows: dict[str, dict]             = {}

        self._build()
        self._start_mt5_poll()
        self._tick_clock()

    # ──────────────────────────────────────────────────────────────────────────
    # BUILD
    # ──────────────────────────────────────────────────────────────────────────

    def _build(self):
        self._build_header()
        tk.Frame(self, bg=BORDER, height=1).pack(fill="x")

        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True, padx=28, pady=22)

        # ── Left column — scrollable ──────────────────────────────────────────
        left_outer = tk.Frame(body, bg=BG, width=276)
        left_outer.pack(side="left", fill="y", padx=(0, 18))
        left_outer.pack_propagate(False)

        left_canvas = tk.Canvas(left_outer, bg=BG, highlightthickness=0, bd=0)
        left_scroll = tk.Scrollbar(
            left_outer, orient="vertical", command=left_canvas.yview,
            bg=BORDER, troughcolor=BG, activebackground=BORDER_A,
            width=6, relief="flat", bd=0,
        )
        left_canvas.configure(yscrollcommand=left_scroll.set)
        left_scroll.pack(side="right", fill="y")
        left_canvas.pack(side="left", fill="both", expand=True)

        left = tk.Frame(left_canvas, bg=BG)
        _left_id = left_canvas.create_window((0, 0), window=left, anchor="nw")

        left.bind("<Configure>",
                  lambda e: left_canvas.configure(scrollregion=left_canvas.bbox("all")))
        left_canvas.bind("<Configure>",
                         lambda e: left_canvas.itemconfig(_left_id, width=e.width))

        def _mw(event):
            left_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        left_canvas.bind("<Enter>", lambda e: left_canvas.bind_all("<MouseWheel>", _mw))
        left_canvas.bind("<Leave>", lambda e: left_canvas.unbind_all("<MouseWheel>"))

        center = tk.Frame(body, bg=BG, width=252)
        right  = tk.Frame(body, bg=BG)

        center.pack(side="left", fill="y", padx=(0, 18))
        center.pack_propagate(False)
        right.pack(side="left", fill="both", expand=True)

        self._build_processes(left)
        self._build_stats(center)
        self._build_logs(right)

    # ── Header ─────────────────────────────────────────────────────────────────

    def _build_header(self):
        h = tk.Frame(self, bg=BG, height=62)
        h.pack(fill="x", padx=28, pady=(18, 0))
        h.pack_propagate(False)

        brand = tk.Frame(h, bg=BG)
        brand.pack(side="left", anchor="center")
        tk.Label(brand, text="MIDAS",        font=("Times New Roman", 20, "bold"), fg=GOLD,  bg=BG).pack(side="left")
        tk.Label(brand, text=" CAPITAL",     font=("Times New Roman", 20),         fg=TEXT,  bg=BG).pack(side="left")
        tk.Label(brand, text="   ·   Operations Console",
                 font=("Times New Roman", 10), fg=TEXT_2, bg=BG).pack(side="left", pady=(4, 0))

        rhs = tk.Frame(h, bg=BG)
        rhs.pack(side="right", anchor="center")

        self.mt5_dot = _dot(rhs, RED, size=8)
        self.mt5_dot.pack(side="right", padx=(8, 0))

        self.mt5_lbl = tk.Label(rhs, text="MT5 OFFLINE",
                                 font=("Times New Roman", 8, "bold"), fg=TEXT_2, bg=BG)
        self.mt5_lbl.pack(side="right")

        self.clock_lbl = tk.Label(rhs, text="", font=("Consolas", 10), fg=TEXT_2, bg=BG)
        self.clock_lbl.pack(side="right", padx=(0, 26))

    # ── Processes ──────────────────────────────────────────────────────────────

    def _build_processes(self, parent):
        _section(parent, "P R O C E S S E S")

        for name in PROCESSES:
            self._proc_rows[name] = self._make_process_card(parent, name)

        _section(parent, "T O O L S", top=20)
        _pill(parent, "▶  Run Backtest",    GOLD, self._run_backtest).pack(fill="x", pady=(0, 8))
        _pill(parent, "⬡  Open Dashboard",  BLUE, self._open_dashboard).pack(fill="x", pady=(0, 0))

        tk.Frame(parent, bg=BORDER, height=1).pack(fill="x", pady=(22, 16))
        _pill(parent, "■  Stop All Processes", BORDER_A, self.stop_all, fg=RED).pack(fill="x")

    def _make_process_card(self, parent, name: str) -> dict:
        card = GlassCard(parent)
        card.pack(fill="x", pady=(0, 10))
        b = card.body

        _rule(b, BORDER_A)

        row = tk.Frame(b, bg=GLASS)
        row.pack(fill="x")
        tk.Label(row, text=name, font=F_BOLD, fg=TEXT, bg=GLASS).pack(side="left")
        dot = _dot(row, RED)
        dot.pack(side="right", pady=1)

        status_lbl = tk.Label(b, text="STOPPED", font=("Times New Roman", 8),
                               fg=TEXT_2, bg=GLASS, anchor="w")
        status_lbl.pack(fill="x", pady=(5, 12))

        btns = tk.Frame(b, bg=GLASS)
        btns.pack(fill="x")
        start_btn = _pill(btns, "START", GOLD, lambda n=name: self.toggle_process(n), small=True)
        start_btn.pack(side="left")
        _pill(btns, "Logs", TEXT_2, lambda n=name: self.show_logs(n),
              fg=TEXT_2, small=True).pack(side="left", padx=(10, 0))

        return {"dot": dot, "status_lbl": status_lbl, "start_btn": start_btn}

    # ── Live Stats ─────────────────────────────────────────────────────────────

    def _build_stats(self, parent):
        _section(parent, "L I V E   S T A T S")

        # Balance
        c = GlassCard(parent)
        c.pack(fill="x", pady=(0, 10))
        b = c.body
        _rule(b, GOLD_DIM)
        tk.Label(b, text="BALANCE", font=F_LABEL, fg=TEXT_2, bg=GLASS, anchor="w").pack(fill="x")
        self.lbl_balance = tk.Label(b, text="—", font=F_XL, fg=TEXT, bg=GLASS, anchor="w")
        self.lbl_balance.pack(fill="x")
        self.lbl_equity  = tk.Label(b, text="Equity  —", font=("Times New Roman", 9),
                                     fg=TEXT_2, bg=GLASS, anchor="w")
        self.lbl_equity.pack(fill="x", pady=(2, 0))

        # Today P&L / Trades
        c2 = GlassCard(parent)
        c2.pack(fill="x", pady=(0, 10))
        b2 = c2.body
        _rule(b2, BORDER_A)
        row2 = tk.Frame(b2, bg=GLASS)
        row2.pack(fill="x")

        ll = tk.Frame(row2, bg=GLASS)
        ll.pack(side="left", expand=True, fill="x")
        tk.Label(ll, text="TODAY P&L", font=F_LABEL, fg=TEXT_2, bg=GLASS, anchor="w").pack(fill="x")
        self.lbl_today_pnl = tk.Label(ll, text="—", font=F_L, fg=TEXT, bg=GLASS, anchor="w")
        self.lbl_today_pnl.pack(fill="x")

        lr = tk.Frame(row2, bg=GLASS)
        lr.pack(side="right")
        tk.Label(lr, text="TRADES", font=F_LABEL, fg=TEXT_2, bg=GLASS, anchor="e").pack(fill="x")
        self.lbl_trades = tk.Label(lr, text="—", font=F_L, fg=TEXT, bg=GLASS, anchor="e")
        self.lbl_trades.pack(fill="x")

        # Win rate / Open P&L
        c3 = GlassCard(parent)
        c3.pack(fill="x", pady=(0, 10))
        b3 = c3.body
        _rule(b3, BORDER_A)
        row3 = tk.Frame(b3, bg=GLASS)
        row3.pack(fill="x")

        wl = tk.Frame(row3, bg=GLASS)
        wl.pack(side="left", expand=True, fill="x")
        tk.Label(wl, text="WIN RATE", font=F_LABEL, fg=TEXT_2, bg=GLASS, anchor="w").pack(fill="x")
        self.lbl_wr = tk.Label(wl, text="—", font=F_L, fg=TEXT, bg=GLASS, anchor="w")
        self.lbl_wr.pack(fill="x")

        wr = tk.Frame(row3, bg=GLASS)
        wr.pack(side="right")
        tk.Label(wr, text="OPEN P&L", font=F_LABEL, fg=TEXT_2, bg=GLASS, anchor="e").pack(fill="x")
        self.lbl_open_pnl = tk.Label(wr, text="—", font=F_L, fg=TEXT, bg=GLASS, anchor="e")
        self.lbl_open_pnl.pack(fill="x")

        # Circuit breaker
        c4 = GlassCard(parent)
        c4.pack(fill="x")
        b4 = c4.body
        _rule(b4, BORDER_A)

        cb_row = tk.Frame(b4, bg=GLASS)
        cb_row.pack(fill="x")
        tk.Label(cb_row, text="CIRCUIT BREAKER", font=F_LABEL, fg=TEXT_2, bg=GLASS).pack(side="left")
        self.cb_dot = _dot(cb_row, GREEN, size=10)
        self.cb_dot.pack(side="right", pady=2)

        self.cb_status = tk.Label(b4, text="ACTIVE", font=("Times New Roman", 11, "bold"),
                                   fg=GREEN, bg=GLASS, anchor="w")
        self.cb_status.pack(fill="x", pady=(6, 0))
        self.cb_detail = tk.Label(b4, text="Monitoring", font=("Times New Roman", 9),
                                   fg=TEXT_2, bg=GLASS, anchor="w")
        self.cb_detail.pack(fill="x")

    # ── Log viewer ─────────────────────────────────────────────────────────────

    def _build_logs(self, parent):
        hdr = tk.Frame(parent, bg=BG)
        hdr.pack(fill="x", pady=(0, 10))
        self.log_title = tk.Label(hdr, text="L O G   O U T P U T  ·  Select a process",
                                   font=F_LABEL, fg=TEXT_2, bg=BG, anchor="w")
        self.log_title.pack(side="left")
        tk.Button(hdr, text="Clear", font=F_SMALL, bg=BG, fg=TEXT_2,
                  activebackground=BG, activeforeground=TEXT,
                  relief="flat", padx=10, pady=2, cursor="hand2", bd=0,
                  command=lambda: self.log_box.delete("1.0", tk.END)).pack(side="right")

        self.log_box = scrolledtext.ScrolledText(
            parent, bg=GLASS, fg="#8080B8", font=F_MONO,
            insertbackground=TEXT, relief="flat", wrap="none",
            padx=18, pady=16,
            highlightthickness=1, highlightbackground=BORDER, highlightcolor=BORDER_A,
        )
        self.log_box.pack(fill="both", expand=True)
        self.log_box.tag_config("gold",    foreground=GOLD)
        self.log_box.tag_config("success", foreground=GREEN)
        self.log_box.tag_config("error",   foreground=RED)
        self.log_box.tag_config("warning", foreground=ORANGE)
        self.log_box.tag_config("info",    foreground="#5858A8")
        self.log_box.tag_config("dim",     foreground=TEXT_3)

        self._log("Midas Capital — Operations Console\n", "gold")
        self._log("Select a process on the left to begin.\n", "info")

    # ══════════════════════════════════════════════════════════════════════════
    # CLOCK
    # ══════════════════════════════════════════════════════════════════════════

    def _tick_clock(self):
        self.clock_lbl.config(text=datetime.utcnow().strftime("%a %d %b  %H:%M:%S UTC"))
        self.after(1000, self._tick_clock)

    # ══════════════════════════════════════════════════════════════════════════
    # MT5 LIVE STATS  (background thread, every 5 s)
    # ══════════════════════════════════════════════════════════════════════════

    def _start_mt5_poll(self):
        threading.Thread(target=self._poll_loop, daemon=True).start()

    def _poll_loop(self):
        while True:
            self._fetch_stats()
            time.sleep(5)

    def _fetch_stats(self):
        try:
            import MetaTrader5 as mt5

            if not mt5.initialize():
                self.after(0, self._render_offline)
                return

            acct = mt5.account_info()
            if not acct:
                mt5.shutdown()
                self.after(0, self._render_offline)
                return

            today_s   = datetime.combine(date.today(), datetime.min.time()).replace(tzinfo=timezone.utc)
            now       = datetime.now(timezone.utc)
            deals     = mt5.history_deals_get(today_s, now) or []
            out_deals = [d for d in deals if d.entry == mt5.DEAL_ENTRY_OUT]

            today_pnl   = sum(d.profit for d in out_deals)
            today_count = len(out_deals)

            positions = mt5.positions_get() or []
            open_pnl  = sum(p.profit for p in positions)
            mt5.shutdown()

            wr, n_trades, consec = self._read_trades_stats()

            stats = {
                "balance":     acct.balance,
                "equity":      acct.equity,
                "today_pnl":   today_pnl,
                "today_count": today_count,
                "open_pnl":    open_pnl,
                "wr":          wr,
                "n_trades":    n_trades,
                "consec":      consec,
            }
            self.after(0, lambda s=stats: self._render_online(s))

        except Exception:
            self.after(0, self._render_offline)

    def _read_trades_stats(self):
        """Returns (win_rate_pct | None, n_trades, consec_losses)."""
        if not os.path.exists(TRADES_FILE):
            return None, 0, 0
        try:
            with open(TRADES_FILE) as f:
                trades = json.load(f)
            if not trades:
                return None, 0, 0
            wins   = sum(1 for t in trades if t.get("result") == "WIN")
            wr     = round(wins / len(trades) * 100, 1)
            consec = 0
            for t in reversed(trades):
                if t.get("result") == "LOSS":
                    consec += 1
                else:
                    break
            return wr, len(trades), consec
        except Exception:
            return None, 0, 0

    def _render_online(self, s: dict):
        self.mt5_dot.itemconfig("dot", fill=GREEN)
        self.mt5_lbl.config(text="MT5 LIVE", fg=GREEN)

        # Balance
        self.lbl_balance.config(text=f"${s['balance']:,.2f}", fg=TEXT)
        diff = s["equity"] - s["balance"]
        self.lbl_equity.config(
            text=f"Equity  ${s['equity']:,.2f}  ({diff:+.2f})",
            fg=GREEN if diff >= 0 else RED,
        )

        # Today P&L
        pnl = s["today_pnl"]
        self.lbl_today_pnl.config(
            text=f"{'+'if pnl>=0 else ''}${pnl:.2f}",
            fg=GREEN if pnl >= 0 else RED,
        )

        # Trades today
        n = s["today_count"]
        self.lbl_trades.config(
            text=f"{n} / {_MAX_TRADES_PER_DAY}",
            fg=TEXT if n < _MAX_TRADES_PER_DAY - 1 else ORANGE,
        )

        # Win rate
        wr = s["wr"]
        if wr is not None:
            self.lbl_wr.config(
                text=f"{wr:.1f}%",
                fg=GREEN if wr >= 55 else (ORANGE if wr >= 40 else RED),
            )
        else:
            self.lbl_wr.config(text="—", fg=TEXT_2)

        # Open P&L
        op = s["open_pnl"]
        if abs(op) > 0.01:
            self.lbl_open_pnl.config(
                text=f"{'+'if op>=0 else ''}${op:.2f}",
                fg=GREEN if op >= 0 else RED,
            )
        else:
            self.lbl_open_pnl.config(text="Flat", fg=TEXT_2)

        # Circuit breaker
        consec  = s["consec"]
        tripped = consec >= _MAX_CONSECUTIVE_LOSSES
        if tripped:
            self.cb_dot.itemconfig("dot", fill=RED)
            self.cb_status.config(text="TRIPPED", fg=RED)
            self.cb_detail.config(text=f"{consec} consecutive losses — bot paused until midnight", fg=RED)
        elif consec > 0:
            self.cb_dot.itemconfig("dot", fill=ORANGE)
            self.cb_status.config(text="WARNING", fg=ORANGE)
            rem = _MAX_CONSECUTIVE_LOSSES - consec
            self.cb_detail.config(text=f"{consec} loss streak — {rem} until trip", fg=ORANGE)
        else:
            self.cb_dot.itemconfig("dot", fill=GREEN)
            self.cb_status.config(text="ACTIVE", fg=GREEN)
            self.cb_detail.config(text="Monitoring", fg=TEXT_2)

    def _render_offline(self):
        self.mt5_dot.itemconfig("dot", fill=RED)
        self.mt5_lbl.config(text="MT5 OFFLINE", fg=TEXT_2)
        for lbl in (self.lbl_balance, self.lbl_equity, self.lbl_today_pnl,
                    self.lbl_trades, self.lbl_open_pnl):
            lbl.config(text="—", fg=TEXT_2)
        # Win rate still readable from trades.json when MT5 is down
        wr, _, consec = self._read_trades_stats()
        if wr is not None:
            self.lbl_wr.config(
                text=f"{wr:.1f}%",
                fg=GREEN if wr >= 55 else (ORANGE if wr >= 40 else RED),
            )
        else:
            self.lbl_wr.config(text="—", fg=TEXT_2)

    # ══════════════════════════════════════════════════════════════════════════
    # PROCESS MANAGEMENT
    # ══════════════════════════════════════════════════════════════════════════

    def toggle_process(self, name: str):
        proc = self.processes.get(name)
        if proc and proc.poll() is None:
            self._stop(name)
        else:
            self._start(name)

    def _start(self, name: str):
        path = PROCESSES[name]
        if not os.path.exists(path):
            messagebox.showerror("File Not Found", f"Cannot find:\n{path}")
            return

        self._log(f"\n{'─' * 52}\n", "dim")
        self._log(f"  Starting {name}…\n", "gold")
        self._log(f"{'─' * 52}\n", "dim")

        try:
            proc = subprocess.Popen(
                [sys.executable, path], cwd=BASE_DIR,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", bufsize=1,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        except Exception as e:
            messagebox.showerror("Launch Error", str(e))
            return

        self.processes[name] = proc
        self.log_title.config(text=f"L O G   O U T P U T  ·  {name}")
        self._set_running(name, True)
        threading.Thread(target=self._stream, args=(name, proc), daemon=True).start()

    def _stop(self, name: str):
        proc = self.processes.get(name)
        if proc and proc.poll() is None:
            proc.terminate()
            self._log(f"\n  {name} — stopped.\n", "warning")
        self._set_running(name, False)

    def stop_all(self):
        for name in list(self.processes):
            self._stop(name)

    def _set_running(self, name: str, running: bool):
        row = self._proc_rows.get(name)
        if not row:
            return
        if running:
            row["dot"].itemconfig("dot", fill=GREEN)
            row["status_lbl"].config(text="RUNNING", fg=GREEN)
            row["start_btn"].config(text="STOP", fg=RED,
                                    highlightbackground=RED, highlightcolor=RED)
        else:
            row["dot"].itemconfig("dot", fill=RED)
            row["status_lbl"].config(text="STOPPED", fg=TEXT_2)
            row["start_btn"].config(text="START", fg=GOLD,
                                    highlightbackground=GOLD_DIM, highlightcolor=GOLD)

    def _stream(self, name: str, proc: subprocess.Popen):
        for line in iter(proc.stdout.readline, ""):
            if line:
                self.after(0, lambda l=line: self._log(l))
        self.after(0, lambda: self._on_exit(name))

    def _on_exit(self, name: str):
        self._set_running(name, False)
        self._log(f"\n  {name} — process ended.\n", "error")

    # ══════════════════════════════════════════════════════════════════════════
    # LOG VIEWER
    # ══════════════════════════════════════════════════════════════════════════

    def show_logs(self, name: str):
        self.log_title.config(text=f"L O G   O U T P U T  ·  {name}")
        self.log_box.delete("1.0", tk.END)

        if not os.path.exists(LOG_DIR):
            self._log("No log directory found.\n", "warning")
            return

        files = sorted(
            [f for f in os.listdir(LOG_DIR) if f.endswith(".log")], reverse=True
        )
        if not files:
            self._log("No log files found.\n", "warning")
            return

        latest = os.path.join(LOG_DIR, files[0])
        self._log(f"  {files[0]}  —  last 150 lines\n", "gold")
        self._log(f"{'─' * 52}\n", "dim")
        try:
            with open(latest, encoding="utf-8", errors="replace") as f:
                for line in f.readlines()[-150:]:
                    self._log(line)
        except Exception as e:
            self._log(f"Error reading log: {e}\n", "error")

    def _log(self, text: str, tag: str | None = None):
        if tag is None:
            low = text.lower()
            if any(k in low for k in ("error", "failed", "crash", "exception", "tripped")):
                tag = "error"
            elif any(k in low for k in ("warn", "degraded")):
                tag = "warning"
            elif any(k in low for k in ("success", "connected", "done", "complete", "executed", "recovered")):
                tag = "success"
            elif any(k in low for k in ("start", "midas", "launch", "running")):
                tag = "gold"
            else:
                tag = "info"

        self.log_box.insert(tk.END, text, tag)
        self.log_box.see(tk.END)

        # Keep log buffer bounded
        if int(self.log_box.index("end-1c").split(".")[0]) > 3000:
            self.log_box.delete("1.0", "800.0")

    # ══════════════════════════════════════════════════════════════════════════
    # TOOLS
    # ══════════════════════════════════════════════════════════════════════════

    def _run_backtest(self):
        path = os.path.join(BASE_DIR, "run_backtest.py")
        if not os.path.exists(path):
            messagebox.showerror("Not Found", "run_backtest.py not found.")
            return
        self.log_title.config(text="L O G   O U T P U T  ·  Backtest")
        self._log(f"\n{'─' * 52}\n", "dim")
        self._log("  Backtest running — may take 1–2 minutes…\n", "gold")
        self._log(f"{'─' * 52}\n", "dim")
        try:
            proc = subprocess.Popen(
                [sys.executable, path], cwd=BASE_DIR,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", bufsize=1,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        except Exception as e:
            messagebox.showerror("Error", str(e))
            return
        threading.Thread(target=self._stream, args=("Backtest", proc), daemon=True).start()

    def _open_dashboard(self):
        path = os.path.join(BASE_DIR, "midas_dashboard_local.py")
        if not os.path.exists(path):
            messagebox.showerror("Not Found", "midas_dashboard_local.py not found.")
            return
        subprocess.Popen(
            [sys.executable, path], cwd=BASE_DIR,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        self._log("\n  Dashboard launched — opening in browser…\n", "success")

    # ══════════════════════════════════════════════════════════════════════════
    # CLEANUP
    # ══════════════════════════════════════════════════════════════════════════

    def on_close(self):
        for proc in self.processes.values():
            if proc.poll() is None:
                proc.terminate()
        self.destroy()


# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = MidasLauncher()
    app.protocol("WM_DELETE_WINDOW", app.on_close)
    app.mainloop()
