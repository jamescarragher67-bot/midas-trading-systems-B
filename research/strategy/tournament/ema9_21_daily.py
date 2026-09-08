"""
strategy/tournament/ema9_21_daily.py - EMA 9/21 crossover, D1. CONTROL CANDIDATE #6.

Deliberately included as a sanity check. Quant-Signals' 8,693-trade XAUUSD
study flagged this exact setup (D1, 1.5x ATR stop, 2:1 reward:risk) as a
loser: 74 trades, 32.4% win rate, 0.96 profit factor, 11.8% max DD, -0.027R
expectancy. It should NOT survive this tournament. If it does, that is a
signal the tournament's harness/methodology has a bug - not that a
well-known loser is secretly good.
"""

import pandas as pd
import MetaTrader5 as mt5

TIMEFRAME_MT5   = mt5.TIMEFRAME_D1
TIMEFRAME_LABEL = "D1"
MAX_HOLD_BARS   = 30
SESSION_HOURS   = None
COOLDOWN_BARS   = 1
MAX_TRADES_PER_DAY = 1

EMA_FAST, EMA_SLOW = 9, 21
SL_ATR_MULT  = 1.5
REWARD_RATIO = 2.0


def precompute(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["ema_fast"] = df["close"].ewm(span=EMA_FAST, adjust=False).mean()
    df["ema_slow"] = df["close"].ewm(span=EMA_SLOW, adjust=False).mean()
    return df


def check_entry(df: pd.DataFrame, i: int, last_trade_bar: int, trades_today: int,
                cooldown_bars: int, max_trades_per_day: int):
    if trades_today >= max_trades_per_day or i - last_trade_bar < cooldown_bars:
        return "NEUTRAL", "gated", None, None

    prev, bar = df.iloc[i - 1], df.iloc[i]
    if any(pd.isna(x) for x in (prev["ema_fast"], prev["ema_slow"], bar["ema_fast"], bar["ema_slow"], bar["atr"])):
        return "NEUTRAL", "warmup", None, None

    close, atr = bar["close"], bar["atr"]
    cross_up   = bar["ema_fast"] > bar["ema_slow"] and prev["ema_fast"] <= prev["ema_slow"]
    cross_down = bar["ema_fast"] < bar["ema_slow"] and prev["ema_fast"] >= prev["ema_slow"]

    if cross_up:
        sl = close - SL_ATR_MULT * atr
        tp = close + (close - sl) * REWARD_RATIO
        return "BUY", "ema9_cross_above_ema21", sl, tp
    if cross_down:
        sl = close + SL_ATR_MULT * atr
        tp = close - (sl - close) * REWARD_RATIO
        return "SELL", "ema9_cross_below_ema21", sl, tp

    return "NEUTRAL", "no_cross", None, None
