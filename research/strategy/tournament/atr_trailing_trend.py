"""
strategy/tournament/atr_trailing_trend.py - EMA20/SMA9 cross entry with a
2xATR initial stop, trailed at 1xATR (chandelier-style), H1. Candidate #9.

From the task spec: "the TradingView EMA20/SMA9 + 2xATR stop, trail at 1xATR
concept" - a common retail TradingView trend-following template (fast
MA-cross entry, ATR-based initial stop, ATR chandelier trail, no fixed take-
profit - ride the trend until the trailing stop catches it). Uses the
engine's trailing-stop exit mode (check_entry returns tp=None).
"""

import pandas as pd
import MetaTrader5 as mt5

TIMEFRAME_MT5   = mt5.TIMEFRAME_H1
TIMEFRAME_LABEL = "H1"
MAX_HOLD_BARS   = 500     # safety valve only - trailing stop is the real exit
SESSION_HOURS   = None
COOLDOWN_BARS   = 1
MAX_TRADES_PER_DAY = 2

EMA_PERIOD = 20
SMA_PERIOD = 9
INIT_SL_ATR_MULT = 2.0
TRAIL_ATR_MULT    = 1.0


def precompute(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["ema20"] = df["close"].ewm(span=EMA_PERIOD, adjust=False).mean()
    df["sma9"]  = df["close"].rolling(SMA_PERIOD).mean()
    return df


def check_entry(df: pd.DataFrame, i: int, last_trade_bar: int, trades_today: int,
                cooldown_bars: int, max_trades_per_day: int):
    if trades_today >= max_trades_per_day or i - last_trade_bar < cooldown_bars:
        return "NEUTRAL", "gated", None, None

    prev, bar = df.iloc[i - 1], df.iloc[i]
    if any(pd.isna(x) for x in (prev["ema20"], prev["sma9"], bar["ema20"], bar["sma9"], bar["atr"])):
        return "NEUTRAL", "warmup", None, None

    close, atr = bar["close"], bar["atr"]
    cross_up   = bar["ema20"] > bar["sma9"] and prev["ema20"] <= prev["sma9"]
    cross_down = bar["ema20"] < bar["sma9"] and prev["ema20"] >= prev["sma9"]

    if cross_up:
        sl = close - INIT_SL_ATR_MULT * atr
        return "BUY", "ema20_cross_above_sma9", sl, None
    if cross_down:
        sl = close + INIT_SL_ATR_MULT * atr
        return "SELL", "ema20_cross_below_sma9", sl, None

    return "NEUTRAL", "no_cross", None, None
