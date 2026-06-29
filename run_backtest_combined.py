"""
run_backtest_combined.py - MIDAS Combined Architecture Backtest

Layer 1: Daily bias (5-voter engine, 3/3 unanimous, locks first qualifying bar)
Layer 2: B->C mean reversion, fires only in daily bias direction

Usage:
    python run_backtest_combined.py

Output:
    backtest_report_combined.html
"""

import os
import sys
import MetaTrader5 as mt5

CONFIG = {
    "symbol":               "XAUUSD",
    "days":                 100,
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

    # B->C trade params (original validated values)
    "bc_sl_atr_mult":       1.0,
    "bc_trail_atr_mult":    0.5,

    # A->B unused (disabled), kept for signal_engine compat
    "ab_sl_atr_mult":       2.0,
    "ab_trail_atr_mult":    1.0,

    # Performance monitor
    "perf_lookback":        20,
    "perf_min_trades":      10,
    "perf_min_wr":          30.0,
    "perf_reduced_risk":    0.5,
}


def main():
    print("=" * 65)
    print("   MIDAS -- Combined Architecture Backtest")
    print("   Layer 1: Daily Bias (3/3 unanimous 5-voter engine)")
    print("   Layer 2: B->C Mean Reversion (ATR>1.5x, VoV>1.3x, wick>0.6)")
    print("=" * 65)
    print(f"Symbol:    {CONFIG['symbol']}")
    print(f"Period:    {CONFIG['days']} days")
    print(f"Balance:   ${CONFIG['initial_balance']}")
    print(f"Risk:      {CONFIG['risk_pct']}% per trade")
    print(f"B->C SL:   ATR x{CONFIG['bc_sl_atr_mult']} | Trail: ATR x{CONFIG['bc_trail_atr_mult']}")
    print(f"Max/day:   {CONFIG['max_trades_per_day']} trades")
    print("=" * 65)

    if not mt5.initialize():
        print(f"ERROR: MT5 not connected. Error: {mt5.last_error()}")
        sys.exit(1)
    print("MT5 connected.\n")

    try:
        from backtest.combined_engine import run_combined_simulation
        from backtest.metrics          import calculate_metrics, monthly_breakdown, strategy_contribution, hourly_breakdown
        from backtest.report           import generate_html_report

        def progress(pct):
            filled = int(pct / 5)
            bar    = "|" * filled + "-" * (20 - filled)
            print(f"\r  [{bar}] {pct:.0f}%", end="", flush=True)

        trades, regime_counts, day_stats = run_combined_simulation(
            symbol=CONFIG["symbol"],
            days=CONFIG["days"],
            config=CONFIG,
            progress_callback=progress,
        )
        print()

        if not trades:
            print("\nNo trades generated.")
            print("Possible causes:")
            print("  - Daily bias rarely hits 3/3 unanimous AND B->C fires in that direction")
            print("  - Try extending days or checking regime_counts for C bar frequency")
            return

        metrics = calculate_metrics(trades, CONFIG["initial_balance"])
        monthly = monthly_breakdown(trades)
        strat   = strategy_contribution(trades)
        hourly  = hourly_breakdown(trades)

        # ── Results table ──────────────────────────────────────────────────────
        print("\n" + "=" * 65)
        print("   BACKTEST RESULTS -- MIDAS COMBINED ARCHITECTURE")
        print("=" * 65)
        print(f"Total trades:       {metrics['total_trades']}")
        print(f"Trades/day:         {metrics['total_trades'] / CONFIG['days']:.2f}")
        print(f"Win rate:           {metrics['win_rate']}%")
        print(f"Profit factor:      {metrics['profit_factor']}")
        print(f"Max drawdown:       {metrics['max_drawdown_pct']}%")
        print(f"Sharpe ratio:       {metrics['sharpe_ratio']}")
        print(f"Expectancy:         ${metrics['expectancy']:+.2f} per trade")
        print(f"Net P&L:            ${metrics['net_pnl']:+.2f}")
        print(f"Total return:       {metrics['total_return']}%")
        print(f"Final balance:      ${metrics['final_balance']}")
        print(f"Avg win:            ${metrics['avg_win']}")
        print(f"Avg loss:           ${metrics['avg_loss']}")
        print("=" * 65)

        # ── Day stats ──────────────────────────────────────────────────────────
        print(f"\nDay-level breakdown:")
        print(f"  Total days:           {day_stats['total_days']}")
        print(f"  Active bias days:     {day_stats['active_bias_days']}  (3/3 unanimous locked)")
        print(f"    BUY bias days:      {day_stats['buy_bias_days']}")
        print(f"    SELL bias days:     {day_stats['sell_bias_days']}")
        print(f"  Trading days:         {day_stats['trading_days']}  (at least 1 B->C trade)")
        bias_pct  = day_stats['active_bias_days'] / max(day_stats['total_days'], 1) * 100
        trade_pct = day_stats['trading_days'] / max(day_stats['active_bias_days'], 1) * 100
        print(f"  Bias lock rate:       {bias_pct:.0f}% of days")
        print(f"  Trade conversion:     {trade_pct:.0f}% of bias days produced trades")

        # ── Regime distribution ────────────────────────────────────────────────
        total_bars = sum(regime_counts.values())
        print(f"\nRegime distribution ({total_bars:,} bars):")
        for code, label in [("A", "Compression"), ("B", "Expansion"),
                             ("C", "Exhaustion"), ("UNKNOWN", "Unknown")]:
            n   = regime_counts.get(code, 0)
            pct = n / total_bars * 100 if total_bars > 0 else 0
            print(f"  {code} ({label}):  {n:>6,} bars  ({pct:.1f}%)")

        # ── Launch gate ────────────────────────────────────────────────────────
        trades_per_day = metrics['total_trades'] / CONFIG['days']
        gate = {
            "Trades >= 60":         metrics['total_trades'] >= 60,
            "WR >= 55%":            metrics['win_rate'] >= 55.0,
            "PF >= 1.5":            metrics['profit_factor'] >= 1.5,
            "DD < 12%":             metrics['max_drawdown_pct'] < 12.0,
            "Sharpe >= 1.5":        metrics['sharpe_ratio'] >= 1.5,
            "Trades/day >= 1.0":    trades_per_day >= 1.0,
        }
        print("\n--- LAUNCH GATE (July 5) ---")
        for check, passed in gate.items():
            mark = "PASS" if passed else "FAIL"
            print(f"  {mark}  {check}")

        all_pass = all(gate.values())
        print(f"\n  {'ALL SYSTEMS GO -- SHIP IT' if all_pass else 'NOT READY -- review above FAILs'}")

        output = "backtest_report_combined.html"
        generate_html_report(metrics, monthly, strat, hourly, trades, CONFIG, output)
        print(f"\nReport: {os.path.abspath(output)}")
        print("Open backtest_report_combined.html in Chrome.")

    finally:
        mt5.shutdown()


if __name__ == "__main__":
    main()
