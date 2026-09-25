"""
research/tools/five_gate_montecarlo_summary.py - Step 2 of the pre-Stage-4 gate:
month-block-reordering Monte Carlo for the 5-gate engine on XAUUSD.a and
GBPJPY.a, LSC's exact corrected methodology and exact decision thresholds.

Reuses research.backtest.monte_carlo.monte_carlo_block_reorder_pct - the same
function LSC's own calibrator and every tournament this week has used - so
this cannot drift from the standard by re-implementation error. Seeds,
n_reorders and the 4.2%/5.7% targets are IMPORTED from
research/tools/risk_calibrator.py, not copied, for the same reason.

This is a full re-run of the risk sweep already done in
research/tools/five_gate_diagnostics.py section 2 (same numbers should come
back) - kept as a separate, single-purpose script so Step 2's headline table
and the sample-size caveat are reported together, cleanly, without digging
through the diagnostics file's two trailing-order variants.
"""
import sys, os, io, contextlib, pickle, json
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
os.environ.setdefault("MT5_LOGIN", "1")
import numpy as np
with contextlib.redirect_stdout(io.StringIO()):
    from research.tools.risk_calibrator import SEED, EXTRA_SEEDS, N_SHUFFLES, WORST5_MARGIN_RATIO, ABS_WORST_MARGIN_RATIO
from research.backtest.metrics import calculate_metrics
from research.backtest.monte_carlo import monte_carlo_block_reorder_pct
from research.strategy import five_gate_voter as fg

M5DIR = "D:/MIDAS TRADING BOT/frozen_bars_2026-09-16/m5"
FROZEN_DIR = "D:/MIDAS TRADING BOT/frozen_bars_2026-09-16"
ACCOUNT, LEV, WALL = 50000.0, 10, 6.0
T5, TAB = round(WALL * WORST5_MARGIN_RATIO, 2), round(WALL * ABS_WORST_MARGIN_RATIO, 2)
LEVELS = [0.1, 0.15, 0.2, 0.3, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0]
SEEDS = (SEED,) + tuple(EXTRA_SEEDS)
LSC_REF = {"trades": 2643, "n_blocks": 47, "risk_pct": 0.015, "pf": 1.1335, "hist_dd": 2.40,
          "worst5_maxseed": 3.92, "worst_maxseed": 5.66, "net": 2712.43}

report = os.path.join(ROOT, "research", "five_gate_montecarlo_2026-09-16.txt")
log = open(report, "w", encoding="utf-8")
def say(s=""): print(s); log.write(s + "\n"); log.flush()

say("=" * 110)
say("STEP 2 - MONTE CARLO, 5-GATE ENGINE, LSC'S EXACT CORRECTED METHODOLOGY")
say("=" * 110)
say(f"Method: month-block reordering (research.backtest.monte_carlo.monte_carlo_block_reorder_pct - the SAME function, "
    f"not a re-implementation, LSC's calibrator and every tournament this week used), {N_SHUFFLES} reorders per seed, "
    f"seeds {SEEDS}, decision on the WORST value over all 5 seeds.")
say(f"Targets (imported from risk_calibrator.py, {WALL}% wall): 95th-pct DD <= {T5}%, absolute-worst DD <= {TAB}%, "
    f"real historical order's DD <= {TAB}%. Framework: ${ACCOUNT:,.0f}, 1:{LEV} leverage, 25% margin-safety budget, "
    f"broker lot floor/step - identical to LSC's own calibration.\n")

fx_rates = json.load(open(os.path.join(FROZEN_DIR, "fx_rates.json")))

results = {}
for sym in ("XAUUSD.a", "GBPJPY.a"):
    blob = pickle.load(open(os.path.join(M5DIR, f"frozen_m5_{sym}.pkl"), "rb"))
    raw = blob["df"][["open", "high", "low", "close"]].copy()
    digits = blob["info"]["digits"]
    point, contract_raw = blob["specs"][0], blob["specs"][1]
    profit_ccy = blob["info"]["profit_ccy"]
    fx_rate = 1.0 if profit_ccy == "USD" else fx_rates[profit_ccy]
    contract = contract_raw * fx_rate   # quote-currency contract value converted to USD, as every other
                                         # tournament/retest script this week does (research/tools/five_gate_retest.py's
                                         # usd_specs) - GBPJPY settles in JPY, so lot sizing and the margin-safe cap
                                         # must use the USD-equivalent contract value, not the raw JPY one.
    specs = (point, contract, blob["specs"][2], blob["specs"][3], blob["specs"][4])

    say(f"[{sym}] full M5 history {raw.index[0]} -> {raw.index[-1]} ({(raw.index[-1]-raw.index[0]).days} calendar days)")
    rows = []
    for risk in LEVELS:
        trades, _ = fg.run(raw, specs, digits, mode="standard", risk_pct=risk, initial_balance=ACCOUNT,
                           spread_points=max(blob["info"].get("spread_median_bars_m5") or 0, blob["info"].get("spread_now_pts") or 0),
                           leverage=LEV)
        if not trades:
            say(f"  risk={risk}%  NO TRADES"); continue
        m = calculate_metrics(trades, ACCOUNT)
        mcs = [monte_carlo_block_reorder_pct(trades, ACCOUNT, n_reorders=N_SHUFFLES, seed=s, wall_pct=WALL) for s in SEEDS]
        w5 = max(x["worst_5pct_dd_pct"] for x in mcs); ww = max(x["worst_dd_pct"] for x in mcs)
        br = max(x["pct_paths_breaching_wall"] for x in mcs); n_blocks = mcs[0]["n_blocks"]
        streak = max(x["losing_month_streak_worst"] for x in mcs)
        ok = w5 <= T5 and ww <= TAB and m["max_drawdown_pct"] <= TAB
        rows.append({"risk_pct": risk, "trades": len(trades), "pf": m["profit_factor"], "net": m["net_pnl"],
                     "hist_dd": m["max_drawdown_pct"], "worst5_maxseed": w5, "worst_maxseed": ww, "breach_maxseed": br,
                     "n_blocks": n_blocks, "losing_streak_worst": streak, "passes": ok,
                     "margin_capped": sum(t["margin_capped"] for t in trades), "floor_clamped": sum(t["floor_clamped"] for t in trades)})
        say(f"  risk={risk:>5}%  trades={len(trades):>4}  PF={m['profit_factor']:>5}  net=${m['net_pnl']:>9,.0f}  "
            f"histDD={m['max_drawdown_pct']:>5}%  95th max-seed={w5:>5}%  worst max-seed={ww:>5}%  breach={br:>5}%  "
            f"worst losing-month-streak={streak}  margin-capped={rows[-1]['margin_capped']:>3}  floor={rows[-1]['floor_clamped']:>3}  "
            f"{'PASS' if ok else 'fail'}")
    passing = [r for r in rows if r["passes"]]
    rec = max(passing, key=lambda r: r["risk_pct"]) if passing else None
    n_blocks_full = rows[0]["n_blocks"] if rows else 0
    say(f"  -> n_blocks (calendar months in this history): {n_blocks_full}  |  trades in full history: {rows[0]['trades'] if rows else 0}")
    if rec:
        say(f"  -> Recommended: {rec['risk_pct']}% risk. PF {rec['pf']}, net ${rec['net']:,.0f}, real DD {rec['hist_dd']}%, "
            f"95th max-seed {rec['worst5_maxseed']}%, worst max-seed {rec['worst_maxseed']}%, breach {rec['breach_maxseed']}%, "
            f"worst losing-month streak {rec['losing_streak_worst']} of {n_blocks_full} months.")
    else:
        say("  -> NO risk level passes with margin.")
    results[sym] = {"rows": rows, "recommended": rec, "n_blocks": n_blocks_full, "n_trades": rows[0]["trades"] if rows else 0}
    say()

say("=" * 110)
say("SAMPLE-SIZE RELIABILITY - explicit, as instructed")
say("=" * 110)
say(f"{'':<10} {'trades':>8} {'months (blocks)':>16} {'trades/month':>13} {'vs LSC trades':>14} {'vs LSC blocks':>14}")
say(f"{'LSC':<10} {LSC_REF['trades']:>8} {LSC_REF['n_blocks']:>16} {LSC_REF['trades']/LSC_REF['n_blocks']:>13.1f} {'baseline':>14} {'baseline':>14}")
for sym, r in results.items():
    tpm = r["n_trades"] / r["n_blocks"] if r["n_blocks"] else 0
    say(f"{sym:<10} {r['n_trades']:>8} {r['n_blocks']:>16} {tpm:>13.1f} {r['n_trades']/LSC_REF['trades']*100:>13.1f}% "
        f"{r['n_blocks']/LSC_REF['n_blocks']*100:>13.1f}%")
say("")
say("Month-block reordering draws its tail estimate from the number of DISTINCT MONTHLY BLOCKS, not the number of trades")
say("or the number of shuffles - 1000 reorders of ~17 blocks is 1000 different orderings of the SAME 17 pieces of data,")
say("not 1000 independent months of evidence. LSC's calibration reorders 47 blocks; GBPJPY's and gold's M5 history here")
say("give roughly a third of that. The 95th-pct and worst-of-1000 statistics reported above are real outputs of the same")
say("code LSC was calibrated with, but with ~17 blocks the tail is necessarily coarser - each block is close to 6% of the")
say("total weight, so a single unusually bad month moves the tail percentiles far more than it would with 45 blocks. This")
say("does not invalidate the numbers; it means they should be read as a rougher, less statistically confident estimate than")
say("LSC's, and this gap will not close by running more shuffles - only by accumulating more months of live/OOS history.")
say("")
say(f"LSC reference: {LSC_REF['trades']} trades / {LSC_REF['n_blocks']} months, {LSC_REF['risk_pct']}% risk, "
    f"PF {LSC_REF['pf']}, real DD {LSC_REF['hist_dd']}%, 95th max-seed {LSC_REF['worst5_maxseed']}%, "
    f"worst max-seed {LSC_REF['worst_maxseed']}%, net ${LSC_REF['net']:,.2f}.")

json.dump(results, open(os.path.join(ROOT, "research", "five_gate_montecarlo_2026-09-16.json"), "w"), indent=2, default=str)
say(f"\nSaved research/five_gate_montecarlo_2026-09-16.txt / .json")
log.close()
