"""
strategy/rsi_extreme.py - Voter 2: Mean Reversion (RSI Extreme)

Counter-trend voice. RSI < 25 = BUY (oversold extreme), RSI > 75 = SELL (overbought extreme).
Deliberately disagrees with Voter 1 (EMA momentum) during strong trends,
providing genuine signal independence.
"""

import pandas as pd
from utils.logger import setup_logger

logger = setup_logger("rsi_extreme")

RSI_BUY_THRESHOLD  = 25.0
RSI_SELL_THRESHOLD = 75.0


def get_signal(df: pd.DataFrame) -> tuple[int, str]:
    try:
        rsi = float(df.iloc[-2]["rsi"])

        if rsi < RSI_BUY_THRESHOLD:
            return 1, f"rsi_oversold_{rsi:.1f}"
        if rsi > RSI_SELL_THRESHOLD:
            return -1, f"rsi_overbought_{rsi:.1f}"
        return 0, f"rsi_neutral_{rsi:.1f}"

    except Exception as e:
        logger.debug(f"RSI extreme error: {e}")
        return 0, "rsi_extreme_error"
