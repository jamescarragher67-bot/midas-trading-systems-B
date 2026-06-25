"""
run_backtest.py

Runs the Midas backtesting engine on historical XAUUSD data.
Generates a full HTML report with charts and statistics.

Usage:
    python run_backtest.py

Output:
    backtest_report.html  — open this in Chrome
"""

import os
import sys
import MetaTrader5 as mt5
from datetime import datetime

# ── BACKTEST CONFIGURATION ────────────────────────────────────────────────────

CONFIG = {
    "symbol":               "XAUUSD",
    "days":                 86,
    "initial_balance":      500,
    "risk_pct":             1.5,
    "vote_threshold":       4,
    "cooldown_bars":        48,
    "session_filter":       True,
    "reward_ratio":         2.0,
    "max_trade_hours":      24,
    "max_trade_hours_hard": 24,
    "trailing_enabled":     True,
    "trailing_step_pips":   25,
    "perf_lookback":        20,
    "perf_min_trades":      10,
    "perf_min_wr":          30.0,
    "perf_reduced_risk":    0.5,
    "max_lot_size":         0.01,
}

# ── RUN ───────────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("   Midas Backtesting Engine")
    print("=" * 60)
    print(f"Symbol:    {CONFIG['symbol']}")
    print(f"Period:    {CONFIG['days']} days")
    print(f"Balance:   ${CONFIG['initial_balance']}")
    print(f"Risk:      {CONFIG['risk_pct']}% per trade")
    print(f"Votes:     {CONFIG['vote_threshold']}/5 required")
    print("=" * 60)

    # Connect to MT5
    if not mt5.initialize():
        print(f"ERROR: Could not connect to MT5. Error: {mt5.last_error()}")
        print("Make sure MT5 is open and logged in.")
        sys.exit(1)

    print("MT5 connected.")

    try:
        from backtest.engine  import run_simulation
        from backtest.metrics import calculate_metrics, monthly_breakdown, strategy_contribution, hourly_breakdown
        from backtest.report  import generate_html_report

        # Run simulation
        trades = run_simulation(
            symbol=CONFIG["symbol"],
            days=CONFIG["days"],
            config=CONFIG,
            progress_callback=lambda pct: print(f"  Progress: {pct:.0f}%") if int(pct) % 20 == 0 else None
        )

        if not trades:
            print("No trades generated. Try reducing vote_threshold or increasing days.")
            return

        print(f"\nCalculating metrics for {len(trades)} trades...")

        # Calculate metrics
        metrics  = calculate_metrics(trades, CONFIG["initial_balance"])
        monthly  = monthly_breakdown(trades)
        strategy = strategy_contribution(trades)
        hourly   = hourly_breakdown(trades)

        # Print summary
        print("\n" + "=" * 60)
        print("   BACKTEST RESULTS")
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

        print("\nStrategy contribution:")
        for s in strategy:
            bar = "█" * int(s["win_rate"] / 5)
            print(f"  {s['strategy']:<18} {s['win_rate']:5.1f}% WR | {s['votes']} votes | {bar}")

        # Generate HTML report
        output = "backtest_report.html"
        generate_html_report(metrics, monthly, strategy, hourly, trades, CONFIG, output)

        # Write best hours for live session filter
        from backtest.engine import write_best_hours
        write_best_hours(trades, "best_hours.json")

        print(f"\n✅ Report generated: {os.path.abspath(output)}")
        print("Open backtest_report.html in Chrome to view.")

    finally:
        mt5.shutdown()


if __name__ == "__main__":
    main()
