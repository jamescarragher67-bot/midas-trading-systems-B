"""
strategy/m5_execution_engine.py - Two-layer execution architecture

Layer 1: Daily bias filter
  - At 00:00 UTC: EMA Stack + ATR Expansion + Prev Day Structure must be 3/3 unanimous
  - Returns 'BUY', 'SELL', or 'NONE'

Layer 2: M5 entry conditions
  - EMA21 pullback (candle touches ema_slow and closes in bias direction)
  - ATR14 >= ATR 20-bar MA (volatility active)
  - Momentum candle: body >= 0.6 x ATR, close in top/bottom 25% of range
"""

import pandas as pd
from utils.logger import setup_logger

logger = setup_logger("m5_execution_engine")

ATR_MA_PERIOD      = 20
BODY_ATR_MULT      = 0.6     # candle body must be >= 60% of ATR14
CLOSE_RANGE_THRESH = 0.75    # close must be in top/bottom 25% of bar range
EMA21_COL          = "ema_slow"   # EMA21 stored as ema_slow in indicators
COOLDOWN_BARS      = 3       # 15 minutes = 3 x M5 bars
MAX_TRADES_PER_DAY = 4
SPREAD_MAX_POINTS  = 20


def get_daily_bias(df: pd.DataFrame) -> str:
    """
    Run 3 active structural voters on the current df slice.
    Requires 3/3 unanimous agreement.
    Returns 'BUY', 'SELL', or 'NONE'.
    """
    from strategy.ema_stack import get_signal as ema_signal
    from strategy.atr_expansion import get_signal as atr_expansion_signal
    from strategy.prev_day_structure import get_signal as prev_day_signal

    score = 0
    for fn in [ema_signal, atr_expansion_signal, prev_day_signal]:
        try:
            vote, _ = fn(df)
        except Exception:
            vote = 0
        score += vote

    if score == 3:
        return "BUY"
    if score == -3:
        return "SELL"
    return "NONE"


def check_m5_entry(df: pd.DataFrame, daily_bias: str,
                   last_trade_bar: int, current_bar: int,
                   trades_today: int) -> tuple[str, str]:
    """
    Check all M5 entry conditions against the current bar (df.iloc[-1]).
    Returns (direction, reason). direction is 'BUY', 'SELL', or 'NEUTRAL'.
    """
    if daily_bias == "NONE":
        return "NEUTRAL", "no_daily_bias"

    if trades_today >= MAX_TRADES_PER_DAY:
        return "NEUTRAL", "max_trades_today"

    if current_bar - last_trade_bar < COOLDOWN_BARS:
        return "NEUTRAL", "cooldown"

    bar    = df.iloc[-1]
    close  = float(bar["close"])
    open_  = float(bar["open"])
    high   = float(bar["high"])
    low    = float(bar["low"])
    ema21  = float(bar[EMA21_COL])
    atr    = float(bar["atr"])
    candle_range = high - low

    # Guard: zero-range candle
    if candle_range <= 0 or atr <= 0:
        return "NEUTRAL", "zero_range_or_atr"

    # Volatility: ATR14 must be >= its 20-bar MA
    atr_ma = float(df["atr"].iloc[-(ATR_MA_PERIOD + 1):-1].mean())
    if atr < atr_ma:
        return "NEUTRAL", f"atr_contracting_{atr:.2f}_vs_ma_{atr_ma:.2f}"

    # Momentum candle: body >= 0.6 x ATR
    body = abs(close - open_)
    if body < BODY_ATR_MULT * atr:
        return "NEUTRAL", f"weak_body_{body:.2f}_need_{BODY_ATR_MULT * atr:.2f}"

    if daily_bias == "BUY":
        # Strict EMA21 touch: low touched ema_slow, closed above it
        if not (low <= ema21 and close > ema21):
            return "NEUTRAL", f"no_buy_touch_ema21={ema21:.2f}_low={low:.2f}_close={close:.2f}"
        # Close in top 40% of bar range
        if (close - low) / candle_range < CLOSE_RANGE_THRESH:
            return "NEUTRAL", "close_not_top_40pct"
        return "BUY", f"m5_buy_ema21={ema21:.2f}_body={body:.2f}_atr={atr:.2f}"

    else:  # SELL
        # Strict EMA21 touch: high touched ema_slow, closed below it
        if not (high >= ema21 and close < ema21):
            return "NEUTRAL", f"no_sell_touch_ema21={ema21:.2f}_high={high:.2f}_close={close:.2f}"
        # Close in bottom 40% of bar range
        if (high - close) / candle_range < CLOSE_RANGE_THRESH:
            return "NEUTRAL", "close_not_bottom_40pct"
        return "SELL", f"m5_sell_ema21={ema21:.2f}_body={body:.2f}_atr={atr:.2f}"
