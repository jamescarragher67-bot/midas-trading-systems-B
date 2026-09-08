"""
firebase_push.py  —  Midas Capital · Firebase sync daemon
Reads trades.json, open_positions.json, heartbeat.json every 30 s
and pushes them to Firebase Realtime Database.

--config production (default): reads jasons/, pushes to FIREBASE_DB_URL at
its normal root paths (trades, stats, equity_curve, ...) — unaffected by
anything below.
--config diagnostic: reads jasons_diagnostic/, and pushes are namespaced
under a "diagnostic/" root node so they can never land on the same
Firebase keys as production even if FIREBASE_DB_URL_DIAGNOSTIC isn't set
(same database, different path). If FIREBASE_DB_URL_DIAGNOSTIC IS set in
.env, diagnostic pushes go to that separate database/project entirely.

Install deps once:
    pip install requests
"""

import argparse
import sys
import json
import time
import os
import datetime
import requests
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", ".env"))

# Force UTF-8 output so Unicode chars survive Windows cp1252 terminals/pipes
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_parser = argparse.ArgumentParser(description="MIDAS Firebase sync daemon")
_parser.add_argument("--config", choices=["production", "diagnostic"], default="production",
                      help="production (default, jasons/, FIREBASE_DB_URL) or "
                           "diagnostic (jasons_diagnostic/, namespaced under diagnostic/, "
                           "or FIREBASE_DB_URL_DIAGNOSTIC if set)")
_args, _ = _parser.parse_known_args()
_IS_DIAGNOSTIC = _args.config == "diagnostic"

# ── Firebase config ──────────────────────────────────────────────────────────
if _IS_DIAGNOSTIC:
    DATABASE_URL = os.getenv("FIREBASE_DB_URL_DIAGNOSTIC") or os.getenv("FIREBASE_DB_URL")
    PUSH_PREFIX  = "" if os.getenv("FIREBASE_DB_URL_DIAGNOSTIC") else "diagnostic/"
else:
    DATABASE_URL = os.getenv("FIREBASE_DB_URL")
    PUSH_PREFIX  = ""
if not DATABASE_URL:
    raise SystemExit("ERROR: FIREBASE_DB_URL missing in config/.env"
                      + (" (or FIREBASE_DB_URL_DIAGNOSTIC, for --config diagnostic)" if _IS_DIAGNOSTIC else ""))

# Optional: leave empty string "" if your DB rules allow public write.
# If you add Firebase Auth later, put your ID token here.
AUTH_TOKEN = ""

# ── File paths ────────────────────────────────────────────────────────────────
BASE_DIR        = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_JASONS_DIR     = "jasons_diagnostic" if _IS_DIAGNOSTIC else "jasons"
TRADES_FILE     = os.path.join(BASE_DIR, _JASONS_DIR, "trades.json")
POSITIONS_FILE  = os.path.join(BASE_DIR, _JASONS_DIR, "open_positions.json")
HEARTBEAT_FILE  = os.path.join(BASE_DIR, _JASONS_DIR, "heartbeat.json")

PUSH_INTERVAL   = 30   # seconds

# ── Helpers ───────────────────────────────────────────────────────────────────

def fb_url(path: str) -> str:
    """Build a Firebase REST URL for the given path. PUSH_PREFIX namespaces
    every diagnostic push under diagnostic/ so it can never land on the
    same keys as production, even on a shared database."""
    suffix = f"?auth={AUTH_TOKEN}" if AUTH_TOKEN else ""
    return f"{DATABASE_URL}/{PUSH_PREFIX}{path}.json{suffix}"


def read_json(filepath: str):
    """Read a JSON file and return its contents, or None on error."""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"[WARN] File not found: {filepath}")
        return None
    except json.JSONDecodeError as e:
        print(f"[ERROR] Invalid JSON in {filepath}: {e}")
        return None


def push(path: str, data) -> bool:
    """PUT data to a Firebase path. Returns True on success."""
    try:
        resp = requests.put(fb_url(path), json=data, timeout=10)
        if resp.status_code == 200:
            return True
        print(f"[ERROR] Firebase PUT /{path} → HTTP {resp.status_code}: {resp.text}")
        return False
    except requests.RequestException as e:
        print(f"[ERROR] Network error pushing /{path}: {e}")
        return False


def compute_stats(trades: list) -> dict:
    """
    Derive summary statistics from the closed-trades list.
    Expects each trade dict to have at minimum: pnl (float), rr (float, optional).
    """
    if not trades:
        return {
            "balance": 25000,
            "total_pnl": 0,
            "win_rate": 0,
            "profit_factor": 0,
            "avg_rr": 0,
            "total_trades": 0,
        }

    total_trades = len(trades)
    pnls         = [float(t.get("pnl", 0)) for t in trades]
    total_pnl    = sum(pnls)
    wins         = [p for p in pnls if p > 0]
    losses       = [p for p in pnls if p < 0]
    win_rate     = round(len(wins) / total_trades * 100, 2) if total_trades else 0
    gross_profit = sum(wins)
    gross_loss   = abs(sum(losses))
    profit_factor= round(gross_profit / gross_loss, 2) if gross_loss else float("inf")

    rr_values = [float(t["rr"]) for t in trades if "rr" in t and t["rr"] is not None]
    avg_rr    = round(sum(rr_values) / len(rr_values), 2) if rr_values else 0

    # Balance = starting balance + total PnL.
    # If your trades carry a "balance" field, use the last one instead.
    # FIXED: was hardcoded to 10000, account actually starts at 25000.
    starting_balance = float(trades[0].get("starting_balance", 25000)) if trades else 25000
    balance = starting_balance + total_pnl

    return {
        "balance":       round(balance, 2),
        "total_pnl":     round(total_pnl, 2),
        "win_rate":      win_rate,
        "profit_factor": profit_factor,
        "avg_rr":        avg_rr,
        "total_trades":  total_trades,
    }


def build_equity_curve(trades: list) -> list:
    """
    Return [{timestamp, cumulative_pnl}, …] sorted by close time.
    Expects each trade to have a 'close_time' (ISO string or epoch ms) and 'pnl'.
    """
    if not trades:
        return []
    sorted_trades = sorted(trades, key=lambda t: t.get("close_time", ""))
    curve = []
    cumulative = 0.0
    for t in sorted_trades:
        cumulative += float(t.get("pnl", 0))
        curve.append({
            "timestamp":      t.get("close_time", ""),
            "cumulative_pnl": round(cumulative, 2),
        })
    return curve


# ── Main loop ─────────────────────────────────────────────────────────────────

def sync():
    print(f"[INFO] Starting Midas Capital Firebase sync ({_args.config}, every {PUSH_INTERVAL}s) …")
    print(f"[INFO] Reading from {_JASONS_DIR}/, pushing to {DATABASE_URL}/{PUSH_PREFIX}")

    while True:
        now_iso = datetime.datetime.utcnow().isoformat() + "Z"
        print(f"\n[{now_iso}] Syncing …")

        # 1. Trades
        trades = read_json(TRADES_FILE) or []
        if trades:
            push("trades", trades)
            stats = compute_stats(trades)
            push("stats", stats)
            equity_curve = build_equity_curve(trades)
            push("equity_curve", equity_curve)
            print(f"  ok: trades ({len(trades)} records) / stats / equity_curve")

        # 2. Open positions
        positions = read_json(POSITIONS_FILE)
        if positions is not None:
            push("open_positions", positions)
            print(f"  ok: open_positions")

        # 3. Heartbeat
        heartbeat = read_json(HEARTBEAT_FILE) or {}
        heartbeat["last_push"] = now_iso   # always stamp the last push time
        push("heartbeat", heartbeat)
        print(f"  ok: heartbeat")

        time.sleep(PUSH_INTERVAL)


if __name__ == "__main__":
    sync()