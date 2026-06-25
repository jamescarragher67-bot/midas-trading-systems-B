"""
risk/trade_manager.py

Handles:
  - Position sizing (1% risk)
  - Order execution
  - Break-even stop
  - Partial close at 1:1 RR
  - Trailing stop
  - Circuit breaker recording
  - Telegram alerts
"""

import MetaTrader5 as mt5
from utils.logger import setup_logger
from utils.smart_sl import calculate_sl
from utils.dynamic_risk import get_risk_pct
from utils.performance_monitor import get_current_risk_pct
from utils.notifications import send_trade_opened, send_trade_closed
from risk.circuit_breaker import circuit_breaker
from config.settings import (
    SYMBOL, RISK_PERCENT, REWARD_RATIO, ATR_SL_MULTIPLIER,
    BREAKEVEN_ENABLED, BREAKEVEN_PIPS,
    PARTIAL_CLOSE_ENABLED, PARTIAL_CLOSE_AT_RR, PARTIAL_CLOSE_PCT,
    TRAILING_ENABLED, TRAILING_STEP_PIPS,
)

logger = setup_logger("trade_manager")

# Track which positions have had partial close applied
_partial_closed = set()


# ── POSITION SIZING ───────────────────────────────────────────────────────────

def calculate_lot_size(symbol: str, sl_points: float) -> float:
    account  = mt5.account_info()
    sym_info = mt5.symbol_info(symbol)
    if not account or not sym_info:
        return 0.01
    balance             = account.balance
    # Performance monitor overrides dynamic risk during bad streaks
    perf_risk    = get_current_risk_pct()
    dynamic_risk = min(get_risk_pct(balance), perf_risk) if perf_risk < get_risk_pct(balance) else get_risk_pct(balance)
    risk_amount         = balance * (dynamic_risk / 100)
    logger.info(f"Dynamic risk: {dynamic_risk}% of ${balance:.0f}")
    point               = sym_info.point
    contract_size       = sym_info.trade_contract_size
    pip_value_per_lot   = point * contract_size
    if pip_value_per_lot == 0 or sl_points == 0:
        return 0.01
    lot_size = risk_amount / (sl_points * pip_value_per_lot)
    lot_size = max(sym_info.volume_min, min(lot_size, sym_info.volume_max))
    step     = sym_info.volume_step
    lot_size = round(round(lot_size / step) * step, 2)
    logger.info(f"Lot size: {lot_size} | Risk: ${risk_amount:.2f} | SL pips: {sl_points:.1f} | Balance: ${balance:.2f}")
    return lot_size

# execution order 

def execute_trade(signal: dict):
    from strategy.signal_engine import increment_trade_count

    symbol    = signal["symbol"]
    direction = signal["direction"]
    atr       = signal["atr"]

    sym_info = mt5.symbol_info(symbol)
    tick     = mt5.symbol_info_tick(symbol)
    if not sym_info or not tick:
        logger.error(f"Could not get symbol/tick data for {symbol}.")
        return

    point = sym_info.point

    # Get price FIRST before anything else
    if direction == "BUY":
        order_type = mt5.ORDER_TYPE_BUY
        price      = tick.ask
    else:
        order_type = mt5.ORDER_TYPE_SELL
        price      = tick.bid

    # Smart SL placement
    df = signal.get("df")
    sl, sl_method = calculate_sl(df, direction, price, atr)
    sl_dist = abs(price - sl)
    sl_pips = sl_dist
    tp_pips = sl_pips * REWARD_RATIO

    sl = round(price - sl_pips, sym_info.digits) if direction == "BUY" else round(price + sl_pips, sym_info.digits)
    tp = round(price + tp_pips, sym_info.digits) if direction == "BUY" else round(price - tp_pips, sym_info.digits)

    lot_size = calculate_lot_size(symbol, sl_pips / point)
    if lot_size <= 0:
        logger.error("Lot size 0. Trade aborted.")
        return

    request = {
        "action":       mt5.TRADE_ACTION_DEAL,
        "symbol":       symbol,
        "volume":       lot_size,
        "type":         order_type,
        "price":        price,
        "sl":           sl,
        "tp":           tp,
        "deviation":    20,
        "magic":        10001,
        "comment":      "Midas",
        "type_time":    mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }

    logger.info(f"Sending {direction} | Price={price} SL={sl} TP={tp} Lots={lot_size}")
    result = mt5.order_send(request)

    if result is None:
        logger.error(f"order_send returned None. Error: {mt5.last_error()}")
        return

    if result.retcode == mt5.TRADE_RETCODE_DONE:
        logger.info(f"✅ Trade executed! Ticket={result.order} {direction} {lot_size} lots @ {result.price}")
        increment_trade_count()
        send_trade_opened(direction, result.price, sl, tp, lot_size)
    else:
        logger.error(f"❌ Trade failed. Retcode={result.retcode} | {result.comment}")
        _log_retcode_hint(result.retcode)


# ── OPEN TRADE MANAGEMENT ─────────────────────────────────────────────────────

def manage_open_trades():
    """
    Called every bot loop.
    Handles break-even, partial close, trailing stop, and closed trade detection.
    """
    positions = mt5.positions_get(symbol=SYMBOL)
    if not positions:
        return

    for pos in positions:
        if pos.magic != 10001:
            continue

        sym_info = mt5.symbol_info(SYMBOL)
        tick     = mt5.symbol_info_tick(SYMBOL)
        if not sym_info or not tick:
            continue

        point      = sym_info.point
        is_buy     = pos.type == mt5.ORDER_TYPE_BUY
        entry      = pos.price_open
        current    = tick.bid if is_buy else tick.ask
        sl_current = pos.sl
        tp         = pos.tp

        pips_profit = (current - entry) / point if is_buy else (entry - current) / point

        logger.debug(
            f"Pos {pos.ticket} | {'BUY' if is_buy else 'SELL'} @ {entry:.2f} | "
            f"Current: {current:.2f} | Pips: {pips_profit:.1f} | P&L: ${pos.profit:.2f}"
        )

        new_sl = sl_current

        # ── Break-even ────────────────────────────────────────────────────────
        if BREAKEVEN_ENABLED and pips_profit >= BREAKEVEN_PIPS:
            be_sl = round(entry + (2 * point), sym_info.digits) if is_buy else round(entry - (2 * point), sym_info.digits)
            if (is_buy and be_sl > sl_current) or (not is_buy and be_sl < sl_current):
                new_sl = be_sl
                logger.info(f"Break-even triggered | Ticket={pos.ticket} | Moving SL to {new_sl:.2f}")

        # ── Partial close ─────────────────────────────────────────────────────
        if PARTIAL_CLOSE_ENABLED and pos.ticket not in _partial_closed and tp > 0:
            sl_dist = abs(entry - pos.sl)
            rr_dist = sl_dist * PARTIAL_CLOSE_AT_RR
            if (is_buy and current >= entry + rr_dist) or (not is_buy and current <= entry - rr_dist):
                _do_partial_close(pos, sym_info)
                _partial_closed.add(pos.ticket)

        # ── Trailing stop ─────────────────────────────────────────────────────
        if TRAILING_ENABLED:
            trail_dist = TRAILING_STEP_PIPS * point
            if is_buy:
                trail_sl = round(current - trail_dist, sym_info.digits)
                if trail_sl > sl_current:
                    new_sl = trail_sl
            else:
                trail_sl = round(current + trail_dist, sym_info.digits)
                if trail_sl < sl_current or sl_current == 0:
                    new_sl = trail_sl

        # ── Apply SL update ───────────────────────────────────────────────────
        if new_sl != sl_current and new_sl > 0:
            _modify_sl(pos, new_sl, sym_info)

    # ── Detect newly closed trades ────────────────────────────────────────────
    _check_for_closed_trades()


def _do_partial_close(pos, sym_info):
    close_volume = round(pos.volume * PARTIAL_CLOSE_PCT / sym_info.volume_step) * sym_info.volume_step
    close_volume = max(sym_info.volume_min, round(close_volume, 2))
    if close_volume >= pos.volume:
        return

    tick  = mt5.symbol_info_tick(SYMBOL)
    price = tick.bid if pos.type == mt5.ORDER_TYPE_BUY else tick.ask

    request = {
        "action":       mt5.TRADE_ACTION_DEAL,
        "symbol":       SYMBOL,
        "volume":       close_volume,
        "type":         mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY,
        "position":     pos.ticket,
        "price":        price,
        "deviation":    20,
        "magic":        10001,
        "comment":      "partial_close",
        "type_time":    mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }
    result = mt5.order_send(request)
    if result and result.retcode == mt5.TRADE_RETCODE_DONE:
        logger.info(f"✅ Partial close | Ticket={pos.ticket} | {close_volume} lots @ {price:.2f}")
    else:
        logger.warning(f"Partial close failed | {result.comment if result else 'None'}")


def _modify_sl(pos, new_sl: float, sym_info):
    request = {
        "action":   mt5.TRADE_ACTION_SLTP,
        "symbol":   SYMBOL,
        "position": pos.ticket,
        "sl":       new_sl,
        "tp":       pos.tp,
    }
    result = mt5.order_send(request)
    if result and result.retcode == mt5.TRADE_RETCODE_DONE:
        logger.info(f"SL updated | Ticket={pos.ticket} | {pos.sl:.2f} → {new_sl:.2f}")
    else:
        logger.debug(f"SL modify failed | {result.comment if result else 'None'}")


# ── CLOSED TRADE DETECTION ────────────────────────────────────────────────────

_known_open_tickets = set()

def _check_for_closed_trades():
    """Detect when a position closes and record result to circuit breaker."""
    global _known_open_tickets
    positions = mt5.positions_get(symbol=SYMBOL) or []
    current_tickets = {p.ticket for p in positions if p.magic == 10001}

    closed = _known_open_tickets - current_tickets
    for ticket in closed:
        _on_trade_closed(ticket)
        _partial_closed.discard(ticket)

    _known_open_tickets = current_tickets


def _on_trade_closed(ticket: int):
    """Called when a trade closes. Records result and sends Telegram alert."""
    from datetime import datetime, timezone
    from_date = datetime(2026, 1, 1, tzinfo=timezone.utc)
    to_date   = datetime.now(timezone.utc)
    deals = mt5.history_deals_get(from_date, to_date)
    if not deals:
        return
    trade_deals = [d for d in deals if d.order == ticket and d.entry == mt5.DEAL_ENTRY_OUT]
    if not trade_deals:
        return
    pnl    = sum(d.profit for d in trade_deals)
    result = "WIN" if pnl > 0 else "LOSS"
    logger.info(f"Trade closed | Ticket={ticket} | P&L=${pnl:.2f} | {result}")
    circuit_breaker.record_trade(pnl)
    send_trade_closed(result, pnl)


# ── HELPERS ───────────────────────────────────────────────────────────────────

def _log_retcode_hint(retcode: int):
    hints = {
        10004: "Requote — will retry next cycle.",
        10013: "Invalid parameters — check SL/TP/lot size.",
        10016: "Invalid SL or TP.",
        10019: "Insufficient margin.",
        10027: "Algo trading disabled in MT5 — enable it!",
        10030: "Order fill type not supported.",
    }
    logger.error(f"Hint: {hints.get(retcode, 'Check MT5 error codes.')}")
