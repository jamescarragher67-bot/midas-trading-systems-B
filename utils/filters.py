"""
utils/filters.py
Spread and volatility filters — block trades when conditions are poor.
"""

import MetaTrader5 as mt5
from utils.logger import setup_logger
from config.settings import (
    SYMBOL,
    SPREAD_FILTER_ENABLED, MAX_SPREAD_POINTS,
    VOLATILITY_FILTER_ENABLED, MIN_ATR,
    ATR_PERIOD,
)

logger = setup_logger("filters")


def spread_ok() -> bool:
    """Returns True if current spread is within acceptable limits."""
    if not SPREAD_FILTER_ENABLED:
        return True
    sym_info = mt5.symbol_info(SYMBOL)
    if not sym_info:
        return True
    spread = sym_info.spread
    if spread > MAX_SPREAD_POINTS:
        logger.info(f"Spread filter blocked: {spread} points (max {MAX_SPREAD_POINTS})")
        return False
    return True


def volatility_ok() -> bool:
    """Returns True if ATR is high enough to trade profitably."""
    if not VOLATILITY_FILTER_ENABLED:
        return True
    rates = mt5.copy_rates_from_pos(SYMBOL, mt5.TIMEFRAME_M5, 0, ATR_PERIOD + 5)
    if rates is None or len(rates) < ATR_PERIOD:
        return True
    import pandas as pd
    import numpy as np
    df       = pd.DataFrame(rates)
    high     = df["high"]
    low      = df["low"]
    close    = df["close"]
    prev_c   = close.shift(1)
    tr       = pd.concat([high - low, (high - prev_c).abs(), (low - prev_c).abs()], axis=1).max(axis=1)
    atr      = tr.ewm(com=ATR_PERIOD - 1, adjust=False).mean().iloc[-1]
    if atr < MIN_ATR:
        logger.info(f"Volatility filter blocked: ATR={atr:.2f} (min {MIN_ATR})")
        return False
    return True


def news_filter_ok() -> bool:
    """
    Returns False if within NEWS_BUFFER_MINUTES of a high-impact news event.
    Reads from news_events.json — add events manually or connect to an API.

    news_events.json format:
    [
        {"datetime": "2026-06-05 14:30", "event": "US NFP"},
        {"datetime": "2026-06-11 18:00", "event": "Fed Rate Decision"}
    ]
    """
    import os
    import json
    from datetime import datetime, timezone, timedelta
    from config.settings import NEWS_FILTER_ENABLED, NEWS_BUFFER_MINUTES

    if not NEWS_FILTER_ENABLED:
        return True

    news_file = "news_events.json"
    if not os.path.exists(news_file):
        return True

    try:
        with open(news_file) as f:
            events = json.load(f)
    except Exception:
        return True

    now    = datetime.now(timezone.utc)
    buffer = timedelta(minutes=NEWS_BUFFER_MINUTES)

    for event in events:
        try:
            event_time = datetime.strptime(event["datetime"], "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
            if abs(now - event_time) <= buffer:
                logger.info(f"News filter blocked: {event['event']} at {event['datetime']}")
                return False
        except Exception:
            continue

    return True