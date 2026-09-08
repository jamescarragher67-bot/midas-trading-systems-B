"""
tools/tournament_round4_oos.py - Round 4: OOS test on the 4 survivors,
re-ranked by OOS score specifically (not in-sample).
"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import MetaTrader5 as mt5
from research.backtest.tournament import engine
from research.backtest.metrics import calculate_metrics
from research.strategy.tournament import ff_momentum, ema9_21_daily, ndays_breakout, sunrise_ogle
from research.tools.tournament_run import WINDOWS, score, MIN_TRADES_FOR_CONFIDENCE, SYMBOL

SURVIVORS = {
    "1_sunrise_ogle":    {"module": sunrise_ogle,     "special": True},
    "5_ff_momentum":     {"module": ff_momentum,       "special": False},
    "6_ema9_21_control": {"module": ema9_21_daily,     "special": False},
    "10_ndays_control":  {"module": ndays_breakout,    "special": False},
}


def run_one_oos(cand):
    module = cand["module"]
    tf = module.TIMEFRAME_LABEL
    win = WINDOWS[tf]
    if cand["special"]:
        df = engine.fetch_data(SYMBOL, module.TIMEFRAME_MT5, win["oos_start"], win["oos_end"])
        trades = module.run(df, initial_balance=engine.DEFAULT_INITIAL_BALANCE,
                             risk_pct=engine.DEFAULT_RISK_PERCENT,
                             spread_points=engine.DEFAULT_SPREAD_POINTS,
                             skip_atr_filter=True)
    else:
        trades = engine.run_backtest(SYMBOL, module, win["oos_start"], win["oos_end"])
    m = calculate_metrics(trades, initial_balance=engine.DEFAULT_INITIAL_BALANCE)
    m.pop("equity_curve", None)
    m["low_sample"] = (m.get("total_trades", 0) < MIN_TRADES_FOR_CONFIDENCE)
    m["score"] = score(m)
    m["timeframe"] = tf
    m["date_from"] = win["oos_start"]
    m["date_to"] = win["oos_end"]
    return trades, m


def main():
    mt5.initialize()
    mt5.symbol_select(SYMBOL, True)
    results = {}
    for key, cand in SURVIVORS.items():
        print(f"OOS: {key} on {cand['module'].TIMEFRAME_LABEL} ...")
        trades, m = run_one_oos(cand)
        results[key] = {"trades": trades, "metrics": m}
        if m.get("error"):
            print(f"  -> ERROR: {m['error']}")
        else:
            print(f"  -> {m['total_trades']} trades | WR {m['win_rate']}% | PF {m['profit_factor']} | "
                  f"MaxDD {m['max_drawdown_pct']}% | Score {m['score']} | LowSample={m['low_sample']}")

    base = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(base, "..", "tournament_oos_results.json"), "w") as f:
        json.dump({k: v["metrics"] for k, v in results.items()}, f, indent=2, default=str)
    with open(os.path.join(base, "..", "tournament_oos_trades.json"), "w") as f:
        json.dump({k: v["trades"] for k, v in results.items()}, f, indent=2, default=str)
    print("Saved OOS results.")


if __name__ == "__main__":
    main()
