"""
main.py — Midas

Full feature set:
  - Multi-timeframe confirmation (H1 + M15 + M5)
  - Confluence scoring
  - Spread + volatility filters
  - Session filter
  - Cooldown between trades
  - Break-even stop
  - Partial close at 1:1
  - Trailing stop
  - Circuit breaker
  - WhatsApp alerts
  - Daily summary at midnight
"""

import json
import time
from datetime import datetime, timezone, timedelta
from utils.logger import setup_logger
from utils.mt5_connection import connect_mt5, disconnect_mt5
from utils.notifications import send_daily_summary
from utils.time_exit import check_time_exits
from utils.performance_monitor import check_performance
from utils.news_filter import is_news_blackout
from strategy.volatility_metrics import get_volatility_fingerprint
from strategy.regime_classifier import classify_regime
from strategy.transition_detector import detect_transition
from strategy.volatility_signal_engine import get_signal, passes_filters
from risk.trade_manager import execute_trade, manage_open_trades
from risk.circuit_breaker import circuit_breaker

# ── Unified config — single source of truth, no more profile mixups ──────────
from config import settings
LOOP_INTERVAL_SECONDS = settings.LOOP_INTERVAL_SECONDS

logger = setup_logger("main")

_summary_date = None

# ── Volatility engine live state ──────────────────────────────────────────────
_regime_history  = []        # rolling list of recent regime strings
_trades_today    = 0
_last_trade_time = None
_last_trade_date = None
_COOLDOWN_MIN    = 15
_MAX_DAY_TRADES  = 6

_LIVE_CONFIG = {
    "session_filter":       True,
    "max_trades_per_day":   _MAX_DAY_TRADES,
    "max_spread_points":    15,
    "cooldown_bars":        3,
    "reward_ratio":         2.0,
    "risk_pct":             settings.RISK_PERCENT,
    "max_lot_size":         0.5,
    "max_trade_hours":      8,
    "max_trade_hours_hard": 24,
    "ab_sl_atr_mult":       settings.VOL_AB_SL_ATR_MULT,
    "ab_trail_atr_mult":    settings.VOL_AB_TRAIL_ATR_MULT,
    "bc_sl_atr_mult":       settings.VOL_BC_SL_ATR_MULT,
    "bc_trail_atr_mult":    settings.VOL_BC_TRAIL_ATR_MULT,
}


def _send_daily_summary_if_needed():
    """Send WhatsApp daily summary once per day at midnight UTC."""
    global _summary_date
    import MetaTrader5 as mt5
    today = datetime.now(timezone.utc).date()
    if _summary_date == today:
        return
    if _summary_date is not None:
        # Read yesterday's real trade data from trades.json
        yesterday = str(_summary_date)
        try:
            with open("trades.json") as f:
                all_trades = json.load(f)
            day_trades = [t for t in all_trades if t.get("timestamp", "")[:10] == yesterday]
        except Exception as e:
            logger.warning(f"Could not read trades.json: {e}", exc_info=True)
            day_trades = []
        wins    = sum(1 for t in day_trades if t.get("result") == "WIN")
        losses  = sum(1 for t in day_trades if t.get("result") == "LOSS")
        pnl     = sum(t.get("pnl", 0) for t in day_trades)
        account = mt5.account_info()
        balance = account.balance if account else 0
        send_daily_summary(len(day_trades), wins, losses, pnl, balance)
    _summary_date = today


def _fetch_m5_bars(n: int = 300) -> "pd.DataFrame | None":
    """Fetch recent M5 bars from MT5 with indicators prepped."""
    import MetaTrader5 as mt5
    import pandas as pd

    mt5.symbol_select(settings.SYMBOL, True)
    rates = mt5.copy_rates_from_pos(settings.SYMBOL, mt5.TIMEFRAME_M5, 0, n)
    if rates is None or len(rates) == 0:
        return None
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    df.set_index("time", inplace=True)
    return df


def _get_vol_signal() -> dict | None:
    """Compute volatility fingerprint, classify regime, detect transition, return signal."""
    global _regime_history, _trades_today, _last_trade_time

    now = datetime.now(timezone.utc)

    # Cooldown: 15 min since last trade
    if _last_trade_time is not None:
        if (now - _last_trade_time).total_seconds() / 60 < _COOLDOWN_MIN:
            return None

    df = _fetch_m5_bars(300)
    if df is None or len(df) < 100:
        return None

    fp = get_volatility_fingerprint(df)
    if fp["atr_ma50"] == 0 or fp["stddev_ma50"] == 0:
        return None

    regime = classify_regime(fp)

    prev_regime = _regime_history[-1] if _regime_history else regime
    _regime_history.append(regime)
    if len(_regime_history) > 30:
        _regime_history.pop(0)

    # Only check transition on regime change
    if regime == prev_regime or len(_regime_history) < 4:
        return None

    current_bar = df.iloc[-1]
    bar_time    = df.index[-1].to_pydatetime().replace(tzinfo=timezone.utc)

    transition = detect_transition(_regime_history, fp, current_bar)
    if transition["transition"] in ("NONE", "C_to_A"):
        return None

    # Filters (session, daily limit, spread)
    import MetaTrader5 as mt5
    tick = mt5.symbol_info_tick(settings.SYMBOL)
    spread = (tick.ask - tick.bid) / mt5.symbol_info(settings.SYMBOL).point if tick else 0

    ok, reason = passes_filters(bar_time, _LIVE_CONFIG, _trades_today,
                                last_trade_bar=0, current_bar=999,
                                spread_points=spread)
    if not ok:
        logger.debug(f"Filter blocked: {reason}")
        return None

    if is_news_blackout(now):
        logger.debug("News blackout active — skipping entry")
        return None

    signal = get_signal(transition, fp, _LIVE_CONFIG)
    if signal:
        logger.info(f"Vol signal: {signal['direction']} | {transition['transition']} | "
                    f"ATR={fp['atr']:.2f} | conf={transition['confidence']:.2f}")
    return signal


def run_bot():
    global _trades_today, _last_trade_time, _last_trade_date

    _send_daily_summary_if_needed()

    # Reset daily trade counter
    today = datetime.now(timezone.utc).date()
    if _last_trade_date != today:
        _trades_today    = 0
        _last_trade_date = today

    # ── Circuit breaker check ─────────────────────────────────────────────
    if circuit_breaker.is_tripped():
        logger.warning(f"Circuit breaker active: {circuit_breaker.status()}")
        return

    # ── Time-based exits ───────────────────────────────────────────────────
    check_time_exits(settings.SYMBOL)

    # ── Manage open trades (trail, BE, partial close) ─────────────────────
    manage_open_trades()

    # ── Performance monitor ───────────────────────────────────────────────
    perf = check_performance()
    if perf["action"] in ("degraded", "recovered"):
        logger.info(
            f"Performance monitor: {perf['action'].upper()} | "
            f"WR={perf['win_rate']:.1f}% over last {perf['trades']} trades"
        )

    # ── Max trades per day check ──────────────────────────────────────────
    if _trades_today >= _MAX_DAY_TRADES:
        logger.debug(f"Max trades reached ({_MAX_DAY_TRADES}) for today.")
        return

    # ── Volatility engine signal check ───────────────────────────────────
    signal = _get_vol_signal()
    if signal:
        execute_trade(signal)
        _trades_today   += 1
        _last_trade_time = datetime.now(timezone.utc)
    else:
        logger.debug(f"No vol signal this cycle. Trades today={_trades_today}")


def main():
    logger.info("=" * 55)
    logger.info("   Midas — Starting")
    logger.info("=" * 55)

    if not connect_mt5():
        logger.error("Failed to connect to MT5. Exiting.")
        return

    logger.info(f"MT5 connected. Scanning every {LOOP_INTERVAL_SECONDS}s.")
    logger.info("Strategy: MIDAS-B Volatility Engine | Regime A/B/C | Transition Detection | News Filter")

    try:
        while True:
            try:
                run_bot()
            except Exception as e:
                logger.error(f"Bot loop error: {e}", exc_info=True)
                import MetaTrader5 as mt5
                if not mt5.terminal_info():
                    logger.warning("MT5 connection lost — attempting reconnect...")
                    for attempt in range(1, 4):
                        time.sleep(10 * attempt)
                        if connect_mt5():
                            logger.info(f"MT5 reconnected on attempt {attempt}.")
                            break
                        logger.warning(f"Reconnect attempt {attempt} failed.")
                    else:
                        logger.error("Could not reconnect to MT5 after 3 attempts. Exiting.")
                        break
            time.sleep(LOOP_INTERVAL_SECONDS)
    except KeyboardInterrupt:
        logger.info("Bot stopped by user.")
    finally:
        disconnect_mt5()
        logger.info("MT5 disconnected.")


if __name__ == "__main__":
    main()