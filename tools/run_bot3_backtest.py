"""
tools/run_bot3_backtest.py — Bot 3 High-Frequency Backtest

Three sequential 100-day tests. Stops on first test that reaches target.

Target: 100+ trades, WR 48-55%, PF > 1.5, DD < 15%

Test 1: 1.5xATR proximity + 0.4xATR body + 30% close range + ATR floor | NO RSI
Test 2: Test 1 + RSI > 50 BUY / < 50 SELL
Test 3: Test 2 + tighten close range to 25%

Run: python tools/run_bot3_backtest.py
"""

import sys
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import MetaTrader5 as mt5
from backtest.bot3_engine import run_bot3_simulation
from backtest.metrics import calculate_metrics

SYMBOL          = "XAUUSD"
DAYS            = 250
INITIAL_BALANCE = 500.0
RISK_PCT        = 1.5
REWARD_RATIO    = 2.0

TARGET_TRADES = 100
TARGET_WR_MIN = 48.0
TARGET_WR_MAX = 55.0
TARGET_PF     = 1.5
TARGET_DD     = 15.0

BASE_CONFIG = {
    "initial_balance":    INITIAL_BALANCE,
    "risk_pct":           RISK_PCT,
    "reward_ratio":       REWARD_RATIO,
    "max_lot_size":       0.01,
    "sl_atr_mult":        1.5,
    "cooldown_bars":      3,        # 15 min on M5
    "max_trades_per_day": 4,
    "proximity_atr_mult": 1.5,
    "body_atr_mult":      0.4,
    "atr_floor_filter":   True,     # ON for all tests
}

TESTS = [
    {
        "name":  "Test 1",
        "label": "1.5xATR prox + 0.4xATR body + 30% close + ATR floor",
        "close_range_thresh": 0.70,
        "rsi_filter":         False,
    },
    {
        "name":  "Test 2",
        "label": "Test 1 + RSI>50 BUY / RSI<50 SELL",
        "close_range_thresh": 0.70,
        "rsi_filter":         True,
    },
    {
        "name":  "Test 3",
        "label": "Test 2 + 25% close range (tightened from 30%)",
        "close_range_thresh": 0.75,
        "rsi_filter":         True,
    },
]


def _passes(m: dict) -> bool:
    return (
        m["total_trades"] >= TARGET_TRADES
        and TARGET_WR_MIN <= m["win_rate"] <= TARGET_WR_MAX
        and m["profit_factor"] >= TARGET_PF
        and m["max_drawdown_pct"] <= TARGET_DD
    )


def _fmt_row(label: str, m: dict) -> str:
    tr_ok = "OK" if m["total_trades"] >= TARGET_TRADES else "  "
    wr_ok = "OK" if TARGET_WR_MIN <= m["win_rate"] <= TARGET_WR_MAX else "  "
    pf_ok = "OK" if m["profit_factor"] >= TARGET_PF else "  "
    dd_ok = "OK" if m["max_drawdown_pct"] <= TARGET_DD else "  "
    return (
        f"  {label:<42} "
        f"{m['total_trades']:>5}{tr_ok} "
        f"{m['win_rate']:>6.1f}%{wr_ok} "
        f"{m['profit_factor']:>5.2f}{pf_ok} "
        f"{m['max_drawdown_pct']:>5.1f}%{dd_ok} "
        f"{m['sharpe_ratio']:>6.2f}  "
        f"${m['expectancy']:>5.2f}"
    )


def main():
    print()
    print("=" * 78)
    print("   MIDAS -- Bot 3 High-Frequency Backtest")
    print(f"   {SYMBOL} | {DAYS} days | ${INITIAL_BALANCE} start | RR {REWARD_RATIO}:1 | Risk {RISK_PCT}%")
    print(f"   Target: {TARGET_TRADES}+ trades | WR {TARGET_WR_MIN}-{TARGET_WR_MAX}% | "
          f"PF>{TARGET_PF} | DD<{TARGET_DD}%")
    print("=" * 78)

    if not mt5.initialize():
        print(f"ERROR: MT5 not connected -- {mt5.last_error()}")
        sys.exit(1)
    print("MT5 connected.\n")

    all_results = []

    for test in TESTS:
        print(f"\n{'─' * 78}")
        print(f"  {test['name']} -- {test['label']}")
        print(f"{'─' * 78}")

        config = {
            **BASE_CONFIG,
            "close_range_thresh": test["close_range_thresh"],
            "rsi_filter":         test["rsi_filter"],
        }

        trades  = run_bot3_simulation(SYMBOL, DAYS, config)
        metrics = calculate_metrics(trades, INITIAL_BALANCE)

        if "error" in metrics:
            print(f"\n  ERROR: {metrics['error']} -- 0 trades generated.")
            all_results.append((test, {
                "total_trades": 0, "win_rate": 0.0, "profit_factor": 0.0,
                "max_drawdown_pct": 0.0, "sharpe_ratio": 0.0,
                "expectancy": 0.0, "net_pnl": 0.0, "max_consec_losses": 0,
            }))
            continue

        all_results.append((test, metrics))

        tr_hit = metrics["total_trades"] >= TARGET_TRADES
        wr_hit = TARGET_WR_MIN <= metrics["win_rate"] <= TARGET_WR_MAX
        pf_hit = metrics["profit_factor"] >= TARGET_PF
        dd_hit = metrics["max_drawdown_pct"] <= TARGET_DD

        print(f"\n  Trades:           {metrics['total_trades']}"
              f"  {'OK' if tr_hit else f'need {TARGET_TRADES}+'}")
        print(f"  Win Rate:         {metrics['win_rate']}%"
              f"  {'OK' if wr_hit else f'need {TARGET_WR_MIN}-{TARGET_WR_MAX}%'}")
        print(f"  Profit Factor:    {metrics['profit_factor']}"
              f"  {'OK' if pf_hit else f'need >{TARGET_PF}'}")
        print(f"  Max Drawdown:     {metrics['max_drawdown_pct']}%"
              f"  {'OK' if dd_hit else f'need <{TARGET_DD}%'}")
        print(f"  Net P&L:          ${metrics['net_pnl']:.2f}")
        print(f"  Sharpe Ratio:     {metrics['sharpe_ratio']}")
        print(f"  Expectancy/trade: ${metrics['expectancy']:.2f}")
        print(f"  Max consec loss:  {metrics['max_consec_losses']}")

        if _passes(metrics):
            print(f"\n  TARGET REACHED -- stopping here.")
            break
        else:
            misses = []
            if not tr_hit:
                misses.append(f"trades {metrics['total_trades']} < {TARGET_TRADES}")
            if not wr_hit:
                misses.append(f"WR {metrics['win_rate']}% not in {TARGET_WR_MIN}-{TARGET_WR_MAX}%")
            if not pf_hit:
                misses.append(f"PF {metrics['profit_factor']} < {TARGET_PF}")
            if not dd_hit:
                misses.append(f"DD {metrics['max_drawdown_pct']}% > {TARGET_DD}%")
            print(f"\n  Not there yet ({' | '.join(misses)}) -- next test.")

    mt5.shutdown()

    print()
    print("=" * 88)
    print("  RESULTS SUMMARY")
    print("=" * 88)
    print(f"  {'Configuration':<42} {'Trades':>7} {'WR%':>9} {'PF':>7} {'DD%':>7} "
          f"{'Sharpe':>6}  {'Exp':>5}")
    print("  " + "─" * 84)
    for test, m in all_results:
        print(_fmt_row(test["label"], m))
    print("=" * 88)

    winners = [(t, m) for t, m in all_results if _passes(m)]
    if winners:
        t, m = winners[0]
        close_pct = int((1 - t["close_range_thresh"]) * 100)
        print(f"\n  PASS: {t['name']} -- {t['label']}")
        print(f"\n  Config for main_bot3.py:")
        print(f"    PROXIMITY_ATR_MULT  = 1.5")
        print(f"    BODY_ATR_MULT       = 0.4")
        print(f"    CLOSE_RANGE_THRESH  = {t['close_range_thresh']}  # top/bottom {close_pct}%")
        print(f"    RSI_FILTER          = {t['rsi_filter']}")
        print(f"    ATR_FLOOR_FILTER    = True")
        print(f"    MAX_TRADES_PER_DAY  = 4")
        print(f"    COOLDOWN_MIN        = 15")
    else:
        best_pf = max(m["profit_factor"] for _, m in all_results)
        best_wr = max(m["win_rate"] for _, m in all_results)
        best_tr = max(m["total_trades"] for _, m in all_results)
        print(f"\n  No configuration reached target.")
        print(f"  Best: trades={best_tr} | WR={best_wr:.1f}% | PF={best_pf:.2f}")
        print(f"  Consider: loosening proximity mult, adjusting session hours,")
        print(f"  or increasing DAYS to 180 for more data.")
    print()


if __name__ == "__main__":
    main()
