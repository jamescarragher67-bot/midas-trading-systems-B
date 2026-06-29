"""
run_backtest_m5.py - MIDAS Two-Layer M5 Backtest

Layer 1: Daily bias — 3/3 voters (EMA Stack, ATR Expansion, Prev Day Structure)
         computed at 00:00 UTC. NONE = no trades that day.
Layer 2: M5 entry — EMA21 pullback + momentum candle + volatility confirmation.

Usage:
    python run_backtest_m5.py

Output:
    backtest_report_m5.html
"""

import os
import sys
import MetaTrader5 as mt5

CONFIG = {
    "symbol":               "XAUUSD",
    "days":                 100,
    "initial_balance":      500,
    "risk_pct":             1.5,
    "cooldown_bars":        3,       # 15 min = 3 x M5 bars
    "session_filter":       True,
    "reward_ratio":         2.0,
    "max_trade_hours":      8,
    "max_trade_hours_hard": 24,
    "max_trades_per_day":   4,
    "trailing_enabled":     True,
    "trailing_atr_mult":    0.5,
    "perf_lookback":        20,
    "perf_min_trades":      10,
    "perf_min_wr":          30.0,
    "perf_reduced_risk":    0.5,
    "max_lot_size":         0.5,
}


def main():
    print("=" * 60)
    print("   MIDAS -- Two-Layer M5 Backtest")
    print("=" * 60)
    print(f"Symbol:    {CONFIG['symbol']}")
    print(f"Period:    {CONFIG['days']} days")
    print(f"Balance:   ${CONFIG['initial_balance']}")
    print(f"Risk:      {CONFIG['risk_pct']}% per trade")
    print(f"Layer 1:   Daily bias (3/3 voters at 00:00 UTC)")
    print(f"Layer 2:   EMA21 pullback + momentum + volatility")
    print(f"Max/day:   {CONFIG['max_trades_per_day']} trades")
    print(f"Trailing:  ATR x{CONFIG['trailing_atr_mult']}")
    print("=" * 60)

    if not mt5.initialize():
        print(f"ERROR: MT5 not connected. Error: {mt5.last_error()}")
        sys.exit(1)
    print("MT5 connected.")

    try:
        from backtest.m5_engine  import run_m5_simulation
        from backtest.metrics    import calculate_metrics, monthly_breakdown, strategy_contribution, hourly_breakdown
        from backtest.report     import generate_html_report
        from backtest.engine     import write_best_hours

        trades = run_m5_simulation(
            symbol=CONFIG["symbol"],
            days=CONFIG["days"],
            config=CONFIG,
        )

        if not trades:
            print("\nNo trades generated. Daily bias may be NONE for most days.")
            return

        print(f"\nCalculating metrics for {len(trades)} trades...")

        metrics  = calculate_metrics(trades, CONFIG["initial_balance"])
        monthly  = monthly_breakdown(trades)
        strategy = strategy_contribution(trades)
        hourly   = hourly_breakdown(trades)

        print("\n" + "=" * 60)
        print("   BACKTEST RESULTS -- TWO-LAYER M5 ENGINE")
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

        buy_trades  = sum(1 for t in trades if t.get("daily_bias") == "BUY")
        sell_trades = sum(1 for t in trades if t.get("daily_bias") == "SELL")
        from collections import defaultdict
        trades_by_day = defaultdict(int)
        for t in trades:
            trades_by_day[t["date"]] += 1
        active_days     = len(trades_by_day)
        avg_per_day     = len(trades) / active_days if active_days else 0
        buy_days_count  = len({t["date"] for t in trades if t.get("daily_bias") == "BUY"})
        sell_days_count = len({t["date"] for t in trades if t.get("daily_bias") == "SELL"})
        avg_buy_day  = buy_trades  / buy_days_count  if buy_days_count  else 0
        avg_sell_day = sell_trades / sell_days_count if sell_days_count else 0
        print(f"\nBias breakdown:")
        print(f"  BUY days:   {buy_trades} trades across {buy_days_count} days  ({avg_buy_day:.1f} trades/day)")
        print(f"  SELL days:  {sell_trades} trades across {sell_days_count} days  ({avg_sell_day:.1f} trades/day)")
        print(f"  Overall:    {avg_per_day:.1f} trades/active day ({active_days} active days / {CONFIG['days']} total)")

        # Ship/no-ship gate
        print("\n--- LAUNCH GATE ---")
        gate = {
            "Trades >= 360":    metrics["total_trades"] >= 360,
            "PF > 1.5":         metrics["profit_factor"] > 1.5,
            "DD < 15%":         metrics["max_drawdown_pct"] < 15.0,
        }
        for check, passed in gate.items():
            print(f"  {'PASS' if passed else 'FAIL'}  {check}")

        output = "backtest_report_m5.html"
        generate_html_report(metrics, monthly, strategy, hourly, trades, CONFIG, output)
        write_best_hours(trades, "best_hours.json")

        print(f"\nReport: {os.path.abspath(output)}")
        print("Open backtest_report_m5.html in Chrome.")

    finally:
        mt5.shutdown()


if __name__ == "__main__":
    main()
