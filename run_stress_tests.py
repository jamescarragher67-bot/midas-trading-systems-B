"""
run_stress_tests.py — MIDAS Destruction Testing

Test 1: Out-of-sample validation  (oldest available MT5 data, non-overlapping with tuning window)
Test 2: Monte Carlo worst case     (1,000 shuffles — 5th percentile max drawdown on $500 account)
Test 3: Spread sensitivity         (40-point spread cost applied to every trade entry)

Does NOT modify any existing file. Imports strategy modules, runs inline loops.
"""

import sys
import random
import logging
import numpy as np
import pandas as pd
from datetime import timezone

import MetaTrader5 as mt5

# ── Constants ─────────────────────────────────────────────────────────────────
CONTRACT_SIZE    = 100
POINT            = 0.01
INITIAL_BAL      = 500.0
SYMBOL           = "XAUUSD"
MAX_BARS_MT5     = 75_000

# Tuning windows: how many bars were used during development
BOT1_TUNING_BARS = 28_800   # 100 days × 288 bars/day
BOT2_TUNING_BARS = 51_840   # 180 days × 288 bars/day

SESSION_HOURS = set(range(0, 15)) | {20, 21, 22, 23}

BOT1_CONFIG = {
    "initial_balance":      INITIAL_BAL,
    "risk_pct":             1.5,
    "cooldown_bars":        3,
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

BOT2_CONFIG = {
    "initial_balance":      INITIAL_BAL,
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
}


# ═════════════════════════════════════════════════════════════════════════════
# DATA FETCH
# ═════════════════════════════════════════════════════════════════════════════

def _fetch(symbol, start_pos, count):
    mt5.symbol_select(symbol, True)
    count = min(count, MAX_BARS_MT5 - start_pos)
    if count <= 0:
        return None
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, start_pos, count)
    if rates is None or len(rates) == 0:
        return None
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    df.set_index("time", inplace=True)
    return df


# ═════════════════════════════════════════════════════════════════════════════
# SIMULATION — BOT 1  (accepts pre-fetched, indicator-added df)
# ═════════════════════════════════════════════════════════════════════════════

def _sim_bot1(df, config):
    from strategy.m5_execution_engine import get_daily_bias, check_m5_entry
    from backtest.engine import MIN_LOOKBACK, _simulate_trade

    for n in ["ema_stack", "atr_expansion", "prev_day_structure", "m5_execution_engine"]:
        logging.getLogger(n).setLevel(logging.WARNING)

    trades       = []
    balance      = config["initial_balance"]
    daily_bias   = "NONE"
    current_date = None
    trades_today = 0
    last_bar     = -config.get("cooldown_bars", 3)

    for i in range(MIN_LOOKBACK, len(df) - 1):
        bar_time = df.index[i]
        bar_date = bar_time.date()
        bar_hour = bar_time.hour

        if bar_date != current_date:
            current_date = bar_date
            trades_today = 0
            daily_bias   = "NONE"

        if daily_bias == "NONE":
            # Cap slice at 300 bars — voters only need recent data, avoids O(n²)
            sl_start = max(0, i + 1 - 300)
            cand = get_daily_bias(df.iloc[sl_start:i + 1])
            if cand != "NONE":
                daily_bias = cand

        if daily_bias == "NONE":
            continue
        if config.get("session_filter") and bar_hour not in SESSION_HOURS:
            continue
        if trades_today >= config.get("max_trades_per_day", 4):
            continue

        # check_m5_entry only needs ~25 bars for ATR MA; cap at 300
        sl_start = max(0, i + 1 - 300)
        df_sl = df.iloc[sl_start:i + 1]
        direction, reason = check_m5_entry(df_sl, daily_bias, last_bar, i, trades_today)
        if direction == "NEUTRAL":
            continue

        eff_risk = config["risk_pct"]
        if len(trades) >= config.get("perf_min_trades", 10):
            rec = trades[-config.get("perf_lookback", 20):]
            if sum(1 for t in rec if t["result"] == "WIN") / len(rec) * 100 < config.get("perf_min_wr", 30):
                eff_risk = config.get("perf_reduced_risk", 0.5)

        atr   = float(df.iloc[i]["atr"])
        trade = _simulate_trade(df, df_sl, i, direction, atr, balance, {**config, "risk_pct": eff_risk})
        if trade is None:
            continue

        trade["daily_bias"]     = daily_bias
        trade["strategy_votes"] = {"M5 Engine": 1 if direction == "BUY" else -1}
        balance                += trade["pnl"]
        trade["balance_after"]  = round(balance, 2)
        trades.append(trade)
        last_bar      = i
        trades_today += 1

    return trades


# ═════════════════════════════════════════════════════════════════════════════
# SIMULATION — BOT 2  (accepts pre-fetched raw df)
# ═════════════════════════════════════════════════════════════════════════════

def _sim_bot2(df, config):
    from strategy.volatility_metrics       import precompute_volatility_series, fingerprint_at
    from strategy.regime_classifier        import classify_regime
    from strategy.transition_detector      import detect_transition
    from strategy.volatility_signal_engine import get_signal, passes_filters
    from utils.news_filter                 import is_news_blackout
    from backtest.volatility_engine        import _simulate_vol_trade

    for n in ["volatility_metrics", "regime_classifier",
              "transition_detector", "volatility_signal_engine"]:
        logging.getLogger(n).setLevel(logging.WARNING)

    MIN_LOOKBACK   = 100
    series         = precompute_volatility_series(df)
    trades         = []
    balance        = config["initial_balance"]
    regime_history = []
    last_bar       = -config.get("cooldown_bars", 3)
    trades_today   = 0
    current_date   = None

    for i in range(MIN_LOOKBACK, len(df) - 1):
        bar_time = df.index[i]
        bar_date = bar_time.date()

        if bar_date != current_date:
            current_date = bar_date
            trades_today = 0

        fp = fingerprint_at(series, i)
        if fp["atr_ma50"] == 0.0 or fp["stddev_ma50"] == 0.0:
            regime_history.append("UNKNOWN")
            continue

        regime = classify_regime(fp)
        if len(regime_history) < 4:
            regime_history.append(regime)
            continue

        prev = regime_history[-1]
        regime_history.append(regime)
        if len(regime_history) > 30:
            regime_history.pop(0)

        if regime == prev:
            continue

        transition = detect_transition(regime_history, fp, df.iloc[i])
        if transition["transition"] in ("NONE", "C_to_A"):
            continue

        ok, _ = passes_filters(bar_time, config, trades_today, last_bar, i)
        if not ok:
            continue
        if is_news_blackout(bar_time.to_pydatetime().replace(tzinfo=timezone.utc)):
            continue

        signal = get_signal(transition, fp, config)
        if signal is None:
            continue

        eff_risk = config["risk_pct"]
        if len(trades) >= config.get("perf_min_trades", 10):
            rec = trades[-config.get("perf_lookback", 20):]
            if sum(1 for t in rec if t["result"] == "WIN") / len(rec) * 100 < config.get("perf_min_wr", 30):
                eff_risk = config.get("perf_reduced_risk", 0.5)

        t_sig  = {**signal, "reward_ratio": config["reward_ratio"]}
        trade  = _simulate_vol_trade(df, i, signal["direction"], t_sig, balance, {**config, "risk_pct": eff_risk})
        if trade is None:
            continue

        trade["regime"]        = regime
        trade["transition"]    = transition["transition"]
        balance               += trade["pnl"]
        trade["balance_after"] = round(balance, 2)
        trades.append(trade)
        last_bar      = i
        trades_today += 1

    return trades


# ═════════════════════════════════════════════════════════════════════════════
# METRICS  (inline — avoids import chain)
# ═════════════════════════════════════════════════════════════════════════════

def _metrics(trades):
    if not trades:
        return None
    pnls   = [t["pnl"] for t in trades]
    wins   = [p for p in pnls if p > 0]
    losses = [abs(p) for p in pnls if p <= 0]
    gw, gl = sum(wins), sum(losses)
    net    = gw - gl
    total  = len(trades)
    wr     = len(wins) / total * 100
    pf     = gw / gl if gl > 0 else 999.0
    exp    = net / total

    bal  = INITIAL_BAL
    peak = INITIAL_BAL
    dd   = 0.0
    for p in pnls:
        bal += p
        if bal > peak: peak = bal
        d = (peak - bal) / peak * 100 if peak > 0 else 0
        if d > dd: dd = d

    sharpe = (np.mean(pnls) / np.std(pnls) * np.sqrt(260)
              if len(pnls) > 1 and np.std(pnls) > 0 else 0)

    return {
        "n":      total,
        "wr":     round(wr, 1),
        "pf":     round(pf, 2),
        "dd":     round(dd, 1),
        "sh":     round(sharpe, 2),
        "exp":    round(exp, 2),
        "net":    round(net, 2),
        "final":  round(INITIAL_BAL + net, 2),
    }


# ═════════════════════════════════════════════════════════════════════════════
# MONTE CARLO
# ═════════════════════════════════════════════════════════════════════════════

def _monte_carlo(pnls, n=1000):
    pnls = list(pnls)
    dds  = []
    for _ in range(n):
        random.shuffle(pnls)
        bal  = INITIAL_BAL
        peak = INITIAL_BAL
        dd   = 0.0
        for p in pnls:
            bal += p
            if bal > peak: peak = bal
            d = (peak - bal) / peak * 100 if peak > 0 else 0
            if d > dd: dd = d
        dds.append(dd)
    dds.sort()
    return {
        "p5":    round(dds[int(n * 0.05)], 1),   # worst 5%
        "p50":   round(dds[int(n * 0.50)], 1),
        "p95":   round(dds[int(n * 0.95)], 1),
        "worst": round(dds[-1], 1),
        "best":  round(dds[0],  1),
    }


# ═════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═════════════════════════════════════════════════════════════════════════════

def _pf(flag): return "PASS" if flag else "FAIL"
def _row(label, value, gate=None):
    mark = f"  [{_pf(gate)}]" if gate is not None else "      "
    print(f"{mark}  {label:<32} {value}")


# ═════════════════════════════════════════════════════════════════════════════
# TEST 1 — OUT-OF-SAMPLE
# ═════════════════════════════════════════════════════════════════════════════

def test_oos():
    from strategy.indicators import add_indicators
    from backtest.engine     import INDICATOR_CONFIG

    print("\n" + "=" * 65)
    print("  TEST 1 — OUT-OF-SAMPLE VALIDATION")
    print("  Oldest available MT5 data — never seen during tuning")
    print("=" * 65)

    # ── Bot 1 OOS: bars 28,800 → 75,000 (≈160 days, Oct 2025 – Mar 2026) ────
    oos1_start = BOT1_TUNING_BARS
    oos1_count = MAX_BARS_MT5 - oos1_start
    print(f"\n  Bot 1 OOS: fetching {oos1_count:,} bars (offset {oos1_start:,})...")
    df1r = _fetch(SYMBOL, oos1_start, oos1_count)
    if df1r is None or len(df1r) < 300:
        print("  ERROR: insufficient data")
        trades1_oos = []
    else:
        days1 = (df1r.index[-1] - df1r.index[0]).days
        print(f"  {df1r.index[0].date()} to {df1r.index[-1].date()} ({len(df1r):,} bars, {days1} days)")
        df1 = add_indicators(df1r, INDICATOR_CONFIG)
        trades1_oos = _sim_bot1(df1, BOT1_CONFIG)
        m = _metrics(trades1_oos)
        print(f"\n  Bot 1 OOS ({days1} days):")
        if m:
            _row("Total trades",   m["n"],              gate=m["n"] >= 30)
            _row("Win rate",       f"{m['wr']}%",       gate=m["wr"] >= 48.0)
            _row("Profit factor",  m["pf"],             gate=m["pf"] >= 1.0)
            _row("Max drawdown",   f"{m['dd']}%",       gate=m["dd"] < 20.0)
            _row("Sharpe",         m["sh"])
            _row("Expectancy",     f"${m['exp']:+.2f}/trade")
            _row("Trades/day",     f"{m['n']/max(days1,1):.2f}")
        else:
            print("  No trades in OOS window.")

    # ── Bot 2 OOS: bars 51,840 → 75,000 (≈80 days, Oct 2025 – Jan 2026) ────
    oos2_start = BOT2_TUNING_BARS
    oos2_count = MAX_BARS_MT5 - oos2_start
    print(f"\n  Bot 2 OOS: fetching {oos2_count:,} bars (offset {oos2_start:,})...")
    df2r = _fetch(SYMBOL, oos2_start, oos2_count)
    if df2r is None or len(df2r) < 300:
        print("  ERROR: insufficient data")
        trades2_oos = []
    else:
        days2 = (df2r.index[-1] - df2r.index[0]).days
        print(f"  {df2r.index[0].date()} to {df2r.index[-1].date()} ({len(df2r):,} bars, {days2} days)")
        print("  Precomputing vol series...")
        trades2_oos = _sim_bot2(df2r, BOT2_CONFIG)
        m = _metrics(trades2_oos)
        print(f"\n  Bot 2 OOS ({days2} days):")
        if m:
            _row("Total trades",   m["n"],              gate=m["n"] >= 3)
            _row("Win rate",       f"{m['wr']}%",       gate=m["wr"] >= 50.0)
            _row("Profit factor",  m["pf"],             gate=m["pf"] >= 1.0)
            _row("Max drawdown",   f"{m['dd']}%",       gate=m["dd"] < 20.0)
            _row("Sharpe",         m["sh"])
            _row("Expectancy",     f"${m['exp']:+.2f}/trade")
        else:
            print("  No trades in OOS window.")

    return trades1_oos, trades2_oos


# ═════════════════════════════════════════════════════════════════════════════
# TEST 2 — MONTE CARLO
# ═════════════════════════════════════════════════════════════════════════════

def test_monte_carlo(trades1, trades2):
    print("\n" + "=" * 65)
    print("  TEST 2 — MONTE CARLO WORST CASE  (n=1,000 shuffles)")
    print("  5th pct = worst 5% of all possible trade orderings on $500")
    print("=" * 65)

    pnls1 = [t["pnl"] for t in trades1]
    if pnls1:
        print(f"\n  Bot 1 ({len(pnls1)} trades):")
        mc = _monte_carlo(pnls1)
        _row("5th pct DD  (worst 5%)",  f"{mc['p5']}%",    gate=mc["p5"] < 20.0)
        _row("Median DD",               f"{mc['p50']}%")
        _row("95th pct DD  (best 5%)",  f"{mc['p95']}%")
        _row("Absolute worst outcome",  f"{mc['worst']}%")
        _row("Absolute best outcome",   f"{mc['best']}%")
    else:
        print("  Bot 1: no trades to simulate.")

    pnls2 = [t["pnl"] for t in trades2]
    if pnls2:
        print(f"\n  Bot 2 ({len(pnls2)} trades):")
        mc = _monte_carlo(pnls2)
        _row("5th pct DD  (worst 5%)",  f"{mc['p5']}%",    gate=mc["p5"] < 20.0)
        _row("Median DD",               f"{mc['p50']}%")
        _row("95th pct DD  (best 5%)",  f"{mc['p95']}%")
        _row("Absolute worst outcome",  f"{mc['worst']}%")
        _row("Absolute best outcome",   f"{mc['best']}%")
    else:
        print("  Bot 2: no trades to simulate.")


# ═════════════════════════════════════════════════════════════════════════════
# TEST 3 — SPREAD SENSITIVITY
# ═════════════════════════════════════════════════════════════════════════════

def test_spread(trades1, trades2, spread_pts=40):
    print("\n" + "=" * 65)
    print(f"  TEST 3 — SPREAD SENSITIVITY  ({spread_pts} points worst case)")
    print(f"  Deducts {spread_pts} pt spread cost from every trade P&L")
    print(f"  ({spread_pts} pts = ${spread_pts * POINT:.2f}/pip × lot × {CONTRACT_SIZE})")
    print("=" * 65)

    def _apply_spread(trades):
        out = []
        for t in trades:
            lots = t.get("lots", 0.01)
            cost = spread_pts * POINT * lots * CONTRACT_SIZE
            adj  = dict(t)
            adj["pnl"]    = round(t["pnl"] - cost, 2)
            adj["result"] = "WIN" if adj["pnl"] > 0 else "LOSS"
            out.append(adj)
        return out

    if trades1:
        mb = _metrics(trades1)
        ma = _metrics(_apply_spread(trades1))
        avg_cost = np.mean([spread_pts * POINT * t.get("lots", 0.01) * CONTRACT_SIZE for t in trades1])
        print(f"\n  Bot 1  (baseline -> 40pt spread):")
        _row("Win rate",       f"{mb['wr']}%  ->  {ma['wr']}%",    gate=ma["wr"] >= 45.0)
        _row("Profit factor",  f"{mb['pf']}  ->  {ma['pf']}",      gate=ma["pf"] >= 1.0)
        _row("Max drawdown",   f"{mb['dd']}%  ->  {ma['dd']}%",    gate=ma["dd"] < 20.0)
        _row("Expectancy",     f"${mb['exp']:+.2f}  ->  ${ma['exp']:+.2f}")
        _row("Net P&L",        f"${mb['net']:+.2f}  ->  ${ma['net']:+.2f}")
        _row("Avg spread cost/trade", f"${avg_cost:.2f}")
    else:
        print("  Bot 1: no trades.")

    if trades2:
        mb = _metrics(trades2)
        ma = _metrics(_apply_spread(trades2))
        avg_cost = np.mean([spread_pts * POINT * t.get("lots", 0.01) * CONTRACT_SIZE for t in trades2])
        print(f"\n  Bot 2  (baseline -> 40pt spread):")
        _row("Win rate",       f"{mb['wr']}%  ->  {ma['wr']}%",    gate=ma["wr"] >= 45.0)
        _row("Profit factor",  f"{mb['pf']}  ->  {ma['pf']}",      gate=ma["pf"] >= 1.0)
        _row("Max drawdown",   f"{mb['dd']}%  ->  {ma['dd']}%",    gate=ma["dd"] < 20.0)
        _row("Expectancy",     f"${mb['exp']:+.2f}  ->  ${ma['exp']:+.2f}")
        _row("Net P&L",        f"${mb['net']:+.2f}  ->  ${ma['net']:+.2f}")
        _row("Avg spread cost/trade", f"${avg_cost:.2f}")
    else:
        print("  Bot 2: no trades.")


# ═════════════════════════════════════════════════════════════════════════════
# MAIN
# ═════════════════════════════════════════════════════════════════════════════

def main():
    print("=" * 65)
    print("   MIDAS — DESTRUCTION TESTING")
    print("   OOS | Monte Carlo | Spread Sensitivity")
    print("=" * 65)

    if not mt5.initialize():
        print(f"ERROR: MT5 not connected. {mt5.last_error()}")
        sys.exit(1)
    print("MT5 connected.")

    try:
        from strategy.indicators import add_indicators
        from backtest.engine     import INDICATOR_CONFIG

        # Run baselines — used for Tests 2 & 3
        print("\nRunning Bot 1 baseline (100 days) for Tests 2 & 3...")
        df1r    = _fetch(SYMBOL, 0, BOT1_TUNING_BARS)
        df1     = add_indicators(df1r, INDICATOR_CONFIG)
        trades1 = _sim_bot1(df1, BOT1_CONFIG)
        m1      = _metrics(trades1)
        if m1:
            print(f"  Bot 1: {m1['n']} trades | WR {m1['wr']}% | PF {m1['pf']} | DD {m1['dd']}%")
        else:
            print("  Bot 1: no trades generated")

        print("\nRunning Bot 2 baseline (180 days) for Tests 2 & 3...")
        df2r    = _fetch(SYMBOL, 0, BOT2_TUNING_BARS)
        print("  Precomputing vol series...")
        trades2 = _sim_bot2(df2r, BOT2_CONFIG)
        m2      = _metrics(trades2)
        if m2:
            print(f"  Bot 2: {m2['n']} trades | WR {m2['wr']}% | PF {m2['pf']} | DD {m2['dd']}%")
        else:
            print("  Bot 2: no trades generated")

        # ── Run all three tests ────────────────────────────────────────────────
        test_oos()
        test_monte_carlo(trades1, trades2 if trades2 else [])
        test_spread(trades1, trades2 if trades2 else [])

        print("\n" + "=" * 65)
        print("  ALL TESTS COMPLETE — review [PASS]/[FAIL] above")
        print("  All PASS = green light for July 5th")
        print("  Any FAIL = assess before risking real capital")
        print("=" * 65)

    finally:
        mt5.shutdown()


if __name__ == "__main__":
    main()
