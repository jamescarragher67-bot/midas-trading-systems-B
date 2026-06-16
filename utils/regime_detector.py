"""
utils/regime_detector.py

Detects the current market regime before allowing any trade.

Four regimes:
  TRENDING_BULL  — strong uptrend, trade BUY signals normally
  TRENDING_BEAR  — strong downtrend, trade SELL signals normally
  RANGING        — choppy market, skip all trades
  VOLATILE       — high volatility spike, reduce size or skip

Uses:
  ADX  — measures trend strength (above 25 = trending)
  ATR  — measures volatility relative to its own average
  EMA  — determines trend direction
"""

import pandas as pd
import numpy as np
from utils.logger import setup_logger
from config.settings import (
    REGIME_FILTER_ENABLED,
    REGIME_ADX_PERIOD,
    REGIME_ADX_THRESHOLD,
    REGIME_ATR_MULTIPLIER,
    REGIME_EMA_PERIOD,
)

logger = setup_logger("regime")

# Regime states
TRENDING_BULL = "TRENDING_BULL"
TRENDING_BEAR = "TRENDING_BEAR"
RANGING       = "RANGING"
VOLATILE      = "VOLATILE"


def _adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Calculate Average Directional Index."""
    high  = df["high"]
    low   = df["low"]
    close = df["close"]

    plus_dm  = high.diff()
    minus_dm = -low.diff()

    plus_dm  = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0)

    atr_s = _atr_series(df, period)

    plus_di  = 100 * (plus_dm.ewm(com=period-1, adjust=False).mean() / atr_s)
    minus_di = 100 * (minus_dm.ewm(com=period-1, adjust=False).mean() / atr_s)

    dx  = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx = dx.ewm(com=period-1, adjust=False).mean()

    return adx, plus_di, minus_di


def _atr_series(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high     = df["high"]
    low      = df["low"]
    close    = df["close"]
    prev_c   = close.shift(1)
    tr       = pd.concat([
        high - low,
        (high - prev_c).abs(),
        (low  - prev_c).abs()
    ], axis=1).max(axis=1)
    return tr.ewm(com=period-1, adjust=False).mean()


def _ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def get_regime(df: pd.DataFrame) -> dict:
    """
    Analyse the current market regime.

    Returns:
        {
            "regime":     str,    — one of TRENDING_BULL/BEAR, RANGING, VOLATILE
            "adx":        float,  — ADX value
            "atr_ratio":  float,  — current ATR / average ATR
            "direction":  str,    — "BUY", "SELL", or "NEUTRAL"
            "tradeable":  bool,   — whether to allow trading
            "reason":     str,    — human readable explanation
        }
    """
    if not REGIME_FILTER_ENABLED:
        return {
            "regime":    TRENDING_BULL,
            "adx":       0,
            "atr_ratio": 1.0,
            "direction": "NEUTRAL",
            "tradeable": True,
            "reason":    "Regime filter disabled",
        }

    if len(df) < REGIME_ADX_PERIOD + 20:
        return {
            "regime":    RANGING,
            "adx":       0,
            "atr_ratio": 1.0,
            "direction": "NEUTRAL",
            "tradeable": False,
            "reason":    "Not enough data for regime detection",
        }

    # ── Calculate indicators ──────────────────────────────────────────────────
    adx_series, plus_di, minus_di = _adx(df, REGIME_ADX_PERIOD)
    atr_series   = _atr_series(df, REGIME_ADX_PERIOD)
    ema_series   = _ema(df["close"], REGIME_EMA_PERIOD)

    curr_adx     = adx_series.iloc[-2]
    curr_plus_di = plus_di.iloc[-2]
    curr_minus_di= minus_di.iloc[-2]
    curr_atr     = atr_series.iloc[-2]
    avg_atr      = atr_series.iloc[-20:-2].mean()
    atr_ratio    = curr_atr / avg_atr if avg_atr > 0 else 1.0
    curr_ema     = ema_series.iloc[-2]
    curr_close   = df["close"].iloc[-2]

    # ── Determine regime ──────────────────────────────────────────────────────

    # Volatile — ATR spike (price moving too fast, unpredictable)
    if atr_ratio > REGIME_ATR_MULTIPLIER:
        regime    = VOLATILE
        tradeable = False
        direction = "NEUTRAL"
        reason    = f"Volatility spike — ATR {atr_ratio:.2f}x average (max {REGIME_ATR_MULTIPLIER}x)"

    # Trending — ADX above threshold
    elif curr_adx >= REGIME_ADX_THRESHOLD:
        if curr_plus_di > curr_minus_di and curr_close > curr_ema:
            regime    = TRENDING_BULL
            tradeable = True
            direction = "BUY"
            reason    = f"Bullish trend — ADX={curr_adx:.1f}, +DI={curr_plus_di:.1f} > -DI={curr_minus_di:.1f}"
        elif curr_minus_di > curr_plus_di and curr_close < curr_ema:
            regime    = TRENDING_BEAR
            tradeable = True
            direction = "SELL"
            reason    = f"Bearish trend — ADX={curr_adx:.1f}, -DI={curr_minus_di:.1f} > +DI={curr_plus_di:.1f}"
        else:
            # ADX high but direction mixed — cautious
            regime    = RANGING
            tradeable = False
            direction = "NEUTRAL"
            reason    = f"Mixed signals despite ADX={curr_adx:.1f} — skipping"

    # Ranging — ADX below threshold (choppy, no clear trend)
    else:
        regime    = RANGING
        tradeable = False
        direction = "NEUTRAL"
        reason    = f"Ranging market — ADX={curr_adx:.1f} below threshold {REGIME_ADX_THRESHOLD}"

    result = {
        "regime":    regime,
        "adx":       round(curr_adx, 1),
        "atr_ratio": round(atr_ratio, 2),
        "direction": direction,
        "tradeable": tradeable,
        "reason":    reason,
    }

    icon = "✅" if tradeable else "⛔"
    logger.info(f"Regime: {icon} {regime} | {reason}")

    return result
