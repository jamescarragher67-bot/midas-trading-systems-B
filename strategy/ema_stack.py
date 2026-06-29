"""
strategy/ema_stack.py

Strategy 1: EMA Stack
Votes BUY when EMA9 > EMA21 > EMA50 (all aligned bullish)
Votes SELL when EMA9 < EMA21 < EMA50 (all aligned bearish)
"""

import pandas as pd
from utils.logger import setup_logger

logger = setup_logger("ema_stack")


def get_signal(df: pd.DataFrame) -> tuple[int, str]:
    """
    Returns (vote, reason)
    vote: +1 = BUY, -1 = SELL, 0 = NEUTRAL
    """
    try:
        curr = df.iloc[-2]
        ema9  = curr["ema_fast"]
        ema21 = curr["ema_slow"]
        ema50 = curr["ema_trend"]
        close = curr["close"]

        # Full bullish stack
        if ema9 > ema21 > ema50 and close > ema50:
            return 1, f"EMA stack bullish (9={ema9:.1f} > 21={ema21:.1f} > 50={ema50:.1f})"

        # Full bearish stack
        if ema9 < ema21 < ema50 and close < ema50:
            return -1, f"EMA stack bearish (9={ema9:.1f} < 21={ema21:.1f} < 50={ema50:.1f})"

        return 0, f"EMA stack mixed (9={ema9:.1f}, 21={ema21:.1f}, 50={ema50:.1f})"

    except Exception as e:
        logger.debug(f"EMA stack error: {e}")
        return 0, "EMA stack: error"
