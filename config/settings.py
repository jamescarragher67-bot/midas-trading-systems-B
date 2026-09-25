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
# Derived: every UTC hour inside ALLOWED_SESSIONS. main.py and
# backtest/lsc_engine.py both read THIS, so live and backtest cannot drift.
SESSION_HOURS = {
    h
    for s in ALLOWED_SESSIONS
    for h in range(int(s["start"][:2]), int(s["end"][:2]) + 1)
}

# ── Spread filter — LSC backtests used an 18pt assumption; 20pt live gives ──
# a small buffer without drifting far from what was actually validated.
SPREAD_FILTER_ENABLED = True
MAX_SPREAD_POINTS     = 20

# ── News filter — fail-closed blackout before/after high-impact US events. ──
# XAU is priced in USD, so US macro events move gold. NEWS_WINDOW_MINS is
# applied to both sides (±). Module: utils/news_filter.py.
# Requires FINNHUB_API_KEY in .env. If the key is missing or the feed fails,
# the filter blocks trades (prop-firm-safe default).
NEWS_FILTER_ENABLED = True
NEWS_WINDOW_MINS    = 30
FINNHUB_API_KEY     = os.getenv("FINNHUB_API_KEY")

# ── LSC strategy parameters (strategy/lsc_m15.py) ────────────────────────
CLOSE_BEYOND_ATR_MULT = 0.2
SL_BUFFER_ATR_MULT    = 0.3
REWARD_RATIO          = 2.0
COOLDOWN_BARS         = 3      # 45 minutes = 3 x M15 bars
MAX_TRADES_PER_DAY    = 4
ATR_PERIOD            = 14

# ── Risk — recalibrated 2026-09-15 against month-block-reordered tail risk ──
# research/tools/risk_calibrator.py --account-size 50000 --leverage 10 --total-wall-pct 6 --bars <frozen 90k bars>
# ($50,000 balance, 1:10 leverage, single 6% trailing-drawdown wall).
# CALIBRATION BASIS: 1000-path MONTH-BLOCK REORDERING Monte Carlo
# (research/backtest/monte_carlo.monte_carlo_block_reorder_pct): the trade
# history is cut into calendar-month blocks, the block order is shuffled
# with each month's internal trade sequence preserved, and percent returns
# are recompounded - checked on 5 seeds, decision taken on the worst seed.
# 0.015% is the highest tested level where, on EVERY seed, the 95th-pct
# max drawdown (<= 3.92%) clears the 4.2% target AND the absolute worst
# path (<= 5.66%) clears the 5.7% target; the real historical order draws
# down 2.4%. 0.0175% fails the absolute-worst target on one seed (6.05%,
# over the wall itself). Full sweep: research/lsc_risk_calibration_2026-09-15.txt.
# SUPERSEDES the 0.045% figure set on 2026-09-01. That figure was
# calibrated against an individual-trade shuffle - an insufficiently
# realistic risk model: it destroys LSC's real month-level loss clustering
# and understated the tail roughly 2x. Under month-block reordering 0.045%
# breaches the 6% wall in 8.6% of paths (95th-pct 6.36%, worst 9.14%),
# and the actual historical order reached 5.99%. Do not revert to it.
# KNOWN LIMIT: at $50K the broker's 0.01-lot floor binds on ~43% of trades
# at this level (the formula wants less than 0.01 lot), so realised risk on
# those trades is set by the floor (up to ~0.29%). The drawdown figures
# above already include that effect; a larger account would let the risk%
# actually govern sizing. Do not change this value without re-running the
# calibrator for whatever account is actually live.
RISK_PERCENT = 0.015

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

# ── MT5 reconnect (main.py) — never gives up; wait doubles per failed ──────
# attempt from MIN up to MAX seconds. Watchdog restart policy lives in
# sync/watchdog.py, which deliberately does not import this module.
RECONNECT_DELAY_MIN = 10
RECONNECT_DELAY_MAX = 300

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
