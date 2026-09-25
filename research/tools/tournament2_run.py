"""
research/tools/tournament2_run.py - CC knockout tournament 2 (2026-09-16):
five candidates from a strategy document, all XAUUSD.a M15, on the same
frozen 90,000-bar set LSC was validated and calibrated on.

Protocol - identical to tournament 1 (research/tools/tournament_run.py):
  round1  in-sample on every candidate and sub-variant, score =
          min(PF, 10) / (1 + maxDD% / 20), 1.0% common-footing risk, $50k,
          1:10, 18pt spread. A candidate is ranked on its BEST sub-variant
          (chosen in-sample, so Round 2 is a fair test of that choice).
          Bottom 2 candidates are dropped.
  round2  out-of-sample on the survivors: the IS-chosen sub-variant is the
          one that is RANKED; the other sub-variants are run and shown for
          information only (picking the best OOS variant after the fact
          would be selection on the test set).
  round3  full-history (IS+OOS, as LSC's own calibration) risk sweep for the
          Round-2 survivors with research/tools/risk_calibrator.py's exact
          standard: month-block reordering (monte_carlo_block_reorder_pct),
          1000 paths on each of seeds 42/1/7/99/123, pass = worst-over-seeds
          95th-pct DD <= 0.70 x wall AND worst-over-seeds absolute-worst DD
          <= 0.95 x wall AND real historical DD <= 0.95 x wall.

Windows on the frozen set (2022-11-17 02:15 -> 2026-09-09 11:00 UTC):
  IS  2022-11-17 .. 2024-12-31 (50,106 bars)   OOS 2025-01-01 .. 2026-09-09 (39,894 bars)
Tournament 1's M15 windows were IS 2022-06-09..2024-12-31 / OOS 2025-01-01..
2026-09-02 fetched live; the frozen set starts 5 months later and ends one
week later, so the split date is the same and the OOS window is the same
non-overlapping period plus one week.

Indicators are computed ONCE on the full frozen history and the bar loop is
then run on the date window (engine.prepare + engine.run_prepared), so OOS
starts with fully warmed indicators instead of a cold restart.

Usage (from the repo root; MT5 is NOT needed - bars come from the pickle):
  python research/tools/tournament2_run.py round1 --bars <frozen.pkl>
  python research/tools/tournament2_run.py round2 --bars <frozen.pkl>            # survivors from round1 json
  python research/tools/tournament2_run.py round3 --bars <frozen.pkl>            # survivors from round2 json
Outputs land in research/ as tournament2_*.json / .txt.
"""

import sys, os, json, argparse, pickle, hashlib, time, io, contextlib
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

import numpy as np
import pandas as pd

from research.backtest.tournament import engine
from research.backtest.metrics import calculate_metrics
from research.backtest.monte_carlo import monte_carlo_block_reorder_pct
from research.backtest.tournament.monte_carlo_pct import monte_carlo_pct_drawdown
from research.tools.tournament_run import score, MIN_TRADES_FOR_CONFIDENCE
from research.strategy.tournament2.ma_crossover import MACrossover
from research.strategy.tournament2.heikin_ashi_reversal import HeikinAshiReversal
from research.strategy.tournament2.bb_squeeze_sma import BBSqueezeSMA
from research.strategy.tournament2.narrow_range_breakout import NarrowRangeBreakout
from research.strategy.tournament2.rsi2_connors import RSI2

# The calibrator's decision constants are IMPORTED, not copied, so this stage
# cannot drift from LSC's own standard. Importing it pulls config.settings,
# which needs MT5_LOGIN in the environment (a dummy is fine - nothing here
# talks to the terminal) and prints a settings banner, silenced below.
os.environ.setdefault("MT5_LOGIN", "1")
with contextlib.redirect_stdout(io.StringIO()):
    from research.tools.risk_calibrator import (RISK_LEVELS, SEED, EXTRA_SEEDS, N_SHUFFLES,
                                                WORST5_MARGIN_RATIO, ABS_WORST_MARGIN_RATIO)

TAG          = "2026-09-16"
RESEARCH_DIR = os.path.join(ROOT, "research")
IS_END       = "2024-12-31 23:59:59"
OOS_START    = "2025-01-01"
WALL_PCT     = 6.0
INITIAL      = engine.DEFAULT_INITIAL_BALANCE
ROUND_RISK   = engine.DEFAULT_RISK_PERCENT     # 1.0% common footing for rounds 1-2, as tournament 1

# LSC reference at its live 0.015% on these exact bars (research/lsc_validation_2026-09-15.txt,
# research/lsc_risk_calibration_2026-09-15.txt) - the bar every survivor is measured against.
LSC_REF = {"risk_pct": 0.015, "trades": 2643, "pf": 1.1335, "hist_dd_pct": 2.40,
           "worst5_maxseed": 3.92, "worst_maxseed": 5.66, "net": 2712.43}

CANDIDATES = {
    "1_ma_cross":    {"label": "Moving Average Crossover (EMA20/60 + EMA100 filter)",
                      "variants": [MACrossover(0.5), MACrossover(1.0)]},
    "2_ha_reversal": {"label": "Heikin-Ashi Reversal + Stochastic(14,7,3)",
                      "variants": [HeikinAshiReversal("level"), HeikinAshiReversal("kd")]},
    "3_bb_squeeze":  {"label": "Bollinger Band Squeeze -> SMA20 close-through",
                      "variants": [BBSqueezeSMA()]},
    "4_nr_breakout": {"label": "Narrow Range Breakout (NR4 / NR7)",
                      "variants": [NarrowRangeBreakout(4), NarrowRangeBreakout(7)]},
    "5_rsi2":        {"label": "2-Period RSI, as the source states (BUY >90 / SELL <10)",
                      "variants": [RSI2("cross", False), RSI2("cross", True),
                                   RSI2("level", False), RSI2("level", True)]},
}
# Not a candidate, not ranked: the source's RSI(2) orientation is the reverse of
# Connors' published rule (see rsi2_connors.py). One true-orientation run is
# shown so the reader can see whether the source has it backwards.
DIAGNOSTICS = {"5_rsi2 (true Connors orientation, DIAGNOSTIC ONLY)": RSI2("cross", True, invert=True)}


# ------------------------------------------------------------------ helpers ----
class Tee:
    def __init__(self, path):
        self.f, self.stdout = open(path, "w", encoding="utf-8"), sys.stdout
    def write(self, s):
        self.stdout.write(s); self.f.write(s)
    def flush(self):
        self.stdout.flush(); self.f.flush()


def load_bars(path):
    with open(path, "rb") as f:
        blob = pickle.load(f)
    df = blob["df"][["open", "high", "low", "close"]].copy()
    return df, blob["specs"]


def bars_md5(df):
    return hashlib.md5(df[["open", "high", "low", "close"]].to_numpy().tobytes()).hexdigest()


def trade_md5(trades):   # same key as research/tools/rolling_month_backtest.trade_md5
    key = [(t["date"], t["time"], t["direction"], t["entry"], t["exit"], t["lots"], t["pnl"]) for t in trades]
    return hashlib.md5(json.dumps(key).encode()).hexdigest()


def run_window(df_full, strat, start, end, risk_pct=ROUND_RISK):
    prepared = engine.prepare(df_full, strat)
    window = prepared.loc[start:end]
    return engine.run_prepared(window, strat, risk_pct=risk_pct), len(window)


def summarise(trades, n_bars, start, end):
    m = calculate_metrics(trades, initial_balance=INITIAL)
    m.pop("equity_curve", None)
    m["low_sample"] = m.get("total_trades", 0) < MIN_TRADES_FOR_CONFIDENCE
    m["score"] = score(m)
    m["bars"] = n_bars
    m["date_from"], m["date_to"] = str(start)[:10], str(end)[:10]
    m["exit_reasons"] = dict(Counter(t["exit_reason"] for t in trades))
    m["n_buy"]  = sum(t["direction"] == "BUY" for t in trades)
    m["n_sell"] = len(trades) - m["n_buy"]
    per_day = Counter(t["date"] for t in trades)
    m["trading_days_with_trades"] = len(per_day)
    m["days_at_daily_cap"] = sum(1 for v in per_day.values() if v >= 4)
    m["trade_md5"] = trade_md5(trades)
    return m


def fmt_line(name, m):
    if m.get("error"):
        return f"  {name:<34} NO TRADES"
    ex = m["exit_reasons"]
    exits = " ".join(f"{k}={v}" for k, v in sorted(ex.items()))
    return (f"  {name:<34} {m['total_trades']:>5} tr | WR {m['win_rate']:>5}% | PF {m['profit_factor']:>5} | "
            f"net ${m['net_pnl']:>10,.2f} | maxDD {m['max_drawdown_pct']:>5}% | score {m['score']:>7} | "
            f"B/S {m['n_buy']}/{m['n_sell']} | cap-days {m['days_at_daily_cap']}/{m['trading_days_with_trades']} | "
            f"{'LOW_SAMPLE' if m['low_sample'] else ''} | exits {exits}")


def dump(obj, name):
    path = os.path.join(RESEARCH_DIR, name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, default=str)
    print(f"Saved {os.path.relpath(path, ROOT)}")


def header(df, specs, title):
    print("=" * 100)
    print(title)
    print("=" * 100)
    print(f"Frozen bars: {len(df)} | {df.index[0]} -> {df.index[-1]} UTC | OHLC md5 {bars_md5(df)}")
    print(f"IS window: {df.index[0]} .. {IS_END[:10]} ({(df.index <= IS_END).sum()} bars)   "
          f"OOS window: {OOS_START} .. {df.index[-1]} ({(df.index >= OOS_START).sum()} bars)")
    print(f"Account ${INITIAL:,.0f} | leverage 1:{engine.LEVERAGE} | margin budget {engine.MARGIN_SAFETY_BUDGET_PCT*100:.0f}% | "
          f"spread {engine.DEFAULT_SPREAD_POINTS}pt | specs point/contract/vmin/vmax/vstep = {specs}")
    print(f"Score = min(PF,10) / (1 + maxDD%/20); LOW_SAMPLE below {MIN_TRADES_FOR_CONFIDENCE} trades. "
          f"Framework: hold cap {M15_HOLD} bars (MA crossover {MACrossover.MAX_HOLD_BARS}), cooldown 3, max 4 trades/day, no session filter.")
    print()


M15_HOLD = BBSqueezeSMA.MAX_HOLD_BARS


# ------------------------------------------------------------------- round 1 ----
def round1(df, specs):
    header(df, specs, f"TOURNAMENT 2 - ROUND 1: IN-SAMPLE, all 5 candidates, {ROUND_RISK}% common-footing risk")
    is_df_end = df.index[df.index <= IS_END][-1]
    results, all_trades = {}, {}
    for key, cand in CANDIDATES.items():
        print(f"[{key}] {cand['label']}")
        variants = {}
        for strat in cand["variants"]:
            t0 = time.time()
            trades, n_bars = run_window(df, strat, df.index[0], IS_END)
            m = summarise(trades, n_bars, df.index[0], is_df_end)
            m["variant_label"] = strat.label
            variants[strat.name] = m
            all_trades[f"{key}/{strat.name}"] = trades
            print(fmt_line(strat.name, m) + f"  [{time.time()-t0:.0f}s]")
        best = max(variants, key=lambda v: variants[v]["score"])
        results[key] = {"label": cand["label"], "variants": variants, "best_variant": best,
                        "best_score": variants[best]["score"]}
        print(f"  -> candidate ranked on: {best} (score {variants[best]['score']})\n")

    print("DIAGNOSTIC (not a candidate, not ranked):")
    diag = {}
    for name, strat in DIAGNOSTICS.items():
        trades, n_bars = run_window(df, strat, df.index[0], IS_END)
        m = summarise(trades, n_bars, df.index[0], is_df_end); m["variant_label"] = strat.label
        diag[name] = m
        print(fmt_line(name, m))
    print()

    ranked = sorted(results, key=lambda k: results[k]["best_score"], reverse=True)
    print("ROUND 1 RANKING (best sub-variant per candidate, in-sample):")
    for r, key in enumerate(ranked, 1):
        m = results[key]["variants"][results[key]["best_variant"]]
        status = "ADVANCES" if r <= 3 else "ELIMINATED"
        results[key]["rank"] = r; results[key]["status"] = status
        print(f"  #{r} {key:<16} {results[key]['best_variant']:<16} score {m['score']:>7}  PF {m['profit_factor']:>5}  "
              f"maxDD {m['max_drawdown_pct']:>5}%  trades {m['total_trades']:>5}  net ${m['net_pnl']:>10,.2f}  -> {status}")
    survivors = ranked[:3]
    print(f"\nSurvivors to Round 2 (OOS): {survivors}")
    print(f"Dropped: {ranked[3:]}")
    out = {"tag": TAG, "round": 1, "window": {"from": str(df.index[0]), "to": IS_END[:10]},
           "bars_md5": bars_md5(df), "risk_pct": ROUND_RISK, "candidates": results,
           "diagnostics": diag, "ranking": ranked, "survivors": survivors}
    dump(out, f"tournament2_is_results.json")
    dump(all_trades, f"tournament2_is_trades.json")


# ------------------------------------------------------------------- round 2 ----
def round2(df, specs, survivors):
    with open(os.path.join(RESEARCH_DIR, "tournament2_is_results.json"), encoding="utf-8") as f:
        is_res = json.load(f)
    if not survivors:
        survivors = is_res["survivors"]
    header(df, specs, f"TOURNAMENT 2 - ROUND 2: OUT-OF-SAMPLE on {len(survivors)} survivors, {ROUND_RISK}% risk, "
                      f"ranked on the IS-chosen sub-variant")
    results, all_trades = {}, {}
    for key in survivors:
        cand = CANDIDATES[key]
        chosen = is_res["candidates"][key]["best_variant"]
        print(f"[{key}] {cand['label']}   (IS-chosen sub-variant: {chosen})")
        variants = {}
        for strat in cand["variants"]:
            trades, n_bars = run_window(df, strat, OOS_START, df.index[-1])
            m = summarise(trades, n_bars, OOS_START, df.index[-1]); m["variant_label"] = strat.label
            m["is_score"] = is_res["candidates"][key]["variants"][strat.name]["score"]
            m["is_pf"]    = is_res["candidates"][key]["variants"][strat.name]["profit_factor"]
            variants[strat.name] = m
            all_trades[f"{key}/{strat.name}"] = trades
            tag = "  <== RANKED" if strat.name == chosen else "  (info only)"
            print(fmt_line(strat.name, m) + f"  | IS score {m['is_score']} PF {m['is_pf']}{tag}")
        results[key] = {"label": cand["label"], "ranked_variant": chosen, "variants": variants,
                        "oos_score": variants[chosen]["score"],
                        "oos_pf": variants[chosen].get("profit_factor"),
                        "is_score": is_res["candidates"][key]["best_score"]}
        print()

    ranked = sorted(results, key=lambda k: results[k]["oos_score"], reverse=True)
    print("ROUND 2 RANKING (OOS score of the IS-chosen sub-variant):")
    for r, key in enumerate(ranked, 1):
        v = results[key]["variants"][results[key]["ranked_variant"]]
        results[key]["rank"] = r
        pf_txt = f"{v.get('profit_factor', 'n/a')}"
        print(f"  #{r} {key:<16} {results[key]['ranked_variant']:<16} OOS score {v['score']:>7} (IS {results[key]['is_score']:>7})  "
              f"OOS PF {pf_txt:>5}  maxDD {v.get('max_drawdown_pct', 'n/a'):>5}%  trades {v.get('total_trades', 0):>5}  "
              f"net ${v.get('net_pnl', 0):>10,.2f}  {'LOW_SAMPLE' if v.get('low_sample') else ''}")
    # Same rule as tournament 1's Round 4 -> final MC: top 2 by OOS score advance.
    # A survivor with OOS PF <= 1 still gets the sweep, but the report says
    # plainly that calibrating a negative-expectancy strategy is moot.
    survivors2 = ranked[:2]
    print(f"\nSurvivors to Round 3 (Monte Carlo + risk calibration): {survivors2}")
    print(f"Eliminated: {ranked[2:]}")
    out = {"tag": TAG, "round": 2, "window": {"from": OOS_START, "to": str(df.index[-1])},
           "bars_md5": bars_md5(df), "risk_pct": ROUND_RISK, "candidates": results,
           "ranking": ranked, "survivors": survivors2}
    dump(out, "tournament2_oos_results.json")
    dump(all_trades, "tournament2_oos_trades.json")


# ------------------------------------------------------------------- round 3 ----
def calibrate(df, strat, key):
    target5   = round(WALL_PCT * WORST5_MARGIN_RATIO, 2)
    target_ab = round(WALL_PCT * ABS_WORST_MARGIN_RATIO, 2)
    prepared = engine.prepare(df, strat)
    seeds = (SEED,) + tuple(EXTRA_SEEDS)
    rows = []
    print(f"  risk sweep, full history {df.index[0]} -> {df.index[-1]}, {N_SHUFFLES} month-block reorders x seeds {seeds}")
    for risk in RISK_LEVELS:
        t0 = time.time()
        trades = engine.run_prepared(prepared, strat, risk_pct=risk)
        if not trades:
            print(f"  risk={risk:>6}%  NO TRADES"); continue
        m = calculate_metrics(trades, INITIAL)
        mcs = [monte_carlo_block_reorder_pct(trades, INITIAL, n_reorders=N_SHUFFLES, seed=s, wall_pct=WALL_PCT) for s in seeds]
        mc0 = mcs[0]
        tsh = monte_carlo_pct_drawdown(trades, INITIAL, n_shuffles=N_SHUFFLES, seed=SEED)
        row = {
            "risk_pct": risk, "trades": len(trades), "win_rate": m["win_rate"], "pf": m["profit_factor"],
            "expectancy": m["expectancy"], "net": m["net_pnl"], "final_balance": m["final_balance"],
            "hist_dd_pct": m["max_drawdown_pct"],
            "n_blocks": mc0["n_blocks"],
            "median_dd_pct": mc0["median_max_dd_pct"], "worst5_dd_pct": mc0["worst_5pct_dd_pct"],
            "worst_dd_pct": mc0["worst_dd_pct"], "breach_pct": mc0["pct_paths_breaching_wall"],
            "worst5_maxseed": max(x["worst_5pct_dd_pct"] for x in mcs),
            "worst_maxseed": max(x["worst_dd_pct"] for x in mcs),
            "breach_maxseed": max(x["pct_paths_breaching_wall"] for x in mcs),
            "losing_month_streak_worst": max(x["losing_month_streak_worst"] for x in mcs),
            "trade_shuffle_worst5": tsh["worst_5pct_dd_pct"], "trade_shuffle_worst": tsh["worst_dd_pct"],
            "floor_clamped": sum(t["floor_clamped"] for t in trades),
            "margin_capped": sum(t["margin_capped"] for t in trades),
            "trade_md5": trade_md5(trades),
        }
        row["passes"] = (row["worst5_maxseed"] <= target5 and row["worst_maxseed"] <= target_ab
                         and row["hist_dd_pct"] <= target_ab)
        rows.append(row)
        print(f"  risk={risk:>6}%  trades={row['trades']:>5}  PF={row['pf']:>5}  net=${row['net']:>10,.2f}  "
              f"histDD={row['hist_dd_pct']:>5}%  medDD={row['median_dd_pct']:>5}%  "
              f"95th={row['worst5_dd_pct']:>5}% (max-seed {row['worst5_maxseed']:>5}%)  "
              f"worst={row['worst_dd_pct']:>5}% (max-seed {row['worst_maxseed']:>5}%)  "
              f"breach={row['breach_pct']:>4}%  tradeShuffle95th={row['trade_shuffle_worst5']:>5}%  "
              f"floor={row['floor_clamped']:>4}/{row['trades']}  {'PASS' if row['passes'] else 'fail'}  [{time.time()-t0:.0f}s]")

    passing = [r for r in rows if r["passes"]]
    any_clears_wall = any(r["worst_maxseed"] <= WALL_PCT for r in rows)
    print()
    if not rows:
        verdict = "no trades"; rec = None
    elif not any_clears_wall:
        rec = None
        verdict = (f"STRUCTURAL: even {min(r['risk_pct'] for r in rows)}% has worst-over-seeds DD "
                   f"{min(rows, key=lambda r: r['worst_maxseed'])['worst_maxseed']}% - does not clear the {WALL_PCT}% wall at any tested size")
    elif not passing:
        rec = None
        best = min(rows, key=lambda r: r["worst_maxseed"])
        verdict = (f"NO LEVEL PASSES with margin: best is {best['risk_pct']}% (95th max-seed {best['worst5_maxseed']}%, "
                   f"worst max-seed {best['worst_maxseed']}%, real order {best['hist_dd_pct']}%) vs targets {target5}% / {target_ab}%")
    else:
        rec = max(passing, key=lambda r: r["risk_pct"])
        verdict = (f"Recommended {rec['risk_pct']}%: 95th max-seed {rec['worst5_maxseed']}% (<= {target5}%), "
                   f"worst max-seed {rec['worst_maxseed']}% (<= {target_ab}%), real order {rec['hist_dd_pct']}%, "
                   f"breach {rec['breach_maxseed']}% of paths (max over seeds), PF {rec['pf']}, net ${rec['net']:,.2f}, "
                   f"floor-clamped {rec['floor_clamped']}/{rec['trades']}")
    print(f"  VERDICT [{key}]: {verdict}")
    pf_full = rows[0]["pf"] if rows else None
    if pf_full is not None and pf_full <= 1.0:
        print(f"  NOTE: full-history PF is {pf_full} (<= 1): this is a losing strategy, so any risk% that 'clears the wall' "
              f"does so only by making the losses small. Calibration is moot.")
    return {"rows": rows, "recommended": rec, "verdict": verdict, "targets": {"worst5": target5, "abs_worst": target_ab}}


def round3(df, specs, survivors):
    with open(os.path.join(RESEARCH_DIR, "tournament2_oos_results.json"), encoding="utf-8") as f:
        oos = json.load(f)
    if not survivors:
        survivors = oos["survivors"]
    header(df, specs, f"TOURNAMENT 2 - ROUND 3: month-block-reorder Monte Carlo + risk calibration, {WALL_PCT}% wall")
    print(f"Standard (imported from research/tools/risk_calibrator.py): pass = worst-over-seeds 95th DD <= "
          f"{WALL_PCT*WORST5_MARGIN_RATIO:.2f}% AND worst-over-seeds absolute-worst DD <= {WALL_PCT*ABS_WORST_MARGIN_RATIO:.2f}% "
          f"AND real historical DD <= {WALL_PCT*ABS_WORST_MARGIN_RATIO:.2f}%; seeds {(SEED,)+tuple(EXTRA_SEEDS)}, {N_SHUFFLES} paths each.")
    print(f"LSC reference on these bars at {LSC_REF['risk_pct']}%: {LSC_REF['trades']} trades, PF {LSC_REF['pf']}, "
          f"net ${LSC_REF['net']:,.2f}, real DD {LSC_REF['hist_dd_pct']}%, 95th max-seed {LSC_REF['worst5_maxseed']}%, "
          f"worst max-seed {LSC_REF['worst_maxseed']}%.\n")
    out = {"tag": TAG, "round": 3, "bars_md5": bars_md5(df), "wall_pct": WALL_PCT, "lsc_reference": LSC_REF, "candidates": {}}
    for key in survivors:
        chosen = oos["candidates"][key]["ranked_variant"]
        strat = next(s for s in CANDIDATES[key]["variants"] if s.name == chosen)
        print(f"[{key}] {CANDIDATES[key]['label']} - sub-variant {chosen}")
        print(f"  OOS: score {oos['candidates'][key]['oos_score']}, PF {oos['candidates'][key]['oos_pf']}")
        res = calibrate(df, strat, key)
        res["variant"] = chosen
        out["candidates"][key] = res
        print()
    print("ROUND 3 SUMMARY vs LSC:")
    print(f"  {'candidate':<16} {'variant':<16} {'rec risk%':>9} {'trades':>6} {'PF':>6} {'net$':>10} {'histDD':>7} {'95thMax':>8} {'worstMax':>9} {'floor':>10}")
    print(f"  {'LSC (live)':<16} {'lsc_m15':<16} {LSC_REF['risk_pct']:>9} {LSC_REF['trades']:>6} {LSC_REF['pf']:>6} "
          f"{LSC_REF['net']:>10,.0f} {LSC_REF['hist_dd_pct']:>7} {LSC_REF['worst5_maxseed']:>8} {LSC_REF['worst_maxseed']:>9} {'1150/2643':>10}")
    for key, res in out["candidates"].items():
        r = res["recommended"]
        if r:
            print(f"  {key:<16} {res['variant']:<16} {r['risk_pct']:>9} {r['trades']:>6} {r['pf']:>6} {r['net']:>10,.0f} "
                  f"{r['hist_dd_pct']:>7} {r['worst5_maxseed']:>8} {r['worst_maxseed']:>9} {str(r['floor_clamped'])+'/'+str(r['trades']):>10}")
        else:
            print(f"  {key:<16} {res['variant']:<16} {'NONE':>9}  - {res['verdict']}")
    dump(out, "tournament2_calibration.json")


# ---------------------------------------------------------------------- main ----
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("round", choices=["round1", "round2", "round3"])
    ap.add_argument("--bars", required=True, help="frozen bars pickle ({'df','specs'})")
    ap.add_argument("--survivors", nargs="*", default=None, help="candidate keys (default: from the previous round's json)")
    args = ap.parse_args()
    df, specs = load_bars(args.bars)
    report = os.path.join(RESEARCH_DIR, f"tournament2_{args.round}_{TAG}.txt")
    sys.stdout = Tee(report)
    try:
        {"round1": lambda: round1(df, specs),
         "round2": lambda: round2(df, specs, args.survivors),
         "round3": lambda: round3(df, specs, args.survivors)}[args.round]()
    finally:
        sys.stdout.flush()
        sys.stdout = sys.stdout.stdout
    print(f"Report written to {os.path.relpath(report, ROOT)}")


if __name__ == "__main__":
    main()
