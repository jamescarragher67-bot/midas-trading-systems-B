"""
backtest/combined_engine.py - MIDAS Combined Architecture Backtest

Layer 1: Daily bias (3/3 unanimous — EMA Stack, ATR Expansion, Prev Day Structure)
         Locks on first qualifying bar of each UTC day. NONE = no trades today.

Layer 2: B->C execution (original tight thresholds: ATR > 1.5x, VoV > 1.3x, wick > 0.6)
         Only fires in the daily bias direction.
         SL = ATR x1.0, TP = SL x2.0, Trail = ATR x0.5.

Imports from existing modules — no logic duplicated or modified.
"""

import logging
import pandas as pd
from datetime import timezone

import MetaTrader5 as mt5

from strategy.indicators           import add_indicators
from strategy.m5_execution_engine  import get_daily_bias
from strategy.volatility_metrics   import precompute_volatility_series, fingerprint_at
from strategy.regime_classifier    import classify_regime
from strategy.transition_detector  import detect_transition
from strategy.volatility_signal_engine import get_signal, passes_filters
from utils.news_filter             import is_news_blackout
from backtest.volatility_engine    import fetch_historical_data, _simulate_vol_trade

INDICATOR_CONFIG = {"EMA_FAST": 9, "EMA_SLOW": 21, "EMA_TREND": 50,
                    "RSI_PERIOD": 14, "ATR_PERIOD": 14}
MIN_LOOKBACK = 120   # enough for EMA50 + ATR_MA50 warmup


def run_combined_simulation(symbol: str, days: int, config: dict,
                            progress_callback=None) -> tuple[list, dict, dict]:
    """
    Run combined daily-bias + B->C backtest.
    Returns (trades, regime_counts, day_stats).
    """
    for name in ["m5_execution_engine", "volatility_metrics", "regime_classifier",
                 "transition_detector", "volatility_signal_engine"]:
        logging.getLogger(name).setLevel(logging.WARNING)

    # Fetch and prepare data
    df_raw = fetch_historical_data(symbol, days)
    df     = add_indicators(df_raw, INDICATOR_CONFIG)   # adds ema_fast/slow/trend, rsi, atr

    print("Precomputing volatility series...")
    series = precompute_volatility_series(df)
    print("Done. Starting simulation...")

    trades         = []
    balance        = config["initial_balance"]
    regime_history = []
    regime_counts  = {"A": 0, "B": 0, "C": 0, "UNKNOWN": 0}
    last_trade_bar = -config.get("cooldown_bars", 3)
    trades_today   = 0
    current_date   = None
    daily_bias     = "NONE"
    total_bars     = len(df) - MIN_LOOKBACK - 1

    # Day-level tracking for the report
    bias_days    = {}   # date -> 'BUY'|'SELL'|'NONE'
    trading_days = set()

    print(f"Simulating {total_bars:,} bars | Combined Engine | Risk: {config['risk_pct']}%")

    for i in range(MIN_LOOKBACK, len(df) - 1):
        if progress_callback and i % 2000 == 0:
            pct = (i - MIN_LOOKBACK) / total_bars * 100
            progress_callback(pct)

        bar_time = df.index[i]
        bar_date = bar_time.date()

        # ── New day: reset state, daily bias will lock on first qualifying bar ──
        if bar_date != current_date:
            current_date = bar_date
            trades_today = 0
            daily_bias   = "NONE"

        # ── Layer 1: lock daily bias on first unanimous bar of the day ──────────
        if daily_bias == "NONE":
            df_slice   = df.iloc[:i + 1]
            candidate  = get_daily_bias(df_slice)
            if candidate != "NONE":
                daily_bias          = candidate
                bias_days[bar_date] = daily_bias
        elif bar_date not in bias_days:
            bias_days[bar_date] = daily_bias

        if daily_bias == "NONE":
            continue

        # ── Get volatility fingerprint (O(1) lookup) ─────────────────────────────
        fp = fingerprint_at(series, i)
        if fp["atr_ma50"] == 0.0 or fp["stddev_ma50"] == 0.0:
            regime_history.append("UNKNOWN")
            regime_counts["UNKNOWN"] += 1
            continue

        # ── Classify regime ───────────────────────────────────────────────────────
        regime = classify_regime(fp)
        regime_counts[regime] = regime_counts.get(regime, 0) + 1

        if len(regime_history) < 4:
            regime_history.append(regime)
            continue

        prev_regime = regime_history[-1]
        regime_history.append(regime)
        if len(regime_history) > 30:
            regime_history.pop(0)

        # Only check on regime change
        if regime == prev_regime:
            continue

        # ── Layer 2: detect B->C transition ──────────────────────────────────────
        current_bar_s = df.iloc[i]
        transition    = detect_transition(regime_history, fp, current_bar_s)

        if transition["transition"] != "B_to_C":
            continue

        # Must align with daily bias direction
        if transition["direction"] != daily_bias:
            continue

        # ── Filters ───────────────────────────────────────────────────────────────
        ok, reason = passes_filters(bar_time, config, trades_today, last_trade_bar, i)
        if not ok:
            continue

        if is_news_blackout(bar_time.to_pydatetime().replace(tzinfo=timezone.utc)):
            continue

        # ── Signal ────────────────────────────────────────────────────────────────
        signal = get_signal(transition, fp, config)
        if signal is None:
            continue

        # ── Performance monitor ───────────────────────────────────────────────────
        effective_risk = config["risk_pct"]
        if len(trades) >= config.get("perf_min_trades", 10):
            recent    = trades[-config.get("perf_lookback", 20):]
            recent_wr = sum(1 for t in recent if t["result"] == "WIN") / len(recent) * 100
            if recent_wr < config.get("perf_min_wr", 30.0):
                effective_risk = config.get("perf_reduced_risk", 0.5)

        trade_config = {**config, "risk_pct": effective_risk}

        # ── Simulate ──────────────────────────────────────────────────────────────
        trade = _simulate_vol_trade(df, i, signal["direction"], signal, balance, trade_config)
        if trade is None:
            continue

        trade["daily_bias"]  = daily_bias
        trade["regime"]      = regime
        trade["transition"]  = "B_to_C"
        trade["confidence"]  = transition["confidence"]
        balance             += trade["pnl"]
        trade["balance_after"] = round(balance, 2)
        trades.append(trade)
        last_trade_bar = i
        trades_today  += 1
        trading_days.add(bar_date)

        if len(trades) % 25 == 0:
            wr = sum(1 for t in trades if t["result"] == "WIN") / len(trades) * 100
            print(f"  {len(trades)} trades | WR: {wr:.1f}% | Balance: ${balance:,.0f}")

    print(f"\nDone: {len(trades)} trades | Final balance: ${balance:,.0f}")

    # Day stats
    bias_set    = {d for d, b in bias_days.items() if b != "NONE"}
    day_stats   = {
        "total_days":       days,
        "active_bias_days": len(bias_set),
        "trading_days":     len(trading_days),
        "buy_bias_days":    sum(1 for b in bias_days.values() if b == "BUY"),
        "sell_bias_days":   sum(1 for b in bias_days.values() if b == "SELL"),
    }

    print(f"Regime distribution: A={regime_counts['A']:,} | B={regime_counts['B']:,} | "
          f"C={regime_counts['C']:,} | UNKNOWN={regime_counts['UNKNOWN']:,}")
    print(f"Bias days: {day_stats['active_bias_days']} active "
          f"(BUY={day_stats['buy_bias_days']}, SELL={day_stats['sell_bias_days']}) | "
          f"Trading days: {day_stats['trading_days']}")

    return trades, regime_counts, day_stats
