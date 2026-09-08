"""
strategy/tournament/msb_trend_breakout.py - "MSB Trend Breakout" concept:
filtered MA trend confirmation + market-structure-break continuation, M15.
Candidate #8.

SOURCE CAVEAT (reporting plainly, per standing instructions): "MSB Trend
Breakout" here refers to a class of retail TradingView indicators (e.g.
kmozkan's "MSB Trend Breakout Indicator", described as tuned for XAUUSD on
15m/30m) plus the general "Market Structure Break" concept documented on
MQL5's blog and various trading-glossary sites. There is no single published
rule set or open-sourced formula for these indicators - what exists is a
qualitative description: an MA-based trend filter, and an entry only on a
FULL CANDLE CLOSE beyond the most recent confirmed swing high/low in the
trend direction (a wick that pierces and retraces doesn't count - it's read
as a liquidity sweep instead). What follows is our own mechanical
implementation of that qualitative description - not a verified replica of
any specific vendor's indicator code.

Mechanics: trend = close vs EMA50. Swing points = 5-bar fractals (a swing
high at bar k if high[k] is the max of high[k-2..k+2]; swing low mirrored).
MSB = current bar's CLOSE beyond the most recent confirmed swing high/low in
the trend direction. SL beyond that swing point + ATR buffer, TP at 2R.
"""

import pandas as pd
import numpy as np
import MetaTrader5 as mt5

TIMEFRAME_MT5   = mt5.TIMEFRAME_M15
TIMEFRAME_LABEL = "M15"
MAX_HOLD_BARS   = 96
SESSION_HOURS   = None
COOLDOWN_BARS   = 3
MAX_TRADES_PER_DAY = 4

EMA_TREND = 50
FRACTAL_WING = 2       # 2 bars each side -> 5-bar fractal
SL_BUFFER_ATR_MULT = 0.3
REWARD_RATIO = 2.0


def precompute(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["ema_trend"] = df["close"].ewm(span=EMA_TREND, adjust=False).mean()

    high, low = df["high"].values, df["low"].values
    n = len(df)
    w = FRACTAL_WING
    swing_high = np.full(n, np.nan)
    swing_low  = np.full(n, np.nan)
    for k in range(w, n - w):
        window_h = high[k - w:k + w + 1]
        if high[k] == window_h.max() and np.argmax(window_h) == w:
            swing_high[k] = high[k]
        window_l = low[k - w:k + w + 1]
        if low[k] == window_l.min() and np.argmin(window_l) == w:
            swing_low[k] = low[k]

    # A fractal at bar k is only confirmed w bars later - carry the most
    # recent CONFIRMED swing forward (no lookahead: at bar i we only know
    # about a fractal centered at k <= i - w).
    last_confirmed_high = pd.Series(swing_high, index=df.index).shift(w).ffill()
    last_confirmed_low  = pd.Series(swing_low,  index=df.index).shift(w).ffill()
    df["last_swing_high"] = last_confirmed_high
    df["last_swing_low"]  = last_confirmed_low
    return df


def check_entry(df: pd.DataFrame, i: int, last_trade_bar: int, trades_today: int,
                cooldown_bars: int, max_trades_per_day: int):
    if trades_today >= max_trades_per_day or i - last_trade_bar < cooldown_bars:
        return "NEUTRAL", "gated", None, None

    bar = df.iloc[i]
    swing_high, swing_low, atr = bar["last_swing_high"], bar["last_swing_low"], bar["atr"]
    if any(pd.isna(x) for x in (swing_high, swing_low, atr, bar["ema_trend"])):
        return "NEUTRAL", "warmup", None, None

    close = bar["close"]
    uptrend   = close > bar["ema_trend"]
    downtrend = close < bar["ema_trend"]
    buffer = atr * SL_BUFFER_ATR_MULT

    if uptrend and close > swing_high:
        sl = swing_low - buffer if not pd.isna(swing_low) and swing_low < close else close - buffer * 3
        sl_dist = close - sl
        if sl_dist > 0:
            tp = close + sl_dist * REWARD_RATIO
            return "BUY", f"msb_break_above_{swing_high:.2f}", sl, tp

    if downtrend and close < swing_low:
        sl = swing_high + buffer if not pd.isna(swing_high) and swing_high > close else close + buffer * 3
        sl_dist = sl - close
        if sl_dist > 0:
            tp = close - sl_dist * REWARD_RATIO
            return "SELL", f"msb_break_below_{swing_low:.2f}", sl, tp

    return "NEUTRAL", "no_msb", None, None
