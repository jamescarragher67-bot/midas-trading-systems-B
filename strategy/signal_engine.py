"""
strategy/signal_engine.py — Statistical Arbitrage Signal Engine

Signal logic:
  Spread   = XAUUSD - (beta × XAGUSD + alpha)   [rolling 50-bar OLS]
  Z > +2.0  →  SELL XAUUSD  (Gold expensive vs Silver)
  Z < -2.0  →  BUY  XAUUSD  (Gold cheap vs Silver)
  Regime filters: min correlation 0.60, ATR regime check
  Z-score must be mature for ≥ 2 consecutive bars before entry
"""

import numpy as np
import MetaTrader5 as mt5
from datetime import datetime, timezone
from utils.logger import setup_logger
from utils.data_fetcher import fetch_both_symbols
from utils.session_filter import is_valid_session
from utils.filters import spread_ok
from strategy.indicators import add_indicators
from strategy.spread_calculator import (
    calculate_beta, calculate_spread_series,
    calculate_zscore, calculate_correlation,
)
from config.settings import (
    SYMBOL, MAGIC,
    MAX_TRADES_PER_DAY, MAX_OPEN_TRADES,
    COOLDOWN_MINUTES,
    FRIDAY_CUTOFF_HOUR,
    STAT_ARB_CONFIG,
)

logger = setup_logger("signal_engine")

INDICATOR_CONFIG = {
    "EMA_FAST":   9,
    "EMA_SLOW":   21,
    "EMA_TREND":  50,
    "RSI_PERIOD": 14,
    "ATR_PERIOD": 14,
}

# ── Module-level state ────────────────────────────────────────────────────────
_trade_count_date = None
_trade_count      = 0
_last_trade_time  = None
_zscore_history   = []          # rolling buffer of recent z-scores for age check


def _reset_daily_count_if_needed():
    global _trade_count_date, _trade_count
    today = datetime.now(timezone.utc).date()
    if _trade_count_date != today:
        _trade_count_date = today
        _trade_count      = 0


def increment_trade_count():
    global _trade_count, _last_trade_time
    _trade_count    += 1
    _last_trade_time = datetime.now(timezone.utc)
    logger.info(f"Trade count: {_trade_count}/{MAX_TRADES_PER_DAY} | Cooldown {COOLDOWN_MINUTES}m started")


def check_zscore_exit() -> bool:
    """
    Check if current z-score has reverted to within exit threshold.
    Returns True if any open MIDAS positions should be closed.
    Called from main.py on each tick alongside check_signal().
    """
    cfg      = STAT_ARB_CONFIG
    z_exit   = cfg.get("zscore_exit_threshold", 0.3)
    lookback = cfg.get("lookback_bars", 50)
    count    = lookback + 10

    open_positions = [p for p in (mt5.positions_get(symbol=SYMBOL) or []) if p.magic == MAGIC]
    if not open_positions:
        return False

    gold_bars, silver_bars = fetch_both_symbols(
        SYMBOL, cfg.get("symbol_secondary", "XAGUSD"), count=count
    )
    if gold_bars is None or silver_bars is None or len(gold_bars) < lookback + 2:
        return False

    gold_closes   = gold_bars["close"].tolist()
    silver_closes = silver_bars["close"].tolist()
    beta, alpha   = calculate_beta(gold_closes, silver_closes, lookback)
    spread_arr    = calculate_spread_series(gold_closes, silver_closes, beta, alpha)
    zscore        = calculate_zscore(spread_arr, lookback)

    for pos in open_positions:
        if pos.type == mt5.ORDER_TYPE_BUY and zscore >= -z_exit:
            logger.info(f"Z-score exit triggered BUY close | z={zscore:.2f} ≥ {-z_exit}")
            return True
        if pos.type == mt5.ORDER_TYPE_SELL and zscore <= z_exit:
            logger.info(f"Z-score exit triggered SELL close | z={zscore:.2f} ≤ {z_exit}")
            return True

    return False


def check_signal() -> dict | None:
    """
    Statistical arbitrage signal — returns signal dict or None.
    """
    global _zscore_history
    _reset_daily_count_if_needed()

    cfg      = STAT_ARB_CONFIG
    lookback = cfg.get("lookback_bars", 50)
    z_entry  = cfg.get("zscore_entry_threshold", 2.0)
    min_corr = cfg.get("min_correlation", 0.60)
    atr_mult = cfg.get("atr_regime_multiplier", 0.8)
    sl_mult  = cfg.get("sl_atr_multiplier", 1.5)
    rr       = cfg.get("reward_ratio", 2.0)
    min_age  = cfg.get("min_zscore_age_bars", 2)
    secondary = cfg.get("symbol_secondary", "XAGUSD")

    # ── Pre-filters ───────────────────────────────────────────────────────────
    if not is_valid_session():
        return None

    now = datetime.now(timezone.utc)
    if now.weekday() == 4 and now.hour >= FRIDAY_CUTOFF_HOUR:
        return None

    if _trade_count >= MAX_TRADES_PER_DAY:
        logger.debug(f"Max daily trades ({MAX_TRADES_PER_DAY}) reached.")
        return None

    open_positions = [p for p in (mt5.positions_get(symbol=SYMBOL) or []) if p.magic == MAGIC]
    if len(open_positions) >= MAX_OPEN_TRADES:
        logger.debug(f"Max open trades ({MAX_OPEN_TRADES}) reached.")
        return None

    global _last_trade_time
    if _last_trade_time is not None:
        elapsed = (now - _last_trade_time).total_seconds() / 60
        if elapsed < COOLDOWN_MINUTES:
            return None

    if not spread_ok():
        return None

    # ── Fetch aligned data ────────────────────────────────────────────────────
    count = lookback + 20
    gold_bars, silver_bars = fetch_both_symbols(SYMBOL, secondary, count=count)
    if gold_bars is None or silver_bars is None or len(gold_bars) < lookback + 5:
        logger.warning("Insufficient aligned data for stat arb.")
        return None

    gold_bars = add_indicators(gold_bars, INDICATOR_CONFIG)

    gold_closes   = gold_bars["close"].tolist()
    silver_closes = silver_bars["close"].tolist()

    # ── Regime filters ────────────────────────────────────────────────────────
    corr = calculate_correlation(gold_closes, silver_closes, lookback)
    if abs(corr) < min_corr:
        logger.debug(f"Low correlation: {corr:.2f} < {min_corr}")
        return None

    atr14   = float(gold_bars.iloc[-2]["atr"])
    atr_ma20 = float(gold_bars["atr"].iloc[-22:-2].mean()) if len(gold_bars) >= 22 else atr14
    if atr14 < atr_ma20 * atr_mult:
        logger.debug(f"Dead market: ATR {atr14:.2f} < MA {atr_ma20:.2f} × {atr_mult}")
        return None

    # ── Spread & z-score ──────────────────────────────────────────────────────
    beta, alpha = calculate_beta(gold_closes, silver_closes, lookback)
    spread_arr  = calculate_spread_series(gold_closes, silver_closes, beta, alpha)
    zscore      = calculate_zscore(spread_arr, lookback)

    # Track z-score history for age check
    _zscore_history.append(zscore)
    if len(_zscore_history) > 10:
        _zscore_history = _zscore_history[-10:]

    # ── Signal direction ──────────────────────────────────────────────────────
    if zscore > z_entry:
        direction = "SELL"
    elif zscore < -z_entry:
        direction = "BUY"
    else:
        logger.debug(f"No signal | z={zscore:.2f}")
        return None

    # ── Z-score maturity ──────────────────────────────────────────────────────
    if min_age > 1 and len(_zscore_history) >= min_age:
        prev = _zscore_history[-(min_age):-1]
        if direction == "SELL" and not all(p > z_entry for p in prev):
            logger.debug(f"Z-score not mature for SELL | prev={prev}")
            return None
        if direction == "BUY" and not all(p < -z_entry for p in prev):
            logger.debug(f"Z-score not mature for BUY | prev={prev}")
            return None

    # ── Build signal ──────────────────────────────────────────────────────────
    entry   = float(gold_closes[-1])
    sl_dist = atr14 * sl_mult
    sl_dist = max(atr14 * 0.5, min(sl_dist, atr14 * 3.0))

    if direction == "SELL":
        sl = round(entry + sl_dist, 2)
        tp = round(entry - sl_dist * rr, 2)
    else:
        sl = round(entry - sl_dist, 2)
        tp = round(entry + sl_dist * rr, 2)

    logger.info("-" * 50)
    logger.info(f"SIGNAL: {direction} | z={zscore:.2f} | corr={corr:.2f} | "
                f"spread={spread_arr[-1]:.2f} | ATR={atr14:.2f}")
    logger.info(f"Entry {entry} | SL {sl} | TP {tp}")
    logger.info("-" * 50)

    return {
        "direction":   direction,
        "symbol":      SYMBOL,
        "atr":         atr14,
        "zscore":      zscore,
        "correlation": corr,
        "beta":        beta,
        "spread":      float(spread_arr[-1]),
        "swing_level": 0.0,      # not used in stat arb
        "fvg_sl":      0.0,      # not used in stat arb
        "grade":       None,
        "pattern":     f"StatArb-{direction}",
    }
