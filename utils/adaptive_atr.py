"""
utils/adaptive_atr.py

Adaptive ATR multiplier — dynamically adjusts SL width based on
current volatility relative to recent average volatility.

Instead of fixed ATR × 1.5:
  - High volatility  → widen SL (avoid premature stops)
  - Low volatility   → tighten SL (better RR)
  - Normal           → use base multiplier

This improves win rate by reducing stop-outs during volatile periods
and improves RR during quiet periods.
"""

import pandas as pd
from utils.logger import setup_logger
from config.settings import (
    ADAPTIVE_ATR_ENABLED,
    ATR_SL_MULTIPLIER,
    ADAPTIVE_ATR_LOOKBACK,
    ADAPTIVE_ATR_MIN,
    ADAPTIVE_ATR_MAX,
)

logger = setup_logger("adaptive_atr")


def get_atr_multiplier(df: pd.DataFrame) -> tuple[float, str]:
    """
    Calculate the adaptive ATR multiplier based on current volatility.

    Returns:
        (multiplier, reason)
    """
    if not ADAPTIVE_ATR_ENABLED:
        return ATR_SL_MULTIPLIER, f"Fixed ATR multiplier: {ATR_SL_MULTIPLIER}"

    try:
        if len(df) < ADAPTIVE_ATR_LOOKBACK + 5:
            return ATR_SL_MULTIPLIER, "Adaptive ATR: not enough data, using default"

        atr_series  = df["atr"]
        curr_atr    = atr_series.iloc[-2]
        avg_atr     = atr_series.iloc[-ADAPTIVE_ATR_LOOKBACK:-2].mean()
        atr_ratio   = curr_atr / avg_atr if avg_atr > 0 else 1.0

        # Scale multiplier based on volatility ratio
        # atr_ratio > 1.5 = high volatility → widen SL
        # atr_ratio < 0.7 = low volatility  → tighten SL
        # atr_ratio ~1.0  = normal          → base multiplier

        if atr_ratio >= 1.5:
            # High volatility — widen SL to avoid premature stops
            multiplier = min(ATR_SL_MULTIPLIER * 1.3, ADAPTIVE_ATR_MAX)
            reason     = f"High volatility (ATR ratio {atr_ratio:.2f}) → wider SL: {multiplier:.2f}x"

        elif atr_ratio <= 0.7:
            # Low volatility — tighten SL for better RR
            multiplier = max(ATR_SL_MULTIPLIER * 0.8, ADAPTIVE_ATR_MIN)
            reason     = f"Low volatility (ATR ratio {atr_ratio:.2f}) → tighter SL: {multiplier:.2f}x"

        else:
            # Normal volatility — linear interpolation around base
            scale      = 1.0 + (atr_ratio - 1.0) * 0.3
            multiplier = max(ADAPTIVE_ATR_MIN, min(ATR_SL_MULTIPLIER * scale, ADAPTIVE_ATR_MAX))
            reason     = f"Normal volatility (ATR ratio {atr_ratio:.2f}) → {multiplier:.2f}x"

        logger.debug(f"Adaptive ATR: {reason}")
        return round(multiplier, 2), reason

    except Exception as e:
        logger.debug(f"Adaptive ATR error: {e}")
        return ATR_SL_MULTIPLIER, f"Adaptive ATR error — using default {ATR_SL_MULTIPLIER}"
