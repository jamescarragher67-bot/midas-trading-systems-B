"""
utils/filters.py
Pre-execution filters applied before a trade is sent.

- spread_ok(): blocks if current spread exceeds MAX_SPREAD_POINTS
- news_ok():  blocks if within ±NEWS_WINDOW_MINS of any high-impact USD
              economic event (XAU is priced in USD, so same filter covers
              gold). Fail-closed: any data-source failure blocks the trade.
"""

from datetime import datetime, timezone

import MetaTrader5 as mt5
from utils.logger import setup_logger
from utils.news_filter import is_news_blackout
from config.settings import (
    SYMBOL, SPREAD_FILTER_ENABLED, MAX_SPREAD_POINTS,
    NEWS_FILTER_ENABLED,
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


def news_ok() -> bool:
    """
    Returns True if it's safe to trade with respect to the news calendar.

    Thin wrapper over news_filter.is_news_blackout(). Respects
    NEWS_FILTER_ENABLED — when disabled, this is always True and no API
    call is made.
    """
    if not NEWS_FILTER_ENABLED:
        return True
    blocked, reason = is_news_blackout()
    if blocked:
        logger.info(f"Blocked by: {reason}")
    return not blocked
