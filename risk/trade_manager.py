"""
risk/trade_manager.py — LSC (Stage 4) order execution and position sizing.

Deliberately simple: static SL/TP from strategy/lsc_m15.py's check_entry(),
placed once at entry, no breakeven/partial-close/trailing. None of those
were part of what LSC was actually backtested with — adding them live
would mean shipping unvalidated behavior, which is exactly what went
wrong with the old Bot 1/Bot 2 system this replaces.

Position sizing NEVER trusts the risk% formula alone: it's capped at a
margin-safe ceiling computed live from the account's actual balance,
leverage, and current price (see calculate_lot_size). Hardcoding a cap
value tuned for one specific account (the $25k FundedNext exploration)
would silently misbehave on a different account — this was learned the
hard way at three separate timeframes in one night.
"""

import json
from datetime import datetime, timezone, timedelta

import MetaTrader5 as mt5
from utils.logger import setup_logger
from utils.notifications import send_trade_opened, send_trade_closed
from risk.circuit_breaker import circuit_breaker
from config.settings import (
    SYMBOL, MAGIC, ORDER_DEVIATION, RISK_PERCENT, MARGIN_SAFETY_BUDGET_PCT,
)

logger = setup_logger("trade_manager")

_known_open_tickets = set()


# ── POSITION SIZING ───────────────────────────────────────────────────────────

def calculate_lot_size(symbol: str, sl_dist_price: float) -> float:
    """
    sl_dist_price: SL distance in price units (not points).
    Returns a lot size that respects BOTH the risk% formula AND a live
    margin-safe ceiling, whichever is smaller.
    """
    account  = mt5.account_info()
    sym_info = mt5.symbol_info(symbol)
    tick     = mt5.symbol_info_tick(symbol)
    if not account or not sym_info or not tick or sl_dist_price <= 0:
        return 0.01

    balance       = account.balance
    point         = sym_info.point
    contract_size = sym_info.trade_contract_size
    sl_points     = sl_dist_price / point

    risk_amount = balance * (RISK_PERCENT / 100)
    raw_lot     = risk_amount / (sl_points * contract_size * point)

    # Margin-safe ceiling, computed live — not a hardcoded value.
    price         = tick.ask
    leverage      = account.leverage or 1
    margin_budget = balance * MARGIN_SAFETY_BUDGET_PCT
    margin_safe_max_lot = (margin_budget * leverage) / (contract_size * price)

    lot = min(raw_lot, margin_safe_max_lot, sym_info.volume_max)
    lot = max(lot, sym_info.volume_min)
    step = sym_info.volume_step
    lot = round(round(lot / step) * step, 2)

    if raw_lot > margin_safe_max_lot:
        logger.warning(
            f"Lot capped by margin-safe ceiling: risk formula wanted {raw_lot:.4f}, "
            f"capped to {lot} (margin ceiling ~{margin_safe_max_lot:.4f} at "
            f"{MARGIN_SAFETY_BUDGET_PCT*100:.0f}% budget, leverage 1:{leverage:.0f})"
        )
    logger.info(f"Lot size: {lot} | Risk: ${risk_amount:.2f} | SL dist: {sl_dist_price:.2f} | Balance: ${balance:.2f}")
    return lot


# ── EXECUTION ─────────────────────────────────────────────────────────────────

def execute_trade(direction: str, sl: float, tp: float) -> bool:
    """
    direction/sl/tp come directly from strategy.lsc_m15.check_entry() —
    no SL/TP re-derivation here, the signal already computed them.
    """
    sym_info = mt5.symbol_info(SYMBOL)
    tick     = mt5.symbol_info_tick(SYMBOL)
    if not sym_info or not tick:
        logger.error(f"Could not get symbol/tick data for {SYMBOL}.")
        return False

    order_type = mt5.ORDER_TYPE_BUY if direction == "BUY" else mt5.ORDER_TYPE_SELL
    price      = tick.ask if direction == "BUY" else tick.bid
    sl_dist    = abs(price - sl)

    lot_size = calculate_lot_size(SYMBOL, sl_dist)
    if lot_size <= 0:
        logger.error("Lot size 0. Trade aborted.")
        return False

    request = {
        "action":       mt5.TRADE_ACTION_DEAL,
        "symbol":       SYMBOL,
        "volume":       lot_size,
        "type":         order_type,
        "price":        price,
        "sl":           round(sl, sym_info.digits),
        "tp":           round(tp, sym_info.digits),
        "deviation":    ORDER_DEVIATION,
        "magic":        MAGIC,
        "comment":      "LSC",
        "type_time":    mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }

    logger.info(f"Sending {direction} | Price={price} SL={sl:.2f} TP={tp:.2f} Lots={lot_size}")
    result = mt5.order_send(request)

    if result is None:
        logger.error(f"order_send returned None. Error: {mt5.last_error()}")
        return False

    if result.retcode == mt5.TRADE_RETCODE_DONE:
        logger.info(f"Trade executed | Ticket={result.order} {direction} {lot_size} lots @ {result.price}")
        send_trade_opened(direction, result.price, sl, tp, lot_size)
        return True

    logger.error(f"Trade failed. Retcode={result.retcode} | {result.comment}")
    _log_retcode_hint(result.retcode)
    return False


# ── OPEN TRADE MANAGEMENT ─────────────────────────────────────────────────────
# Deliberately minimal: no breakeven, partial close, or trailing — none of
# that was part of what was validated. Time-based hard exit is handled
# separately by utils/time_exit.py, matching the backtest's own fallback.

def manage_open_trades():
    """Called every bot loop. Detects closed trades for circuit breaker + notifications."""
    _check_for_closed_trades()


def _check_for_closed_trades():
    global _known_open_tickets
    positions = mt5.positions_get(symbol=SYMBOL) or []
    current_tickets = {p.ticket for p in positions if p.magic == MAGIC}

    closed = _known_open_tickets - current_tickets
    for ticket in closed:
        _on_trade_closed(ticket)

    _known_open_tickets = current_tickets


def _on_trade_closed(ticket: int):
    """Called when a trade closes. Records result and sends WhatsApp alert."""
    from_date = datetime.now(timezone.utc) - timedelta(days=2)
    to_date   = datetime.now(timezone.utc)
    deals = mt5.history_deals_get(from_date, to_date)
    if not deals:
        return
    pos_deals = [d for d in deals if d.position_id == ticket]
    out_deals = [d for d in pos_deals if d.entry == mt5.DEAL_ENTRY_OUT]
    in_deals  = [d for d in pos_deals if d.entry == mt5.DEAL_ENTRY_IN]
    if not out_deals:
        return
    pnl        = sum(d.profit + d.commission + d.swap for d in out_deals)
    result     = "WIN" if pnl > 0 else "LOSS"
    exit_price = out_deals[-1].price
    direction  = "BUY" if (in_deals and in_deals[0].type == mt5.DEAL_TYPE_BUY) else "SELL"
    entry      = in_deals[0].price if in_deals else 0.0
    acct       = mt5.account_info()
    balance    = acct.balance if acct else 0.0
    logger.info(f"Trade closed | Ticket={ticket} | {direction} | P&L=${pnl:.2f} | {result}")
    circuit_breaker.record_trade(pnl)
    send_trade_closed(result, direction, entry, exit_price, pnl, balance)


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
