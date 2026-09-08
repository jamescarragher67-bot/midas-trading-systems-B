"""
config/settings_diagnostic.py — Midas: LSC diagnostic account ($5K FundedNext)

NOT a second deployment target. This is an isolated plumbing/execution/
signal-sanity test: does the bot connect, read signals, size positions,
place orders, log trades, and sync correctly — on a real funded account,
end to end. It is NOT expected to survive: tools/risk_calibrator.py's
lot-floor check (2026-09-01, $5,000, leverage 1:10) found the broker's
0.01-lot minimum forced onto 85-100% of trades regardless of risk% dialed
in (checked 0.005%-0.5%), each floor-clamped trade risking up to several
percent of balance uncontrolled. This account is expected to breach
quickly. No resets planned — a breach here is an expected test outcome,
not an incident.

Completely separate from config/settings.py (the real $50,000 / 0.045%
deployment config - see its own header). Uses its own MT5_DIAGNOSTIC_*
env vars specifically so both accounts' credentials can sit in the same
config/.env without one overwriting the other, and its own MAGIC number
so trade tracking / circuit breaker state never conflate the two accounts.
"""

import os
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent / ".env")

import MetaTrader5 as mt5

# ── Core ──────────────────────────────────────────────────────────────────
SYMBOL                = "XAUUSD.a"   # same instrument as production - this tests the account, not a different symbol
MAGIC                 = 20002        # distinct from production's 20001 - never conflate the two accounts
ORDER_DEVIATION       = 20
SIGNAL_TIMEFRAME      = mt5.TIMEFRAME_M15
LOOP_INTERVAL_SECONDS = 60

# ── MT5 connection — DIAGNOSTIC account, own env vars ────────────────────
# Deliberately MT5_DIAGNOSTIC_* rather than MT5_LOGIN/PASSWORD/SERVER, so
# this can sit in the same config/.env as the production credentials
# without either one clobbering the other. Not set yet - fails fast below
# exactly like config/settings.py does, until the FundedNext $5K
# credentials are actually added.
try:
    MT5_LOGIN = int(os.getenv("MT5_DIAGNOSTIC_LOGIN"))
except (TypeError, ValueError):
    raise SystemExit(
        "ERROR: MT5_DIAGNOSTIC_LOGIN missing or invalid in config/.env\n"
        "Add once the $5K FundedNext account exists:\n"
        "  MT5_DIAGNOSTIC_LOGIN=your_diagnostic_account_number\n"
        "  MT5_DIAGNOSTIC_PASSWORD=your_diagnostic_password\n"
        "  MT5_DIAGNOSTIC_SERVER=your_diagnostic_server"
    )
MT5_PASSWORD = os.getenv("MT5_DIAGNOSTIC_PASSWORD")
MT5_SERVER   = os.getenv("MT5_DIAGNOSTIC_SERVER")

# ── WhatsApp / news — shared infra, same phone/feed regardless of account ──
WHATSAPP_ENABLED = True
WHATSAPP_PHONE   = os.getenv("WHATSAPP_PHONE")
WHATSAPP_API_KEY = os.getenv("CALLMEBOT_API_KEY")

# ── Logging ───────────────────────────────────────────────────────────────
LOG_LEVEL = "INFO"
LOG_DIR   = "logs"

# ── Session filter — same as production, same validated window ──────────
SESSION_FILTER_ENABLED = True
ALLOWED_SESSIONS = [
    {"start": "00:00", "end": "14:59"},
    {"start": "20:00", "end": "23:59"},
]

# ── Spread filter ─────────────────────────────────────────────────────────
SPREAD_FILTER_ENABLED = True
MAX_SPREAD_POINTS     = 20

# ── News filter ────────────────────────────────────────────────────────────
NEWS_FILTER_ENABLED = True
NEWS_WINDOW_MINS    = 30
FINNHUB_API_KEY     = os.getenv("FINNHUB_API_KEY")

# ── LSC strategy parameters — identical to production ────────────────────
# This is testing the ACCOUNT, not a different strategy - keep every
# parameter matched to config/settings.py so the diagnostic actually
# exercises the same signal logic that would run for real.
CLOSE_BEYOND_ATR_MULT = 0.2
SL_BUFFER_ATR_MULT    = 0.3
REWARD_RATIO          = 2.0
COOLDOWN_BARS         = 3
MAX_TRADES_PER_DAY    = 4
ATR_PERIOD            = 14

# ── Risk — carried over from the real $50K/0.045% calibration, NOT a ────
# fresh number for $5K. At this account size the 0.01-lot floor decides
# actual risk regardless of what's dialed in here (see module docstring) -
# this value exists so the diagnostic exercises the real position-sizing
# code path, not because 0.045% means anything protective at $5,000.
RISK_PERCENT = 0.045

MARGIN_SAFETY_BUDGET_PCT = 0.25

# ── Time-based exit / Friday cutoff — identical to production ───────────
TIME_EXIT_ENABLED    = True
MAX_TRADE_HOURS_HARD = 24
FRIDAY_CUTOFF_HOUR = 20
FRIDAY_CLOSE_HOUR  = 21

# ── Circuit breaker — identical to production ────────────────────────────
CIRCUIT_BREAKER_ENABLED = True
MAX_CONSECUTIVE_LOSSES  = 4
MAX_DAILY_LOSS_PCT      = 5.0

# ── Startup banner — unmistakably labeled, so a diagnostic session's ────
# logs can never be confused with the real $50K production run.
print("=" * 55)
print("   MIDAS CONFIG — *** DIAGNOSTIC *** ($5K FundedNext)")
print("   Plumbing/signal-sanity test only. NOT a real deployment.")
print("   Expected to breach quickly (0.01-lot floor dominates).")
print("=" * 55)
print(f"   Symbol:            {SYMBOL}")
print(f"   Risk per trade:    {RISK_PERCENT}%  (floor-dominated at this size)")
print(f"   Reward ratio:      {REWARD_RATIO}:1")
print(f"   Max spread:        {MAX_SPREAD_POINTS}pt")
print(f"   Margin safety cap: {MARGIN_SAFETY_BUDGET_PCT * 100:.0f}% of equity")
print("=" * 55)
