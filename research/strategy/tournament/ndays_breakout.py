"""
strategy/tournament/ndays_breakout.py - N-day channel breakout, D1.
CONTROL CANDIDATE #10.

Representative "simple timing rule" in the spirit of Baur, Dichtl, Drobetz &
Wendt's critique of the technical-trading-rule literature: after correcting
for data-snooping/multiple-testing bias, simple timing rules (moving-average
crossovers, N-day breakouts) on gold did NOT show robust out-of-sample edge.
N=50 trading days (~10 weeks), a standard Donchian-style lookback. Included
deliberately expecting a loss, same role as candidate #6.

Adaptation note: the engine's one-trade-at-a-time / risk-per-trade paradigm
needs an SL/TP, so this is tested as a risk-managed breakout (2x ATR stop,
2:1 reward:risk) rather than a raw always-in-market long/short flip with no
stop at all - a common, reasonable practical form of the rule, not the bare
academic version (which has no risk overlay to even compute a comparable
PF/DD against).
"""

import pandas as pd
import MetaTrader5 as mt5

TIMEFRAME_MT5   = mt5.TIMEFRAME_D1
TIMEFRAME_LABEL = "D1"
MAX_HOLD_BARS   = 30
SESSION_HOURS   = None
COOLDOWN_BARS   = 1
MAX_TRADES_PER_DAY = 1

N_DAYS = 50
SL_ATR_MULT  = 2.0
REWARD_RATIO = 2.0


def precompute(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["n_high"] = df["high"].rolling(N_DAYS).max().shift(1)
    df["n_low"]  = df["low"].rolling(N_DAYS).min().shift(1)
    return df


def check_entry(df: pd.DataFrame, i: int, last_trade_bar: int, trades_today: int,
                cooldown_bars: int, max_trades_per_day: int):
    if trades_today >= max_trades_per_day or i - last_trade_bar < cooldown_bars:
        return "NEUTRAL", "gated", None, None

    bar = df.iloc[i]
    if any(pd.isna(x) for x in (bar["n_high"], bar["n_low"], bar["atr"])):
        return "NEUTRAL", "warmup", None, None

    close, atr = bar["close"], bar["atr"]
    if close > bar["n_high"]:
        sl = close - SL_ATR_MULT * atr
        tp = close + (close - sl) * REWARD_RATIO
        return "BUY", f"{N_DAYS}d_breakout_up", sl, tp
    if close < bar["n_low"]:
        sl = close + SL_ATR_MULT * atr
        tp = close - (sl - close) * REWARD_RATIO
        return "SELL", f"{N_DAYS}d_breakout_down", sl, tp

    return "NEUTRAL", "no_breakout", None, None
