"""
strategy/macd_strategy.py

Strategy 6: MACD Crossover
Measures momentum acceleration/deceleration.

BUY:  MACD line crosses above signal line + histogram positive
SELL: MACD line crosses below signal line + histogram negative
"""

import pandas as pd
from utils.logger import setup_logger

logger = setup_logger("macd")

MACD_FAST   = 12
MACD_SLOW   = 26
MACD_SIGNAL = 9


def _ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def get_signal(df: pd.DataFrame) -> tuple[int, str]:
    """
    Returns (vote, reason)
    vote: +1 = BUY, -1 = SELL, 0 = NEUTRAL
    """
    try:
        if len(df) < MACD_SLOW + MACD_SIGNAL + 5:
            return 0, "MACD: not enough data"

        close       = df["close"]
        ema_fast    = _ema(close, MACD_FAST)
        ema_slow    = _ema(close, MACD_SLOW)
        macd_line   = ema_fast - ema_slow
        signal_line = _ema(macd_line, MACD_SIGNAL)
        histogram   = macd_line - signal_line

        # Use last two completed candles
        macd_prev  = macd_line.iloc[-3]
        macd_curr  = macd_line.iloc[-2]
        sig_prev   = signal_line.iloc[-3]
        sig_curr   = signal_line.iloc[-2]
        hist_curr  = histogram.iloc[-2]
        hist_prev  = histogram.iloc[-3]

        # ── Bullish crossover ──────────────────────────────────────────────
        # MACD crosses above signal + histogram turning positive
        cross_up = macd_prev <= sig_prev and macd_curr > sig_curr
        if cross_up and hist_curr > 0:
            return 1, f"MACD bullish crossover (hist={hist_curr:.3f})"

        # ── Bearish crossover ──────────────────────────────────────────────
        cross_down = macd_prev >= sig_prev and macd_curr < sig_curr
        if cross_down and hist_curr < 0:
            return -1, f"MACD bearish crossover (hist={hist_curr:.3f})"

        # ── Histogram momentum direction (weaker signal) ───────────────────
        if macd_curr > sig_curr and hist_curr > hist_prev and hist_curr > 0:
            return 1, f"MACD bullish momentum building (hist={hist_curr:.3f})"

        if macd_curr < sig_curr and hist_curr < hist_prev and hist_curr < 0:
            return -1, f"MACD bearish momentum building (hist={hist_curr:.3f})"

        # ── Position relative to zero line ─────────────────────────────────
        if macd_curr > 0 and macd_curr > sig_curr:
            return 1, f"MACD above zero bullish (macd={macd_curr:.3f})"
        if macd_curr < 0 and macd_curr < sig_curr:
            return -1, f"MACD below zero bearish (macd={macd_curr:.3f})"

        return 0, f"MACD neutral (macd={macd_curr:.3f}, sig={sig_curr:.3f})"

    except Exception as e:
        logger.debug(f"MACD error: {e}")
        return 0, "MACD: error"
