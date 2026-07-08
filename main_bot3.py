"""
main_bot3.py — Bot 3: High-Frequency M5 Engine (standalone)

Self-contained runner. Does NOT share state with main_combined.py.
Run independently: python main_bot3.py

Strategy:
  - Entry: M5 EMA21 proximity (1.5xATR) + 0.4xATR body + close in top/bottom 30%
  - ATR floor filter: ATR14 must be above 10-bar minimum
  - RSI filter: RSI14 > 50 BUY / < 50 SELL
  - Session: 00:00-14:59 UTC and 20:00-23:59 UTC
  - Spread <= 20 points
  - 15 min cooldown between trades
  - Max 4 trades per day

NOT LIVE until backtest validates target: 100+ trades, 48-55% WR, PF > 1.5, DD < 15%.
"""

import sys
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

import time
from datetime import datetime, timezone, timedelta

import MetaTrader5 as mt5
import pandas as pd

from utils.logger import setup_logger
from utils.mt5_connection import connect_mt5, disconnect_mt5
from utils.notifications import (
    send_trade_opened, send_bot_started, send_bot_stopped,
)
from risk.circuit_breaker import circuit_breaker
from strategy.indicators import add_indicators
from strategy.m5_highfreq_engine import check_entry
from config import settings

logger = setup_logger("BOT3")

# ── Strategy config (update from backtest results) ────────────────────────────
PROXIMITY_ATR_MULT  = 1.5
BODY_ATR_MULT       = 0.4
CLOSE_RANGE_THRESH  = 0.70   # top/bottom 30%
ATR_FLOOR_FILTER    = True
RSI_FILTER          = True

# ── Execution params ─────────────────────────────────────────────────────────
SYMBOL             = settings.SYMBOL
MAGIC              = settings.MAGIC + 2   # 10003 — distinct from Bot 1/2 (10001)
SL_ATR_MULT        = 1.5
REWARD_RATIO       = 2.0
MAX_LOT            = 0.01
MAX_TRADES_PER_DAY = 4
COOLDOWN_MIN       = 15
SPREAD_MAX_PTS     = 20
SESSION_HOURS      = set(range(0, 15)) | {20, 21, 22, 23}

INDICATOR_CONFIG = {
    "EMA_FAST":   9,
    "EMA_SLOW":   21,
    "EMA_TREND":  50,
    "RSI_PERIOD": 14,
    "ATR_PERIOD": 14,
}

# ── State ─────────────────────────────────────────────────────────────────────
_last_trade_time: datetime | None = None
_trades_today:    int             = 0
_wins_today:      int             = 0
_losses_today:    int             = 0
_day_start_balance: float         = 0.0
_trade_date:      object          = None
_known_tickets:   set             = set()


def _reset_day(balance: float):
    global _trades_today, _wins_today, _losses_today, _day_start_balance, _trade_date
    today = datetime.now(timezone.utc).date()
    _trade_date        = today
    _trades_today      = 0
    _wins_today        = 0
    _losses_today      = 0
    _day_start_balance = balance
    logger.info(f"Day reset | {today} | Balance: ${balance:.2f}")


def _fetch_df() -> pd.DataFrame | None:
    mt5.symbol_select(SYMBOL, True)
    rates = mt5.copy_rates_from_pos(SYMBOL, mt5.TIMEFRAME_M5, 0, 300)
    if rates is None or len(rates) < 70:
        return None
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    df.set_index("time", inplace=True)
    return add_indicators(df, INDICATOR_CONFIG)


def _execute_trade(direction: str, atr: float) -> bool:
    sym   = mt5.symbol_info(SYMBOL)
    tick  = mt5.symbol_info_tick(SYMBOL)
    acct  = mt5.account_info()
    if not sym or not tick or not acct:
        logger.error("Cannot get symbol/tick/account info")
        return False

    spread_pts = (tick.ask - tick.bid) / sym.point
    if spread_pts > SPREAD_MAX_PTS:
        logger.debug(f"Spread {spread_pts:.0f}pts exceeds {SPREAD_MAX_PTS} -- skip")
        return False

    price  = tick.ask if direction == "BUY" else tick.bid
    sl_dist = atr * SL_ATR_MULT
    tp_dist = sl_dist * REWARD_RATIO

    if direction == "BUY":
        sl = round(price - sl_dist, sym.digits)
        tp = round(price + tp_dist, sym.digits)
        order_type = mt5.ORDER_TYPE_BUY
    else:
        sl = round(price + sl_dist, sym.digits)
        tp = round(price - tp_dist, sym.digits)
        order_type = mt5.ORDER_TYPE_SELL

    risk_amt  = acct.balance * (settings.RISK_PERCENT / 100)
    sl_points = sl_dist / sym.point
    pip_val   = sym.point * sym.trade_contract_size
    if pip_val * sl_points <= 0:
        logger.error("Invalid SL distance -- cannot size lot")
        return False
    raw_lot = risk_amt / (sl_points * pip_val)
    lot     = max(sym.volume_min,
                  min(round(round(raw_lot / sym.volume_step) * sym.volume_step, 2),
                      MAX_LOT))

    request = {
        "action":      mt5.TRADE_ACTION_DEAL,
        "symbol":      SYMBOL,
        "volume":      lot,
        "type":        order_type,
        "price":       price,
        "sl":          sl,
        "tp":          tp,
        "deviation":   settings.ORDER_DEVIATION,
        "magic":       MAGIC,
        "comment":     "MIDAS_B3",
        "type_time":   mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }

    logger.info(f"Sending {direction} {lot} lots | SL={sl:.2f} TP={tp:.2f} | ATR={atr:.2f}")
    result = mt5.order_send(request)

    if result and result.retcode == mt5.TRADE_RETCODE_DONE:
        logger.info(f"FILLED | Ticket={result.order} | {direction} {lot} lots @ {result.price:.2f}")
        send_trade_opened(direction, result.price, sl, tp, lot)
        _known_tickets.add(result.order)
        return True

    code = result.retcode if result else "None"
    msg  = result.comment if result else "None"
    logger.error(f"Order FAILED | retcode={code} | {msg}")
    return False


def _check_closed_positions():
    """Detect any Bot 3 positions that closed since last check and record P&L."""
    global _wins_today, _losses_today
    if not _known_tickets:
        return
    open_tickets = {p.ticket for p in (mt5.positions_get(symbol=SYMBOL) or [])
                    if p.magic == MAGIC}
    for ticket in list(_known_tickets):
        if ticket not in open_tickets:
            _known_tickets.discard(ticket)
            deals = mt5.history_deals_get(position=ticket)
            if not deals:
                continue
            pnl = sum(d.profit for d in deals)
            circuit_breaker.record_trade(pnl)
            if pnl >= 0:
                _wins_today += 1
                logger.info(f"Position {ticket} closed WIN | P&L +${pnl:.2f}")
            else:
                _losses_today += 1
                logger.info(f"Position {ticket} closed LOSS | P&L ${pnl:.2f}")


def run_bot3():
    global _last_trade_time, _trades_today, _trade_date

    now   = datetime.now(timezone.utc)
    today = now.date()

    if _trade_date != today:
        acct = mt5.account_info()
        _reset_day(acct.balance if acct else 0.0)

    _check_closed_positions()

    if circuit_breaker.is_tripped():
        logger.debug(f"Circuit breaker: {circuit_breaker.status()}")
        return

    if now.hour not in SESSION_HOURS:
        return

    if _trades_today >= MAX_TRADES_PER_DAY:
        return

    if _last_trade_time and (now - _last_trade_time).total_seconds() < COOLDOWN_MIN * 60:
        return

    if now.weekday() == 4 and now.hour >= settings.FRIDAY_CUTOFF_HOUR:
        return

    df = _fetch_df()
    if df is None:
        logger.warning("Insufficient M5 data")
        return

    direction, reason = check_entry(
        df,
        atr_floor_filter   = ATR_FLOOR_FILTER,
        rsi_filter         = RSI_FILTER,
        proximity_atr_mult = PROXIMITY_ATR_MULT,
        body_atr_mult      = BODY_ATR_MULT,
        close_range_thresh = CLOSE_RANGE_THRESH,
    )

    if direction == "NEUTRAL":
        logger.debug(f"No signal: {reason}")
        return

    atr = float(df.iloc[-1]["atr"])
    logger.info(f"Signal: {direction} | {reason}")

    ok = _execute_trade(direction, atr)
    if ok:
        _last_trade_time = now
        _trades_today   += 1
        logger.info(f"Trades today: {_trades_today}/{MAX_TRADES_PER_DAY}")


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    logger.info("=" * 60)
    logger.info("   BOT 3 -- High-Frequency M5 Engine")
    logger.info(f"   Prox {PROXIMITY_ATR_MULT}xATR | Body {BODY_ATR_MULT}xATR | "
                f"Close {int((1 - CLOSE_RANGE_THRESH) * 100)}% | "
                f"RSI={'ON' if RSI_FILTER else 'OFF'} | "
                f"ATRFloor={'ON' if ATR_FLOOR_FILTER else 'OFF'}")
    logger.info(f"   Max {MAX_TRADES_PER_DAY} trades/day | {COOLDOWN_MIN}min cooldown | "
                f"Spread<={SPREAD_MAX_PTS}pts | Magic={MAGIC}")
    logger.info("=" * 60)

    if not connect_mt5():
        logger.error("Failed to connect to MT5 -- exiting")
        return

    acct = mt5.account_info()
    if acct:
        mode = "DEMO" if acct.trade_mode == 0 else "LIVE"
        logger.info(f"Account: {acct.login} | Balance: ${acct.balance:.2f} | {mode}")
        send_bot_started(acct.balance)
        _reset_day(acct.balance)

    try:
        while True:
            if mt5.terminal_info() is None:
                logger.warning("MT5 connection lost -- reconnecting...")
                if not connect_mt5():
                    logger.error("Reconnect failed -- exiting")
                    break
            try:
                run_bot3()
            except Exception as e:
                logger.error(f"Loop error: {e}", exc_info=True)
            time.sleep(settings.LOOP_INTERVAL_SECONDS)
    except KeyboardInterrupt:
        logger.info("Stopped by user (KeyboardInterrupt)")
    finally:
        acct = mt5.account_info()
        bal  = acct.balance if acct else 0.0
        send_bot_stopped("User stopped", bal)
        disconnect_mt5()
        logger.info(f"Done | Final balance: ${bal:.2f} | "
                    f"Trades: {_trades_today} | W:{_wins_today} L:{_losses_today}")


if __name__ == "__main__":
    main()
