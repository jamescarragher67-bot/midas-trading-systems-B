"""
strategy/ema_pullback.py
Gate 2: M5 pullback to the 21 EMA.

In an uptrend, price pulls back to (or briefly through) EMA21 then starts recovering.
In a downtrend, price rallies up to (or briefly through) EMA21 then resumes falling.

Also returns the structural SL reference level:
  BUY  → swing low  of the pullback bars (SL goes just below this)
  SELL → swing high of the pullback bars (SL goes just above this)
"""

import pandas as pd
from utils.logger import setup_logger

logger = setup_logger("ema_pullback")

from config.settings import PULLBACK_BARS, ATR_TOLERANCE_PCT


def check_pullback(df: pd.DataFrame, direction: str) -> dict:
    """
    Returns {
        "valid":       bool,
        "swing_level": float,  # swing low (BUY) or swing high (SELL) of the pullback
        "reason":      str,
    }

    df must have columns: ema_slow (EMA21), atr, high, low, close.
    Uses df.iloc[-2] as the last completed bar.
    """
    try:
        last      = df.iloc[-2]
        atr       = last["atr"]
        ema21     = last["ema_slow"]
        tolerance = atr * ATR_TOLERANCE_PCT

        # Scan completed candles in the lookback window (excludes the forming bar)
        lookback = df.iloc[-(PULLBACK_BARS + 2):-1]

        if direction == "BUY":
            # At least one bar must have CLOSED at or below EMA21 (real pullback, not just a wick)
            touched     = (lookback["close"] <= lookback["ema_slow"] + tolerance).any()
            recovering  = last["close"] > ema21
            swing_level = float(lookback["low"].min())

            if touched and recovering:
                return {
                    "valid":       True,
                    "swing_level": round(swing_level, 2),
                    "reason":      f"BUY pullback to EMA21={ema21:.2f} | swing low={swing_level:.2f}",
                }
            return {
                "valid":       False,
                "swing_level": 0.0,
                "reason":      f"No BUY pullback | touched={touched} recovering={recovering} EMA21={ema21:.2f}",
            }

        else:  # SELL
            # At least one bar must have CLOSED at or above EMA21 (real pullback, not just a wick)
            touched     = (lookback["close"] >= lookback["ema_slow"] - tolerance).any()
            recovering  = last["close"] < ema21
            swing_level = float(lookback["high"].max())

            if touched and recovering:
                return {
                    "valid":       True,
                    "swing_level": round(swing_level, 2),
                    "reason":      f"SELL pullback to EMA21={ema21:.2f} | swing high={swing_level:.2f}",
                }
            return {
                "valid":       False,
                "swing_level": 0.0,
                "reason":      f"No SELL pullback | touched={touched} recovering={recovering} EMA21={ema21:.2f}",
            }

    except Exception as e:
        logger.warning(f"Pullback check error: {e}")
        return {"valid": False, "swing_level": 0.0, "reason": f"error: {e}"}
