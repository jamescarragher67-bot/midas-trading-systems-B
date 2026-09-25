"""
main.py — Midas: Liquidity-Sweep Continuation (LSC), Stage 4

Single strategy, single entry point. No multi-bot orchestration layer —
the old main_combined.py ran Bot 1 (M5 pullback) and Bot 2 (volatility
regime) side by side; both were retracted after being properly backtested
for the first time and found to have no real edge. LSC is the one
strategy that survived independent validation, split-window checks, and
a 1000-shuffle Monte Carlo stress test. See project memory for the full
history.

Calls strategy/lsc_m15.py's check_entry() directly — the exact same
function backtest/lsc_engine.py validated, no separate live approximation.
"""

import os
import time
from datetime import datetime, timezone

import MetaTrader5 as mt5
import pandas as pd

from utils.logger import setup_logger
from utils.mt5_connection import connect_mt5, disconnect_mt5
from utils.notifications import (
    send_bot_started, send_bot_stopped, send_connection_lost, send_connection_restored,
)
from utils.filters import spread_ok, news_ok
from risk.trade_manager import execute_trade, manage_open_trades
from risk.circuit_breaker import circuit_breaker
from strategy.lsc_m15 import precompute, check_entry
from backtest.lsc_engine import compute_atr14
from config import settings

log = setup_logger("main")

# ── State ─────────────────────────────────────────────────────────────────────
_bias_date       = None
_trades_today    = 0
_last_trade_bar  = -settings.COOLDOWN_BARS
_last_bar_time   = None
_bar_index       = 0   # monotonic counter standing in for the backtest's integer bar index

# Bars fetched per scan. strategy/lsc_m15.py needs the WHOLE prior server-time
# day present in the frame (prior_high/prior_low are that day's extremes) plus
# ATR warm-up; the earlier 150 left the prior day truncated once the current
# day was past ~13:30 server time. 300 covers two full days (2 x 96) with room.
BARS_TO_FETCH = 300
MIN_BARS      = 100   # = backtest/lsc_engine.py MIN_LOOKBACK


def _reconnect():
    """
    Retry the MT5 connection until it succeeds. Never gives up: the wait
    doubles from RECONNECT_DELAY_MIN up to RECONNECT_DELAY_MAX between
    attempts, and one WhatsApp alert marks the start of the outage and one
    marks recovery. The old 3-attempts-then-exit policy left the box silent
    after a few minutes of terminal/network trouble.
    """
    delay   = settings.RECONNECT_DELAY_MIN
    started = time.time()
    attempt = 0
    while True:
        attempt += 1
        time.sleep(delay)
        if connect_mt5():
            outage = time.time() - started
            log.info(f"MT5 reconnected (attempt {attempt}, {outage:.0f}s outage)")
            send_connection_restored(attempt, outage)
            return
        error = str(mt5.last_error())
        delay = min(delay * 2, settings.RECONNECT_DELAY_MAX)
        log.warning(f"Reconnect attempt {attempt} failed: {error} — next attempt in {delay}s")
        if attempt == 1:
            send_connection_lost(error, delay)


def _weekend_close_all():
    """Force-close every open LSC position before market close."""
    positions = mt5.positions_get(symbol=settings.SYMBOL) or []
    midas_pos = [p for p in positions if p.magic == settings.MAGIC]
    if not midas_pos:
        return

    log.info(f"Weekend close triggered — force closing {len(midas_pos)} position(s)")
    for pos in midas_pos:
        close_type = mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
        tick = mt5.symbol_info_tick(settings.SYMBOL)
        if not tick:
            log.error(f"Weekend close: no tick data for ticket {pos.ticket}")
            continue
        price = tick.bid if pos.type == mt5.ORDER_TYPE_BUY else tick.ask
        request = {
            "action": mt5.TRADE_ACTION_DEAL, "symbol": settings.SYMBOL, "volume": pos.volume,
            "type": close_type, "position": pos.ticket, "price": price,
            "deviation": settings.ORDER_DEVIATION, "magic": settings.MAGIC,
            "comment": "LSC_WEEKEND_CLOSE", "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        mt5.order_send(request)


def _check_time_exit():
    """
    Hard fallback close if a position has been open too long — matches
    backtest/lsc_engine.py's MAX_HOLD_BARS (96 M15 bars = ~1 day). No
    soft/profit-conditional exit — that was never part of what LSC's
    backtest actually modeled.
    """
    if not settings.TIME_EXIT_ENABLED:
        return
    positions = mt5.positions_get(symbol=settings.SYMBOL) or []
    if not positions:
        return
    # pos.time is stamped in BROKER SERVER time (UTC+3 measured 2026-09-15),
    # so it must be compared against the server clock, not this machine's
    # UTC - the wall-clock version fired the exit ~27h after entry, not 24h.
    # The last tick's time is the server clock, fresh to the second while
    # the market is open (positions are force-flat before the weekend).
    tick = mt5.symbol_info_tick(settings.SYMBOL)
    if not tick:
        return
    server_now = tick.time
    for pos in positions:
        if pos.magic != settings.MAGIC:
            continue
        hours_open = (server_now - pos.time) / 3600
        if hours_open >= settings.MAX_TRADE_HOURS_HARD:
            close_type = mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
            price = tick.bid if pos.type == mt5.ORDER_TYPE_BUY else tick.ask
            request = {
                "action": mt5.TRADE_ACTION_DEAL, "symbol": settings.SYMBOL, "volume": pos.volume,
                "type": close_type, "position": pos.ticket, "price": price,
                "deviation": settings.ORDER_DEVIATION, "magic": settings.MAGIC,
                "comment": "LSC_TIME_EXIT", "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_IOC,
            }
            result = mt5.order_send(request)
            if result and result.retcode == mt5.TRADE_RETCODE_DONE:
                log.info(f"Time exit | Ticket={pos.ticket} | {hours_open:.1f}h open")


def _get_signal() -> dict | None:
    """
    Fetch M15 bars, compute the LSC signal on the latest closed bar.

    Every time-based gate in here is evaluated on the CLOSED BAR'S OWN
    TIMESTAMP - broker server time, the clock backtest/lsc_engine.py's bar
    index runs on - never on this machine's wall clock. The session window,
    the per-day trade cap and the cooldown bar count therefore mean exactly
    what they meant in the validated backtest. Before 2026-09-15 the session
    test used wall-clock UTC; Pepperstone's server clock is UTC+3, so live
    was trading 12-14 UTC (never backtested) and skipping 17-19 UTC (validated).
    """
    global _bias_date, _trades_today, _last_trade_bar, _bar_index, _last_bar_time

    mt5.symbol_select(settings.SYMBOL, True)
    rates = mt5.copy_rates_from_pos(settings.SYMBOL, settings.SIGNAL_TIMEFRAME, 0, BARS_TO_FETCH)
    if rates is None or len(rates) < MIN_BARS:
        n = len(rates) if rates is not None else 0
        log.warning(f"Insufficient M15 bars — got {n}/{MIN_BARS} required")
        return None

    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    df.set_index("time", inplace=True)

    # Only evaluate once per newly closed bar, not every 60s inside the same bar.
    latest_closed_time = df.index[-2]
    if latest_closed_time == _last_bar_time:
        return None
    _last_bar_time = latest_closed_time
    _bar_index += 1   # advances on EVERY closed bar, filtered or not - like the backtest's i

    # Daily cap resets on the bar's server-time date, as in the backtest.
    bar_date = latest_closed_time.date()
    if _bias_date != bar_date:
        _bias_date, _trades_today = bar_date, 0

    if _trades_today >= settings.MAX_TRADES_PER_DAY:
        return None
    if latest_closed_time.hour not in settings.SESSION_HOURS:
        return None
    if not spread_ok():
        log.debug("Spread filter blocked")
        return None
    if not news_ok():
        # news_ok() logs the blocking event itself.
        return None

    df["atr"] = compute_atr14(df, settings.ATR_PERIOD)
    df = precompute(df)

    i = len(df) - 2   # last fully closed bar
    # check_entry measures the cooldown as (i - last_trade_bar) in bar-index
    # units. i is a position inside THIS fetched frame, _last_trade_bar is
    # the global closed-bar counter, so the last trade must be translated
    # into frame coordinates. Passing the raw counter (as this did before
    # 2026-09-15) meant no cooldown at all for the first ~frame-length bars
    # of uptime and a PERMANENT cooldown after that - proven by replaying
    # this function over frozen bars against the backtest harness.
    last_trade_in_frame = i - (_bar_index - _last_trade_bar)
    direction, reason, sl, tp = check_entry(
        df, i, last_trade_in_frame, _trades_today,
        settings.COOLDOWN_BARS, settings.MAX_TRADES_PER_DAY,
    )
    if direction == "NEUTRAL":
        return None

    log.info(f"Signal: {direction} | {reason}")
    return {"direction": direction, "sl": sl, "tp": tp}


def run():
    global _trades_today, _last_trade_bar

    now = datetime.now(timezone.utc)

    if circuit_breaker.is_tripped():
        log.warning(f"Circuit breaker: {circuit_breaker.status()}")
        return

    _check_time_exit()
    manage_open_trades()

    is_friday  = now.weekday() == 4
    is_weekend = now.weekday() in (5, 6)
    if is_weekend or (is_friday and now.hour >= settings.FRIDAY_CLOSE_HOUR):
        _weekend_close_all()
        return
    if is_friday and now.hour >= settings.FRIDAY_CUTOFF_HOUR:
        return

    signal = _get_signal()
    if signal:
        ok = execute_trade(signal["direction"], signal["sl"], signal["tp"])
        if ok:
            _trades_today  += 1
            _last_trade_bar = _bar_index

    acct = mt5.account_info()
    bal_str = f"${acct.balance:.2f}" if acct else "N/A"
    cb_ok = "TRIPPED" if circuit_breaker.is_tripped() else "OK"
    log.info(f"Running OK | Trades today: {_trades_today}/{settings.MAX_TRADES_PER_DAY} | "
             f"Circuit: {cb_ok} | Balance: {bal_str}")


def main():
    os.makedirs("jasons", exist_ok=True)
    log.info("=" * 60)
    log.info("   MIDAS — Stage 4: Liquidity-Sweep Continuation (M15)")
    log.info("=" * 60)

    if not connect_mt5():
        log.error("Failed to connect to MT5 — exiting")
        return

    account = mt5.account_info()
    if account:
        log.info(f"Account: {account.login} | Server: {account.server} | "
                 f"Balance: ${account.balance:.2f} | {'DEMO' if account.trade_mode == 0 else 'LIVE'}")
        send_bot_started(account.balance)
    else:
        log.warning("Could not read account info")

    log.info(f"Scanning every {settings.LOOP_INTERVAL_SECONDS}s | Max {settings.MAX_TRADES_PER_DAY} trades/day")
    log.info(f"Spread limit: {settings.MAX_SPREAD_POINTS}pts | Risk: {settings.RISK_PERCENT}% "
             f"(margin-safe capped at {settings.MARGIN_SAFETY_BUDGET_PCT*100:.0f}% budget)")
    log.info(f"Circuit breaker: {settings.MAX_CONSECUTIVE_LOSSES} losses OR {settings.MAX_DAILY_LOSS_PCT}% daily loss")

    try:
        while True:
            if mt5.terminal_info() is None:
                log.warning(f"MT5 connection lost (error {mt5.last_error()}) — reconnecting...")
                _reconnect()

            try:
                run()
            except Exception as e:
                log.error(f"Loop error: {e}", exc_info=True)
                if not mt5.terminal_info():
                    log.warning("MT5 connection lost after exception — reconnecting...")
                    _reconnect()
            time.sleep(settings.LOOP_INTERVAL_SECONDS)

    except KeyboardInterrupt:
        log.info("Stopped by user")
    finally:
        account = mt5.account_info()
        balance = account.balance if account else 0.0
        send_bot_stopped("Manual stop", balance)
        disconnect_mt5()
        log.info("MT5 disconnected")


if __name__ == "__main__":
    main()
