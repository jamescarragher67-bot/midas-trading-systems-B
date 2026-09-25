"""
trade_sync.py
Runs alongside main.py — syncs closed trades, open positions, and heartbeat.
Correctly matches entry and exit deals via position_id for accurate RR logging.
Sends WhatsApp notifications on trade close and hourly balance updates.

--config production (default) uses config/settings.py, MAGIC 20001, and
jasons/ — completely unaffected by anything below; this is the existing
behavior main.py's production run depends on.
--config diagnostic uses config/settings_diagnostic.py, MAGIC 20002, and
jasons_diagnostic/, so the $5K diagnostic account's trade records can
never mix with or overwrite the production account's.
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path
from datetime import datetime, timezone, timedelta

# Resolve project root so imports from utils/ and config/ work regardless of CWD
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
os.chdir(_ROOT)

import MetaTrader5 as mt5
from utils.logger import setup_logger
from utils.mt5_connection import connect_mt5, disconnect_mt5
from utils.notifications import send_trade_closed, send_hourly_update

_parser = argparse.ArgumentParser(description="MIDAS trade sync daemon")
_parser.add_argument("--config", choices=["production", "diagnostic"], default="production",
                      help="production (default, config/settings.py, jasons/) or "
                           "diagnostic (config/settings_diagnostic.py, jasons_diagnostic/)")
_args, _ = _parser.parse_known_args()

if _args.config == "diagnostic":
    from config import settings_diagnostic as _cfg
    _JASONS_DIR = "jasons_diagnostic"
else:
    from config import settings as _cfg
    _JASONS_DIR = "jasons"

SYMBOL       = _cfg.SYMBOL
MAGIC        = _cfg.MAGIC
_MT5_LOGIN   = _cfg.MT5_LOGIN
_MT5_PASSWORD = _cfg.MT5_PASSWORD
_MT5_SERVER  = _cfg.MT5_SERVER

logger = setup_logger("trade_sync")

os.makedirs(str(_ROOT / _JASONS_DIR), exist_ok=True)

TRADES_FILE    = str(_ROOT / _JASONS_DIR / "trades.json")
SEEN_FILE      = str(_ROOT / _JASONS_DIR / "seen_tickets.json")
HEARTBEAT_FILE = str(_ROOT / _JASONS_DIR / "heartbeat.json")
POSITIONS_FILE = str(_ROOT / _JASONS_DIR / "open_positions.json")
CHECK_INTERVAL = 30       # seconds between sync cycles

# ── JSON helpers ──────────────────────────────────────────────────────────────

def load_json(path, default):
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return default

def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)

# ── Heartbeat ─────────────────────────────────────────────────────────────────

def write_heartbeat(status: str = "running"):
    """status reflects the ACTUAL MT5 link: "running" only when the terminal
    answers, "disconnected" while reconnecting. A blind "running" was proven
    (2026-09-15) to keep being written for a process whose MT5 IPC had died."""
    save_json(HEARTBEAT_FILE, {
        "last_seen": datetime.now(timezone.utc).isoformat(),
        "status":    status,
    })


def _reconnect():
    """
    Same never-give-up policy as main.py._reconnect: retry until the terminal
    answers, waiting RECONNECT_DELAY_MIN doubling up to RECONNECT_DELAY_MAX
    between attempts. connect_mt5() relaunches terminal64.exe if it is not
    running. No WhatsApp from here - main.py already alerts on the same
    outage, and a second alert per outage would be noise.

    Why this exists: a process that was connected before the terminal died
    stays at (-10001, 'IPC send failed') FOREVER after the terminal is
    relaunched by another process - only its own initialize() revives it.
    Before 2026-09-15 this loop never re-initialised, so one MT5 restart
    left trade_sync silently dead while still writing heartbeats.
    """
    delay   = getattr(_cfg, "RECONNECT_DELAY_MIN", 10)
    d_max   = getattr(_cfg, "RECONNECT_DELAY_MAX", 300)
    started = time.time()
    attempt = 0
    while True:
        attempt += 1
        time.sleep(delay)
        if connect_mt5(login=_MT5_LOGIN, password=_MT5_PASSWORD, server=_MT5_SERVER, symbol=SYMBOL):
            logger.info(f"MT5 reconnected (attempt {attempt}, {time.time() - started:.0f}s outage)")
            return
        logger.warning(f"Reconnect attempt {attempt} failed: {mt5.last_error()} — next attempt in "
                       f"{min(delay * 2, d_max)}s")
        delay = min(delay * 2, d_max)

# ── Balance helper ────────────────────────────────────────────────────────────

def get_balance() -> float:
    info = mt5.account_info()
    return round(info.balance, 2) if info else 0.0

# ── RR helper ─────────────────────────────────────────────────────────────────

def get_rr(position_id: int, entry_price: float, exit_price: float, pnl: float):
    """
    Calculate Risk:Reward for a closed trade using the original SL on the
    entry order. Returns rr as a float (positive for wins, negative for
    losses), or None if SL info isn't available.
    """
    try:
        orders = mt5.history_orders_get(position=position_id)
        if not orders:
            return None

        # The entry order is the one that opened the position
        entry_order = next((o for o in orders if o.position_id == position_id and o.sl != 0), None)
        if entry_order is None:
            entry_order = orders[0]

        sl = entry_order.sl
        if not sl:
            return None

        risk_distance = abs(entry_price - sl)
        if risk_distance == 0:
            return None

        reward_distance = abs(exit_price - entry_price)
        rr = reward_distance / risk_distance

        return round(rr, 2) if pnl > 0 else round(-rr, 2)

    except Exception as e:
        logger.debug(f"Could not compute RR for position {position_id}: {e}")
        return None

# ── Open positions ────────────────────────────────────────────────────────────

def sync_open_positions() -> int:
    """Sync open positions to file. Returns count of open positions."""
    positions = mt5.positions_get(symbol=SYMBOL)
    pos_list  = []
    if positions:
        for p in positions:
            if p.magic != MAGIC:
                continue
            pos_list.append({
                "ticket":     p.ticket,
                "type":       "BUY" if p.type == 0 else "SELL",
                "volume":     round(p.volume, 2),
                "price_open": round(p.price_open, 2),
                "profit":     round(p.profit, 2),
                "time_open":  datetime.fromtimestamp(p.time, tz=timezone.utc).strftime("%H:%M"),
            })
    save_json(POSITIONS_FILE, {
        "positions":    pos_list,
        "last_updated": datetime.now(timezone.utc).isoformat()
    })
    return len(pos_list)

# ── Daily stats helper ────────────────────────────────────────────────────────

def get_today_stats(trades: list) -> dict:
    """Return today's trade stats from the trades list."""
    today        = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    today_trades = [t for t in trades if t.get("date") == today]
    wins         = [t for t in today_trades if t.get("result") == "WIN"]
    losses       = [t for t in today_trades if t.get("result") == "LOSS"]
    pnl          = round(sum(float(t.get("pnl", 0)) for t in today_trades), 2)
    return {
        "total":  len(today_trades),
        "wins":   len(wins),
        "losses": len(losses),
        "pnl":    pnl
    }

# ── Core sync ─────────────────────────────────────────────────────────────────

def sync(new_trade_callback):
    """
    Fetch closed deals from MT5, match entry/exit pairs, log new trades.
    Calls new_trade_callback(trade, balance) for each newly closed trade.
    """
    seen   = set(load_json(SEEN_FILE, []))
    trades = load_json(TRADES_FILE, [])

    now       = datetime.now(timezone.utc)
    from_date = now - timedelta(days=7)
    to_date   = now
    deals     = mt5.history_deals_get(from_date, to_date)

    if not deals:
        logger.debug(f"No deals returned. Error: {mt5.last_error()}")
        return trades

    logger.info(f"Found {len(deals)} total deals in history")

    # ── Separate entry and exit deals ─────────────────────────────────────────
    # MT5 represents each trade as two deals sharing the same position_id:
    #   DEAL_ENTRY_IN  → opening deal  (entry price)
    #   DEAL_ENTRY_OUT → closing deal  (exit price + realised P&L)

    entry_deals = {}
    exit_deals  = []

    for d in deals:
        if d.symbol != SYMBOL:
            continue
        if d.magic != MAGIC:
            continue
        if d.entry == mt5.DEAL_ENTRY_IN:
            entry_deals[d.position_id] = d
        elif d.entry == mt5.DEAL_ENTRY_OUT:
            exit_deals.append(d)

    logger.debug(f"Entry deals: {len(entry_deals)} | Exit deals: {len(exit_deals)}")

    # ── Match and log ─────────────────────────────────────────────────────────
    new_count = 0
    for exit_d in exit_deals:
        if str(exit_d.ticket) in seen:
            continue

        entry_d = entry_deals.get(exit_d.position_id)
        if entry_d is None:
            logger.warning(f"No entry deal for position_id {exit_d.position_id} — skipping")
            continue

        entry_price = round(entry_d.price, 2)
        exit_price  = round(exit_d.price, 2)
        pnl         = round(exit_d.profit, 2)
        direction   = "BUY" if entry_d.type == mt5.DEAL_TYPE_BUY else "SELL"
        price_delta = round(abs(exit_price - entry_price), 2)
        rr          = get_rr(exit_d.position_id, entry_price, exit_price, pnl)

        trade = {
            "id":          exit_d.ticket,
            "position_id": exit_d.position_id,
            "date":        datetime.fromtimestamp(exit_d.time, tz=timezone.utc).strftime("%Y-%m-%d"),
            "time":        datetime.fromtimestamp(exit_d.time, tz=timezone.utc).strftime("%H:%M"),
            "time_open":   datetime.fromtimestamp(entry_d.time, tz=timezone.utc).strftime("%H:%M"),
            "dir":         direction,
            "entry":       entry_price,
            "exit":        exit_price,
            "price_delta": price_delta,
            "lots":        round(exit_d.volume, 2),
            "pnl":         pnl,
            "rr":          rr if rr is not None else 0.0,
            "result":      "WIN" if pnl > 0 else ("BE" if pnl == 0.0 else "LOSS"),
            "notes":       "Auto-synced",
            "source":      "auto"
        }

        trades.append(trade)
        seen.add(str(exit_d.ticket))
        new_count += 1

        rr_str = f"{rr:+.2f}R" if rr is not None else "RR n/a"
        logger.info(
            f"Synced: {direction} | {trade['date']} {trade['time_open']} to {trade['time']} | "
            f"Entry: {entry_price} Exit: {exit_price} Delta: {price_delta} | "
            f"P&L: {pnl} | {rr_str} | {trade['result']}"
        )

        # Fire WhatsApp notification for this trade
        balance = get_balance()
        new_trade_callback(trade, balance)

    if new_count > 0:
        save_json(TRADES_FILE, trades)
        save_json(SEEN_FILE, list(seen))
        logger.info(f"{new_count} new trade(s) saved.")
    else:
        logger.debug("No new trades this cycle.")

    return trades

# ── Hourly notification ───────────────────────────────────────────────────────

def send_hourly(trades: list):
    balance    = get_balance()
    stats      = get_today_stats(trades)
    open_count = len(load_json(POSITIONS_FILE, {}).get("positions", []))
    logger.info("Sending hourly WhatsApp update...")
    send_hourly_update(
        balance        = balance,
        daily_pnl      = stats["pnl"],
        trades_today   = stats["total"],
        wins_today     = stats["wins"],
        losses_today   = stats["losses"],
        open_positions = open_count
    )

# ── Trade closed callback ─────────────────────────────────────────────────────

def on_trade_closed(trade: dict, balance: float):
    """Called immediately after each new trade is logged."""
    send_trade_closed(
        result     = trade["result"],
        direction  = trade["dir"],
        entry      = trade["entry"],
        exit_price = trade["exit"],
        pnl        = trade["pnl"],
        balance    = balance
    )

# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    logger.info(f"========== Trade Sync Starting ({_args.config}) ==========")
    if not connect_mt5(login=_MT5_LOGIN, password=_MT5_PASSWORD, server=_MT5_SERVER, symbol=SYMBOL):
        return

    logger.info(f"Watching {SYMBOL} every {CHECK_INTERVAL}s... [{_JASONS_DIR}/]")

    last_hourly = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    trades      = load_json(TRADES_FILE, [])

    try:
        while True:
            if mt5.terminal_info() is None:
                logger.warning(f"MT5 connection lost (error {mt5.last_error()}) — reconnecting...")
                write_heartbeat("disconnected")
                _reconnect()
            write_heartbeat("running")

            try:
                # Sync trades and positions
                trades     = sync(on_trade_closed)
                open_count = sync_open_positions()

                # Hourly update — fires once at the top of each hour
                now          = datetime.now(timezone.utc)
                current_hour = now.replace(minute=0, second=0, microsecond=0)
                if current_hour > last_hourly:
                    send_hourly(trades)
                    last_hourly = current_hour
            except Exception as e:
                logger.error(f"Sync loop error: {e}", exc_info=True)

            time.sleep(CHECK_INTERVAL)

    except KeyboardInterrupt:
        logger.info("Stopped.")
    finally:
        disconnect_mt5()

if __name__ == "__main__":
    main()