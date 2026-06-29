"""
run_backtest_vol.py - MIDAS-B Volatility Engine Backtest

Three-layer architecture:
  1. Volatility fingerprint (ATR, StdDev, HL compression, VoV, body ratio)
  2. Regime classifier (A=compression, B=expansion, C=exhaustion)
  3. Transition detector (A->B breakout, B->C reversion, C->A cooling)

Usage:
    python run_backtest_vol.py

Output:
    backtest_report_vol.html
"""

import os
import sys
import MetaTrader5 as mt5

CONFIG = {
    "symbol":               "XAUUSD",
    "days":                 180,
    "initial_balance":      500,
    "risk_pct":             1.5,
    "reward_ratio":         2.0,
    "cooldown_bars":        3,        # 15 min = 3 x M5 bars
    "session_filter":       True,
    "max_trades_per_day":   6,
    "max_spread_points":    20,
    "max_trade_hours":      8,
    "max_trade_hours_hard": 24,
    "max_lot_size":         0.5,

    # A→B breakout trade params
    "ab_sl_atr_mult":       2.0,      # wide SL — breakouts need room
    "ab_trail_atr_mult":    1.0,      # trail immediately with ATR×1.0

    # B→C mean reversion trade params
    "bc_sl_atr_mult":       1.0,      # tight SL — exhaustion reversal
    "bc_trail_atr_mult":    0.5,      # fast exit

    # Performance monitor
    "perf_lookback":        20,
    "perf_min_trades":      10,
    "perf_min_wr":          30.0,
    "perf_reduced_risk":    0.5,
}


def main():
    print("=" * 60)
    print("   MIDAS-B -- Volatility Engine Backtest")
    print("=" * 60)
    print(f"Symbol:    {CONFIG['symbol']}")
    print(f"Period:    {CONFIG['days']} days")
    print(f"Balance:   ${CONFIG['initial_balance']}")
    print(f"Risk:      {CONFIG['risk_pct']}% per trade")
    print(f"A->B SL:   ATR x{CONFIG['ab_sl_atr_mult']} | Trail: ATR x{CONFIG['ab_trail_atr_mult']}")
    print(f"B->C SL:   ATR x{CONFIG['bc_sl_atr_mult']} | Trail: ATR x{CONFIG['bc_trail_atr_mult']}")
    print(f"Max/day:   {CONFIG['max_trades_per_day']} trades")
    print("=" * 60)

    if not mt5.initialize():
        print(f"ERROR: MT5 not connected. Error: {mt5.last_error()}")
        sys.exit(1)
    print("MT5 connected.")

    try:
        from backtest.volatility_engine import run_volatility_simulation
        from backtest.metrics           import calculate_metrics, monthly_breakdown, strategy_contribution, hourly_breakdown
        from backtest.report            import generate_html_report

        trades, regime_counts = run_volatility_simulation(
            symbol=CONFIG["symbol"],
            days=CONFIG["days"],
            config=CONFIG,
        )

        if not trades:
            print("\nNo trades generated. Check regime thresholds or extend data window.")
            return

        print(f"\nCalculating metrics for {len(trades)} trades...")

        metrics  = calculate_metrics(trades, CONFIG["initial_balance"])
        monthly  = monthly_breakdown(trades)
        strategy = strategy_contribution(trades)
        hourly   = hourly_breakdown(trades)

        print("\n" + "=" * 60)
        print("   BACKTEST RESULTS -- MIDAS-B VOLATILITY ENGINE")
        print("=" * 60)
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
        print("=" * 60)

        # Regime distribution
        total_classified = sum(regime_counts.values())
        print(f"\nRegime distribution ({total_classified:,} bars classified):")
        for regime, label in [("A", "Compression"), ("B", "Expansion"), ("C", "Exhaustion"), ("UNKNOWN", "Unknown")]:
            count = regime_counts.get(regime, 0)
            pct   = count / total_classified * 100 if total_classified > 0 else 0
            print(f"  Regime {regime} ({label}):  {count:>6,} bars  ({pct:.1f}%)")

        # Transition breakdown
        ab_trades = [t for t in trades if t.get("transition") == "A_to_B"]
        bc_trades = [t for t in trades if t.get("transition") == "B_to_C"]
        ab_wins   = sum(1 for t in ab_trades if t["result"] == "WIN")
        bc_wins   = sum(1 for t in bc_trades if t["result"] == "WIN")
        print(f"\nTransition breakdown:")
        print(f"  A->B (breakout):    {len(ab_trades)} trades | WR: {ab_wins/len(ab_trades)*100:.1f}%" if ab_trades else "  A->B (breakout):    0 trades")
        if bc_trades:
            from collections import defaultdict
            bc_by_day = defaultdict(int)
            for t in bc_trades:
                bc_by_day[t["date"]] += 1
            active_days = len(bc_by_day)
            avg_per_day = len(bc_trades) / active_days if active_days else 0
            print(f"  B->C (reversion):   {len(bc_trades)} trades | WR: {bc_wins/len(bc_trades)*100:.1f}% | {avg_per_day:.1f} trades/active day ({active_days} active days)")
        else:
            print("  B->C (reversion):   0 trades")

        # Launch gate
        print("\n--- LAUNCH GATE ---")
        gate = {
            "Trades 200-400": 200 <= metrics["total_trades"] <= 400,
            "WR >= 55%":      metrics["win_rate"] >= 55.0,
            "PF >= 1.5":      metrics["profit_factor"] >= 1.5,
            "DD < 15%":       metrics["max_drawdown_pct"] < 15.0,
            "Sharpe >= 1.5":  metrics["sharpe_ratio"] >= 1.5,
        }
        for check, passed in gate.items():
            print(f"  {'PASS' if passed else 'FAIL'}  {check}")

        output = "backtest_report_vol.html"
        generate_html_report(metrics, monthly, strategy, hourly, trades, CONFIG, output)
        print(f"\nReport: {os.path.abspath(output)}")
        print("Open backtest_report_vol.html in Chrome.")

    finally:
        mt5.shutdown()


if __name__ == "__main__":
    main()
