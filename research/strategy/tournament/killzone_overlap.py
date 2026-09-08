"""
strategy/tournament/killzone_overlap.py - London/NY overlap session filter
layered on a simple trend + range-breakout trigger, M15. Candidate #4.

Not from any single external source - built per the task spec ("killzone/
session-overlay strategy - London/NY overlap timing filter layered on a
simple trend signal"). London is ~08:00-17:00 UTC, NY ~13:00-22:00 UTC; the
overlap is ~13:00-17:00 UTC, the highest-liquidity window in FX/gold. Trend
filter: close vs EMA50. Trigger: break of the prior 4-bar high/low in the
trend direction (a "simple trend signal", deliberately not LSC's own
liquidity-sweep logic - this candidate tests whether session timing alone
adds edge to a plain breakout, not a repeat of LSC).
"""

import pandas as pd
import MetaTrader5 as mt5

TIMEFRAME_MT5   = mt5.TIMEFRAME_M15
TIMEFRAME_LABEL = "M15"
MAX_HOLD_BARS   = 96
SESSION_HOURS   = {13, 14, 15, 16}   # London/NY overlap, UTC
COOLDOWN_BARS   = 3
MAX_TRADES_PER_DAY = 4

EMA_TREND    = 50
RANGE_BARS   = 4
SL_BUFFER_ATR_MULT = 0.3
REWARD_RATIO = 2.0


def precompute(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["ema_trend"] = df["close"].ewm(span=EMA_TREND, adjust=False).mean()
    df["range_high"] = df["high"].rolling(RANGE_BARS).max().shift(1)
    df["range_low"]  = df["low"].rolling(RANGE_BARS).min().shift(1)
    return df


def check_entry(df: pd.DataFrame, i: int, last_trade_bar: int, trades_today: int,
                cooldown_bars: int, max_trades_per_day: int):
    if trades_today >= max_trades_per_day or i - last_trade_bar < cooldown_bars:
        return "NEUTRAL", "gated", None, None

    bar = df.iloc[i]
    if any(pd.isna(x) for x in (bar["ema_trend"], bar["range_high"], bar["range_low"], bar["atr"])):
        return "NEUTRAL", "warmup", None, None

    close, high, low, atr = bar["close"], bar["high"], bar["low"], bar["atr"]
    uptrend   = close > bar["ema_trend"]
    downtrend = close < bar["ema_trend"]
    buffer    = atr * SL_BUFFER_ATR_MULT

    if uptrend and high > bar["range_high"]:
        sl = bar["range_low"] - buffer
        sl_dist = close - sl
        if sl_dist > 0:
            tp = close + sl_dist * REWARD_RATIO
            return "BUY", "overlap_range_breakout_up", sl, tp

    if downtrend and low < bar["range_low"]:
        sl = bar["range_high"] + buffer
        sl_dist = sl - close
        if sl_dist > 0:
            tp = close - sl_dist * REWARD_RATIO
            return "SELL", "overlap_range_breakout_down", sl, tp

    return "NEUTRAL", "no_breakout", None, None
