"""
strategy/candlestick_patterns.py

Strategy 5: Candlestick Patterns
Detects high-probability reversal and continuation patterns.

Bullish patterns: Hammer, Bullish Engulfing, Morning Star, Bullish Pin Bar
Bearish patterns: Shooting Star, Bearish Engulfing, Evening Star, Bearish Pin Bar
"""

import pandas as pd
from utils.logger import setup_logger

logger = setup_logger("candlestick")


def _body(candle) -> float:
    return abs(candle["close"] - candle["open"])

def _upper_wick(candle) -> float:
    return candle["high"] - max(candle["open"], candle["close"])

def _lower_wick(candle) -> float:
    return min(candle["open"], candle["close"]) - candle["low"]

def _range(candle) -> float:
    return candle["high"] - candle["low"]

def _is_bullish(candle) -> bool:
    return candle["close"] > candle["open"]

def _is_bearish(candle) -> bool:
    return candle["close"] < candle["open"]


def get_signal(df: pd.DataFrame) -> tuple[int, str]:
    """
    Returns (vote, reason)
    vote: +1 = BUY, -1 = SELL, 0 = NEUTRAL
    """
    try:
        c0 = df.iloc[-4]   # 3 candles ago
        c1 = df.iloc[-3]   # 2 candles ago
        c2 = df.iloc[-2]   # last completed candle

        body2   = _body(c2)
        body1   = _body(c1)
        range2  = _range(c2)
        uw2     = _upper_wick(c2)
        lw2     = _lower_wick(c2)

        if range2 == 0:
            return 0, "Candlestick: doji / no range"

        # ── Bullish Engulfing ─────────────────────────────────────────────────
        if (_is_bearish(c1) and _is_bullish(c2)
                and c2["open"] < c1["close"]
                and c2["close"] > c1["open"]
                and body2 > body1):
            return 1, "Bullish engulfing"

        # ── Bearish Engulfing ─────────────────────────────────────────────────
        if (_is_bullish(c1) and _is_bearish(c2)
                and c2["open"] > c1["close"]
                and c2["close"] < c1["open"]
                and body2 > body1):
            return -1, "Bearish engulfing"

        # ── Hammer (bullish) ──────────────────────────────────────────────────
        # Long lower wick >= 2x body, small upper wick, small body
        if (lw2 >= body2 * 2
                and uw2 <= body2 * 0.5
                and body2 <= range2 * 0.35
                and _is_bearish(c1)):
            return 1, f"Hammer (lower wick={lw2:.2f}, body={body2:.2f})"

        # ── Shooting Star (bearish) ───────────────────────────────────────────
        # Long upper wick >= 2x body, small lower wick, small body
        if (uw2 >= body2 * 2
                and lw2 <= body2 * 0.5
                and body2 <= range2 * 0.35
                and _is_bullish(c1)):
            return -1, f"Shooting star (upper wick={uw2:.2f}, body={body2:.2f})"

        # ── Bullish Pin Bar ───────────────────────────────────────────────────
        # Very long lower wick (>= 60% of range), small body at top
        if (lw2 >= range2 * 0.6
                and body2 <= range2 * 0.25):
            return 1, f"Bullish pin bar (lower wick={lw2:.2f}/{range2:.2f} range)"

        # ── Bearish Pin Bar ───────────────────────────────────────────────────
        # Very long upper wick (>= 60% of range), small body at bottom
        if (uw2 >= range2 * 0.6
                and body2 <= range2 * 0.25):
            return -1, f"Bearish pin bar (upper wick={uw2:.2f}/{range2:.2f} range)"

        # ── Morning Star (3-candle bullish reversal) ──────────────────────────
        if (_is_bearish(c0)
                and _body(c1) <= _range(c1) * 0.3   # small middle candle
                and _is_bullish(c2)
                and c2["close"] > (c0["open"] + c0["close"]) / 2):
            return 1, "Morning star (3-candle bullish reversal)"

        # ── Evening Star (3-candle bearish reversal) ──────────────────────────
        if (_is_bullish(c0)
                and _body(c1) <= _range(c1) * 0.3
                and _is_bearish(c2)
                and c2["close"] < (c0["open"] + c0["close"]) / 2):
            return -1, "Evening star (3-candle bearish reversal)"

        # ── Strong momentum candle ────────────────────────────────────────────
        if _is_bullish(c2) and body2 >= range2 * 0.7:
            return 1, f"Strong bullish candle (body={body2:.2f}/{range2:.2f})"
        if _is_bearish(c2) and body2 >= range2 * 0.7:
            return -1, f"Strong bearish candle (body={body2:.2f}/{range2:.2f})"

        return 0, "No candlestick pattern"

    except Exception as e:
        logger.debug(f"Candlestick error: {e}")
        return 0, "Candlestick: error"
