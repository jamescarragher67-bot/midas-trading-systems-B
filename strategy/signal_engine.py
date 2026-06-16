"""
strategy/signal_engine.py

Ultimate signal engine with 5-strategy voting system.

Flow:
  1. Filters (session, cooldown, spread, volatility, open trades, daily limit)
  2. Get M5 candle data + indicators
  3. Run voting engine (5 strategies, need 3/5 to agree)
  4. MTF confluence check (H1 + M15 alignment)
  5. Fire signal if all gates pass
"""

import MetaTrader5 as mt5
from datetime import datetime, timezone
from utils.logger import setup_logger
from utils.data_fetcher import get_candles
from utils.session_filter import is_valid_session
from utils.filters import spread_ok, volatility_ok
from utils.regime_detector import get_regime, RANGING, VOLATILE
from strategy.indicators import add_indicators
from strategy.multi_timeframe import get_htf_bias
from strategy.voting_engine import get_vote
from config.settings import (
    SYMBOL, EMA_FAST, EMA_SLOW, EMA_TREND,
    RSI_PERIOD, ATR_PERIOD,
    MAX_TRADES_PER_DAY, MAX_OPEN_TRADES,
    MTF_ENABLED, MTF_MIN_CONFLUENCE,
    COOLDOWN_MINUTES,
)

logger = setup_logger("signal_engine")

# ── State ─────────────────────────────────────────────────────────────────────
_trade_count_date = None
_trade_count      = 0
_last_trade_time  = None

INDICATOR_CONFIG = {
    "EMA_FAST":   EMA_FAST,
    "EMA_SLOW":   EMA_SLOW,
    "EMA_TREND":  EMA_TREND,
    "RSI_PERIOD": RSI_PERIOD,
    "ATR_PERIOD": ATR_PERIOD,
}


def _reset_daily_count_if_needed():
    global _trade_count_date, _trade_count
    today = datetime.now(timezone.utc).date()
    if _trade_count_date != today:
        _trade_count_date = today
        _trade_count = 0


def increment_trade_count():
    global _trade_count, _last_trade_time
    _trade_count    += 1
    _last_trade_time = datetime.now(timezone.utc)
    logger.info(f"Trade count: {_trade_count}/{MAX_TRADES_PER_DAY} | Cooldown {COOLDOWN_MINUTES}m started")


def check_signal() -> dict | None:
    """
    Full signal check with voting engine + MTF confirmation.
    Returns signal dict or None.
    """
    _reset_daily_count_if_needed()

    # ── Filter: session ───────────────────────────────────────────────────────
    if not is_valid_session():
        return None

    # ── Filter: max daily trades ──────────────────────────────────────────────
    if _trade_count >= MAX_TRADES_PER_DAY:
        logger.debug(f"Max daily trades ({MAX_TRADES_PER_DAY}) reached.")
        return None

    # ── Filter: max open trades ───────────────────────────────────────────────
    open_positions = mt5.positions_get(symbol=SYMBOL)
    if open_positions is None:
        open_positions = []
    if len(open_positions) >= MAX_OPEN_TRADES:
        logger.debug(f"Max open trades ({MAX_OPEN_TRADES}) reached.")
        return None

    # ── Filter: cooldown ──────────────────────────────────────────────────────
    global _last_trade_time
    if _last_trade_time is not None:
        mins = (datetime.now(timezone.utc) - _last_trade_time).total_seconds() / 60
        if mins < COOLDOWN_MINUTES:
            logger.debug(f"Cooldown: {COOLDOWN_MINUTES - mins:.0f}m remaining.")
            return None

    # ── Filter: spread ────────────────────────────────────────────────────────
    if not spread_ok():
        return None

    # ── Filter: volatility ────────────────────────────────────────────────────
    if not volatility_ok():
        return None

    # ── Get M5 candle data + indicators ──────────────────────────────────────
    df = get_candles(count=100)
    if df.empty or len(df) < 55:
        logger.warning("Not enough M5 candle data.")
        return None

    df = add_indicators(df, INDICATOR_CONFIG)

    atr_curr = df.iloc[-2]["atr"]

    # ── Voting engine ─────────────────────────────────────────────────────────
    logger.info("─" * 50)
    logger.info("Running voting engine...")
    result = get_vote(df)

    if result["direction"] == "NEUTRAL":
        logger.info(f"Vote: NEUTRAL (score={result['score']}/5, need ±{result['threshold']})")
        return None

    direction = result["direction"]
    score     = result["score"]
    logger.info(f"✅ Vote passed: {direction} | Score={score}/5")

    # ── MTF confluence check ──────────────────────────────────────────────────
    if MTF_ENABLED:
        mtf = get_htf_bias()

        for detail in mtf["details"]:
            logger.debug(f"MTF | {detail}")

        if mtf["score"] < MTF_MIN_CONFLUENCE:
            logger.info(f"MTF confluence too low: {mtf['score']}/5 (need {MTF_MIN_CONFLUENCE}). Skipping.")
            return None

        if mtf["bias"] != "NEUTRAL" and mtf["bias"] != direction:
            logger.info(f"MTF bias ({mtf['bias']}) conflicts with vote ({direction}). Skipping.")
            return None

        logger.info(f"MTF confirmed | Bias={mtf['bias']} Score={mtf['score']}/5")

    logger.info(f"🚀 SIGNAL: {direction} | Vote={score}/5 | ATR={atr_curr:.2f}")
    logger.info("─" * 50)

    return {
        "direction":  direction,
        "symbol":     SYMBOL,
        "atr":        atr_curr,
        "vote_score": score,
        "df":         df,
    }

