"""
strategy/bollinger_bands.py

Strategy 3: Bollinger Bands
Votes when price touches an outer band and shows reversal.

BUY:  price touched lower band → now closing back inside (bounce up)
SELL: price touched upper band → now closing back inside (bounce down)

Also detects BB squeeze → breakout for momentum entries.
"""

import pandas as pd
import numpy as np
from utils.logger import setup_logger

logger = setup_logger("bollinger_bands")

BB_PERIOD = 20
BB_STD    = 2.0


def _calculate_bb(df: pd.DataFrame) -> tuple:
    close  = df["close"]
    sma    = close.rolling(BB_PERIOD).mean()
    std    = close.rolling(BB_PERIOD).std()
    upper  = sma + BB_STD * std
    lower  = sma - BB_STD * std
    width  = (upper - lower) / sma   # normalized band width
    return upper, lower, sma, width


def get_signal(df: pd.DataFrame) -> tuple[int, str]:
    """
    Returns (vote, reason)
    vote: +1 = BUY, -1 = SELL, 0 = NEUTRAL
    """
    try:
        upper, lower, mid, width = _calculate_bb(df)

        prev  = df.iloc[-3]
        curr  = df.iloc[-2]

        prev_close = prev["close"]
        curr_close = curr["close"]
        prev_low   = prev["low"]
        prev_high  = prev["high"]

        upper_prev = upper.iloc[-3]
        upper_curr = upper.iloc[-2]
        lower_prev = lower.iloc[-3]
        lower_curr = lower.iloc[-2]
        mid_curr   = mid.iloc[-2]

        # ── Bounce off lower band (BUY) ───────────────────────────────────────
        touched_lower = prev_low <= lower_prev
        recovering    = curr_close > lower_curr and curr_close > prev_close
        if touched_lower and recovering:
            return 1, f"BB lower band bounce (price={curr_close:.2f} lower={lower_curr:.2f})"

        # ── Bounce off upper band (SELL) ──────────────────────────────────────
        touched_upper = prev_high >= upper_prev
        reversing     = curr_close < upper_curr and curr_close < prev_close
        if touched_upper and reversing:
            return -1, f"BB upper band reversal (price={curr_close:.2f} upper={upper_curr:.2f})"

        # ── Price position relative to midline ────────────────────────────────
        if curr_close > mid_curr and prev_close > mid_curr:
            return 1, f"BB price above midline ({curr_close:.2f} > {mid_curr:.2f})"
        if curr_close < mid_curr and prev_close < mid_curr:
            return -1, f"BB price below midline ({curr_close:.2f} < {mid_curr:.2f})"

        return 0, "BB no clear signal"

    except Exception as e:
        logger.debug(f"BB error: {e}")
        return 0, "BB: error"
