"""
research/tools/five_gate_diagnostics.py - two follow-ups to five_gate_retest.py:

1. Intrabar trailing-stop ordering. The original engine raises the trailing
   stop with a bar's high and then tests that same bar's low against the raised
   stop (BUY case), i.e. it assumes the favourable extreme printed first inside
   every bar. With ~97% of exits being trailing exits, that assumption may be
   carrying the result. Same signals, same everything, stop tested BEFORE the
   raise: how much PF survives?
2. Extended risk sweep. LSC's RISK_LEVELS grid tops out at 0.1%, where this
   engine's drawdowns are still under 2%, so "0.1% passes" is a grid ceiling,
   not a calibration. Sweep upward until the 6% wall binds, both orderings.
"""
import sys, os, json, pickle, io, contextlib
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT); os.environ.setdefault("MT5_LOGIN", "1")
with contextlib.redirect_stdout(io.StringIO()):
    from research.tools.risk_calibrator import SEED, EXTRA_SEEDS, N_SHUFFLES, WORST5_MARGIN_RATIO, ABS_WORST_MARGIN_RATIO
from research.backtest.metrics import calculate_metrics
from research.backtest.monte_carlo import monte_carlo_block_reorder_pct
from research.strategy import five_gate_voter as fg

M5DIR, FDIR = sys.argv[1], sys.argv[2]
ACCOUNT, LEV, WALL = 50000.0, 10, 6.0
T5, TAB = WALL * WORST5_MARGIN_RATIO, WALL * ABS_WORST_MARGIN_RATIO
fx = json.load(open(os.path.join(FDIR, "fx_rates.json")))
out_path = os.path.join(ROOT, "research", "five_gate_diagnostics_2026-09-16.txt"); log = open(out_path, "w", encoding="utf-8")
def say(s=""): print(s); log.write(s + "\n"); log.flush()

def load(sym):
    b = pickle.load(open(os.path.join(M5DIR, f"frozen_m5_{sym}.pkl"), "rb")); info = b["info"]
    point, contract, vmin, vmax, vstep = b["specs"]
    rate = 1.0 if info["profit_ccy"] == "USD" else fx[info["profit_ccy"]]
    spread = max(info.get("spread_median_bars_m5") or 0, info.get("spread_now_pts") or 0)
    return b["df"][["open", "high", "low", "close"]].copy(), (point, contract * rate, vmin, vmax, vstep), info["digits"], spread

def pf(tr):
    m = calculate_metrics(tr, ACCOUNT); return m["profit_factor"], m["net_pnl"], m["win_rate"], m["max_drawdown_pct"], len(tr)

say("=" * 100); say("5-GATE DIAGNOSTICS: intrabar trailing ordering + extended risk sweep"); say("=" * 100)
say("\n1) TRAILING ORDERING - original (raise stop, then test) vs conservative (test, then raise), 0.1% risk, same signals")
say(f"  {'instrument / window':<34} {'trades':>6} | {'orig PF':>7} {'net$':>7} {'WR%':>5} | {'cons PF':>7} {'net$':>7} {'WR%':>5} | PF change")
for sym, windows in (("XAUUSD.a", (("full", None, None), ("IS", None, "2026-02-28 23:59:59"), ("OOS", "2026-03-01", None))),
                     ("GBPJPY.a", (("full", None, None), ("IS", None, "2026-02-28 23:59:59"), ("OOS", "2026-03-01", None))),
                     ("US30.a", (("full", None, None),)), ("BTCUSD.a", (("full", None, None),)), ("EURJPY.a", (("full", None, None),))):
    df, specs, digits, spread = load(sym)
    for wname, a, b_ in windows:
        w = df.loc[a:b_]
        res = {}
        for cons in (False, True):
            tr, _ = fg.run(w, specs, digits, mode="standard", risk_pct=0.1, initial_balance=ACCOUNT, spread_points=spread, leverage=LEV, conservative_trailing=cons)
            res[cons] = pf(tr) if tr else (0, 0, 0, 0, 0)
        o, c = res[False], res[True]
        say(f"  {sym + ' ' + wname:<34} {o[4]:>6} | {o[0]:>7} {o[1]:>7,.0f} {o[2]:>5} | {c[0]:>7} {c[1]:>7,.0f} {c[2]:>5} | {c[0]-o[0]:+.2f}")
# M15 adaptation on gold, full history
b = pickle.load(open(os.path.join(FDIR, "xauusd_m15_frozen.pkl"), "rb")); m15 = b["df"][["open", "high", "low", "close"]].copy()
res = {}
for cons in (False, True):
    tr, _ = fg.run(m15, b["specs"], 2, mode="standard", risk_pct=0.1, initial_balance=ACCOUNT, spread_points=18, leverage=LEV, bars_per_hour=4, cooldown_bars=2, conservative_trailing=cons)
    res[cons] = pf(tr)
o, c = res[False], res[True]
say(f"  {'XAUUSD.a M15 adaptation 2022-2026':<34} {o[4]:>6} | {o[0]:>7} {o[1]:>7,.0f} {o[2]:>5} | {c[0]:>7} {c[1]:>7,.0f} {c[2]:>5} | {c[0]-o[0]:+.2f}")

say("\n2) EXTENDED RISK SWEEP (month-block reorder, worst of seeds 42/1/7/99/123; pass = 95th <= 4.2%, worst <= 5.7%, real <= 5.7%)")
LEVELS = [0.1, 0.15, 0.2, 0.3, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0]
seeds = (SEED,) + tuple(EXTRA_SEEDS)
for sym in ("XAUUSD.a", "GBPJPY.a"):
    df, specs, digits, spread = load(sym)
    for cons in (False, True):
        say(f"  [{sym}] full M5 history, {'CONSERVATIVE' if cons else 'ORIGINAL'} trailing ordering")
        best = None
        for risk in LEVELS:
            tr, _ = fg.run(df, specs, digits, mode="standard", risk_pct=risk, initial_balance=ACCOUNT, spread_points=spread, leverage=LEV, conservative_trailing=cons)
            if not tr: continue
            m = calculate_metrics(tr, ACCOUNT)
            mcs = [monte_carlo_block_reorder_pct(tr, ACCOUNT, n_reorders=N_SHUFFLES, seed=s, wall_pct=WALL) for s in seeds]
            w5 = max(x["worst_5pct_dd_pct"] for x in mcs); ww = max(x["worst_dd_pct"] for x in mcs); br = max(x["pct_paths_breaching_wall"] for x in mcs)
            ok = w5 <= T5 and ww <= TAB and m["max_drawdown_pct"] <= TAB
            if ok: best = (risk, m["profit_factor"], m["net_pnl"], w5, ww, m["max_drawdown_pct"])
            margin = sum(t["margin_capped"] for t in tr); floor = sum(t["floor_clamped"] for t in tr)
            say(f"    risk={risk:>5}%  trades={len(tr):>4}  PF={m['profit_factor']:>5}  net=${m['net_pnl']:>9,.0f}  histDD={m['max_drawdown_pct']:>5}%  "
                f"95th max-seed={w5:>5}%  worst max-seed={ww:>5}%  breach={br:>5}%  floor={floor}  margin={margin}  {'PASS' if ok else 'fail'}")
        if best:
            say(f"    -> highest passing size {best[0]}%: PF {best[1]}, net ${best[2]:,.0f} over the {str(df.index[0])[:10]}..{str(df.index[-1])[:10]} history, "
                f"95th {best[3]}%, worst {best[4]}%, real {best[5]}%")
        else:
            say("    -> nothing passes")
say("\nLSC reference (gold M15, 45 months): 0.015%, PF 1.1335, net $2,712, real DD 2.40%, 95th 3.92%, worst 5.66%.")
say(f"Saved {os.path.relpath(out_path, ROOT)}"); log.close()
