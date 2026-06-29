"""
utils/time_exit.py

Time-based exit — closes trades that have been open too long.

Prevents trades sitting open through unfavourable sessions,
news events, or market structure changes that occurred after entry.

Logic:
  - If a trade has been open longer than MAX_TRADE_HOURS — close it
  - Only closes if trade is in profit (protect losing trades with SL)
  - If trade is losing after MAX_TRADE_HOURS_HARD — close regardless
"""

import MetaTrader5 as mt5
from datetime import datetime, timezone, timedelta
from utils.logger import setup_logger
from config.settings import (
    MAGIC, ORDER_DEVIATION,
    TIME_EXIT_ENABLED,
    MAX_TRADE_HOURS,
    MAX_TRADE_HOURS_HARD,
    TIME_EXIT_MIN_PROFIT_USD,
    FRIDAY_CLOSE_HOUR,
)

logger = setup_logger("time_exit")


def check_time_exits(symbol: str):
    """
    Check all open positions and close any that have exceeded time limits.
    Called every loop cycle from main.py.
    """
    if not TIME_EXIT_ENABLED:
        return

    positions = mt5.positions_get(symbol=symbol)
    if not positions:
        return

    now = datetime.now(timezone.utc)

    # Friday close — shut everything before the weekend gap
    if now.weekday() == 4 and now.hour >= FRIDAY_CLOSE_HOUR:
        for pos in positions:
            if pos.magic != MAGIC:
                continue
            _close_position(pos, reason=f"Friday close ({now.strftime('%H:%M')} UTC — pre-weekend)")
        return

    for pos in positions:
        if pos.magic != MAGIC:
            continue
        open_time = datetime.fromtimestamp(pos.time, tz=timezone.utc)
        hours_open = (now - open_time).total_seconds() / 3600
        current_profit = pos.profit

        # Hard close — trade has been open way too long regardless of P&L
        if hours_open >= MAX_TRADE_HOURS_HARD:
            _close_position(pos, reason=f"Hard time exit ({hours_open:.1f}h open, P&L ${current_profit:.2f})")
            continue

        # Soft close — trade open too long AND profit meets minimum threshold
        if hours_open >= MAX_TRADE_HOURS and current_profit >= TIME_EXIT_MIN_PROFIT_USD:
            _close_position(pos, reason=f"Time exit in profit ({hours_open:.1f}h open, P&L +${current_profit:.2f})")
            continue

        logger.debug(f"Position {pos.ticket} open {hours_open:.1f}h | P&L ${current_profit:.2f} — no exit")


def _close_position(pos, reason: str):
    """Close a specific position at market price."""
    symbol    = pos.symbol
    ticket    = pos.ticket
    volume    = pos.volume
    direction = pos.type   # 0 = BUY, 1 = SELL

    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        logger.warning(f"Could not get tick for {symbol} — skipping time exit")
        return

    close_price = tick.bid if direction == mt5.ORDER_TYPE_BUY else tick.ask
    order_type  = mt5.ORDER_TYPE_SELL if direction == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY

    request = {
        "action":       mt5.TRADE_ACTION_DEAL,
        "symbol":       symbol,
        "volume":       volume,
        "type":         order_type,
        "position":     ticket,
        "price":        close_price,
        "deviation":    ORDER_DEVIATION,
        "magic":        MAGIC,
        "comment":      "time_exit",
        "type_time":    mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }

    result = mt5.order_send(request)

    if result and result.retcode == mt5.TRADE_RETCODE_DONE:
        logger.info(f"Time exit executed: ticket={ticket} | {reason}")
    else:
        code = result.retcode if result else "None"
        logger.warning(f"Time exit failed: ticket={ticket} | retcode={code} | {reason}")
