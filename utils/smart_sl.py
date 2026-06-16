"""
utils/smart_sl.py

Smart stop-loss placement using structural swing levels.

Instead of ATR x 1.5 (arbitrary), places SL just beyond the nearest
swing high/low — levels the market has already respected.

BUY  → SL just below the nearest swing low
SELL → SL just above the nearest swing high

Falls back to ATR-based SL if no valid structural level is found.
"""

import pandas as pd
from utils.logger import setup_logger
from config.settings import (
    SMART_SL_ENABLED,
    SMART_SL_LOOKBACK,
    SMART_SL_BUFFER,
    ATR_SL_MULTIPLIER,
)
from utils.adaptive_atr import get_atr_multiplier

logger = setup_logger("smart_sl")


def _find_swing_lows(series: pd.Series, window: int = 2) -> list:
    lows = []
    for i in range(window, len(series) - window):
        if series.iloc[i] == series.iloc[i - window:i + window + 1].min():
            lows.append(series.iloc[i])
    return lows


def _find_swing_highs(series: pd.Series, window: int = 2) -> list:
    highs = []
    for i in range(window, len(series) - window):
        if series.iloc[i] == series.iloc[i - window:i + window + 1].max():
            highs.append(series.iloc[i])
    return highs


def calculate_sl(df: pd.DataFrame, direction: str,
                 entry_price: float, atr: float) -> tuple[float, str]:
    """
    Calculate optimal stop loss level.

    Returns:
        (sl_price, method) where method is "structural" or "atr"
    """
    # ATR fallback
    mult, _     = get_atr_multiplier(df) if df is not None and len(df) > 25 else (ATR_SL_MULTIPLIER, "")
    atr_sl_dist = atr * mult
    if direction == "BUY":
        atr_sl = entry_price - atr_sl_dist
    else:
        atr_sl = entry_price + atr_sl_dist

    if not SMART_SL_ENABLED or df is None or len(df) < SMART_SL_LOOKBACK + 5:
        return round(atr_sl, 2), "atr"

    recent = df.iloc[-(SMART_SL_LOOKBACK + 5): -1]
    buffer = atr * SMART_SL_BUFFER

    if direction == "BUY":
        swing_lows = _find_swing_lows(recent["low"])
        valid_lows = [l for l in swing_lows if l < entry_price]

        if valid_lows:
            nearest_low = max(valid_lows)           # closest swing low below entry
            structural_sl = nearest_low - buffer     # just below the swing

            sl_dist = entry_price - structural_sl
            min_sl_dist = atr * 0.5                  # SL can't be too tight
            max_sl_dist = atr * 2.5                  # SL can't be too wide

            if min_sl_dist <= sl_dist <= max_sl_dist:
                logger.debug(f"Smart SL (structural): {structural_sl:.2f} | swing low={nearest_low:.2f}")
                return round(structural_sl, 2), "structural"

    else:  # SELL
        swing_highs = _find_swing_highs(recent["high"])
        valid_highs = [h for h in swing_highs if h > entry_price]

        if valid_highs:
            nearest_high = min(valid_highs)
            structural_sl = nearest_high + buffer

            sl_dist = structural_sl - entry_price
            min_sl_dist = atr * 0.5
            max_sl_dist = atr * 2.5

            if min_sl_dist <= sl_dist <= max_sl_dist:
                logger.debug(f"Smart SL (structural): {structural_sl:.2f} | swing high={nearest_high:.2f}")
                return round(structural_sl, 2), "structural"

    logger.debug(f"Smart SL fallback to ATR: {atr_sl:.2f}")
    return round(atr_sl, 2), "atr"
