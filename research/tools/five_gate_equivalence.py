"""
research/tools/five_gate_equivalence.py - proves research/strategy/five_gate_voter.py
reproduces the ORIGINAL a53504f engine trade-for-trade.

The original code (backtest/engine.py + the five voters + indicators, extracted
verbatim from git into <orig_dir>) is imported untouched, its MT5 fetch is
monkeypatched to return the same frozen M5 bars, and both are run with the
validated CONFIG from tools/run_backtest.py (57cd845). Trade lists must match
on date/time/direction/entry/exit/sl/lots/pnl.

  python research/tools/five_gate_equivalence.py <orig_dir> <frozen_m5_XAUUSD.a.pkl> [n_bars]
"""
import sys, os, pickle, importlib, importlib.util, types, logging
logging.disable(logging.CRITICAL)

orig_dir, pkl = sys.argv[1], sys.argv[2]
n_bars = int(sys.argv[3]) if len(sys.argv) > 3 else 12000

# --- the port, loaded by file path so it does not touch sys.path ---------------
here = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("five_gate_voter",
        os.path.join(os.path.dirname(here), "strategy", "five_gate_voter.py"))
port = importlib.util.module_from_spec(spec); spec.loader.exec_module(port)

# --- the original, imported from its own tree only ------------------------------
sys.path.insert(0, orig_dir)
os.chdir(orig_dir)                       # session_bias resolves ../best_hours.json relative to strategy/
import backtest.engine as orig           # noqa: E402

blob = pickle.load(open(pkl, "rb"))
df = blob["df"][["open", "high", "low", "close"]].iloc[-n_bars:].copy()
orig.fetch_historical_data = lambda symbol, days: df.copy()

CONFIG = {"symbol": "XAUUSD", "days": 100, "initial_balance": 500, "risk_pct": 1.5, "vote_threshold": 4,
          "cooldown_bars": 5, "session_filter": True, "reward_ratio": 2.0, "max_trade_hours": 8,
          "max_trade_hours_hard": 24, "trailing_enabled": True, "trailing_atr_mult": 0.5, "perf_lookback": 20,
          "perf_min_trades": 10, "perf_min_wr": 30.0, "perf_reduced_risk": 0.5, "max_lot_size": 0.5}

o_trades = orig.run_simulation("XAUUSD", 100, CONFIG)
p_trades, diag = port.run(df, specs=(0.01, 100.0, 0.01, 50.0, 0.01), digits=2, mode="original",
                          risk_pct=1.5, initial_balance=500.0, max_lot=0.5)

keys = ("date", "time", "direction", "entry", "exit", "sl", "lots", "pnl")
o = [tuple(t[k] for k in keys) for t in o_trades]
p = [tuple(t[k] for k in keys) for t in p_trades]
print(f"original: {len(o)} trades | port: {len(p)} trades | bars {len(df)} {df.index[0]} -> {df.index[-1]}")
if o == p:
    print("EQUIVALENCE PASSED - trade lists identical")
else:
    print("EQUIVALENCE FAILED")
    for a, b in zip(o, p):
        if a != b:
            print("  first diff  orig:", a); print("              port:", b); break
    if len(o) != len(p):
        print("  count differs; extra:", (o[len(p):] or p[len(o):])[:2])
    sys.exit(1)
# vote-level check as well
ov = [t["strategy_votes"] for t in o_trades]; pv = [t["votes"] for t in p_trades]
same_votes = all(a["EMA Stack"] == b["v_ema"] and a["RSI Extreme"] == b["v_rsi"] and a["ATR Expansion"] == b["v_atr"]
                 and a["Prev Day Struct"] == b["v_pd"] and a["Session Bias"] == b["v_sess"] for a, b in zip(ov, pv))
print("per-voter votes identical on every trade:", same_votes)
