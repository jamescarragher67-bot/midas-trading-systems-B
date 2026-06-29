"""
run_backtest_vol_m1.py - EXPERIMENT 3: Bot 2 with M1 execution confirmation

After all M5 B->C conditions pass, drop to M1 to confirm:
  - Require 1 M1 candle to close in the signal direction
  - Body >= 0.5 x M1 ATR14 (14-bar rolling average of M1 HL ranges)
  - Look at up to 10 M1 bars after the M5 signal bar
  - If no confirming M1 candle found within that window: skip trade
  - If M1 data not available for the window: skip trade (conservative)

All other logic identical to run_backtest_vol.py.
TEMPORARY: Do not ship without review.
"""

import os
import sys
import pandas as pd
import numpy as np
from datetime import timezone

import MetaTrader5 as mt5

CONFIG = {
    "symbol":               "XAUUSD",
    "days":                 180,
    "initial_balance":      500,
    "risk_pct":             1.5,
    "reward_ratio":         2.0,
    "cooldown_bars":        3,
    "session_filter":       True,
    "max_trades_per_day":   6,
    "max_spread_points":    20,
    "max_trade_hours":      8,
    "max_trade_hours_hard": 24,
    "max_lot_size":         0.5,
    "bc_sl_atr_mult":       1.0,
    "bc_trail_atr_mult":    0.5,
    "ab_sl_atr_mult":       2.0,
    "ab_trail_atr_mult":    1.0,
    "perf_lookback":        20,
    "perf_min_trades":      10,
    "perf_min_wr":          30.0,
    "perf_reduced_risk":    0.5,
    # M1 confirmation settings
    "m1_confirmation":      True,
    "m1_body_atr_mult":     0.5,   # body >= 0.5 x M1 ATR14
    "m1_window_bars":       10,    # look at up to 10 M1 bars after M5 signal
    "m1_atr_period":        14,
}

M1_ATR_PERIOD = 14


def _fetch_m1_data(symbol: str, max_bars: int = 75000) -> dict:
    """
    Fetch M1 bars and return a dict keyed by rounded timestamp.
    Also computes ATR14 on the M1 series.
    Returns {} if fetch fails.
    """
    mt5.symbol_select(symbol, True)
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M1, 0, max_bars)
    if rates is None or len(rates) == 0:
        print(f"  WARNING: Could not fetch M1 data. Error: {mt5.last_error()}")
        return {}

    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    df.set_index("time", inplace=True)

    # Compute ATR14 as rolling mean of HL range (no overnight gap issue on M1)
    df["hl"]  = df["high"] - df["low"]
    df["atr"] = df["hl"].rolling(M1_ATR_PERIOD).mean()
    df["atr"] = df["atr"].fillna(df["hl"])   # fallback for early bars

    print(f"  M1 bars: {len(df):,} ({df.index[0].date()} to {df.index[-1].date()})")
    # Build lookup dict: floor-minute timestamp -> row dict
    m1_lookup = {}
    for ts, row in df.iterrows():
        key = ts.floor("min")
        m1_lookup[key] = row
    return m1_lookup


def _check_m1_confirmation(m1_lookup: dict, m5_bar_time: pd.Timestamp,
                            direction: str, body_atr_mult: float,
                            window: int) -> bool:
    """
    Scan up to `window` M1 bars starting at m5_bar_time.
    Return True if any M1 bar confirms direction with body >= body_atr_mult * M1 ATR.
    """
    start = m5_bar_time.floor("min")
    for offset in range(window):
        ts  = start + pd.Timedelta(minutes=offset)
        row = m1_lookup.get(ts)
        if row is None:
            continue
        close = float(row["close"])
        open_ = float(row["open"])
        atr   = float(row["atr"])
        body  = abs(close - open_)
        if atr <= 0:
            continue
        if body < body_atr_mult * atr:
            continue
        # Check direction
        if direction == "BUY"  and close > open_:
            return True
        if direction == "SELL" and close < open_:
            return True
    return False


def main():
    print("=" * 65)
    print("   MIDAS-B -- Volatility Engine + M1 Execution Confirmation")
    print("   EXPERIMENT 3 — Temporary, not for live deployment yet")
    print("=" * 65)
    print(f"Symbol:   {CONFIG['symbol']}")
    print(f"Period:   {CONFIG['days']} days (M5) | M1 window: {CONFIG['m1_window_bars']} bars")
    print(f"M1 body:  >= {CONFIG['m1_body_atr_mult']}x M1 ATR14")
    print("=" * 65)

    if not mt5.initialize():
        print(f"ERROR: MT5 not connected. {mt5.last_error()}")
        sys.exit(1)
    print("MT5 connected.")

    try:
        from backtest.volatility_engine    import fetch_historical_data, _simulate_vol_trade
        from strategy.volatility_metrics   import precompute_volatility_series, fingerprint_at
        from strategy.regime_classifier    import classify_regime
        from strategy.transition_detector  import detect_transition
        from strategy.volatility_signal_engine import get_signal, passes_filters
        from utils.news_filter             import is_news_blackout
        from backtest.metrics              import calculate_metrics, monthly_breakdown, strategy_contribution, hourly_breakdown

        import logging
        for name in ["volatility_metrics", "regime_classifier",
                     "transition_detector", "volatility_signal_engine"]:
            logging.getLogger(name).setLevel(logging.WARNING)

        # Fetch M5
        df_m5 = fetch_historical_data(CONFIG["symbol"], CONFIG["days"])

        # Fetch M1 (max 75k bars = ~52 days of coverage)
        print("Fetching M1 data...")
        m1_lookup = _fetch_m1_data(CONFIG["symbol"], max_bars=75000)
        m1_coverage = len(m1_lookup)
        m1_skipped  = 0
        m1_filtered = 0
        m1_passed   = 0

        print("Precomputing volatility series...")
        series = precompute_volatility_series(df_m5)
        print("Done. Starting simulation...")

        MIN_LOOKBACK = 100
        trades         = []
        balance        = CONFIG["initial_balance"]
        regime_history = []
        regime_counts  = {"A": 0, "B": 0, "C": 0, "UNKNOWN": 0}
        last_trade_bar = -CONFIG.get("cooldown_bars", 3)
        trades_today   = 0
        current_date   = None
        total_bars     = len(df_m5) - MIN_LOOKBACK - 1

        print(f"Simulating {total_bars:,} bars | M1 confirmation: {m1_coverage:,} lookup entries")

        for i in range(MIN_LOOKBACK, len(df_m5) - 1):
            bar_time = df_m5.index[i]
            bar_date = bar_time.date()

            if bar_date != current_date:
                current_date = bar_date
                trades_today = 0

            fp = fingerprint_at(series, i)
            if fp["atr_ma50"] == 0.0 or fp["stddev_ma50"] == 0.0:
                regime_history.append("UNKNOWN")
                regime_counts["UNKNOWN"] += 1
                continue

            regime = classify_regime(fp)
            regime_counts[regime] = regime_counts.get(regime, 0) + 1

            if len(regime_history) < 4:
                regime_history.append(regime)
                continue

            prev_regime = regime_history[-1]
            regime_history.append(regime)
            if len(regime_history) > 30:
                regime_history.pop(0)

            if regime == prev_regime:
                continue

            current_bar_s = df_m5.iloc[i]
            transition    = detect_transition(regime_history, fp, current_bar_s)
            if transition["transition"] in ("NONE", "C_to_A"):
                continue

            ok, reason = passes_filters(bar_time, CONFIG, trades_today, last_trade_bar, i)
            if not ok:
                continue

            if is_news_blackout(bar_time.to_pydatetime().replace(tzinfo=timezone.utc)):
                continue

            signal = get_signal(transition, fp, CONFIG)
            if signal is None:
                continue

            # ── M1 CONFIRMATION ──────────────────────────────────────────────────
            # Check M1 bars in the M5 entry bar's window (i+1 bar)
            entry_m5_time = df_m5.index[min(i + 1, len(df_m5) - 1)]

            if m1_coverage == 0:
                # No M1 data at all — skip trade (conservative)
                m1_skipped += 1
                continue

            confirmed = _check_m1_confirmation(
                m1_lookup,
                entry_m5_time,
                signal["direction"],
                CONFIG["m1_body_atr_mult"],
                CONFIG["m1_window_bars"],
            )

            if not confirmed:
                # Check if M1 data existed for this window at all
                window_has_data = any(
                    m1_lookup.get(entry_m5_time.floor("min") + pd.Timedelta(minutes=k))
                    is not None
                    for k in range(CONFIG["m1_window_bars"])
                )
                if not window_has_data:
                    m1_skipped += 1   # No M1 data for this period — skip
                else:
                    m1_filtered += 1  # M1 data existed but no confirmation
                continue
            m1_passed += 1

            # Performance monitor
            effective_risk = CONFIG["risk_pct"]
            if len(trades) >= CONFIG.get("perf_min_trades", 10):
                recent    = trades[-CONFIG.get("perf_lookback", 20):]
                recent_wr = sum(1 for t in recent if t["result"] == "WIN") / len(recent) * 100
                if recent_wr < CONFIG.get("perf_min_wr", 30.0):
                    effective_risk = CONFIG.get("perf_reduced_risk", 0.5)

            trade_signal = {**signal, "reward_ratio": CONFIG["reward_ratio"]}
            trade_config = {**CONFIG, "risk_pct": effective_risk}

            trade = _simulate_vol_trade(df_m5, i, signal["direction"], trade_signal, balance, trade_config)
            if trade is None:
                continue

            trade["regime"]     = regime
            trade["transition"] = transition["transition"]
            trade["confidence"] = transition["confidence"]
            trade["m1_confirmed"] = True
            balance            += trade["pnl"]
            trade["balance_after"] = round(balance, 2)
            trades.append(trade)
            last_trade_bar = i
            trades_today  += 1

        print(f"\nDone: {len(trades)} trades | Final balance: ${balance:,.0f}")
        print(f"M1 filter stats:")
        print(f"  Signals passed M1 confirmation:  {m1_passed}")
        print(f"  Signals filtered by M1:          {m1_filtered}")
        print(f"  Signals skipped (no M1 data):    {m1_skipped}")

        if not trades:
            print("\nNo trades generated. M1 confirmation may be too strict.")
            print("Consider: lowering m1_body_atr_mult or extending m1_window_bars.")
            return

        metrics = calculate_metrics(trades, CONFIG["initial_balance"])
        monthly = monthly_breakdown(trades)
        strat   = strategy_contribution(trades)
        hourly  = hourly_breakdown(trades)

        print("\n" + "=" * 65)
        print("   BACKTEST RESULTS -- BOT 2 + M1 EXECUTION CONFIRMATION")
        print("=" * 65)
        print(f"Total trades:    {metrics['total_trades']}")
        print(f"Win rate:        {metrics['win_rate']}%")
        print(f"Net P&L:         ${metrics['net_pnl']:+.2f}")
        print(f"Total return:    {metrics['total_return']}%")
        print(f"Profit factor:   {metrics['profit_factor']}")
        print(f"Max drawdown:    {metrics['max_drawdown_pct']}%")
        print(f"Sharpe ratio:    {metrics['sharpe_ratio']}")
        print(f"Expectancy:      ${metrics['expectancy']:+.2f} per trade")
        print(f"Avg win:         ${metrics['avg_win']}")
        print(f"Avg loss:        ${metrics['avg_loss']}")
        print(f"Final balance:   ${metrics['final_balance']}")
        print("=" * 65)

        # Compare vs baseline
        print("\nVs. Bot 2 baseline (no M1):")
        print(f"  Baseline:    20 trades | WR 60.0% | B->C only")
        print(f"  M1 filter:   {metrics['total_trades']} trades | WR {metrics['win_rate']}% | PF {metrics['profit_factor']}")

    finally:
        mt5.shutdown()


if __name__ == "__main__":
    main()
