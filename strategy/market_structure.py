"""
strategy/market_structure.py

Strategy 8: Market Structure Break
The most institutional concept in trading.

Detects when price breaks a significant swing high or low,
signalling that institutional order flow has shifted direction.

BUY:  price breaks above most recent swing high (bullish structure break)
SELL: price breaks below most recent swing low (bearish structure break)
"""

import pandas as pd
from utils.logger import setup_logger

logger = setup_logger("market_structure")

LOOKBACK     = 20   # candles to look back for swing points
SWING_WINDOW = 3    # candles each side to confirm a swing


def _find_swing_highs(df: pd.DataFrame, window: int = SWING_WINDOW) -> list:
    highs = []
    high  = df["high"]
    for i in range(window, len(high) - window):
        if high.iloc[i] == high.iloc[i - window: i + window + 1].max():
            highs.append((i, high.iloc[i]))
    return highs


def _find_swing_lows(df: pd.DataFrame, window: int = SWING_WINDOW) -> list:
    lows = []
    low  = df["low"]
    for i in range(window, len(low) - window):
        if low.iloc[i] == low.iloc[i - window: i + window + 1].min():
            lows.append((i, low.iloc[i]))
    return lows


def get_signal(df: pd.DataFrame) -> tuple[int, str]:
    """
    Returns (vote, reason)
    vote: +1 = BUY, -1 = SELL, 0 = NEUTRAL
    """
    try:
        if len(df) < LOOKBACK + SWING_WINDOW + 5:
            return 0, "Market structure: not enough data"

        recent    = df.iloc[-(LOOKBACK + SWING_WINDOW + 5):]
        curr_candle = df.iloc[-2]
        curr_close  = curr_candle["close"]
        curr_high   = curr_candle["high"]
        curr_low    = curr_candle["low"]

        # ── Find swing highs and lows in recent data ───────────────────────
        swing_highs = _find_swing_highs(recent)
        swing_lows  = _find_swing_lows(recent)

        if not swing_highs or not swing_lows:
            return 0, "Market structure: no swing points found"

        # Most recent swing high and low (excluding last few candles)
        valid_highs = [(i, p) for i, p in swing_highs if i < len(recent) - SWING_WINDOW - 1]
        valid_lows  = [(i, p) for i, p in swing_lows  if i < len(recent) - SWING_WINDOW - 1]

        if not valid_highs or not valid_lows:
            return 0, "Market structure: no valid swing points"

        last_swing_high = max(valid_highs, key=lambda x: x[0])[1]
        last_swing_low  = min(valid_lows,  key=lambda x: x[0])[1]

        # Structural range
        structure_range = last_swing_high - last_swing_low
        if structure_range <= 0:
            return 0, "Market structure: invalid range"

        # ── Bullish break of structure ─────────────────────────────────────
        if curr_close > last_swing_high:
            breakout_pct = (curr_close - last_swing_high) / structure_range * 100
            return 1, f"Bullish structure break (close={curr_close:.2f} > swing high={last_swing_high:.2f}, +{breakout_pct:.1f}%)"

        # ── Bearish break of structure ─────────────────────────────────────
        if curr_close < last_swing_low:
            breakdown_pct = (last_swing_low - curr_close) / structure_range * 100
            return -1, f"Bearish structure break (close={curr_close:.2f} < swing low={last_swing_low:.2f}, -{breakdown_pct:.1f}%)"

        # ── Price position within structure ────────────────────────────────
        position = (curr_close - last_swing_low) / structure_range

        if position > 0.6:
            return 1, f"Price in upper structure ({position*100:.0f}% of range)"
        if position < 0.4:
            return -1, f"Price in lower structure ({position*100:.0f}% of range)"

        return 0, f"Price mid-structure ({position*100:.0f}% of range)"

    except Exception as e:
        logger.debug(f"Market structure error: {e}")
        return 0, "Market structure: error"
