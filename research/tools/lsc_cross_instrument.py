"""
research/tools/lsc_cross_instrument.py - LSC's exact, unmodified signal logic
screened across a 24-instrument shortlist (2026-09-16).

The question: is the liquidity-sweep-continuation mechanic specific to gold,
or does the same code find signal elsewhere when given a fair shot?

What is held fixed (nothing re-tuned per instrument):
  - Signal: strategy/lsc_m15.py precompute/check_entry, via
    research/tools/risk_calibrator.run_backtest - the loop proven md5-identical
    to backtest/lsc_engine.py on gold, and the only LSC path that records
    floor_clamped / margin_capped per trade (the brief needs both).
  - Session gate (config.settings.SESSION_HOURS), 3-bar cooldown, 4/day cap,
    ATR multiples, 2:1 target, 96-bar hold cap, 100-bar warmup, M15.
  - $50,000, 1:10 leverage, 25% margin budget, 0.1% risk (the FX-basket
    screening level: high enough that the 0.01-lot floor rarely binds on gold,
    low enough that the margin cap rarely binds - both are reported anyway).
  - Windows: IS = frozen history .. 2024-12-31; OOS = 2025-01-01 .. end, held
    out per instrument. Only IS is run at this stage.

What necessarily differs per instrument, and how it is handled:
  - Spread: LSC's gold backtests assume 18pt. Here each instrument gets
    max(median stored bar spread, live spread at freeze time) in its own
    points - the FX bars store no spread, so live is the only source there.
    Gold at that rule is 18pt, i.e. the standard assumption.
  - Quote currency: the engine computes P&L in the quote currency and treats
    it as USD. For JPY/EUR/GBP/MXN/ZAR/CAD-quoted instruments the contract
    size is scaled by a STATIC quote->USD rate taken at freeze time so lot
    sizing and dollar P&L are USD-correct in level. PF, win rate and trade
    counts are unaffected by this; max-DD% and net $ for those instruments
    carry the static-rate approximation and are marked.
  - Crypto trades 7 days: the backtest engine has no weekend gate (that lives
    in main.py's run loop), so BTC/ETH results include weekend trades the
    live bot would never take. A weekday-only subset is reported alongside.

Distortion checks reported for EVERY instrument (the tournament-2 lessons):
  cap-days   share of trading days on which the 4/day cap was hit
  floor%     share of trades forced up to the broker minimum lot
  margin%    share of trades forced down by the margin budget
  spread/SL  spread cost as a share of median SL distance (cost load)
  half-year  PF and net by half-year, so a single carrying sub-period is
             visible before anyone calls a result real.
"""

import sys, os, json, pickle, glob, io, contextlib, argparse, time
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
os.environ.setdefault("MT5_LOGIN", "1")

import numpy as np
import pandas as pd

with contextlib.redirect_stdout(io.StringIO()):
    import research.tools.risk_calibrator as rc
from research.backtest.metrics import calculate_metrics
from research.tools.tournament_run import score

TAG           = "2026-09-16"
RESEARCH_DIR  = os.path.join(ROOT, "research")
IS_END        = "2024-12-31 23:59:59"
OOS_START     = "2025-01-01"
ACCOUNT       = 50000.0
LEVERAGE      = 10
RISK_PCT      = 0.1
EDGE_PF_MIN   = 1.05
MIN_TRADES    = 100        # below this an IS PF is not a sample, it is noise

rc.INITIAL_BALANCE  = ACCOUNT
rc.LEVERAGE_ASSUMED = LEVERAGE


def pf_of(trades):
    gw = sum(t["pnl"] for t in trades if t["pnl"] > 0)
    gl = -sum(t["pnl"] for t in trades if t["pnl"] <= 0)
    return round(gw / gl, 3) if gl > 0 else (999.0 if gw > 0 else 0.0)


def half_year(date):
    return f"{date[:4]}-H{1 if int(date[5:7]) <= 6 else 2}"


def run_instrument(blob, fx_rates, window):
    info, specs = blob["info"], blob["specs"]
    point, contract, vmin, vmax, vstep = specs
    ccy = info["profit_ccy"]
    rate = 1.0 if ccy == "USD" else fx_rates[ccy]
    contract_usd = contract * rate

    df = blob["df"][["open", "high", "low", "close"]].copy()
    df["atr"] = rc.compute_atr14(df)
    df = rc.precompute(df)
    if window == "IS":
        df = df.loc[:IS_END]
    elif window == "OOS":
        df = df.loc[OOS_START:]

    spread_pts = max(info.get("spread_median_bars") or 0.0, info.get("spread_now_pts") or 0.0)
    rc.SPREAD_POINTS = spread_pts
    trades = rc.run_backtest(df, RISK_PCT, point, contract_usd, vmin, vmax, vstep)
    n_days = len(set(df.index.date))

    out = {"symbol": info["resolved"], "requested": info["requested"], "profit_ccy": ccy, "fx_rate_static": rate,
           "spread_pts_used": spread_pts, "spread_now_pts": info.get("spread_now_pts"),
           "spread_median_bars": info.get("spread_median_bars"),
           "bars": len(df), "from": str(df.index[0])[:10], "to": str(df.index[-1])[:10],
           "trading_days": n_days, "contract": contract, "point": point, "vmin": vmin}
    if not trades:
        out.update({"total_trades": 0, "error": "no trades"}); return out, trades

    m = calculate_metrics(trades, ACCOUNT); m.pop("equity_curve", None)
    out.update({k: m[k] for k in ("total_trades", "win_rate", "profit_factor", "net_pnl", "max_drawdown_pct",
                                   "expectancy", "max_consec_losses")})
    out["score"] = score(m)
    per_day = Counter(t["date"] for t in trades)
    out["days_with_trades"] = len(per_day)
    out["cap_days"] = sum(1 for v in per_day.values() if v >= rc.MAX_TRADES_PER_DAY)
    out["cap_days_pct_of_trading_days"] = round(out["cap_days"] / n_days * 100, 1)
    out["floor_pct"]  = round(sum(t["floor_clamped"] for t in trades) / len(trades) * 100, 1)
    out["margin_pct"] = round(sum(t["margin_capped"] for t in trades) / len(trades) * 100, 1)
    sl_pts = np.array([abs(t["entry"] - t["sl"]) / point for t in trades])
    # entry/sl are stored at 2dp by the engine - for 5-digit FX that is useless,
    # so recover the SL distance from the trade's own risk arithmetic instead:
    # actual_risk_pct * balance = lot * contract_usd * sl_dist
    sl_dist = np.array([(t["actual_risk_pct"] / 100 * (t["balance_after"] - t["pnl"])) / (t["lots"] * contract_usd)
                        for t in trades])
    out["median_sl_pts"] = round(float(np.median(sl_dist) / point), 1)
    out["spread_over_sl_pct"] = round(spread_pts / (np.median(sl_dist) / point) * 100, 1)
    out["n_buy"]  = sum(t["direction"] == "BUY" for t in trades)
    out["n_sell"] = len(trades) - out["n_buy"]
    out["pf_buy"]  = pf_of([t for t in trades if t["direction"] == "BUY"])
    out["pf_sell"] = pf_of([t for t in trades if t["direction"] == "SELL"])
    hy = defaultdict(list)
    for t in trades:
        hy[half_year(t["date"])].append(t)
    out["half_years"] = {k: {"n": len(v), "pf": pf_of(v), "net": round(sum(t["pnl"] for t in v), 2)} for k, v in sorted(hy.items())}
    out["positive_half_years"] = sum(1 for v in out["half_years"].values() if v["net"] > 0)
    wk = [t for t in trades if pd.Timestamp(t["date"]).dayofweek < 5]
    out["weekend_trades"] = len(trades) - len(wk)
    if out["weekend_trades"]:
        mw = calculate_metrics(wk, ACCOUNT)
        out["weekday_only"] = {"n": len(wk), "pf": mw["profit_factor"], "net": mw["net_pnl"], "max_dd_pct": mw["max_drawdown_pct"]}
    return out, trades


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--frozen-dir", required=True, help="directory holding frozen_<symbol>.pkl and fx_rates.json")
    ap.add_argument("--window", choices=["IS", "OOS", "ALL"], default="IS",
                    help="ALL = every available bar, descriptive only (instruments with no pre-2025 history)")
    ap.add_argument("--symbols", nargs="*", default=None, help="restrict to these resolved symbols")
    args = ap.parse_args()

    fx_rates = json.load(open(os.path.join(args.frozen_dir, "fx_rates.json")))
    pickles = sorted(glob.glob(os.path.join(args.frozen_dir, "frozen_*.pkl")))
    report = os.path.join(RESEARCH_DIR, f"lsc_cross_instrument_{args.window}_{TAG}.txt")
    log = open(report, "w", encoding="utf-8")
    def say(s=""):
        print(s); log.write(s + "\n"); log.flush()

    say("=" * 110)
    say(f"LSC CROSS-INSTRUMENT SCREEN - {args.window} - strategy/lsc_m15.py unmodified, via risk_calibrator.run_backtest")
    say("=" * 110)
    say(f"${ACCOUNT:,.0f} | 1:{LEVERAGE} | margin budget {rc.MARGIN_BUDGET_PCT*100:.0f}% | risk {RISK_PCT}% | cooldown {rc.COOLDOWN_BARS} | "
        f"cap {rc.MAX_TRADES_PER_DAY}/day | session hours {sorted(rc.SESSION_HOURS)} | hold {rc.MAX_HOLD_BARS} bars")
    wdesc = {"IS": "history .. " + IS_END[:10], "OOS": OOS_START + " .. end",
             "ALL": "ALL available bars - DESCRIPTIVE ONLY, not comparable to the IS screen"}[args.window]
    say(f"window {args.window}: {wdesc} | "
        f"static quote->USD rates {fx_rates}")
    say(f"Edge gate for OOS: PF > {EDGE_PF_MIN} on >= {MIN_TRADES} trades AND no distortion flag.\n")

    results, all_trades = {}, {}
    for pk in pickles:
        blob = pickle.load(open(pk, "rb"))
        sym = blob["info"]["resolved"]
        if args.symbols and sym not in args.symbols:
            continue
        t0 = time.time()
        r, trades = run_instrument(blob, fx_rates, args.window)
        results[sym] = r; all_trades[sym] = trades
        if r.get("error"):
            say(f"{sym:<14} {r['requested']:<20} {r['error']}"); continue
        # Hard flags block advancement (the result reflects the floor / margin cap /
        # spread load / sample size rather than the signal). Soft flags are reported
        # but do not block: LSC on gold itself hits the 4/day cap on ~60% of
        # trading days (it fires repeatedly on trending days by design), so
        # cap-binding cannot discriminate between instruments here - it is
        # reported so the reader sees it, as the brief asks.
        hard, soft = [], []
        if r["floor_pct"] >= 30: hard.append(f"FLOOR {r['floor_pct']}%")
        if r["margin_pct"] >= 30: hard.append(f"MARGIN {r['margin_pct']}%")
        if r["spread_over_sl_pct"] >= 25: hard.append(f"SPREAD {r['spread_over_sl_pct']}% of SL")
        if r["total_trades"] < MIN_TRADES: hard.append("LOW SAMPLE")
        if r["cap_days_pct_of_trading_days"] >= 50: soft.append(f"cap-bound {r['cap_days_pct_of_trading_days']}% of days")
        if r["profit_factor"] > 1.0 and r["positive_half_years"] <= len(r["half_years"]) // 2:
            soft.append(f"only {r['positive_half_years']}/{len(r['half_years'])} half-years positive")
        r["hard_flags"], r["soft_flags"] = hard, soft
        flags = hard + soft
        r["flags"] = flags
        hys = "  ".join(f"{k[2:]}:{v['pf']}" for k, v in r["half_years"].items())
        say(f"{sym:<14} {r['requested']:<20} {r['total_trades']:>5} tr | PF {r['profit_factor']:>5} | WR {r['win_rate']:>5}% | "
            f"DD {r['max_drawdown_pct']:>5}% | net ${r['net_pnl']:>10,.0f}{'*' if r['profit_ccy'] != 'USD' else ' '} | "
            f"B/S {r['n_buy']}/{r['n_sell']} (PF {r['pf_buy']}/{r['pf_sell']}) | cap {r['cap_days']}/{r['trading_days']}d | "
            f"floor {r['floor_pct']}% | margin {r['margin_pct']}% | spread {r['spread_pts_used']}pt = {r['spread_over_sl_pct']}% of SL | "
            f"HY PF [{hys}] | {'; '.join(flags) if flags else 'clean'}  [{time.time()-t0:.0f}s]")

    ranked = sorted([s for s in results if not results[s].get("error")], key=lambda s: results[s]["profit_factor"], reverse=True)
    say("\nRANKING BY IN-SAMPLE PF (all instruments together):")
    say(f"  {'#':>2} {'symbol':<14} {'trades':>6} {'PF':>6} {'WR%':>6} {'DD%':>6} {'score':>7} {'flags'}")
    advancing = []
    for i, s in enumerate(ranked, 1):
        r = results[s]
        ok = r["profit_factor"] > EDGE_PF_MIN and r["total_trades"] >= MIN_TRADES and not r["hard_flags"]
        if ok: advancing.append(s)
        say(f"  {i:>2} {s:<14} {r['total_trades']:>6} {r['profit_factor']:>6} {r['win_rate']:>6} {r['max_drawdown_pct']:>6} {r['score']:>7} "
            f"{'; '.join(r['flags']) if r['flags'] else '-'}{'   -> ADVANCES TO OOS' if ok else ''}")
    say(f"\nAdvancing to OOS (PF > {EDGE_PF_MIN}, >= {MIN_TRADES} trades, no HARD flag): {advancing if advancing else 'NONE'}")
    say("* = quote currency converted to USD at a static freeze-time rate (PF/WR unaffected; DD/net approximate)")
    for s, r in results.items():
        if r.get("weekend_trades"):
            say(f"  {s}: {r['weekend_trades']} weekend trades included above (no weekend gate in the backtest engine); "
                f"weekday-only: {r['weekday_only']}")

    json.dump({"tag": TAG, "window": args.window, "risk_pct": RISK_PCT, "account": ACCOUNT, "leverage": LEVERAGE,
               "fx_rates": fx_rates, "results": results, "ranking": ranked, "advancing": advancing},
              open(os.path.join(RESEARCH_DIR, f"lsc_cross_instrument_{args.window}_{TAG}.json"), "w"), indent=2, default=str)
    json.dump(all_trades, open(os.path.join(RESEARCH_DIR, f"lsc_cross_instrument_{args.window}_trades_{TAG}.json"), "w"), default=str)
    say(f"\nSaved research/lsc_cross_instrument_{args.window}_{TAG}.json / .txt / _trades")
    log.close()


if __name__ == "__main__":
    main()
