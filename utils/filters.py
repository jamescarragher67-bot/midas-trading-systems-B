"""
utils/filters.py
Spread filter — blocks trades when the spread is too wide. No volatility
filter — that was never part of what LSC's backtest validated.
"""

import MetaTrader5 as mt5
from utils.logger import setup_logger
from config.settings import SYMBOL, SPREAD_FILTER_ENABLED, MAX_SPREAD_POINTS

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

