"""
backtest/tournament/engine.py - Generic backtest harness for the CC knockout
tournament (10 candidate strategies vs LSC's validated methodology).

Mechanics deliberately copied from backtest/lsc_engine.py, generalized to any
strategy module instead of hardcoding strategy/lsc_m15.py: next-bar-open
entry (no lookahead), intrabar SL/TP hit detection, margin-safe lot capping,
spread cost. One position at a time (no overlapping trades), same as LSC.

Two exit modes, selected by what a strategy's check_entry() returns:
  - Fixed SL/TP: check_entry returns (direction, reason, sl, tp) with tp set.
    Bar-by-bar scan for whichever of SL/TP is hit first.
  - ATR trailing stop: check_entry returns (direction, reason, sl, None).
    Initial stop is that sl; stop then trails by TRAIL_ATR_MULT * ATR off the
    highest high (BUY) / lowest low (SELL) seen since entry, monotonically
    tightening, no fixed take-profit. Needs strategy.TRAIL_ATR_MULT.

A strategy module must expose:
  TIMEFRAME_MT5, TIMEFRAME_LABEL   - e.g. mt5.TIMEFRAME_M15, "M15"
  MAX_HOLD_BARS                    - safety-valve close-out if neither exit fires
  SESSION_HOURS                    - set of allowed UTC hours, or None for no filter
  precompute(df) -> df             - adds strategy-specific indicator columns
  check_entry(df, i, last_trade_bar, trades_today, cooldown_bars,
              max_trades_per_day) -> (direction, reason, sl, tp_or_None)
  COOLDOWN_BARS, MAX_TRADES_PER_DAY

Account/backtest assumptions mirror config/settings.py + risk/trade_manager.py
exactly: $50,000 balance, 1:10 leverage, 25% margin-safety budget, 18pt spread
(LSC's backtest assumption). RISK_PERCENT is a tournament-wide default of 1.0%
(NOT LSC's 0.045%, which was reverse-engineered specifically for LSC's own
Monte-Carlo tail against the 6% wall) so that all 10 candidates are ranked on
a common, un-gamed footing; the final-round Monte Carlo separately calibrates
risk% against the 6% wall for whichever strategies survive to that stage.
"""

import MetaTrader5 as mt5
import pandas as pd
import numpy as np

POINT                     = 0.01
CONTRACT_SIZE              = 100.0
LEVERAGE                   = 10
MARGIN_SAFETY_BUDGET_PCT   = 0.25
DEFAULT_SPREAD_POINTS      = 18
DEFAULT_RISK_PERCENT       = 1.0
DEFAULT_INITIAL_BALANCE    = 50_000.0
DEFAULT_MAX_LOT            = 50.0   # symbol volume_max ceiling fallback


def compute_atr14(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low, (high - prev_close).abs(), (low - prev_close).abs()
    ], axis=1).max(axis=1)
    return tr.ewm(com=period - 1, adjust=False).mean()


def fetch_data(symbol: str, timeframe_mt5: int, date_from, date_to) -> pd.DataFrame:
    mt5.symbol_select(symbol, True)
    rates = mt5.copy_rates_range(symbol, timeframe_mt5, date_from, date_to)
    if rates is None or len(rates) == 0:
        rates_all = mt5.copy_rates_from_pos(symbol, timeframe_mt5, 0, 99999)
        if rates_all is None or len(rates_all) == 0:
            raise ValueError(f"No data returned for {symbol}: {mt5.last_error()}")
        df_all = pd.DataFrame(rates_all)
        df_all["time"] = pd.to_datetime(df_all["time"], unit="s")
        mask = (df_all["time"] >= pd.Timestamp(date_from)) & (df_all["time"] <= pd.Timestamp(date_to))
        df_all = df_all[mask]
        if len(df_all) == 0:
            raise ValueError(f"No data in requested window for {symbol}")
        df_all.set_index("time", inplace=True)
        return df_all
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    df.set_index("time", inplace=True)
    return df


def _lot_size(balance: float, sl_dist: float, risk_pct: float, price: float,
              max_lot: float) -> float:
    if sl_dist <= 0:
        return 0.0
    risk_amt  = balance * (risk_pct / 100)
    sl_points = sl_dist / POINT
    raw_lot   = risk_amt / (sl_points * CONTRACT_SIZE * POINT)

    margin_budget = balance * MARGIN_SAFETY_BUDGET_PCT
    margin_safe_max_lot = (margin_budget * LEVERAGE) / (CONTRACT_SIZE * price)

    lot = min(raw_lot, margin_safe_max_lot, max_lot)
    lot = max(lot, 0.01)
    return round(round(lot / 0.01) * 0.01, 2)


def _simulate_fixed(df, entry_idx, direction, sl, tp, balance, risk_pct,
                     spread_points, max_lot, max_hold_bars):
    if entry_idx + 1 >= len(df):
        return None
    entry_candle = df.iloc[entry_idx + 1]
    entry_price  = float(entry_candle["open"])
    sl_dist = (entry_price - sl) if direction == "BUY" else (sl - entry_price)
    if sl_dist <= 0:
        return None

    lot_size = _lot_size(balance, sl_dist, risk_pct, entry_price, max_lot)
    mult     = lot_size * CONTRACT_SIZE
    spread_cost = spread_points * POINT * mult

    result, exit_price = None, None
    for j in range(entry_idx + 2, min(entry_idx + max_hold_bars + 2, len(df))):
        c = df.iloc[j]
        high, low = float(c["high"]), float(c["low"])
        if direction == "BUY":
            if low <= sl:
                result, exit_price = "LOSS", sl; break
            if high >= tp:
                result, exit_price = "WIN", tp; break
        else:
            if high >= sl:
                result, exit_price = "LOSS", sl; break
            if low <= tp:
                result, exit_price = "WIN", tp; break

    if result is None:
        last = df.iloc[min(entry_idx + max_hold_bars + 1, len(df) - 1)]
        exit_price = float(last["close"])

    raw_move = (exit_price - entry_price) if direction == "BUY" else (entry_price - exit_price)
    pnl    = raw_move * mult - spread_cost
    result = "WIN" if pnl > 0 else "LOSS"
    entry_time = df.index[entry_idx + 1]
    return {
        "date": entry_time.strftime("%Y-%m-%d"), "time": entry_time.strftime("%H:%M"),
        "direction": direction, "entry": round(entry_price, 2), "exit": round(exit_price, 2),
        "sl": round(sl, 2), "tp": round(tp, 2), "lots": lot_size,
        "spread_cost": round(spread_cost, 2), "pnl": round(pnl, 2), "result": result,
        "balance_after": round(balance + pnl, 2),
    }


def _simulate_trailing(df, entry_idx, direction, initial_sl, balance, risk_pct,
                        spread_points, max_lot, max_hold_bars, trail_atr_mult):
    if entry_idx + 1 >= len(df):
        return None
    entry_candle = df.iloc[entry_idx + 1]
    entry_price  = float(entry_candle["open"])
    sl_dist = (entry_price - initial_sl) if direction == "BUY" else (initial_sl - entry_price)
    if sl_dist <= 0:
        return None

    lot_size = _lot_size(balance, sl_dist, risk_pct, entry_price, max_lot)
    mult     = lot_size * CONTRACT_SIZE
    spread_cost = spread_points * POINT * mult

    stop = initial_sl
    extreme = entry_price   # highest high (BUY) / lowest low (SELL) since entry
    result, exit_price, j = None, None, entry_idx + 1

    for j in range(entry_idx + 2, min(entry_idx + max_hold_bars + 2, len(df))):
        c = df.iloc[j]
        high, low, atr = float(c["high"]), float(c["low"]), float(c["atr"])
        if direction == "BUY":
            if low <= stop:
                result, exit_price = ("LOSS" if stop < entry_price else "WIN"), stop; break
            extreme = max(extreme, high)
            if not np.isnan(atr):
                stop = max(stop, extreme - trail_atr_mult * atr)
        else:
            if high >= stop:
                result, exit_price = ("LOSS" if stop > entry_price else "WIN"), stop; break
            extreme = min(extreme, low)
            if not np.isnan(atr):
                stop = min(stop, extreme + trail_atr_mult * atr)

    if result is None:
        last = df.iloc[min(entry_idx + max_hold_bars + 1, len(df) - 1)]
        exit_price = float(last["close"])

    raw_move = (exit_price - entry_price) if direction == "BUY" else (entry_price - exit_price)
    pnl    = raw_move * mult - spread_cost
    result = "WIN" if pnl > 0 else "LOSS"
    entry_time = df.index[entry_idx + 1]
    return {
        "date": entry_time.strftime("%Y-%m-%d"), "time": entry_time.strftime("%H:%M"),
        "direction": direction, "entry": round(entry_price, 2), "exit": round(exit_price, 2),
        "sl": round(stop, 2), "tp": None, "lots": lot_size,
        "spread_cost": round(spread_cost, 2), "pnl": round(pnl, 2), "result": result,
        "balance_after": round(balance + pnl, 2), "exit_idx": j,
    }


def run_backtest(symbol: str, strategy, date_from, date_to,
                  risk_pct: float = DEFAULT_RISK_PERCENT,
                  spread_points: int = DEFAULT_SPREAD_POINTS,
                  initial_balance: float = DEFAULT_INITIAL_BALANCE,
                  max_lot: float = DEFAULT_MAX_LOT,
                  min_lookback: int = 100) -> list:
    df = fetch_data(symbol, strategy.TIMEFRAME_MT5, date_from, date_to)
    if len(df) < min_lookback + 10:
        return []
    df["atr"] = compute_atr14(df)
    df = strategy.precompute(df)

    trades = []
    balance = initial_balance
    current_date = None
    trades_today = 0
    last_trade_bar = -strategy.COOLDOWN_BARS

    # Mirrors backtest/lsc_engine.py exactly: increments one bar at a time
    # regardless of a just-opened trade's simulated duration (cooldown_bars is
    # the only throttle on re-entry) - this is what LSC was actually validated
    # with, carried forward unchanged rather than "improved" to be stricter.
    for i in range(min_lookback, len(df) - 1):
        bar_time = df.index[i]
        bar_date = bar_time.date()
        if bar_date != current_date:
            current_date = bar_date
            trades_today = 0

        if strategy.SESSION_HOURS is not None and bar_time.hour not in strategy.SESSION_HOURS:
            continue

        direction, reason, sl, tp = strategy.check_entry(
            df, i, last_trade_bar, trades_today, strategy.COOLDOWN_BARS, strategy.MAX_TRADES_PER_DAY
        )
        if direction == "NEUTRAL":
            continue

        if tp is not None:
            trade = _simulate_fixed(df, i, direction, sl, tp, balance, risk_pct,
                                     spread_points, max_lot, strategy.MAX_HOLD_BARS)
        else:
            trade = _simulate_trailing(df, i, direction, sl, balance, risk_pct,
                                        spread_points, max_lot, strategy.MAX_HOLD_BARS,
                                        strategy.TRAIL_ATR_MULT)
        if trade is None:
            continue

        balance += trade["pnl"]
        trade["balance_after"] = round(balance, 2)
        trade.pop("exit_idx", None)
        trades.append(trade)
        last_trade_bar = i
        trades_today += 1

    return trades
