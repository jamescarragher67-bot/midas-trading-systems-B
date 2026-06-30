"""
main_combined.py — MIDAS Live: Bot 1 + Bot 2

Bot 1: Daily bias (3/3 unanimous: EMA Stack + ATR Expansion + Prev Day Structure)
       + EMA21 M5 pullback execution
       Spread limit: 15 points (tightened after stress test)

Bot 2: B->C regime transition mean reversion (ATR > 1.5x, VoV > 1.3x, wick > 0.6)
       Spread limit: 20 points (robust to wider spread per stress test)

Shared:
  - Single MT5 connection
  - Shared circuit breaker (4 consecutive losses OR 5% daily loss)
  - Combined 6 trades/day cap
  - trades.json (both bots append on close)
  - WhatsApp alerts via notifications.py
  - Single log file: BOT1 and BOT2 prefixes on every line

Usage:
    python main_combined.py

Switch to live: update MT5_LOGIN / MT5_PASSWORD / MT5_SERVER in config/.env
"""

import json
import os
import time
from datetime import datetime, timezone, timedelta

import MetaTrader5 as mt5
import pandas as pd

from utils.logger        import setup_logger
from utils.mt5_connection import connect_mt5, disconnect_mt5
from utils.notifications  import (send_daily_summary, send_trade_opened,
                                   send_trade_closed, send_bot_started, send_bot_stopped)
from utils.time_exit      import check_time_exits
from utils.news_filter    import is_news_blackout
from utils.filters        import spread_ok          # reads MAX_SPREAD_POINTS=15 from settings
from risk.trade_manager   import manage_open_trades
from risk.circuit_breaker import circuit_breaker
from strategy.indicators          import add_indicators
from strategy.m5_execution_engine import get_daily_bias, check_m5_entry
from strategy.volatility_metrics  import get_volatility_fingerprint
from strategy.regime_classifier   import classify_regime
from strategy.transition_detector import detect_transition
from strategy.volatility_signal_engine import get_signal
from utils.snapshot_logger import start as start_snapshot_logger
from config import settings

# ── Loggers — same daily file, different name prefix ─────────────────────────
log1    = setup_logger("BOT1")
log2    = setup_logger("BOT2")
log_sys = setup_logger("COMBINED")

INDICATOR_CONFIG = {"EMA_FAST": 9, "EMA_SLOW": 21, "EMA_TREND": 50,
                    "RSI_PERIOD": 14, "ATR_PERIOD": 14}

SESSION_HOURS = set(range(0, 15)) | {20, 21, 22, 23}   # 00:00-14:59 + 20:00-23:59 UTC

# ── Combined limits ───────────────────────────────────────────────────────────
MAX_COMBINED_TRADES = 6       # both bots together
BOT1_COOLDOWN_MIN   = 15
BOT2_COOLDOWN_MIN   = 15

# ── Bot configs ───────────────────────────────────────────────────────────────
_BOT1 = {
    "reward_ratio":         settings.REWARD_RATIO,
    "risk_pct":             settings.RISK_PERCENT,
    "max_spread_points":    15,
    "sl_atr_mult":          1.5,   # structural fallback SL
}

_BOT2 = {
    "reward_ratio":         settings.REWARD_RATIO,
    "risk_pct":             settings.RISK_PERCENT,
    "max_spread_points":    20,
    "bc_sl_atr_mult":       1.0,
    "bc_trail_atr_mult":    0.5,
    "ab_sl_atr_mult":       2.0,
    "ab_trail_atr_mult":    1.0,
    "session_filter":       True,
    "max_trades_per_day":   MAX_COMBINED_TRADES,
    "cooldown_bars":        3,
}

# ── State — Bot 1 ─────────────────────────────────────────────────────────────
_b1_bias            = "NONE"
_b1_bias_date       = None
_b1_last_trade_time = None

# ── State — Bot 2 ─────────────────────────────────────────────────────────────
_b2_regime_history  = []
_b2_last_trade_time = None

# ── State — Combined ──────────────────────────────────────────────────────────
_combined_trades_today = 0
_combined_date         = None
_summary_date          = None
_known_tickets         = {}   # ticket -> {"bot": "BOT1"|"BOT2"}

TRADES_FILE = "jasons/trades.json"


def _reconnect() -> bool:
    """Attempt up to 3 reconnects with exponential back-off. Returns True on success."""
    for attempt in range(1, 4):
        time.sleep(10 * attempt)
        if connect_mt5():
            log_sys.info(f"MT5 reconnected (attempt {attempt})")
            return True
        log_sys.warning(f"Reconnect attempt {attempt} failed: {mt5.last_error()}")
    return False


# ═════════════════════════════════════════════════════════════════════════════
# EXECUTION — shared by both bots
# ═════════════════════════════════════════════════════════════════════════════

def _execute_trade(direction: str, atr: float, sl_atr_mult: float,
                   reward_ratio: float, risk_pct: float,
                   bot_label: str, logger, comment: str) -> bool:
    """
    Place a market order. SL = entry ± ATR × sl_atr_mult. TP = SL dist × RR.
    Returns True on successful fill.
    """
    global _combined_trades_today, _known_tickets

    symbol   = settings.SYMBOL
    sym_info = mt5.symbol_info(symbol)
    tick     = mt5.symbol_info_tick(symbol)
    account  = mt5.account_info()
    if not sym_info or not tick or not account:
        logger.error("Cannot get symbol/tick/account — trade aborted")
        return False

    price   = tick.ask if direction == "BUY" else tick.bid
    sl_dist = atr * sl_atr_mult

    if direction == "BUY":
        sl         = round(price - sl_dist, sym_info.digits)
        tp         = round(price + sl_dist * reward_ratio, sym_info.digits)
        order_type = mt5.ORDER_TYPE_BUY
    else:
        sl         = round(price + sl_dist, sym_info.digits)
        tp         = round(price - sl_dist * reward_ratio, sym_info.digits)
        order_type = mt5.ORDER_TYPE_SELL

    # Lot size: balance × risk% / (SL points × pip value)
    balance   = account.balance
    risk_amt  = balance * (risk_pct / 100)
    sl_points = sl_dist / sym_info.point
    pip_val   = sym_info.point * sym_info.trade_contract_size
    if pip_val * sl_points <= 0:
        logger.error("Invalid SL distance — trade aborted")
        return False

    lot = risk_amt / (sl_points * pip_val)
    # Hard cap: 0.01 lots maximum during demo/testing phase
    MAX_LOT = 0.01
    raw_lot = lot
    lot = max(sym_info.volume_min,
              min(round(round(lot / sym_info.volume_step) * sym_info.volume_step, 2), MAX_LOT))
    if raw_lot > MAX_LOT:
        logger.warning(f"Lot size capped at {MAX_LOT} (formula gave {raw_lot:.4f})")

    request = {
        "action":       mt5.TRADE_ACTION_DEAL,
        "symbol":       symbol,
        "volume":       lot,
        "type":         order_type,
        "price":        price,
        "sl":           sl,
        "tp":           tp,
        "deviation":    settings.ORDER_DEVIATION,
        "magic":        settings.MAGIC,
        "comment":      comment,
        "type_time":    mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }

    logger.info(f"Sending {direction} {lot} lots | SL={sl:.2f} TP={tp:.2f} | ATR={atr:.2f}")
    result = mt5.order_send(request)

    if result and result.retcode == mt5.TRADE_RETCODE_DONE:
        logger.info(
            f"FILLED | Ticket={result.order} | {direction} {lot} lots @ {result.price:.2f}"
        )
        send_trade_opened(direction, result.price, sl, tp, lot)
        _combined_trades_today += 1
        _known_tickets[result.order] = {"bot": bot_label}
        return True
    else:
        code     = result.retcode if result else "None"
        err_msg  = result.comment if result else "None"
        logger.error(f"Order FAILED | retcode={code} | {err_msg}")
        _log_retcode_hint(result.retcode if result else 0, logger)
        return False


def _log_retcode_hint(retcode: int, logger):
    hints = {
        10004: "Requote — will retry next cycle.",
        10013: "Invalid params — check SL/TP/lots.",
        10016: "Invalid SL or TP.",
        10019: "Insufficient margin.",
        10027: "Algo trading disabled — enable in MT5!",
        10030: "Fill type not supported.",
    }
    if retcode in hints:
        logger.error(f"Hint: {hints[retcode]}")


# ═════════════════════════════════════════════════════════════════════════════
# CLOSED TRADE DETECTION + trades.json
# ═════════════════════════════════════════════════════════════════════════════

def _check_combined_closed_trades():
    global _known_tickets
    positions       = mt5.positions_get(symbol=settings.SYMBOL) or []
    current_tickets = {p.ticket for p in positions if p.magic == settings.MAGIC}
    closed          = set(_known_tickets.keys()) - current_tickets
    for ticket in closed:
        bot_label = _known_tickets[ticket].get("bot", "UNKNOWN")
        _on_trade_closed(ticket, bot_label)
        _known_tickets.pop(ticket, None)


def _on_trade_closed(ticket: int, bot_label: str):
    logger   = log1 if bot_label == "BOT1" else log2
    from_dt  = datetime.now(timezone.utc) - timedelta(days=2)
    to_dt    = datetime.now(timezone.utc)
    deals    = mt5.history_deals_get(from_dt, to_dt)
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

    logger.info(
        f"CLOSED | Ticket={ticket} | {direction} | P&L=${pnl:.2f} | {result} | Balance=${balance:.2f}"
    )
    circuit_breaker.record_trade(pnl)
    send_trade_closed(result, direction, entry, exit_price, pnl, balance)

    record = {
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
        "bot":       bot_label,
        "ticket":    ticket,
        "direction": direction,
        "result":    result,
        "entry":     round(entry, 2),
        "exit":      round(exit_price, 2),
        "pnl":       round(pnl, 2),
        "balance":   round(balance, 2),
    }
    _append_to_trades_json(record)


def _append_to_trades_json(record: dict):
    try:
        try:
            with open(TRADES_FILE) as f:
                all_trades = json.load(f)
        except Exception:
            all_trades = []
        all_trades.append(record)
        with open(TRADES_FILE, "w") as f:
            json.dump(all_trades, f, indent=2)
    except Exception as e:
        log_sys.warning(f"trades.json write failed: {e}")


# ═════════════════════════════════════════════════════════════════════════════
# BOT 1 — Daily bias + EMA21 M5 pullback
# ═════════════════════════════════════════════════════════════════════════════

def _get_bot1_signal() -> dict | None:
    global _b1_bias, _b1_bias_date, _b1_last_trade_time

    now   = datetime.now(timezone.utc)
    today = now.date()

    # Time-based cooldown
    if _b1_last_trade_time:
        if (now - _b1_last_trade_time).total_seconds() < BOT1_COOLDOWN_MIN * 60:
            return None

    # News blackout
    if is_news_blackout(now):
        log1.debug("News blackout active")
        return None

    # Spread check — 15pt limit (reads from settings.MAX_SPREAD_POINTS)
    if not spread_ok():
        log1.debug("Spread > 15pts — skipping")
        return None

    # Session filter
    if now.hour not in SESSION_HOURS:
        return None

    # Reset bias on new day
    if _b1_bias_date != today:
        _b1_bias      = "NONE"
        _b1_bias_date = today
        log1.info(f"New day — bias reset | {today}")

    # Fetch and indicator-add M5 bars
    mt5.symbol_select(settings.SYMBOL, True)
    rates = mt5.copy_rates_from_pos(settings.SYMBOL, mt5.TIMEFRAME_M5, 0, 300)
    if rates is None or len(rates) < 70:
        n = len(rates) if rates is not None else 0
        log1.warning(f"Insufficient M5 bars for Bot 1 — got {n}/70 required | MT5 error: {mt5.last_error()}")
        return None
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    df.set_index("time", inplace=True)
    df = add_indicators(df, INDICATOR_CONFIG)

    # Try to lock daily bias on first 3/3 unanimous bar
    if _b1_bias == "NONE":
        candidate = get_daily_bias(df)
        if candidate != "NONE":
            _b1_bias = candidate
            log1.info(f"Daily bias locked: {_b1_bias}")

    if _b1_bias == "NONE":
        return None

    # M5 entry conditions (cooldown handled above by time; pass passthrough values)
    direction, reason = check_m5_entry(df, _b1_bias,
                                       last_trade_bar=-9999, current_bar=0,
                                       trades_today=0)
    if direction == "NEUTRAL":
        return None

    atr = float(df.iloc[-1]["atr"])
    log1.info(f"Signal: {direction} | {reason} | ATR={atr:.2f} | Bias={_b1_bias}")
    return {"direction": direction, "atr": atr, "sl_atr_mult": _BOT1["sl_atr_mult"]}


# ═════════════════════════════════════════════════════════════════════════════
# BOT 2 — B->C regime transition mean reversion
# ═════════════════════════════════════════════════════════════════════════════

def _get_bot2_signal() -> dict | None:
    global _b2_regime_history, _b2_last_trade_time

    now = datetime.now(timezone.utc)

    # Cooldown
    if _b2_last_trade_time:
        if (now - _b2_last_trade_time).total_seconds() < BOT2_COOLDOWN_MIN * 60:
            return None

    # News blackout
    if is_news_blackout(now):
        return None

    # Session filter
    if now.hour not in SESSION_HOURS:
        return None

    # Spread check — 20pt limit for Bot 2
    sym_info = mt5.symbol_info(settings.SYMBOL)
    tick     = mt5.symbol_info_tick(settings.SYMBOL)
    if sym_info and tick:
        spread = (tick.ask - tick.bid) / sym_info.point
        if spread > _BOT2["max_spread_points"]:
            log2.debug(f"Spread {spread:.0f}pts > 20 — skipping")
            return None

    # Fetch M5 bars
    mt5.symbol_select(settings.SYMBOL, True)
    rates = mt5.copy_rates_from_pos(settings.SYMBOL, mt5.TIMEFRAME_M5, 0, 300)
    if rates is None or len(rates) < 110:
        n = len(rates) if rates is not None else 0
        log2.warning(f"Insufficient M5 bars for Bot 2 — got {n}/110 required | MT5 error: {mt5.last_error()}")
        return None
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    df.set_index("time", inplace=True)

    # Volatility fingerprint
    fp = get_volatility_fingerprint(df)
    if fp["atr_ma50"] == 0 or fp["stddev_ma50"] == 0:
        return None

    # Regime classification
    regime      = classify_regime(fp)
    prev_regime = _b2_regime_history[-1] if _b2_regime_history else regime
    _b2_regime_history.append(regime)
    if len(_b2_regime_history) > 30:
        _b2_regime_history.pop(0)

    # Only fire on regime change with sufficient history
    if regime == prev_regime or len(_b2_regime_history) < 4:
        return None

    # Detect transition
    current_bar = df.iloc[-1]
    transition  = detect_transition(_b2_regime_history, fp, current_bar)
    if transition["transition"] in ("NONE", "C_to_A"):
        return None

    # Generate signal
    signal = get_signal(transition, fp, _BOT2)
    if signal is None:
        return None

    log2.info(
        f"Signal: {transition['transition']} | {signal['direction']} | "
        f"ATR={fp['atr']:.2f} | conf={transition['confidence']:.2f}"
    )
    return {
        "direction":   signal["direction"],
        "atr":         signal["atr"],
        "sl_atr_mult": signal["sl_atr_mult"],
    }


# ═════════════════════════════════════════════════════════════════════════════
# DAILY SUMMARY
# ═════════════════════════════════════════════════════════════════════════════

def _send_daily_summary_if_needed():
    global _summary_date
    today = datetime.now(timezone.utc).date()
    if _summary_date == today:
        return
    if _summary_date is not None:
        yesterday = str(_summary_date)
        try:
            with open(TRADES_FILE) as f:
                all_trades = json.load(f)
            day_trades = [t for t in all_trades if t.get("timestamp", "")[:10] == yesterday]
        except Exception:
            day_trades = []
        wins    = sum(1 for t in day_trades if t.get("result") == "WIN")
        losses  = sum(1 for t in day_trades if t.get("result") == "LOSS")
        pnl     = sum(t.get("pnl", 0) for t in day_trades)
        account = mt5.account_info()
        balance = account.balance if account else 0.0
        b1_cnt  = sum(1 for t in day_trades if t.get("bot") == "BOT1")
        b2_cnt  = sum(1 for t in day_trades if t.get("bot") == "BOT2")
        log_sys.info(
            f"Daily summary | {yesterday} | {len(day_trades)} trades "
            f"(B1={b1_cnt}, B2={b2_cnt}) | {wins}W/{losses}L | P&L=${pnl:.2f}"
        )
        send_daily_summary(len(day_trades), wins, losses, pnl, balance)
    _summary_date = today


# ═════════════════════════════════════════════════════════════════════════════
# MAIN LOOP BODY
# ═════════════════════════════════════════════════════════════════════════════

def run_combined():
    global _combined_trades_today, _combined_date
    global _b1_last_trade_time, _b2_last_trade_time

    now   = datetime.now(timezone.utc)
    today = now.date()

    _send_daily_summary_if_needed()

    # Reset combined counter at day boundary
    if _combined_date != today:
        _combined_trades_today = 0
        _combined_date         = today
        log_sys.info(f"Day reset | {today} | Combined counter: 0")

    # Circuit breaker (shared — covers both bots)
    if circuit_breaker.is_tripped():
        log_sys.warning(f"Circuit breaker: {circuit_breaker.status()}")
        return

    # Time-based exits and SL/TP management (trailing, BE, partial close).
    # manage_open_trades() also syncs partial-close tracking but does NOT fire
    # circuit_breaker or WhatsApp — that is handled exclusively below.
    check_time_exits(settings.SYMBOL)
    manage_open_trades()

    # SINGLE SOURCE OF TRUTH for closed-trade recording:
    # circuit_breaker.record_trade(), send_trade_closed(), and trades.json
    # are all written here. manage_open_trades() / trade_manager.py does NOT
    # call _on_trade_closed() — see _check_for_closed_trades() comment there.
    _check_combined_closed_trades()

    # Hard stop if combined limit hit
    if _combined_trades_today >= MAX_COMBINED_TRADES:
        log_sys.debug(f"Combined daily limit ({MAX_COMBINED_TRADES}) reached")
        return

    # ── Bot 1 ─────────────────────────────────────────────────────────────────
    sig1 = _get_bot1_signal()
    if sig1:
        ok = _execute_trade(
            direction    = sig1["direction"],
            atr          = sig1["atr"],
            sl_atr_mult  = sig1["sl_atr_mult"],
            reward_ratio = _BOT1["reward_ratio"],
            risk_pct     = _BOT1["risk_pct"],
            bot_label    = "BOT1",
            logger       = log1,
            comment      = "MIDAS_B1",
        )
        if ok:
            _b1_last_trade_time = now

    # ── Bot 2 ─────────────────────────────────────────────────────────────────
    if _combined_trades_today < MAX_COMBINED_TRADES:
        sig2 = _get_bot2_signal()
        if sig2:
            ok = _execute_trade(
                direction    = sig2["direction"],
                atr          = sig2["atr"],
                sl_atr_mult  = sig2["sl_atr_mult"],
                reward_ratio = _BOT2["reward_ratio"],
                risk_pct     = _BOT2["risk_pct"],
                bot_label    = "BOT2",
                logger       = log2,
                comment      = "MIDAS_B2",
            )
            if ok:
                _b2_last_trade_time = now

    # Heartbeat — visible confirmation the bot is alive each cycle
    acct    = mt5.account_info()
    bal_str = f"${acct.balance:.2f}" if acct else "N/A"
    cb_ok   = "TRIPPED" if circuit_breaker.is_tripped() else "OK"
    log_sys.info(
        f"Running OK | Trades today: {_combined_trades_today}/{MAX_COMBINED_TRADES} | "
        f"Bot1 bias: {_b1_bias} | Circuit: {cb_ok} | Balance: {bal_str}"
    )


# ═════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ═════════════════════════════════════════════════════════════════════════════

def main():
    os.makedirs("jasons", exist_ok=True)
    log_sys.info("=" * 60)
    log_sys.info("   MIDAS COMBINED — Starting")
    log_sys.info("   Bot 1: M5 EMA21 pullback | Bot 2: B->C mean reversion")
    log_sys.info("=" * 60)

    if not connect_mt5():
        log_sys.error("Failed to connect to MT5 — exiting")
        return

    account = mt5.account_info()
    if account:
        log_sys.info(
            f"Account: {account.login} | Server: {account.server} | "
            f"Balance: ${account.balance:.2f} | {'DEMO' if account.trade_mode == 0 else 'LIVE'}"
        )
        send_bot_started(account.balance)
    else:
        log_sys.warning("Could not read account info")

    log_sys.info(f"Scanning every {settings.LOOP_INTERVAL_SECONDS}s | Max {MAX_COMBINED_TRADES} trades/day combined")
    log_sys.info(f"Bot 1 spread limit: 15pts | Bot 2 spread limit: 20pts")
    log_sys.info(f"Circuit breaker: {settings.MAX_CONSECUTIVE_LOSSES} losses OR {settings.MAX_DAILY_LOSS_PCT}% daily loss")

    # Start background snapshot logger (every 15 min → logs/signal_snapshots.log)
    start_snapshot_logger()
    log_sys.info("Signal snapshot logger started (15-min interval → logs/signal_snapshots.log)")

    try:
        while True:
            # Proactive health check — MT5 drops return None silently, never raise exceptions,
            # so we must check every cycle rather than waiting for an exception to trigger reconnect.
            if mt5.terminal_info() is None:
                log_sys.warning(f"MT5 connection lost (error {mt5.last_error()}) — reconnecting...")
                if not _reconnect():
                    log_sys.error("Could not reconnect after 3 attempts — exiting")
                    break

            try:
                run_combined()
            except Exception as e:
                log_sys.error(f"Loop error: {e}", exc_info=True)
                if not mt5.terminal_info():
                    log_sys.warning("MT5 connection lost after exception — reconnecting...")
                    if not _reconnect():
                        log_sys.error("Could not reconnect after 3 attempts — exiting")
                        break
            time.sleep(settings.LOOP_INTERVAL_SECONDS)

    except KeyboardInterrupt:
        log_sys.info("Stopped by user")
    finally:
        account = mt5.account_info()
        balance = account.balance if account else 0.0
        send_bot_stopped("Manual stop", balance)
        disconnect_mt5()
        log_sys.info("MT5 disconnected")


if __name__ == "__main__":
    main()
