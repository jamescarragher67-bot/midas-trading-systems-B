"""
tools/backtest_gold_compression.py - Candidate 5 full-rigor test.

Stage 1: full-history M5 XAUUSD.a backtest of strategy/gold_compression_breakout.py
         via backtest/gold_compression_engine.py, split in-sample/out-of-sample
         (first half / second half by trade index, same convention lsc_m15.py's
         own validation used).
Stage 2 (ONLY if Stage 1 shows genuine PF > 1.0 in BOTH halves): 1000-shuffle
         percent-return recompounding Monte Carlo (same method as
         tools/risk_calibrator.py) at $10,000 and $50,000, leverage 1:10,
         single 6% trailing wall - reporting floor-clamp % explicitly at
         both sizes, since that's the number this candidate exists to answer.

If Stage 1 does not show genuine edge, this STOPS and reports that plainly -
no Monte Carlo is run on a strategy without a demonstrated edge.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import numpy as np
import pandas as pd
import MetaTrader5 as mt5

from research.strategy.gold_compression_breakout import precompute
from research.backtest.gold_compression_engine import (
    compute_atr14, get_symbol_specs, fetch_data, run_simulation,
    SESSION_HOURS, MAX_HOLD_BARS, MIN_LOOKBACK,
)
from research.backtest.metrics import calculate_metrics

SYMBOL          = "XAUUSD.a"
MAX_BARS        = 90000     # start here; script reports the ACTUAL calendar span returned - M5 covers less time per bar than M15
SPREAD_POINTS   = 18        # same assumption as LSC's backtests
LEVERAGE        = 10
MARGIN_BUDGET_PCT = 0.25
N_SHUFFLES      = 1000
SEED            = 42
BASELINE_RISK_PCT = 0.1     # PF/win-rate are risk_pct-invariant for edge detection - see module docstring reasoning

EDGE_PF_MIN     = 1.05      # minimum PF required in BOTH halves to call this a "genuine" edge, not noise


def margin_safe_max_lot(balance, price, contract_size, leverage=LEVERAGE, budget_pct=MARGIN_BUDGET_PCT):
    return (balance * budget_pct * leverage) / (contract_size * price)


def run_backtest_at_risk(df, risk_pct, point, contract_size, vol_min, vol_max, vol_step, initial_balance):
    """Re-implements the trade loop with a REAL margin-safe/floor-aware lot
    formula (matching tools/risk_calibrator.py's _simulate_trade), since
    backtest/gold_compression_engine.py's own _lot_size is the simpler
    LSC-style flat-cap version - Stage 2 needs the same floor-clamp
    tracking risk_calibrator.py already does, not a re-derivation."""
    from research.strategy.gold_compression_breakout import check_entry
    trades = []
    balance = initial_balance
    current_date = None
    trades_today = 0
    last_trade_bar = -9

    for i in range(MIN_LOOKBACK, len(df) - 1):
        bar_time = df.index[i]
        bar_date = bar_time.date()
        if bar_date != current_date:
            current_date = bar_date
            trades_today = 0
        if bar_time.hour not in SESSION_HOURS:
            continue

        direction, _, sl, tp = check_entry(df, i, last_trade_bar, trades_today, 9, 4)
        if direction == "NEUTRAL":
            continue

        if i + 1 >= len(df):
            continue
        entry_price = float(df.iloc[i + 1]["open"])
        sl_dist = (entry_price - sl) if direction == "BUY" else (sl - entry_price)
        if sl_dist <= 0:
            continue

        risk_amt = balance * (risk_pct / 100)
        sl_points = sl_dist / point
        raw_lot = risk_amt / (sl_points * contract_size * point)
        cap = margin_safe_max_lot(balance, entry_price, contract_size)
        lot = min(raw_lot, cap, vol_max)
        lot = max(lot, vol_min)
        lot = round(round(lot / vol_step) * vol_step, 2)
        floor_clamped = raw_lot < vol_min
        margin_capped = raw_lot > cap

        mult = lot * contract_size
        spread_cost = SPREAD_POINTS * point * mult

        result, exit_price = None, None
        for j in range(i + 2, min(i + MAX_HOLD_BARS + 2, len(df))):
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
            last = df.iloc[min(i + MAX_HOLD_BARS + 1, len(df) - 1)]
            exit_price = float(last["close"])

        raw_move = (exit_price - entry_price) if direction == "BUY" else (entry_price - exit_price)
        pnl = raw_move * mult - spread_cost
        result = "WIN" if pnl > 0 else "LOSS"

        entry_time = df.index[i + 1]
        balance += pnl
        trades.append({
            "date": entry_time.strftime("%Y-%m-%d"), "time": entry_time.strftime("%H:%M"),
            "direction": direction, "entry": round(entry_price, 2), "exit": round(exit_price, 2),
            "sl": round(sl, 2), "tp": round(tp, 2), "lots": lot,
            "spread_cost": round(spread_cost, 2), "pnl": round(pnl, 2), "result": result,
            "balance_after": round(balance, 2), "floor_clamped": floor_clamped, "margin_capped": margin_capped,
        })
        last_trade_bar = i
        trades_today += 1

    return trades


def monte_carlo_drawdown_pct(trades, initial_balance, n_shuffles=N_SHUFFLES, seed=SEED):
    """Identical method to tools/risk_calibrator.py's function of the same name."""
    balances_before = np.array([initial_balance] + [t["balance_after"] for t in trades[:-1]])
    pct_returns = np.array([t["pnl"] for t in trades]) / balances_before
    rng = np.random.default_rng(seed)
    max_dds = np.zeros(n_shuffles)
    final_balances = np.zeros(n_shuffles)
    for s in range(n_shuffles):
        shuffled = rng.permutation(pct_returns)
        balance = initial_balance
        peak = initial_balance
        max_dd_pct = 0.0
        for r in shuffled:
            balance *= (1 + r)
            if balance > peak:
                peak = balance
            dd_pct = (peak - balance) / peak * 100 if peak > 0 else 0.0
            if dd_pct > max_dd_pct:
                max_dd_pct = dd_pct
        max_dds[s] = max_dd_pct
        final_balances[s] = balance
    return {
        "median_max_dd_pct": round(float(np.median(max_dds)), 2),
        "worst_5pct_dd_pct": round(float(np.percentile(max_dds, 95)), 2),
        "worst_1pct_dd_pct": round(float(np.percentile(max_dds, 99)), 2),
        "worst_dd_pct": round(float(np.max(max_dds)), 2),
    }


def main():
    print("=" * 70)
    print("CANDIDATE 5 - Gold Compression Breakout (M5) - full rigor test")
    print("=" * 70)

    df = fetch_data(SYMBOL, MAX_BARS)
    print(f"Bars fetched: {len(df)} | {df.index[0]} -> {df.index[-1]}")
    calendar_days = (df.index[-1] - df.index[0]).days
    print(f"Calendar span: ~{calendar_days} days (~{calendar_days/365.25:.2f} years) - "
          f"NOTE: M5 covers less calendar time per fetched bar than M15 did")

    df["atr"] = compute_atr14(df)
    df = precompute(df)

    point, contract_size = get_symbol_specs(SYMBOL)
    info = mt5.symbol_info(SYMBOL)
    vol_min, vol_max, vol_step = info.volume_min, info.volume_max, info.volume_step
    print(f"point={point} contract_size={contract_size} vol_min={vol_min} vol_max={vol_max}\n")

    print("-" * 70)
    print("STAGE 1 - In-sample / out-of-sample split (baseline risk, edge detection)")
    print("-" * 70)
    baseline_trades = run_backtest_at_risk(df, BASELINE_RISK_PCT, point, contract_size,
                                            vol_min, vol_max, vol_step, initial_balance=50000.0)
    n = len(baseline_trades)
    if n < 30:
        print(f"*** STOPPING: only {n} trades generated - not enough signal to evaluate an edge.")
        print("This strategy design does not produce enough trades on this instrument/timeframe")
        print("to be testable, let alone validated. Reporting honestly rather than forcing it forward.")
        return

    half = n // 2
    is_trades, oos_trades = baseline_trades[:half], baseline_trades[half:]
    m_all = calculate_metrics(baseline_trades, 50000.0)
    m_is  = calculate_metrics(is_trades, 50000.0)
    m_oos = calculate_metrics(oos_trades, 50000.0)

    print(f"Total trades: {n}  |  Overall PF: {m_all['profit_factor']}  |  Win rate: {m_all['win_rate']}%")
    print(f"In-sample  ({len(is_trades)} trades):  PF {m_is['profit_factor']}   win rate {m_is['win_rate']}%")
    print(f"Out-of-sample ({len(oos_trades)} trades): PF {m_oos['profit_factor']}   win rate {m_oos['win_rate']}%")

    genuine_edge = m_is["profit_factor"] >= EDGE_PF_MIN and m_oos["profit_factor"] >= EDGE_PF_MIN
    print(f"\nGenuine edge (PF >= {EDGE_PF_MIN} in BOTH halves)? {'YES' if genuine_edge else 'NO'}")

    if not genuine_edge:
        print("\n*** STOPPING HERE ***")
        print("Compression Breakout does not show a genuine edge in both the in-sample and")
        print("out-of-sample halves. Per the standard set for this test, no Monte Carlo is run")
        print("on a strategy without a demonstrated edge - that would just be dressing up noise")
        print("with a stress test. This candidate is NOT validated. Reporting as a failed test,")
        print("not forcing it to the next stage.")
        return

    print("\n" + "-" * 70)
    print("STAGE 2 - 6% trailing wall Monte Carlo at $10,000 and $50,000, leverage 1:10")
    print("-" * 70)
    for account_size in [10000.0, 50000.0]:
        trades = run_backtest_at_risk(df, BASELINE_RISK_PCT, point, contract_size,
                                       vol_min, vol_max, vol_step, initial_balance=account_size)
        mc = monte_carlo_drawdown_pct(trades, account_size)
        floor_clamped = sum(1 for t in trades if t["floor_clamped"])
        print(f"\n${account_size:,.0f} account, risk={BASELINE_RISK_PCT}%:")
        print(f"  worst-5th-pct DD: {mc['worst_5pct_dd_pct']}%   absolute-worst: {mc['worst_dd_pct']}%")
        print(f"  FLOOR-CLAMPED: {floor_clamped}/{len(trades)} ({floor_clamped/len(trades)*100:.1f}%)")

    print("\nDone.")


if __name__ == "__main__":
    main()
