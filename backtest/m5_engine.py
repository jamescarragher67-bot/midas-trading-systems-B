"""
backtest/m5_engine.py - Two-layer M5 backtest simulation

Layer 1: Daily bias (3/3 voters at first bar of each UTC day)
Layer 2: M5 entry (EMA21 pullback + momentum + volatility)

Reuses trade simulation, SL, lot sizing, and data fetching from backtest/engine.py.
"""

import logging
import pandas as pd
from strategy.indicators import add_indicators
from strategy.m5_execution_engine import get_daily_bias, check_m5_entry
from backtest.engine import (
    fetch_historical_data,
    _smart_sl,
    _lot_size,
    _simulate_trade,
    INDICATOR_CONFIG,
    MIN_LOOKBACK,
)

SESSION_HOURS = set(range(0, 15)) | {20, 21, 22, 23}


def run_m5_simulation(symbol: str, days: int, config: dict,
                      progress_callback=None) -> list:

    for name in ["ema_stack", "atr_expansion", "prev_day_structure",
                 "session_bias", "m5_execution_engine"]:
        logging.getLogger(name).setLevel(logging.WARNING)

    df = fetch_historical_data(symbol, days)
    df = add_indicators(df, INDICATOR_CONFIG)

    trades         = []
    balance        = config["initial_balance"]
    daily_bias     = "NONE"
    bias_counts    = {"BUY": 0, "SELL": 0, "NONE": 0}
    current_date   = None
    trades_today   = 0
    last_trade_bar = -config.get("cooldown_bars", 3)
    total_bars     = len(df) - MIN_LOOKBACK - 1

    print(f"Simulating {total_bars:,} bars | Two-layer M5 | Risk: {config['risk_pct']}% | "
          f"Max {config.get('max_trades_per_day', 4)} trades/day")

    for i in range(MIN_LOOKBACK, len(df) - 1):
        if progress_callback and i % 1000 == 0:
            pct = (i - MIN_LOOKBACK) / total_bars * 100
            progress_callback(pct)

        bar_time = df.index[i]
        bar_date = bar_time.date()
        bar_hour = bar_time.hour

        # Reset state on new calendar day
        if bar_date != current_date:
            current_date = bar_date
            trades_today = 0
            daily_bias   = "NONE"   # will lock in on first unanimous bar today

        # Layer 1: scan each bar until the first 3/3 unanimous vote of the day,
        # then lock that bias for the rest of the day.
        if daily_bias == "NONE":
            df_slice   = df.iloc[:i + 1]
            candidate  = get_daily_bias(df_slice)
            if candidate != "NONE":
                daily_bias = candidate
                bias_counts[candidate] = bias_counts.get(candidate, 0) + 1

        if daily_bias == "NONE":
            continue

        # Session filter
        if config.get("session_filter", True) and bar_hour not in SESSION_HOURS:
            continue

        # Max trades per day
        if trades_today >= config.get("max_trades_per_day", 4):
            continue

        # Layer 2: M5 entry check
        df_slice  = df.iloc[:i + 1]
        direction, reason = check_m5_entry(
            df_slice, daily_bias, last_trade_bar, i, trades_today
        )

        if direction == "NEUTRAL":
            continue

        # Performance monitor
        effective_risk = config["risk_pct"]
        if len(trades) >= config.get("perf_min_trades", 10):
            recent    = trades[-config.get("perf_lookback", 20):]
            recent_wr = sum(1 for t in recent if t["result"] == "WIN") / len(recent) * 100
            if recent_wr < config.get("perf_min_wr", 30.0):
                effective_risk = config.get("perf_reduced_risk", 0.5)

        atr          = float(df.iloc[i]["atr"])
        trade_config = {**config, "risk_pct": effective_risk}

        trade = _simulate_trade(df, df_slice, i, direction, atr, balance, trade_config)
        if trade is None:
            continue

        trade["daily_bias"]    = daily_bias
        trade["entry_reason"]  = reason
        trade["strategy_votes"] = {"M5 Engine": 1 if direction == "BUY" else -1}
        balance               += trade["pnl"]
        trade["balance_after"] = round(balance, 2)
        trades.append(trade)
        last_trade_bar = i
        trades_today  += 1

        if len(trades) % 50 == 0:
            wr = sum(1 for t in trades if t["result"] == "WIN") / len(trades) * 100
            print(f"  {len(trades)} trades | WR: {wr:.1f}% | Balance: ${balance:,.0f}")

    print(f"\nDone: {len(trades)} trades | Final balance: ${balance:,.0f}")
    print(f"Daily bias: BUY days={bias_counts.get('BUY',0)} | "
          f"SELL days={bias_counts.get('SELL',0)} | "
          f"NONE days={bias_counts.get('NONE',0)}")
    return trades
