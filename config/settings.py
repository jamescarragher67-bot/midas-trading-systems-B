"""
settings.py — Midas (Unified Config)
ONE file. ONE active profile. No more A/B/C mixups.

To switch strategy profile, change ACTIVE_PROFILE below.
"""

import MetaTrader5 as mt5

# ══════════════════════════════════════════════════════════════════════════
# ACTIVE PROFILE — change this one line to switch strategy
# Options: "AGGRESSIVE", "BALANCED", "CONSERVATIVE"
# ══════════════════════════════════════════════════════════════════════════
ACTIVE_PROFILE = "AGGRESSIVE"


# ── Core ──────────────────────────────────────────────────────────────────
SYMBOL                = "XAUUSD"
MAGIC                 = 10001
LOOP_INTERVAL_SECONDS = 30

# ── MT5 connection (loaded from .env — never hardcode secrets here) ──────
import os
from dotenv import load_dotenv
load_dotenv()

MT5_LOGIN    = int(os.getenv("MT5_LOGIN"))
MT5_PASSWORD = os.getenv("MT5_PASSWORD")
MT5_SERVER   = os.getenv("MT5_SERVER")

# ── WhatsApp notifications (CallMeBot) ───────────────────────────────────
WHATSAPP_ENABLED = True
WHATSAPP_PHONE   = os.getenv("WHATSAPP_PHONE")
WHATSAPP_API_KEY = os.getenv("CALLMEBOT_API_KEY")

# ── Logging ───────────────────────────────────────────────────────────────
LOG_LEVEL = "INFO"
LOG_DIR   = "logs"

# ── Session filter ────────────────────────────────────────────────────────
SESSION_FILTER_ENABLED = True
ALLOWED_HOURS_UTC = list(range(0, 15)) + list(range(20, 24))
ALLOWED_SESSIONS  = [{"start": "00:00", "end": "23:59"}]
HOUR_FILTER_ENABLED = False
MIN_HOUR_WIN_RATE   = 40.0
BEST_HOURS_FILE     = "best_hours.json"

# ── Spread / volatility filters ──────────────────────────────────────────
SPREAD_FILTER_ENABLED     = True
MAX_SPREAD                = 50
MAX_SPREAD_POINTS         = 50
VOLATILITY_FILTER_ENABLED = True
MIN_ATR    = 1.0
MAX_ATR    = 20.0
ATR_PERIOD = 14

# ── Regime detector (disabled — hurt backtest performance) ──────────────
REGIME_FILTER_ENABLED   = False
REGIME_ADX_THRESHOLD    = 20
REGIME_ADX_PERIOD       = 14
REGIME_ATR_PERIOD       = 14
REGIME_ATR_MULTIPLIER   = 2.0
REGIME_LOOKBACK         = 50
REGIME_EMA_PERIOD       = 50
RANGING_ADX_MAX         = 20
VOLATILE_ATR_MULTIPLIER = 2.0

# ── Voting engine ─────────────────────────────────────────────────────────
VOTES_REQUIRED = 4   # out of 5 strategies must agree
VOTE_THRESHOLD = 4

# ── Timeframes ────────────────────────────────────────────────────────────
SIGNAL_TIMEFRAME = mt5.TIMEFRAME_M5
MTF_ENABLED        = True
MTF_H1             = mt5.TIMEFRAME_H1
MTF_M15            = mt5.TIMEFRAME_M15
MTF_MIN_CONFLUENCE = 2
EMA_TREND = 50
EMA_SLOW  = 200

# ── Signal engine indicators ─────────────────────────────────────────────
EMA_FAST   = 9
EMA_MEDIUM = 21
RSI_PERIOD = 14
BB_PERIOD  = 20
BB_STD     = 2.0

# ── Trade frequency / limits ─────────────────────────────────────────────
MAX_TRADES_PER_DAY     = 15
COOLDOWN_MINUTES       = 15
MIN_SIGNAL_GAP_MINUTES = 5
MAX_OPEN_TRADES        = 1
MIN_CONFIDENCE         = 0.6

# ── Time-based exits ──────────────────────────────────────────────────────
TIME_EXIT_ENABLED    = True
MAX_TRADE_HOURS      = 24   # soft close — close if open >8h AND in profit
MAX_TRADE_HOURS_HARD = 24   # hard close — close if open >24h regardless
TIME_EXIT_MIN_PROFIT_USD  = 50

# ── Stop loss placement ──────────────────────────────────────────────────
SMART_SL_ENABLED  = False
SMART_SL_LOOKBACK = 20
SMART_SL_BUFFER   = 5
ATR_SL_MULTIPLIER = 1.5

# ── Adaptive ATR ──────────────────────────────────────────────────────────
ADAPTIVE_ATR_ENABLED  = False
ADAPTIVE_ATR_LOOKBACK = 50
ADAPTIVE_ATR_MIN      = 1.0
ADAPTIVE_ATR_MAX      = 3.0

# ── Dynamic risk scaling ──────────────────────────────────────────────────
DYNAMIC_RISK_ENABLED = False

# ── Performance monitor ──────────────────────────────────────────────────
PERF_MONITOR_ENABLED           = True
PERF_MONITOR_LOOKBACK          = 20
PERF_MONITOR_MIN_TRADES        = 10
PERF_MONITOR_MIN_WIN_RATE      = 35.0
PERF_MONITOR_RECOVERY_WIN_RATE = 50.0
PERF_MONITOR_REDUCED_RISK      = 0.5

# ── Circuit breaker ───────────────────────────────────────────────────────
CIRCUIT_BREAKER_ENABLED = True
MAX_CONSECUTIVE_LOSSES  = 4
MAX_DAILY_LOSS_PCT      = 5.0


# ══════════════════════════════════════════════════════════════════════════
# PROFILE DEFINITIONS
# ══════════════════════════════════════════════════════════════════════════
PROFILES = {
    "AGGRESSIVE": {
        "RISK_PERCENT":          2,
        "BREAKEVEN_ENABLED":     False,
        "BREAKEVEN_PIPS":        0,
        "TRAILING_ENABLED":      False,
        "TRAILING_STEP_PIPS":    0,
        "PARTIAL_CLOSE_ENABLED": False,
        "PARTIAL_CLOSE_AT_RR":   0,
        "PARTIAL_CLOSE_PCT":     0,
        "MIN_RR":                1.5,
    },
    "BALANCED": {
        "RISK_PERCENT":          1.5,
        "BREAKEVEN_ENABLED":     False,
        "BREAKEVEN_PIPS":        0,
        "TRAILING_ENABLED":      False,
        "TRAILING_STEP_PIPS":    0,
        "PARTIAL_CLOSE_ENABLED": False,
        "PARTIAL_CLOSE_AT_RR":   0,
        "PARTIAL_CLOSE_PCT":     0,
        "MIN_RR":                2.0,
    },
    "CONSERVATIVE": {
        "RISK_PERCENT":          1.0,
        "BREAKEVEN_ENABLED":     False,
        "BREAKEVEN_PIPS":        0,
        "TRAILING_ENABLED":      False,
        "TRAILING_STEP_PIPS":    0,
        "PARTIAL_CLOSE_ENABLED": False,
        "PARTIAL_CLOSE_AT_RR":   0,
        "PARTIAL_CLOSE_PCT":     0,
        "MIN_RR":                2.5,
    },
}

if ACTIVE_PROFILE not in PROFILES:
    raise ValueError(f"Unknown ACTIVE_PROFILE '{ACTIVE_PROFILE}'. Choose from: {list(PROFILES.keys())}")

_profile = PROFILES[ACTIVE_PROFILE]

RISK_PERCENT          = _profile["RISK_PERCENT"]
BREAKEVEN_ENABLED     = _profile["BREAKEVEN_ENABLED"]
BREAKEVEN_PIPS        = _profile["BREAKEVEN_PIPS"]
TRAILING_ENABLED      = _profile["TRAILING_ENABLED"]
TRAILING_STEP_PIPS    = _profile["TRAILING_STEP_PIPS"]
PARTIAL_CLOSE_ENABLED = _profile["PARTIAL_CLOSE_ENABLED"]
PARTIAL_CLOSE_AT_RR   = _profile["PARTIAL_CLOSE_AT_RR"]
PARTIAL_CLOSE_PCT     = _profile["PARTIAL_CLOSE_PCT"]
MIN_RR                = _profile["MIN_RR"]
REWARD_RATIO          = MIN_RR


# ── Startup banner ────────────────────────────────────────────────────────
print("=" * 55)
print(f"   MIDAS CONFIG - Profile: {ACTIVE_PROFILE}")
print("=" * 55)
print(f"   Risk per trade:    {RISK_PERCENT}%")
print(f"   Min RR:            {MIN_RR}:1")
print(f"   Breakeven:         {BREAKEVEN_ENABLED}")
print(f"   Trailing:          {TRAILING_ENABLED}")
print(f"   Partial close:     {PARTIAL_CLOSE_ENABLED}")
print("=" * 55)


# ══════════════════════════════════════════════════════════════════════════
# CATCH-ALL — any setting not defined above defaults safely instead of
# crashing with ImportError.
# ══════════════════════════════════════════════════════════════════════════
def __getattr__(name):
    if name.endswith("_ENABLED"):
        default = False
    elif name.endswith(("_PCT", "_PERCENT", "_RATE", "_MULTIPLIER", "_STD")):
        default = 0.0
    elif name.endswith(("_PERIOD", "_HOURS", "_MINUTES", "_PIPS", "_POINTS",
                          "_THRESHOLD", "_TRADES", "_DAY", "_RR")):
        default = 0
    elif name.endswith("_FILE"):
        default = ""
    else:
        default = None

    print(f"[settings] WARNING: '{name}' not defined, defaulting to {default!r}")
    return default