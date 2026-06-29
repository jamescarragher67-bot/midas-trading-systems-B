"""
strategy/reversal_candle.py
Gate 3: Reversal candle at the EMA21 level.

Accepts the high-probability reversal patterns most reliable at a moving average:
  Bullish: engulfing, hammer, pin bar, strong close back above EMA21
  Bearish: engulfing, shooting star, pin bar, strong close back below EMA21

Uses df.iloc[-2] — the last completed M5 bar.
"""

import pandas as pd
from utils.logger import setup_logger

logger = setup_logger("reversal_candle")


def _body(c)  -> float: return abs(c["close"] - c["open"])
def _uw(c)    -> float: return c["high"] - max(c["open"], c["close"])
def _lw(c)    -> float: return min(c["open"], c["close"]) - c["low"]
def _range(c) -> float: return c["high"] - c["low"]
def _bull(c)  -> bool:  return c["close"] > c["open"]
def _bear(c)  -> bool:  return c["close"] < c["open"]


def check_reversal(df: pd.DataFrame, direction: str) -> dict:
    """
    Returns {"valid": bool, "pattern": str}

    df must have columns: open, high, low, close, ema_slow (EMA21).
    """
    try:
        c1   = df.iloc[-3]   # previous completed bar
        c2   = df.iloc[-2]   # last completed bar (the signal candle)
        ema21 = c2["ema_slow"]

        b2 = _body(c2);  b1 = _body(c1)
        r2 = _range(c2); uw2 = _uw(c2); lw2 = _lw(c2)

        if r2 == 0:
            return {"valid": False, "pattern": "Doji / no range"}

        if direction == "BUY":
            # Bullish engulfing: prev bearish, curr bullish, full engulf
            if (_bear(c1) and _bull(c2)
                    and c2["open"] < c1["close"] and c2["close"] > c1["open"]
                    and b2 > b1):
                return {"valid": True, "pattern": "Bullish engulfing"}

            # Hammer: long lower wick, small body, close at or above EMA21
            if (lw2 >= b2 * 2 and uw2 <= b2 * 0.5
                    and b2 <= r2 * 0.35 and c2["close"] >= ema21):
                return {"valid": True, "pattern": "Hammer"}

            # Bullish pin bar: lower wick >= 60% of range, close at or above EMA21
            if lw2 >= r2 * 0.6 and b2 <= r2 * 0.25 and c2["close"] >= ema21:
                return {"valid": True, "pattern": "Bullish pin bar"}

            # Strong bullish close: body >= 60% of range, candle crossed up through EMA21
            if _bull(c2) and b2 >= r2 * 0.6 and c2["close"] > ema21 > c2["open"]:
                return {"valid": True, "pattern": "Strong close above EMA21"}

            return {"valid": False, "pattern": "No bullish reversal at EMA21"}

        else:  # SELL
            # Bearish engulfing: prev bullish, curr bearish, full engulf
            if (_bull(c1) and _bear(c2)
                    and c2["open"] > c1["close"] and c2["close"] < c1["open"]
                    and b2 > b1):
                return {"valid": True, "pattern": "Bearish engulfing"}

            # Shooting star: long upper wick, small body, close at or below EMA21
            if (uw2 >= b2 * 2 and lw2 <= b2 * 0.5
                    and b2 <= r2 * 0.35 and c2["close"] <= ema21):
                return {"valid": True, "pattern": "Shooting star"}

            # Bearish pin bar: upper wick >= 60% of range, close at or below EMA21
            if uw2 >= r2 * 0.6 and b2 <= r2 * 0.25 and c2["close"] <= ema21:
                return {"valid": True, "pattern": "Bearish pin bar"}

            # Strong bearish close: body >= 60% of range, candle crossed down through EMA21
            if _bear(c2) and b2 >= r2 * 0.6 and c2["close"] < ema21 < c2["open"]:
                return {"valid": True, "pattern": "Strong close below EMA21"}

            return {"valid": False, "pattern": "No bearish reversal at EMA21"}

    except Exception as e:
        logger.warning(f"Reversal check error: {e}")
        return {"valid": False, "pattern": f"error: {e}"}
