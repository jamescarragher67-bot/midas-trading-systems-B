"""
research/tools/five_gate_retest.py - the original 5-gate voting engine re-tested
from scratch under the current validation standard (2026-09-16).

Engine: research/strategy/five_gate_voter.py (proven trade-identical to the
a53504f original by research/tools/five_gate_equivalence.py).
Standard: $50,000, 1:10, 25% margin budget, broker lot floor/step, spread cost
= max(median stored M5 bar spread, live spread), static quote->USD rates for
non-USD instruments; 0.1% risk for the screen; risk sweep on the calibrator's
RISK_LEVELS with month-block reordering on seeds 42/1/7/99/123 (worst of
seeds), pass = 95th <= 4.2%, worst <= 5.7%, real order <= 5.7% (6% wall).

DATA CONSTRAINT (broker-imposed, stated up front): this terminal serves at
most ~100k M5 bars per symbol, so every instrument's M5 history starts in
Apr/May 2025 (BTC/ETH Oct 2025, US500 Aug 2025). The LSC / 24-instrument
split at 2024-12-31 therefore cannot be applied to the M5 engine. Split used
here: IS = start .. 2026-02-28, OOS = 2026-03-01 .. end (~10 + ~6.5 months on
gold). The full-history Monte Carlo on M5 has only ~17 month blocks.

SUPPLEMENTARY (labelled, not the headline): the identical logic run on the
90k-bar M15 gold freeze (2022-11 .. 2026-09) with hour-based parameters kept
in hours (soft 8h / hard 24h / 25-min cooldown -> 2 bars) and bar-based ones
kept in bars (EMA 9/21/50, RSI 14, ATR 14/20, 24-bar swing lookback). That is
an adaptation, not the validated M5 system; it exists only because the M5
data cannot support the 2022-2024 split or a 45-month-block Monte Carlo.

  python research/tools/five_gate_retest.py --m5-dir <dir with frozen_m5_*.pkl> --frozen-dir <dir with fx_rates.json, xauusd_m15_frozen.pkl>
"""
import sys, os, json, pickle, glob, io, contextlib, argparse, time
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
os.environ.setdefault("MT5_LOGIN", "1")
import numpy as np, pandas as pd
with contextlib.redirect_stdout(io.StringIO()):
    from research.tools.risk_calibrator import (RISK_LEVELS, SEED, EXTRA_SEEDS, N_SHUFFLES,
                                                WORST5_MARGIN_RATIO, ABS_WORST_MARGIN_RATIO)
from research.backtest.metrics import calculate_metrics
from research.backtest.monte_carlo import monte_carlo_block_reorder_pct
from research.tools.tournament_run import score
from research.strategy import five_gate_voter as fg

TAG = "2026-09-16"; RESEARCH_DIR = os.path.join(ROOT, "research")
ACCOUNT, LEVERAGE, RISK_PCT, WALL = 50000.0, 10, 0.1, 6.0
IS_END_M5, OOS_START_M5 = "2026-02-28 23:59:59", "2026-03-01"
IS_END_M15, OOS_START_M15 = "2024-12-31 23:59:59", "2025-01-01"
EDGE_PF_MIN, MIN_TRADES = 1.05, 100
LSC_REF = {"pf": 1.1335, "risk_pct": 0.015, "worst_maxseed": 5.66, "worst5_maxseed": 3.92, "hist_dd": 2.40, "trades": 2643}
T5, TAB = round(WALL * WORST5_MARGIN_RATIO, 2), round(WALL * ABS_WORST_MARGIN_RATIO, 2)


def pf_of(tr):
    gw = sum(t["pnl"] for t in tr if t["pnl"] > 0); gl = -sum(t["pnl"] for t in tr if t["pnl"] <= 0)
    return round(gw / gl, 3) if gl > 0 else (999.0 if gw > 0 else 0.0)


def describe(trades, df, spread_pts, point, contract_usd, period="quarter"):
    n_days = len(set(df.index.date))
    if not trades: return {"total_trades": 0, "error": "no trades", "trading_days": n_days}
    m = calculate_metrics(trades, ACCOUNT); m.pop("equity_curve", None)
    out = {k: m[k] for k in ("total_trades", "win_rate", "profit_factor", "net_pnl", "max_drawdown_pct", "expectancy")}
    out["score"] = score(m); out["trading_days"] = n_days
    per_day = Counter(t["date"] for t in trades)
    out["days_with_trades"] = len(per_day); out["max_trades_in_a_day"] = max(per_day.values())
    out["days_ge4_trades_pct"] = round(sum(v >= 4 for v in per_day.values()) / n_days * 100, 1)
    out["floor_pct"] = round(sum(t["floor_clamped"] for t in trades) / len(trades) * 100, 1)
    out["margin_pct"] = round(sum(t["margin_capped"] for t in trades) / len(trades) * 100, 1)
    sl_dist = np.array([(t["actual_risk_pct"] / 100 * (t["balance_after"] - t["pnl"])) / (t["lots"] * contract_usd) for t in trades])
    out["median_sl_pts"] = round(float(np.median(sl_dist) / point), 1)
    out["spread_over_sl_pct"] = round(spread_pts / (np.median(sl_dist) / point) * 100, 1)
    out["exit_reasons"] = dict(Counter(t["exit_reason"] for t in trades))
    out["sl_methods"] = dict(Counter(t["sl_method"] for t in trades))
    out["n_buy"] = sum(t["direction"] == "BUY" for t in trades); out["n_sell"] = len(trades) - out["n_buy"]
    out["pf_buy"] = pf_of([t for t in trades if t["direction"] == "BUY"]); out["pf_sell"] = pf_of([t for t in trades if t["direction"] == "SELL"])
    out["vote_patterns"] = dict(Counter(f"ema{t['votes']['v_ema']:+d} rsi{t['votes']['v_rsi']:+d} atr{t['votes']['v_atr']:+d} pd{t['votes']['v_pd']:+d}" for t in trades))
    sub = defaultdict(list)
    for t in trades:
        y, mth = t["date"][:4], int(t["date"][5:7])
        key = f"{y}-Q{(mth - 1) // 3 + 1}" if period == "quarter" else f"{y}-H{1 if mth <= 6 else 2}"
        sub[key].append(t)
    out["sub_periods"] = {k: {"n": len(v), "pf": pf_of(v), "net": round(sum(t["pnl"] for t in v), 2)} for k, v in sorted(sub.items())}
    out["positive_sub_periods"] = f"{sum(1 for v in out['sub_periods'].values() if v['net'] > 0)}/{len(out['sub_periods'])}"
    wk = [t for t in trades if pd.Timestamp(t["date"]).dayofweek < 5]
    out["weekend_trades"] = len(trades) - len(wk)
    return out


def line(name, r, extra=""):
    if r.get("error"): return f"  {name:<16} NO TRADES ({r['trading_days']} trading days)"
    subs = "  ".join(f"{k[2:]}:{v['pf']}({v['n']})" for k, v in r["sub_periods"].items())
    return (f"  {name:<16} {r['total_trades']:>4} tr | PF {r['profit_factor']:>5} | WR {r['win_rate']:>5}% | DD {r['max_drawdown_pct']:>5}% | "
            f"net ${r['net_pnl']:>9,.0f} | B/S {r['n_buy']}/{r['n_sell']} (PF {r['pf_buy']}/{r['pf_sell']}) | max/day {r['max_trades_in_a_day']}, "
            f"days>=4: {r['days_ge4_trades_pct']}% | floor {r['floor_pct']}% | margin {r['margin_pct']}% | spread {r['spread_over_sl_pct']}% of SL | "
            f"exits {r['exit_reasons']} | sub [{subs}] pos {r['positive_sub_periods']}{extra}")


def load_m5(path):
    b = pickle.load(open(path, "rb")); info = b["info"]
    return b["df"][["open", "high", "low", "close"]].copy(), b["specs"], info


def spread_for(info, m5=True):
    med = info.get("spread_median_bars_m5" if m5 else "spread_median_bars") or 0.0
    return max(med, info.get("spread_now_pts") or 0.0)


def usd_specs(specs, info, fx):
    point, contract, vmin, vmax, vstep = specs
    rate = 1.0 if info["profit_ccy"] == "USD" else fx[info["profit_ccy"]]
    return (point, contract * rate, vmin, vmax, vstep), rate


def calibrate(df, specs, digits, spread, say, bars_per_hour=12, cooldown=fg.COOLDOWN_BARS, label=""):
    seeds = (SEED,) + tuple(EXTRA_SEEDS); rows = []
    say(f"  risk sweep {label}: full history {str(df.index[0])[:10]} -> {str(df.index[-1])[:10]}, {N_SHUFFLES} month-block reorders x seeds {seeds}")
    for risk in RISK_LEVELS:
        trades, _ = fg.run(df, specs, digits, mode="standard", risk_pct=risk, initial_balance=ACCOUNT, spread_points=spread,
                           leverage=LEVERAGE, bars_per_hour=bars_per_hour, cooldown_bars=cooldown)
        if not trades: say(f"  risk={risk}% NO TRADES"); continue
        m = calculate_metrics(trades, ACCOUNT)
        mcs = [monte_carlo_block_reorder_pct(trades, ACCOUNT, n_reorders=N_SHUFFLES, seed=s, wall_pct=WALL) for s in seeds]
        row = {"risk_pct": risk, "trades": len(trades), "pf": m["profit_factor"], "net": m["net_pnl"], "hist_dd": m["max_drawdown_pct"],
               "n_blocks": mcs[0]["n_blocks"], "median_dd": mcs[0]["median_max_dd_pct"], "worst5": mcs[0]["worst_5pct_dd_pct"],
               "worst": mcs[0]["worst_dd_pct"], "worst5_maxseed": max(x["worst_5pct_dd_pct"] for x in mcs),
               "worst_maxseed": max(x["worst_dd_pct"] for x in mcs), "breach_maxseed": max(x["pct_paths_breaching_wall"] for x in mcs),
               "floor": sum(t["floor_clamped"] for t in trades), "margin": sum(t["margin_capped"] for t in trades)}
        row["passes"] = row["worst5_maxseed"] <= T5 and row["worst_maxseed"] <= TAB and row["hist_dd"] <= TAB
        rows.append(row)
        say(f"  risk={risk:>6}%  trades={row['trades']:>4}  PF={row['pf']:>5}  net=${row['net']:>9,.0f}  histDD={row['hist_dd']:>5}%  "
            f"medDD={row['median_dd']:>5}%  95th={row['worst5']:>5}% (max-seed {row['worst5_maxseed']:>5}%)  worst={row['worst']:>5}% "
            f"(max-seed {row['worst_maxseed']:>5}%)  breach={row['breach_maxseed']:>4}%  floor={row['floor']:>3}/{row['trades']}  "
            f"margin={row['margin']:>3}  blocks={row['n_blocks']}  {'PASS' if row['passes'] else 'fail'}")
    passing = [r for r in rows if r["passes"]]
    rec = max(passing, key=lambda r: r["risk_pct"]) if passing else None
    if rec:
        say(f"  -> highest passing size {rec['risk_pct']}%: PF {rec['pf']}, net ${rec['net']:,.0f}, 95th max-seed {rec['worst5_maxseed']}%, "
            f"worst max-seed {rec['worst_maxseed']}%, real order {rec['hist_dd']}%   [LSC: 0.015%, PF {LSC_REF['pf']}, 95th {LSC_REF['worst5_maxseed']}%, worst {LSC_REF['worst_maxseed']}%]")
    else:
        say("  -> NO risk level passes the wall with margin")
    if rows and rows[0]["pf"] <= 1.0:
        say(f"  NOTE: full-history PF {rows[0]['pf']} <= 1 - a losing strategy; a passing size only means the losses are small. Calibration is moot.")
    return {"rows": rows, "recommended": rec}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--m5-dir", required=True); ap.add_argument("--frozen-dir", required=True)
    args = ap.parse_args()
    fx = json.load(open(os.path.join(args.frozen_dir, "fx_rates.json")))
    report = os.path.join(RESEARCH_DIR, f"five_gate_retest_{TAG}.txt"); log = open(report, "w", encoding="utf-8")
    def say(s=""): print(s); log.write(s + "\n"); log.flush()
    out = {"tag": TAG, "standard": {"account": ACCOUNT, "leverage": LEVERAGE, "screen_risk": RISK_PCT, "wall": WALL, "targets": [T5, TAB]}}

    say("=" * 110); say("5-GATE VOTING ENGINE RE-TEST (a53504f engine, reconstructed + proven equivalent) - current standard"); say("=" * 110)
    say(f"${ACCOUNT:,.0f} | 1:{LEVERAGE} | margin budget {fg.MARGIN_BUDGET_PCT*100:.0f}% | screen risk {RISK_PCT}% | 3-of-4 effective vote (Session Bias abstains, see module docstring) | "
        f"cooldown {fg.COOLDOWN_BARS} bars | no daily cap in this engine | soft exit {fg.SOFT_EXIT_HOURS}h / hard {fg.HARD_EXIT_HOURS}h | trail {fg.TRAIL_ATR_MULT} ATR | RR {fg.REWARD_RATIO}")
    say(f"M5 windows: IS start..{IS_END_M5[:10]}  OOS {OOS_START_M5}..end  (M5 history on this broker starts Apr/May 2025 - the 2024-12-31 split is impossible on M5)\n")

    # ---------------- gold gauntlet ----------------
    gdf, gspecs, ginfo = load_m5(os.path.join(args.m5_dir, "frozen_m5_XAUUSD.a.pkl"))
    gspread = spread_for(ginfo); digits = ginfo["digits"]
    say(f"[XAUUSD.a] M5 {gdf.index[0]} -> {gdf.index[-1]} ({len(gdf)} bars) | spread {gspread}pt")
    # 0. original-config replay on the last 100 days, for context against the committed 96-trade PF 1.55 report
    last100 = gdf.iloc[-100 * 288:]
    o_tr, o_diag = fg.run(last100, gspecs, digits, mode="original", risk_pct=1.5, initial_balance=500.0, max_lot=0.5)
    om = calculate_metrics(o_tr, 500.0) if o_tr else {}
    say(f"  original config replay (last 100 days, $500, 1.5%, no spread, perf monitor on): {len(o_tr)} trades | PF {om.get('profit_factor')} | "
        f"WR {om.get('win_rate')}% | DD {om.get('max_drawdown_pct')}% | final ${om.get('final_balance')} | perf-monitor triggers {o_diag['perf_monitor_triggers']}   "
        f"[committed report at a53504f: 96 trades, PF 1.55, WR 53.1%, DD 15.6%; 'PF 1.75 / 81 trades' not found in the repo]")
    res_g = {}
    for wname, sl_ in (("IS", slice(None, IS_END_M5)), ("OOS", slice(OOS_START_M5, None))):
        w = gdf.loc[sl_]
        tr, diag = fg.run(w, gspecs, digits, mode="standard", risk_pct=RISK_PCT, initial_balance=ACCOUNT, spread_points=gspread, leverage=LEVERAGE)
        r = describe(tr, w, gspread, gspecs[0], gspecs[1]); r["signals"] = diag["n_signals"]; res_g[wname] = r
        say(line(f"{wname} {str(w.index[0])[:10]}..{str(w.index[-1])[:10]}", r))
    say(f"  vote patterns on IS trades: {res_g['IS'].get('vote_patterns')}")
    say("  full-history month-block calibration (M5, ~17 blocks):")
    res_g["calibration_m5"] = calibrate(gdf, gspecs, digits, gspread, say, label="M5")
    out["xauusd"] = res_g

    # ---------------- supplementary M15 adaptation on 2022-2026 gold ----------------
    say("\n[XAUUSD.a - SUPPLEMENTARY M15 ADAPTATION, hours preserved; NOT the validated M5 system]")
    b = pickle.load(open(os.path.join(args.frozen_dir, "xauusd_m15_frozen.pkl"), "rb"))
    m15 = b["df"][["open", "high", "low", "close"]].copy(); m15specs = b["specs"]
    res_m = {}
    for wname, sl_ in (("IS", slice(None, IS_END_M15)), ("OOS", slice(OOS_START_M15, None))):
        w = m15.loc[sl_]
        tr, diag = fg.run(w, m15specs, 2, mode="standard", risk_pct=RISK_PCT, initial_balance=ACCOUNT, spread_points=18, leverage=LEVERAGE, bars_per_hour=4, cooldown_bars=2)
        r = describe(tr, w, 18, 0.01, 100.0, period="half"); res_m[wname] = r
        say(line(f"M15 {wname} {str(w.index[0])[:10]}..{str(w.index[-1])[:10]}", r))
    res_m["calibration_m15"] = calibrate(m15, m15specs, 2, 18, say, bars_per_hour=4, cooldown=2, label="M15 adaptation")
    out["xauusd_m15_adaptation"] = res_m

    # ---------------- all instruments, IS screen ----------------
    say("\n[ALL INSTRUMENTS - M5 in-sample screen, 5-gate engine unmodified (SL rounding at symbol digits)]")
    res_all = {}
    for pk in sorted(glob.glob(os.path.join(args.m5_dir, "frozen_m5_*.pkl"))):
        df, specs, info = load_m5(pk); sym = info["resolved"]
        specs_usd, rate = usd_specs(specs, info, fx); spread = spread_for(info)
        w = df.loc[:IS_END_M5]
        if len(w) < 5000:
            say(f"  {sym:<16} only {len(w)} IS bars - skipped"); continue
        t0 = time.time()
        tr, diag = fg.run(w, specs_usd, info["digits"], mode="standard", risk_pct=RISK_PCT, initial_balance=ACCOUNT, spread_points=spread, leverage=LEVERAGE)
        r = describe(tr, w, spread, specs_usd[0], specs_usd[1]); r.update({"symbol": sym, "requested": info["requested"], "spread_pts": spread,
             "profit_ccy": info["profit_ccy"], "is_from": str(w.index[0])[:10], "is_to": str(w.index[-1])[:10], "signals": diag["n_signals"]})
        hard = []
        if not r.get("error"):
            if r["floor_pct"] >= 30: hard.append(f"FLOOR {r['floor_pct']}%")
            if r["margin_pct"] >= 30: hard.append(f"MARGIN {r['margin_pct']}%")
            if r["spread_over_sl_pct"] >= 25: hard.append(f"SPREAD {r['spread_over_sl_pct']}% of SL")
            if r["total_trades"] < MIN_TRADES: hard.append("LOW SAMPLE")
            if r["weekend_trades"]: hard.append(f"{r['weekend_trades']} weekend trades")
        r["hard_flags"] = hard; res_all[sym] = r
        say(line(sym, r, f" | {r['is_from']}..{r['is_to']}{' | ' + '; '.join(hard) if hard else ''}  [{time.time()-t0:.0f}s]"))
    ranked = sorted([s for s in res_all if not res_all[s].get("error")], key=lambda s: res_all[s]["profit_factor"], reverse=True)
    say("\nRANKING (M5 in-sample PF, 5-gate engine):")
    say(f"  {'#':>2} {'symbol':<14} {'trades':>6} {'PF':>6} {'WR%':>6} {'DD%':>6} {'score':>7} {'pos sub':>8}  flags")
    promising = []
    for i, s in enumerate(ranked, 1):
        r = res_all[s]; ok = r["profit_factor"] > EDGE_PF_MIN and r["total_trades"] >= MIN_TRADES and not r["hard_flags"]
        if ok: promising.append(s)
        say(f"  {i:>2} {s:<14} {r['total_trades']:>6} {r['profit_factor']:>6} {r['win_rate']:>6} {r['max_drawdown_pct']:>6} {r['score']:>7} {r['positive_sub_periods']:>8}  "
            f"{'; '.join(r['hard_flags']) or '-'}{'   -> PROMISING: run OOS' if ok else ''}")
    # OOS + regime check for anything promising
    res_oos = {}
    if promising:
        say("\nOOS on promising instruments (own held-out window 2026-03-01..end):")
        for s in promising:
            df, specs, info = load_m5(os.path.join(args.m5_dir, f"frozen_m5_{s}.pkl")); specs_usd, _ = usd_specs(specs, info, fx); spread = spread_for(info)
            w = df.loc[OOS_START_M5:]
            tr, _ = fg.run(w, specs_usd, info["digits"], mode="standard", risk_pct=RISK_PCT, initial_balance=ACCOUNT, spread_points=spread, leverage=LEVERAGE)
            r = describe(tr, w, spread, specs_usd[0], specs_usd[1]); res_oos[s] = r
            say(line(f"OOS {s}", r, f" | IS PF {res_all[s]['profit_factor']}"))
    else:
        say("\nNo instrument clears PF > 1.05 on >= 100 trades without a hard flag - nothing advances to OOS.")
    out["instruments"] = res_all; out["instruments_oos"] = res_oos; out["ranking"] = ranked; out["promising"] = promising
    say("\nSIDE BY SIDE: LSC on gold (live standard) = PF 1.1335 at 0.015%, real DD 2.40%, 95th max-seed 3.92%, worst max-seed 5.66%, 2643 trades on 2022-11..2026-09 M15.")
    json.dump(out, open(os.path.join(RESEARCH_DIR, f"five_gate_retest_{TAG}.json"), "w"), indent=2, default=str)
    say(f"Saved research/five_gate_retest_{TAG}.txt / .json"); log.close()


if __name__ == "__main__":
    main()
