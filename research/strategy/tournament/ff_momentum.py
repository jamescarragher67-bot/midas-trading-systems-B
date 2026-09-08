"""
strategy/tournament/ff_momentum.py - Fama-French-style momentum, adapted to
a single instrument, D1. Candidate #5.

Real Fama-French / Carhart momentum is cross-sectional (rank many assets by
trailing 12-1 month return, long the winners, short the losers) - that
construction is meaningless for a single instrument. The established
single-asset adaptation of the same "trailing formation-period return
predicts continuation" idea is TIME-SERIES ("absolute") momentum
(Moskowitz/Ooi/Pedersen 2012, "Time Series Momentum"): each month, look at
gold's trailing lookback-month return; if positive go/stay long, if negative
go/stay short, hold one month, rebalance. That is what this module
implements - flagged explicitly since "Fama-French style momentum" and
"time-series momentum" are related but not the same construction, and a
single-instrument backtest cannot faithfully reproduce the original
cross-sectional factor.

Lookback = 6 months (~126 trading days), a compromise inside the task's
"6-12 month" range. Rebalances on the first trading day of each new
calendar month. No SL/TP in the conventional sense - the "exit" is time
(next month's rebalance), so SL/TP are set intentionally wide (5x ATR) as a
tail-risk backstop only, and MAX_HOLD_BARS (~21 sessions) is the real exit
mechanism, mirroring how LSC itself uses a hard time-exit as a fallback.
"""

import pandas as pd
import MetaTrader5 as mt5

TIMEFRAME_MT5   = mt5.TIMEFRAME_D1
TIMEFRAME_LABEL = "D1"
MAX_HOLD_BARS   = 21     # ~1 trading month - this IS the exit, not a fallback
SESSION_HOURS   = None
COOLDOWN_BARS   = 1
MAX_TRADES_PER_DAY = 1

LOOKBACK_DAYS = 126       # ~6 trading months
SL_ATR_MULT   = 5.0       # wide tail-risk backstop only
TP_ATR_MULT   = 100.0     # effectively unreachable - MAX_HOLD_BARS is the real exit


def precompute(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["mom_return"] = df["close"].pct_change(LOOKBACK_DAYS)
    df["is_month_start"] = df.index.to_series().dt.month.diff().fillna(1) != 0
    return df


def check_entry(df: pd.DataFrame, i: int, last_trade_bar: int, trades_today: int,
                cooldown_bars: int, max_trades_per_day: int):
    bar = df.iloc[i]
    if not bool(bar["is_month_start"]):
        return "NEUTRAL", "not_rebalance_day", None, None
    if pd.isna(bar["mom_return"]) or pd.isna(bar["atr"]):
        return "NEUTRAL", "warmup", None, None
    if i - last_trade_bar < cooldown_bars:
        return "NEUTRAL", "gated", None, None

    close, atr = bar["close"], bar["atr"]
    if bar["mom_return"] > 0:
        sl = close - SL_ATR_MULT * atr
        tp = close + TP_ATR_MULT * atr
        return "BUY", f"ts_momentum_{bar['mom_return']:.3f}", sl, tp
    elif bar["mom_return"] < 0:
        sl = close + SL_ATR_MULT * atr
        tp = close - TP_ATR_MULT * atr
        return "SELL", f"ts_momentum_{bar['mom_return']:.3f}", sl, tp

    return "NEUTRAL", "flat_momentum", None, None
