"""
strategy/rsi_divergence.py

Strategy 2: RSI Divergence + RSI Momentum
Detects divergence between price and RSI, plus overbought/oversold signals.

Three signal types:
  1. Classic divergence — price and RSI moving in opposite directions
  2. RSI momentum — RSI direction and level combined
  3. RSI extreme — overbought/oversold with reversal candle

Previous version had window=3 which was too strict for H1 data.
Now uses window=2 and multiple fallback signals.
"""

import pandas as pd
import numpy as np
from utils.logger import setup_logger

logger = setup_logger("rsi_divergence")

LOOKBACK    = 20
SWING_WIN   = 2    # reduced from 3 — finds more swing points on H1
OVERBOUGHT  = 65   # slightly lower threshold for more signals
OVERSOLD    = 35


def _find_swing_lows(series: pd.Series, window: int = SWING_WIN) -> list:
    lows = []
    for i in range(window, len(series) - window):
        if series.iloc[i] == series.iloc[i - window: i + window + 1].min():
            lows.append(i)
    return lows


def _find_swing_highs(series: pd.Series, window: int = SWING_WIN) -> list:
    highs = []
    for i in range(window, len(series) - window):
        if series.iloc[i] == series.iloc[i - window: i + window + 1].max():
            highs.append(i)
    return highs


def get_signal(df: pd.DataFrame) -> tuple[int, str]:
    """
    Returns (vote, reason)
    vote: +1 = BUY, -1 = SELL, 0 = NEUTRAL
    """
    try:
        if len(df) < LOOKBACK + 5:
            return 0, "RSI: not enough data"

        recent    = df.iloc[-LOOKBACK - 5: -1].copy()
        price     = recent["close"]
        rsi_s     = recent["rsi"]

        curr_rsi  = rsi_s.iloc[-1]
        prev_rsi  = rsi_s.iloc[-2]
        curr_price= price.iloc[-1]
        prev_price= price.iloc[-2]

        # ── Signal 1: Classic divergence ──────────────────────────────────────
        swing_lows = _find_swing_lows(price)
        if len(swing_lows) >= 2:
            i1, i2 = swing_lows[-2], swing_lows[-1]
            if price.iloc[i2] < price.iloc[i1] and rsi_s.iloc[i2] > rsi_s.iloc[i1]:
                gap = rsi_s.iloc[i2] - rsi_s.iloc[i1]
                if gap >= 2.0:   # minimum meaningful divergence
                    return 1, f"Bullish RSI divergence (price ↓ RSI ↑ +{gap:.1f})"

        swing_highs = _find_swing_highs(price)
        if len(swing_highs) >= 2:
            i1, i2 = swing_highs[-2], swing_highs[-1]
            if price.iloc[i2] > price.iloc[i1] and rsi_s.iloc[i2] < rsi_s.iloc[i1]:
                gap = rsi_s.iloc[i1] - rsi_s.iloc[i2]
                if gap >= 2.0:
                    return -1, f"Bearish RSI divergence (price ↑ RSI ↓ -{gap:.1f})"

        # ── Signal 2: RSI oversold/overbought with momentum turn ──────────────
        if curr_rsi < OVERSOLD and curr_rsi > prev_rsi:
            return 1, f"RSI oversold + turning up ({curr_rsi:.1f})"

        if curr_rsi > OVERBOUGHT and curr_rsi < prev_rsi:
            return -1, f"RSI overbought + turning down ({curr_rsi:.1f})"

        # ── Signal 3: RSI momentum direction with mid-level confirmation ──────
        rsi_slope = curr_rsi - rsi_s.iloc[-4]   # 3-bar slope

        if rsi_slope > 3.0 and curr_rsi < 55:
            return 1, f"RSI bullish momentum (slope +{rsi_slope:.1f}, level {curr_rsi:.1f})"

        if rsi_slope < -3.0 and curr_rsi > 45:
            return -1, f"RSI bearish momentum (slope {rsi_slope:.1f}, level {curr_rsi:.1f})"

        return 0, f"RSI neutral ({curr_rsi:.1f})"

    except Exception as e:
        logger.debug(f"RSI divergence error: {e}")
        return 0, "RSI: error"
