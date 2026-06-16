"""
strategy/vwap_strategy.py

Strategy 4: VWAP (Volume Weighted Average Price)
The institutional reference price — where big money judges fair value.

BUY:  price is above VWAP and pulls back to it (institutional support)
SELL: price is below VWAP and bounces up to it (institutional resistance)

Also gives directional bias based on price position vs VWAP.
"""

import pandas as pd
from datetime import datetime, timezone
from utils.logger import setup_logger

logger = setup_logger("vwap")


def _calculate_vwap(df: pd.DataFrame) -> pd.Series:
    """
    Calculate intraday VWAP from today's candles only.
    Falls back to rolling VWAP if not enough today's data.
    """
    today = datetime.now(timezone.utc).date()

    # Filter to today's candles
    if hasattr(df.index, 'date'):
        today_mask = df.index.date == today
        today_df   = df[today_mask]
    else:
        today_df = pd.DataFrame()

    # Need at least 5 candles for a meaningful VWAP
    if len(today_df) >= 5:
        source = today_df
    else:
        source = df.iloc[-50:]   # rolling fallback

    typical_price  = (source["high"] + source["low"] + source["close"]) / 3
    volume         = source["tick_volume"]
    cum_tp_vol     = (typical_price * volume).cumsum()
    cum_vol        = volume.cumsum()
    vwap           = cum_tp_vol / cum_vol

    # Return as full-length series aligned to df
    result = pd.Series(index=df.index, dtype=float)
    result[vwap.index] = vwap
    result.ffill(inplace=True)
    return result


def get_signal(df: pd.DataFrame) -> tuple[int, str]:
    """
    Returns (vote, reason)
    vote: +1 = BUY, -1 = SELL, 0 = NEUTRAL
    """
    try:
        vwap = _calculate_vwap(df)

        prev_close = df["close"].iloc[-3]
        curr_close = df["close"].iloc[-2]
        vwap_curr  = vwap.iloc[-2]
        vwap_prev  = vwap.iloc[-3]

        if pd.isna(vwap_curr) or vwap_curr == 0:
            return 0, "VWAP: not enough data"

        distance_pct = abs(curr_close - vwap_curr) / vwap_curr * 100

        # ── Price above VWAP = bullish bias ───────────────────────────────────
        if curr_close > vwap_curr:
            # Pullback to VWAP and holding above (strongest signal)
            if prev_close <= vwap_prev * 1.001 and curr_close > vwap_curr:
                return 1, f"VWAP bounce from above ({curr_close:.2f} > VWAP {vwap_curr:.2f})"
            # Simply above VWAP
            return 1, f"VWAP bullish ({curr_close:.2f} above {vwap_curr:.2f}, +{distance_pct:.2f}%)"

        # ── Price below VWAP = bearish bias ───────────────────────────────────
        if curr_close < vwap_curr:
            # Bounce up to VWAP and failing (strongest signal)
            if prev_close >= vwap_prev * 0.999 and curr_close < vwap_curr:
                return -1, f"VWAP rejection from below ({curr_close:.2f} < VWAP {vwap_curr:.2f})"
            # Simply below VWAP
            return -1, f"VWAP bearish ({curr_close:.2f} below {vwap_curr:.2f}, -{distance_pct:.2f}%)"

        return 0, "VWAP: price at VWAP"

    except Exception as e:
        logger.debug(f"VWAP error: {e}")
        return 0, "VWAP: error"
