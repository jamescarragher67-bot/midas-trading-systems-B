"""
run_backtest.py - MIDAS Voting Engine Backtest

Runs the 5-strategy voting engine on historical XAUUSD M5 data.
Outputs voter contribution breakdown for diagnostics.

Usage:
    python run_backtest.py

Output:
    backtest_report.html
"""

import os
import sys
from pathlib import Path

# Resolve project root so backtest/ and config/ imports work from tools/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import MetaTrader5 as mt5

CONFIG = {
    "symbol":               "XAUUSD",
    "days":                 100,
    "initial_balance":      500,
    "risk_pct":             1.5,
    "vote_threshold":       4,      # 4/5 when Session votes, 3/4 when it abstains
    "cooldown_bars":        5,
    "session_filter":       True,
    "reward_ratio":         2.0,
    "max_trade_hours":      8,
    "max_trade_hours_hard": 24,
    "trailing_enabled":     True,
    "trailing_atr_mult":    0.5,    # trail SL at ATR×0.5 behind the high (~$7-10 on Gold)
    "perf_lookback":        20,
    "perf_min_trades":      10,
    "perf_min_wr":          30.0,
    "perf_reduced_risk":    0.5,
    "max_lot_size":         0.5,
}


def _voter_diagnostic(trades: list) -> list:
    """Per-voter: when it agreed with the trade direction, how often did the trade win?"""
    from collections import defaultdict
    voters = defaultdict(lambda: {"agrees": 0, "agree_wins": 0, "overruled": 0})
    for t in trades:
        sv = t.get("strategy_votes", {})
        for voter, vote in sv.items():
            agreed = (vote > 0 and t["direction"] == "BUY") or (vote < 0 and t["direction"] == "SELL")
            if agreed:
                voters[voter]["agrees"] += 1
                if t["result"] == "WIN":
                    voters[voter]["agree_wins"] += 1
            elif vote != 0:
                voters[voter]["overruled"] += 1
    result = []
    for name, d in voters.items():
        agree_wr = round(d["agree_wins"] / d["agrees"] * 100, 1) if d["agrees"] > 0 else 0
        result.append({"voter": name, "agrees": d["agrees"], "agree_wr": agree_wr,
                       "overruled": d["overruled"]})
    return sorted(result, key=lambda x: -x["agree_wr"])


def main():
    print("=" * 60)
    print("   MIDAS — Voting Engine Backtest")
    print("=" * 60)
    print(f"Symbol:    {CONFIG['symbol']}")
    print(f"Period:    {CONFIG['days']} days")
    print(f"Balance:   ${CONFIG['initial_balance']}")
    print(f"Risk:      {CONFIG['risk_pct']}% per trade")
    print(f"Votes:     {CONFIG['vote_threshold']}/5 required (3/4 when Session abstains)")
    trail_info = f"ON (ATR x{CONFIG['trailing_atr_mult']})" if CONFIG['trailing_enabled'] else "OFF"
    print(f"Trailing:  {trail_info}")
    print("=" * 60)

    if not mt5.initialize():
        print(f"ERROR: MT5 not connected. Error: {mt5.last_error()}")
        sys.exit(1)
    print("MT5 connected.")

    try:
        from backtest.engine  import run_simulation
        from backtest.metrics import calculate_metrics, monthly_breakdown, strategy_contribution, hourly_breakdown
        from backtest.report  import generate_html_report

        trades = run_simulation(
            symbol=CONFIG["symbol"],
            days=CONFIG["days"],
            config=CONFIG,
            progress_callback=lambda pct: print(f"  Progress: {pct:.0f}%") if int(pct) % 20 == 0 else None,
        )

        if not trades:
            print("\nNo trades generated. Try reducing vote_threshold or increasing days.")
            return

        print(f"\nCalculating metrics for {len(trades)} trades...")

        metrics  = calculate_metrics(trades, CONFIG["initial_balance"])
        monthly  = monthly_breakdown(trades)
        strategy = strategy_contribution(trades)
        hourly   = hourly_breakdown(trades)

        print("\n" + "=" * 60)
        print("   BACKTEST RESULTS — VOTING ENGINE")
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

        print("\nVoter diagnostic (when this voter agreed with trade direction):")
        print(f"  {'Strategy':<20} {'AgreeWR':>8}  {'Agrees':>7}  {'Overruled':>10}  Grade")
        print("  " + "-" * 60)
        voter_stats = _voter_diagnostic(trades)
        for v in voter_stats:
            grade = "KEEP" if v["agree_wr"] >= 50 else "WEAK" if v["agree_wr"] >= 40 else "DROP"
            bar   = "|" * int(v["agree_wr"] / 5)
            print(f"  {v['voter']:<20} {v['agree_wr']:>7.1f}%  {v['agrees']:>7}  {v['overruled']:>10}  {grade}  {bar}")

        output = "backtest_report.html"
        generate_html_report(metrics, monthly, strategy, hourly, trades, CONFIG, output)

        from backtest.engine import write_best_hours
        write_best_hours(trades, "best_hours.json")

        print(f"\nReport: {os.path.abspath(output)}")
        print("Open backtest_report.html in Chrome.")

    finally:
        mt5.shutdown()


if __name__ == "__main__":
    main()
