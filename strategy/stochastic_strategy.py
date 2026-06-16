"""
strategy/stochastic_strategy.py

Strategy 7: Stochastic Oscillator
Measures where price sits within its recent high/low range.
Different to RSI — uses price range, not speed of change.

BUY:  %K crosses above %D from below 20 (oversold)
SELL: %K crosses below %D from above 80 (overbought)
"""

import pandas as pd
from utils.logger import setup_logger

logger = setup_logger("stochastic")

STOCH_K     = 14   # %K period
STOCH_D     = 3    # %D smoothing
STOCH_SLOW  = 3    # Slow stochastic smoothing
OVERSOLD    = 20
OVERBOUGHT  = 80


def _stochastic(df: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """Calculate slow stochastic %K and %D."""
    low_min  = df["low"].rolling(STOCH_K).min()
    high_max = df["high"].rolling(STOCH_K).max()

    # Fast %K
    fast_k = 100 * (df["close"] - low_min) / (high_max - low_min + 1e-10)

    # Slow %K (smoothed fast %K)
    slow_k = fast_k.rolling(STOCH_SLOW).mean()

    # %D (signal line)
    slow_d = slow_k.rolling(STOCH_D).mean()

    return slow_k, slow_d


def get_signal(df: pd.DataFrame) -> tuple[int, str]:
    """
    Returns (vote, reason)
    vote: +1 = BUY, -1 = SELL, 0 = NEUTRAL
    """
    try:
        if len(df) < STOCH_K + STOCH_SLOW + STOCH_D + 5:
            return 0, "Stochastic: not enough data"

        k, d = _stochastic(df)

        k_prev = k.iloc[-3]
        k_curr = k.iloc[-2]
        d_prev = d.iloc[-3]
        d_curr = d.iloc[-2]

        # ── Bullish: cross up from oversold ───────────────────────────────
        cross_up_oversold = (
            k_prev <= d_prev and
            k_curr > d_curr and
            k_curr < 50 and
            k_prev < OVERSOLD + 10
        )
        if cross_up_oversold:
            return 1, f"Stochastic bullish cross from oversold (%K={k_curr:.1f})"

        # ── Bearish: cross down from overbought ────────────────────────────
        cross_down_overbought = (
            k_prev >= d_prev and
            k_curr < d_curr and
            k_curr > 50 and
            k_prev > OVERBOUGHT - 10
        )
        if cross_down_overbought:
            return -1, f"Stochastic bearish cross from overbought (%K={k_curr:.1f})"

        # ── Bullish: K above D in lower half ───────────────────────────────
        if k_curr > d_curr and k_curr < 50:
            return 1, f"Stochastic bullish (%K={k_curr:.1f} > %D={d_curr:.1f}, lower half)"

        # ── Bearish: K below D in upper half ───────────────────────────────
        if k_curr < d_curr and k_curr > 50:
            return -1, f"Stochastic bearish (%K={k_curr:.1f} < %D={d_curr:.1f}, upper half)"

        # ── Oversold/overbought zone ────────────────────────────────────────
        if k_curr < OVERSOLD:
            return 1, f"Stochastic oversold (%K={k_curr:.1f})"
        if k_curr > OVERBOUGHT:
            return -1, f"Stochastic overbought (%K={k_curr:.1f})"

        return 0, f"Stochastic neutral (%K={k_curr:.1f}, %D={d_curr:.1f})"

    except Exception as e:
        logger.debug(f"Stochastic error: {e}")
        return 0, "Stochastic: error"
