"""
utils/notifications.py
WhatsApp notifications via CallMeBot.

Setup:
  1. Add +34 644 25 34 86 to your WhatsApp contacts
  2. Send: "I allow callmebot to send me messages"
  3. They reply with your API key
  4. Add phone + API key to config/settings.py
  5. Set WHATSAPP_ENABLED = True
"""

import requests
from urllib.parse import quote
from utils.logger import setup_logger
from config.settings import WHATSAPP_ENABLED, WHATSAPP_PHONE, WHATSAPP_API_KEY

logger = setup_logger("notifications")

# ── Core sender ───────────────────────────────────────────────────────────────

def _send(message: str):
    if not WHATSAPP_ENABLED or not WHATSAPP_PHONE or not WHATSAPP_API_KEY:
        return
    try:
        encoded = quote(message)
        url     = (
            f"https://api.callmebot.com/whatsapp.php"
            f"?phone={WHATSAPP_PHONE}&text={encoded}&apikey={WHATSAPP_API_KEY}"
        )
        resp = requests.get(url, timeout=10)
        if resp.ok:
            logger.info("WhatsApp notification sent.")
        else:
            logger.warning(f"WhatsApp send failed: {resp.status_code} {resp.text}")
    except Exception as e:
        logger.warning(f"WhatsApp error: {e}")

# ── Trade opened ──────────────────────────────────────────────────────────────

def send_trade_opened(direction: str, price: float, sl: float, tp: float, lots: float):
    emoji     = "🟢" if direction == "BUY" else "🔴"
    dir_emoji = "📈" if direction == "BUY" else "📉"
    sl_dist   = round(abs(price - sl), 2)
    tp_dist   = round(abs(tp - price), 2)
    rr        = round(tp_dist / sl_dist, 2) if sl_dist > 0 else "—"
    _send(
        f"{emoji} TRADE OPENED\n"
        f"{'─' * 20}\n"
        f"{dir_emoji} XAUUSD {direction}\n"
        f"💰 Entry:  {price:.2f}\n"
        f"🛑 SL:     {sl:.2f}  ({sl_dist:.2f} pts)\n"
        f"🎯 TP:     {tp:.2f}  ({tp_dist:.2f} pts)\n"
        f"📊 RR:     1:{rr}\n"
        f"🔢 Lots:   {lots}"
    )

# ── Trade closed ──────────────────────────────────────────────────────────────

def send_trade_closed(
    result: str,
    direction: str,
    entry: float,
    exit_price: float,
    pnl: float,
    balance: float
):
    emoji     = "✅" if result == "WIN" else "❌"
    sign      = "+" if pnl >= 0 else ""
    price_dir = "▲" if exit_price >= entry else "▼"
    _send(
        f"{emoji} TRADE CLOSED — {result}\n"
        f"{'─' * 20}\n"
        f"📉 XAUUSD {direction}\n"
        f"💰 Entry:   {entry:.2f}\n"
        f"{price_dir}  Exit:    {exit_price:.2f}\n"
        f"💵 P&L:     {sign}${pnl:.2f}\n"
        f"🏦 Balance: ${balance:.2f}"
    )

# ── Hourly update ─────────────────────────────────────────────────────────────

def send_hourly_update(
    balance: float,
    daily_pnl: float,
    trades_today: int,
    wins_today: int,
    losses_today: int,
    open_positions: int
):
    sign    = "+" if daily_pnl >= 0 else ""
    wr      = round((wins_today / trades_today) * 100) if trades_today > 0 else 0
    pos_str = f"{open_positions} open" if open_positions > 0 else "none"
    _send(
        f"⏱️ MIDAS — HOURLY UPDATE\n"
        f"{'─' * 20}\n"
        f"🏦 Balance:    ${balance:.2f}\n"
        f"💵 Day P&L:    {sign}${daily_pnl:.2f}\n"
        f"📊 Trades:     {trades_today} ({wins_today}W / {losses_today}L)\n"
        f"🎯 Win rate:   {wr}%\n"
        f"📂 Positions:  {pos_str}"
    )

# ── Daily summary ─────────────────────────────────────────────────────────────

def send_daily_summary(
    total_trades: int,
    wins: int,
    losses: int,
    total_pnl: float,
    balance: float
):
    win_rate = round((wins / total_trades) * 100) if total_trades > 0 else 0
    sign     = "+" if total_pnl >= 0 else ""
    result   = "📈" if total_pnl >= 0 else "📉"
    _send(
        f"📋 MIDAS — DAILY SUMMARY\n"
        f"{'─' * 20}\n"
        f"{result} Day result\n"
        f"🔢 Trades:   {total_trades} ({wins}W / {losses}L)\n"
        f"🎯 Win rate: {win_rate}%\n"
        f"💵 P&L:      {sign}${total_pnl:.2f}\n"
        f"🏦 Balance:  ${balance:.2f}"
    )

# ── Circuit breaker ───────────────────────────────────────────────────────────

def send_circuit_breaker(reason: str, balance: float):
    _send(
        f"🚨 CIRCUIT BREAKER TRIPPED\n"
        f"{'─' * 20}\n"
        f"⛔ Reason:   {reason}\n"
        f"🏦 Balance:  ${balance:.2f}\n"
        f"🕛 Paused until midnight UTC"
    )

# ── Bot status ────────────────────────────────────────────────────────────────

def send_bot_started(balance: float):
    _send(
        f"🚀 MIDAS STARTED\n"
        f"{'─' * 20}\n"
        f"⚙️  Config:   Midas B\n"
        f"🏦 Balance:  ${balance:.2f}\n"
        f"📡 Watching: XAUUSD"
    )

def send_bot_stopped(reason: str, balance: float):
    _send(
        f"🛑 MIDAS STOPPED\n"
        f"{'─' * 20}\n"
        f"⚠️  Reason:   {reason}\n"
        f"🏦 Balance:  ${balance:.2f}"
    )
