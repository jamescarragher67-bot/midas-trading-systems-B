"""
utils/filters.py
Spread and volatility filters — block trades when conditions are poor.
"""

import MetaTrader5 as mt5
from utils.logger import setup_logger
from config.settings import (
    SYMBOL,
    SPREAD_FILTER_ENABLED, MAX_SPREAD_POINTS,
    VOLATILITY_FILTER_ENABLED, MIN_ATR, MAX_ATR,
    ATR_PERIOD,
)

logger = setup_logger("filters")


def spread_ok() -> bool:
    """Returns True if current spread is within acceptable limits."""
    if not SPREAD_FILTER_ENABLED:
        return True
    mt5.symbol_select(SYMBOL, True)
    sym_info = mt5.symbol_info(SYMBOL)
    if not sym_info:
        logger.warning(f"spread_ok: could not get symbol info — MT5 error: {mt5.last_error()} — blocking trade")
        return False
    spread = sym_info.spread
    if spread > MAX_SPREAD_POINTS:
        logger.info(f"Spread filter blocked: {spread} points (max {MAX_SPREAD_POINTS})")
        return False
    return True


def volatility_ok(atr: float | None = None) -> bool:
    """Returns True if ATR is high enough to trade profitably.

    Pass a pre-computed ATR to avoid a redundant MT5 fetch (signal_engine
    already calculates ATR from the same candle data).
    """
    if not VOLATILITY_FILTER_ENABLED:
        return True
    if atr is None:
        import pandas as pd
        rates = mt5.copy_rates_from_pos(SYMBOL, mt5.TIMEFRAME_M5, 0, ATR_PERIOD + 5)
        if rates is None or len(rates) < ATR_PERIOD:
            logger.warning("volatility_ok: could not get ATR data — blocking trade")
            return False
        df     = pd.DataFrame(rates)
        high   = df["high"]
        low    = df["low"]
        close  = df["close"]
        prev_c = close.shift(1)
        tr     = pd.concat([high - low, (high - prev_c).abs(), (low - prev_c).abs()], axis=1).max(axis=1)
        atr    = tr.ewm(com=ATR_PERIOD - 1, adjust=False).mean().iloc[-1]
    if atr < MIN_ATR:
        logger.info(f"Volatility filter blocked: ATR={atr:.2f} (min {MIN_ATR})")
        return False
    if atr > MAX_ATR:
        logger.info(f"Volatility filter blocked: ATR={atr:.2f} (max {MAX_ATR})")
        return False
    return True


