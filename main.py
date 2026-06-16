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

import time
from datetime import datetime, timezone
from utils.logger import setup_logger
from utils.mt5_connection import connect_mt5, disconnect_mt5
from utils.notifications import send_daily_summary
from utils.time_exit import check_time_exits
from utils.performance_monitor import check_performance
from strategy.signal_engine import check_signal
from risk.trade_manager import execute_trade, manage_open_trades
from risk.circuit_breaker import circuit_breaker

# ── Unified config — single source of truth, no more profile mixups ──────────
from config import settings
LOOP_INTERVAL_SECONDS = settings.LOOP_INTERVAL_SECONDS

logger = setup_logger("main")

# Daily summary tracking
_summary_date   = None
_day_trades     = 0
_day_wins       = 0
_day_losses     = 0
_day_pnl        = 0.0


def _send_daily_summary_if_needed():
    """Send WhatsApp daily summary once per day at midnight UTC."""
    global _summary_date, _day_trades, _day_wins, _day_losses, _day_pnl
    import MetaTrader5 as mt5
    today = datetime.now(timezone.utc).date()
    if _summary_date == today:
        return
    if _summary_date is not None:
        account = mt5.account_info()
        balance = account.balance if account else 0
        send_daily_summary(_day_trades, _day_wins, _day_losses, _day_pnl, balance)
    _summary_date = today
    _day_trades   = 0
    _day_wins     = 0
    _day_losses   = 0
    _day_pnl      = 0.0


def run_bot():
    try:
        _send_daily_summary_if_needed()

        # ── Circuit breaker check ─────────────────────────────────────────────
        if circuit_breaker.is_tripped():
            logger.warning(f"Circuit breaker active: {circuit_breaker.status()}")
            return

        # ── Time-based exits ───────────────────────────────────────────────────
        check_time_exits(settings.SYMBOL)

        # ── Manage open trades (BE, partial close, trail) ─────────────────────
        manage_open_trades()

        # ── Performance monitor (auto risk reduction on bad streaks) ──────────
        check_performance()

        # ── Check for new signal ──────────────────────────────────────────────
        signal = check_signal()

        if signal:
            logger.info(f"Signal: {signal['direction']} | ATR={signal['atr']:.2f}")
            execute_trade(signal)
        else:
            logger.debug("No signal this cycle.")

    except Exception as e:
        logger.error(f"Bot loop error: {e}", exc_info=True)


def main():
    logger.info("=" * 55)
    logger.info("   Midas — Starting")
    logger.info("=" * 55)

    if not connect_mt5():
        logger.error("Failed to connect to MT5. Exiting.")
        return

    logger.info(f"MT5 connected. Scanning every {LOOP_INTERVAL_SECONDS}s.")
    logger.info("Features: MTF | Confluence | Spread | Volatility | BE | Partial | Trail | Circuit Breaker")

    try:
        while True:
            run_bot()
            time.sleep(LOOP_INTERVAL_SECONDS)
    except KeyboardInterrupt:
        logger.info("Bot stopped by user.")
    finally:
        disconnect_mt5()
        logger.info("MT5 disconnected.")


if __name__ == "__main__":
    main()