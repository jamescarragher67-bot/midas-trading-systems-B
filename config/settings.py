"""
config/settings.py — Midas: Liquidity-Sweep Continuation (LSC), Stage 4

Single validated strategy, M15 timeframe. No profile system, no
multi-bot config — only the parameters LSC actually uses, plus
infrastructure config shared by any strategy running on this account.
"""

import os
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent / ".env")

import MetaTrader5 as mt5

# ── Core ──────────────────────────────────────────────────────────────────
SYMBOL                = "XAUUSD.a"
MAGIC                 = 20001   # order magic number — new number, not the old Bot1/Bot2 10001
ORDER_DEVIATION       = 20      # max price deviation (points) accepted on order send
SIGNAL_TIMEFRAME      = mt5.TIMEFRAME_M15
LOOP_INTERVAL_SECONDS = 60

# ── MT5 connection (loaded from .env — never hardcode secrets here) ──────
try:
    MT5_LOGIN = int(os.getenv("MT5_LOGIN"))
except (TypeError, ValueError):
    raise SystemExit(
        "ERROR: MT5_LOGIN missing or invalid in config/.env\n"
        "Add: MT5_LOGIN=your_account_number"
    )
MT5_PASSWORD = os.getenv("MT5_PASSWORD")
MT5_SERVER   = os.getenv("MT5_SERVER")

# ── WhatsApp notifications (CallMeBot) ───────────────────────────────────
WHATSAPP_ENABLED = True
WHATSAPP_PHONE   = os.getenv("WHATSAPP_PHONE")
WHATSAPP_API_KEY = os.getenv("CALLMEBOT_API_KEY")

# ── Logging ───────────────────────────────────────────────────────────────
LOG_LEVEL = "INFO"
LOG_DIR   = "logs"

# ── Session filter — matches the window LSC was validated on ────────────
SESSION_FILTER_ENABLED = True
ALLOWED_SESSIONS = [
    {"start": "00:00", "end": "14:59"},   # Asian + London + NY morning
    {"start": "20:00", "end": "23:59"},   # NY PM + evening
]

# ── Spread filter — LSC backtests used an 18pt assumption; 20pt live gives ──
# a small buffer without drifting far from what was actually validated.
SPREAD_FILTER_ENABLED = True
MAX_SPREAD_POINTS     = 20

# ── LSC strategy parameters (strategy/lsc_m15.py) ────────────────────────
CLOSE_BEYOND_ATR_MULT = 0.2
SL_BUFFER_ATR_MULT    = 0.3
REWARD_RATIO          = 2.0
COOLDOWN_BARS         = 3      # 45 minutes = 3 x M15 bars
MAX_TRADES_PER_DAY    = 4
ATR_PERIOD            = 14

# ── Risk — locked at the Stage 4 validated setting ───────────────────────
RISK_PERCENT = 1.0

# Margin-safe position sizing: NEVER let the risk% formula alone decide lot
# size. Tonight proved (M5, M15, and H1 all independently) that naive
# risk-based sizing can demand more margin than the account has, at tight
# SL distances. The safe ceiling is computed live from actual account
# balance/leverage/price at trade time (see risk/trade_manager.py), not
# hardcoded — it must stay correct regardless of which account is
# connected. This is the ceiling on how much of the account's margin any
# single position may use.
MARGIN_SAFETY_BUDGET_PCT = 0.25   # max 25% of account equity committed as margin per trade

# ── Time-based exit — matches what the backtest actually modeled: a hard ──
# fallback close if neither SL nor TP hit within ~1 trading day. No soft/
# profit-conditional exit — that was never tested for LSC.
TIME_EXIT_ENABLED    = True
MAX_TRADE_HOURS_HARD = 24   # M15: 96 bars = 1 day, matches backtest/lsc_engine.py's MAX_HOLD_BARS

# ── Friday cutoff — weekend holds are normal for M15 swing-continuation, ──
# but still force-flat before the weekend gap for safety.
FRIDAY_CUTOFF_HOUR = 20   # UTC — no new trades on Friday after this hour
FRIDAY_CLOSE_HOUR  = 21   # UTC — force-close all positions on Friday after this hour

# ── Circuit breaker ───────────────────────────────────────────────────────
CIRCUIT_BREAKER_ENABLED = True
MAX_CONSECUTIVE_LOSSES  = 4
MAX_DAILY_LOSS_PCT      = 5.0

# ── Startup banner ────────────────────────────────────────────────────────
print("=" * 55)
print("   MIDAS CONFIG — Stage 4: Liquidity-Sweep Continuation (M15)")
print("=" * 55)
print(f"   Symbol:            {SYMBOL}")
print(f"   Risk per trade:    {RISK_PERCENT}%")
print(f"   Reward ratio:      {REWARD_RATIO}:1")
print(f"   Max spread:        {MAX_SPREAD_POINTS}pt")
print(f"   Margin safety cap: {MARGIN_SAFETY_BUDGET_PCT * 100:.0f}% of equity")
print("=" * 55)
