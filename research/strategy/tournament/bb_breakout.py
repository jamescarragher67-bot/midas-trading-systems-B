"""
strategy/tournament/bb_breakout.py - Bollinger Band squeeze breakout, D1.

Candidate #2 in the CC knockout tournament. Concept from the Quant-Signals
8,693-trade XAUUSD study ("BB Squeeze", D1: 55 trades, 36.4% WR, 1.14 PF,
8.0% max DD, +0.091R expectancy) - see quant-signals.com/xauusd-trading-strategies/
(archived 2026-07-21). That article states the risk framework (1.5x ATR stop,
2:1 reward:risk) but does NOT publish the exact numeric "squeeze" detection
threshold used in their own backtest - only the qualitative description
("waits for periods of low volatility/band compression before triggering on
subsequent breakouts"). The squeeze definition below (bandwidth in the bottom
20th percentile of its own trailing 100-bar history) is our own faithful-to-
the-concept implementation, not a verified replica of their exact code -
flagged here rather than silently presented as identical.
"""

import pandas as pd
import MetaTrader5 as mt5

TIMEFRAME_MT5   = mt5.TIMEFRAME_D1
TIMEFRAME_LABEL = "D1"
MAX_HOLD_BARS   = 30      # ~1 month safety-valve close-out
SESSION_HOURS   = None    # daily bars - no intraday session filter
COOLDOWN_BARS   = 1
MAX_TRADES_PER_DAY = 1

BB_PERIOD        = 20
BB_STD           = 2.0
SQUEEZE_LOOKBACK = 100
SQUEEZE_PCTL     = 20     # bandwidth must be <= this percentile of its own trailing history
SL_ATR_MULT      = 1.5
REWARD_RATIO     = 2.0
ATR_PERIOD       = 14


def precompute(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    mid = df["close"].rolling(BB_PERIOD).mean()
    std = df["close"].rolling(BB_PERIOD).std()
    df["bb_mid"]   = mid
    df["bb_upper"] = mid + BB_STD * std
    df["bb_lower"] = mid - BB_STD * std
    bandwidth = (df["bb_upper"] - df["bb_lower"]) / mid
    df["bb_bandwidth"] = bandwidth
    df["bb_squeeze_thresh"] = bandwidth.rolling(SQUEEZE_LOOKBACK).quantile(SQUEEZE_PCTL / 100)
    return df


def check_entry(df: pd.DataFrame, i: int, last_trade_bar: int, trades_today: int,
                cooldown_bars: int, max_trades_per_day: int):
    if trades_today >= max_trades_per_day or i - last_trade_bar < cooldown_bars:
        return "NEUTRAL", "gated", None, None

    prev = df.iloc[i - 1]
    bar  = df.iloc[i]
    if any(pd.isna(x) for x in (prev["bb_bandwidth"], prev["bb_squeeze_thresh"],
                                 bar["bb_upper"], bar["bb_lower"], bar["atr"])):
        return "NEUTRAL", "warmup", None, None

    was_squeezed = prev["bb_bandwidth"] <= prev["bb_squeeze_thresh"]
    if not was_squeezed:
        return "NEUTRAL", "no_squeeze", None, None

    close, atr = bar["close"], bar["atr"]
    if close > bar["bb_upper"]:
        sl = close - SL_ATR_MULT * atr
        tp = close + (close - sl) * REWARD_RATIO
        return "BUY", "squeeze_breakout_up", sl, tp
    if close < bar["bb_lower"]:
        sl = close + SL_ATR_MULT * atr
        tp = close - (sl - close) * REWARD_RATIO
        return "SELL", "squeeze_breakout_down", sl, tp

    return "NEUTRAL", "no_breakout", None, None
