"""
strategy/atr_expansion.py - Voter 3: Volatility (ATR Expansion)

Votes only when ATR14 > ATR_MA20 × 1.2 (volatility is expanding).
Direction comes from the net price movement of the last 3 closed candles.
Filters out low-volatility noise trades where signals are statistically weaker.
"""

import pandas as pd
import numpy as np
from utils.logger import setup_logger

logger = setup_logger("atr_expansion")

ATR_EXPANSION_MULT = 1.2
ATR_MA_PERIOD      = 20


def get_signal(df: pd.DataFrame) -> tuple[int, str]:
    try:
        if len(df) < ATR_MA_PERIOD + 5:
            return 0, "insufficient_data"

        atr_series = df["atr"].iloc[:-1]   # exclude current (unclosed) bar
        atr        = float(atr_series.iloc[-1])
        atr_ma     = float(atr_series.rolling(ATR_MA_PERIOD).mean().iloc[-1])

        if np.isnan(atr_ma) or atr_ma <= 0:
            return 0, "atr_ma_unavailable"

        if atr < atr_ma * ATR_EXPANSION_MULT:
            return 0, f"atr_contracting_{atr:.2f}_vs_ma_{atr_ma:.2f}"

        # Direction: net close-to-close move of last 3 completed candles
        last3 = df["close"].iloc[-4:-1]   # 3 closed candles before current
        net   = float(last3.iloc[-1] - last3.iloc[0])

        if net > 0:
            return 1, f"atr_expansion_bullish_net+{net:.2f}_atr={atr:.2f}"
        if net < 0:
            return -1, f"atr_expansion_bearish_net{net:.2f}_atr={atr:.2f}"
        return 0, "atr_expansion_flat"

    except Exception as e:
        logger.debug(f"ATR expansion error: {e}")
        return 0, "atr_expansion_error"
