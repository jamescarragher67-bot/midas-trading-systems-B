"""
tools/tournament_run.py - CC knockout tournament runner.

Runs all 10 candidate strategies in-sample, scores them, and dumps a JSON
results file for reporting. Round 4 (OOS) and the final Monte Carlo are
separate functions called explicitly with the survivor list, since they
depend on Round 1-3's outcome.

Score formula (documented, not hidden): score = PF / (1 + max_dd_pct/20).
Rewards profit factor, penalizes drawdown proportionally (a strategy with
20% max DD needs 2x the PF of a 0% DD strategy to score equally). Strategies
with fewer than 20 trades are flagged LOW_SAMPLE - not excluded, but their
score should be read with much less confidence.
"""

import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import MetaTrader5 as mt5
from research.backtest.tournament import engine
from research.backtest.metrics import calculate_metrics
from research.strategy.tournament import (
    bb_breakout, rsi_mean_reversion, killzone_overlap, ff_momentum,
    ema9_21_daily, goldmine, msb_trend_breakout, atr_trailing_trend,
    ndays_breakout, sunrise_ogle,
)

SYMBOL = "XAUUSD.a"

WINDOWS = {
    "D1":  {"is_start": "2019-01-01", "is_end": "2024-12-31", "oos_start": "2025-01-01", "oos_end": "2026-09-02"},
    "H1":  {"is_start": "2019-01-01", "is_end": "2024-12-31", "oos_start": "2025-01-01", "oos_end": "2026-09-02"},
    "M15": {"is_start": "2022-06-09", "is_end": "2024-12-31", "oos_start": "2025-01-01", "oos_end": "2026-09-02"},
    "M5":  {"is_start": "2025-04-03", "is_end": "2026-03-31", "oos_start": "2026-04-01", "oos_end": "2026-09-02"},
}

CANDIDATES = {
    "1_sunrise_ogle":      {"module": sunrise_ogle,       "special": True,  "label": "Sunrise Ogle (4-phase EMA pullback)"},
    "2_bb_breakout":       {"module": bb_breakout,        "special": False, "label": "Bollinger Band squeeze breakout"},
    "3_rsi_mean_rev":      {"module": rsi_mean_reversion, "special": False, "label": "RSI(14) mean reversion"},
    "4_killzone":          {"module": killzone_overlap,   "special": False, "label": "Killzone/session overlap breakout"},
    "5_ff_momentum":       {"module": ff_momentum,        "special": False, "label": "Time-series momentum (FF-style)"},
    "6_ema9_21_control":   {"module": ema9_21_daily,      "special": False, "label": "EMA 9/21 daily crossover [CONTROL - expect loser]"},
    "7_goldmine":          {"module": goldmine,           "special": False, "label": "\"Goldmine\" killzone sweep+retest"},
    "8_msb_breakout":      {"module": msb_trend_breakout, "special": False, "label": "\"MSB Trend Breakout\""},
    "9_atr_trailing":      {"module": atr_trailing_trend, "special": False, "label": "EMA20/SMA9 + ATR trailing stop"},
    "10_ndays_control":    {"module": ndays_breakout,     "special": False, "label": "50-day breakout [CONTROL - expect loser]"},
}

MIN_TRADES_FOR_CONFIDENCE = 20


def score(m: dict) -> float:
    if m.get("error") or not m.get("total_trades"):
        return -999.0
    pf = min(m["profit_factor"], 10.0)   # cap runaway PF from tiny samples
    dd = m["max_drawdown_pct"]
    return round(pf / (1 + dd / 20), 4)


def run_one(cand_key: str, cand: dict, date_from: str, date_to: str) -> dict:
    module = cand["module"]
    if cand["special"]:
        df = engine.fetch_data(SYMBOL, module.TIMEFRAME_MT5, date_from, date_to)
        # skip_atr_filter=True: the source's ATR range filter is an absolute-
        # dollar threshold miscalibrated for current gold price levels (see
        # sunrise_ogle.py docstring) - verbatim port produces only 4 trades,
        # not enough to score. Ranked on the scale-adjusted variant instead;
        # both numbers are reported in the writeup.
        trades = module.run(df, initial_balance=engine.DEFAULT_INITIAL_BALANCE,
                             risk_pct=engine.DEFAULT_RISK_PERCENT,
                             spread_points=engine.DEFAULT_SPREAD_POINTS,
                             skip_atr_filter=True)
    else:
        trades = engine.run_backtest(SYMBOL, module, date_from, date_to)

    m = calculate_metrics(trades, initial_balance=engine.DEFAULT_INITIAL_BALANCE)
    m.pop("equity_curve", None)
    m["low_sample"] = (m.get("total_trades", 0) < MIN_TRADES_FOR_CONFIDENCE)
    m["score"] = score(m)
    m["timeframe"] = module.TIMEFRAME_LABEL
    m["date_from"] = date_from
    m["date_to"] = date_to
    return {"trades": trades, "metrics": m}


def main():
    ok = mt5.initialize()
    if not ok:
        print("MT5 init failed:", mt5.last_error()); return
    mt5.symbol_select(SYMBOL, True)

    results = {}
    for key, cand in CANDIDATES.items():
        tf = cand["module"].TIMEFRAME_LABEL
        win = WINDOWS[tf]
        print(f"Running {key} ({cand['label']}) on {tf}, IS {win['is_start']}..{win['is_end']} ...")
        try:
            res = run_one(key, cand, win["is_start"], win["is_end"])
            results[key] = res
            m = res["metrics"]
            if m.get("error"):
                print(f"  -> ERROR: {m['error']}")
            else:
                print(f"  -> {m['total_trades']} trades | WR {m['win_rate']}% | PF {m['profit_factor']} | "
                      f"MaxDD {m['max_drawdown_pct']}% | Score {m['score']} | LowSample={m['low_sample']}")
        except Exception as e:
            print(f"  -> EXCEPTION: {e}")
            results[key] = {"trades": [], "metrics": {"error": str(e), "score": -999.0}}

    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tournament_is_results.json")
    serializable = {k: {"metrics": v["metrics"], "n_trades": len(v["trades"])} for k, v in results.items()}
    with open(out_path, "w") as f:
        json.dump(serializable, f, indent=2, default=str)
    print(f"\nSaved summary to {out_path}")

    # Save full trades too, for later Monte Carlo / OOS work
    trades_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tournament_is_trades.json")
    with open(trades_path, "w") as f:
        json.dump({k: v["trades"] for k, v in results.items()}, f, indent=2, default=str)
    print(f"Saved trades to {trades_path}")


if __name__ == "__main__":
    main()
