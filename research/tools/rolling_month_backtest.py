"""
research/tools/rolling_month_backtest.py - Combinatorial / rolling month-window
robustness test for LSC (Stage 4) on the full available XAUUSD.a M15 history.

Framework: $50K account, 1:10 leverage, config.settings.RISK_PERCENT risk
per trade (0.045% when this was first run on 2026-09-15), 25% margin
budget, 18pt spread, 6% trailing-drawdown wall - identical to the
2026-09-01 calibration (research/tools/risk_calibrator.py) and the
2026-09-08 validation (research/lsc_validation_2026-09-08.txt).

What it runs
  1. Baseline: risk_calibrator.run_backtest on the frozen bars (the harness
     the validated PF 1.08 figure came from). Everything below is checked
     against it: the fast window engine must reproduce its trade list
     exactly on the full range before any window result is trusted.
  2. Every calendar month run standalone from a fresh $50K.
  3. Rolling 6-month windows, sliding one month at a time, fresh $50K each.
  4. EVERY contiguous month-window of every length (n*(n+1)/2 windows),
     fresh $50K each - the full combinatorial set over contiguous windows.
  5. Month-block reorderings: the full-history trade sequence is cut into
     calendar-month blocks, the BLOCK order is shuffled (each month's
     internal trade sequence preserved), and the per-trade percent returns
     are recompounded along the new path - the same percent-return
     recompounding method as risk_calibrator.monte_carlo_drawdown_pct.
  6. Month carry/drag diagnostics: per-month contribution in the full run,
     leave-one-month-out PF, share of net profit in the best months.

Why the window engine is exact: LSC's entry signal (strategy/lsc_m15.py)
and each trade's exit price depend only on price data, never on balance.
Only lot size / P&L depend on balance. So signals and exits are computed
once on the full series and each window replays the cooldown / daily-cap
/ sizing logic from a fresh balance over its own bar range. A trade
opened inside a window is followed to its exit even if that falls in the
next month (as it would live). Cooldown and daily-cap state start fresh
at each window's first bar.

SCOPE - read before quoting the numbers: every run here is a re-slicing
of the same 2022-11 -> 2026-09 dataset. That period is one regime (a
sustained gold bull market). This tests how sensitive LSC is to WHICH
part of that regime it sees and in WHAT order - it is NOT a test of
cross-regime robustness. That question stays open until real 2013-2018
data is available.

Usage:
  set MT5_LOGIN=<anything>   (config/settings.py insists on it at import)
  python research/tools/rolling_month_backtest.py --bars frozen.pkl [--out report.txt]
  Without --bars it fetches 90,000 bars live from MT5 and freezes them
  to xauusd_m15_frozen.pkl in the current directory.
"""

import argparse
import hashlib
import json
import pickle
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import numpy as np
import pandas as pd

from strategy.lsc_m15 import precompute, check_entry
from backtest.lsc_engine import compute_atr14, SESSION_HOURS, MAX_HOLD_BARS, MIN_LOOKBACK
from research.backtest.metrics import calculate_metrics
from research.backtest.monte_carlo import monte_carlo_block_reorder_pct
from config.settings import RISK_PERCENT
import research.tools.risk_calibrator as rc

SYMBOL          = "XAUUSD.a"
MAX_BARS        = 90000
ACCOUNT_SIZE    = 50000.0
LEVERAGE        = 10
RISK_PCT        = RISK_PERCENT   # whatever is live; the 2026-09-15 run was at the then-current 0.045%
WALL_PCT        = 6.0
ROLL_MONTHS     = 6
N_REORDERS      = 1000
SEED            = 42
MIN_MONTH_BARS  = 1500   # a complete M15 month has ~1900-2200 bars; below this it is a partial edge month


# ---------------------------------------------------------------- data ----
def load_bars(path):
    if path:
        with open(path, "rb") as f:
            blob = pickle.load(f)
        df, specs = blob["df"], blob["specs"]
    else:
        import MetaTrader5 as mt5
        if not mt5.initialize():
            raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")
        mt5.symbol_select(SYMBOL, True)
        rates = mt5.copy_rates_from_pos(SYMBOL, mt5.TIMEFRAME_M15, 0, MAX_BARS)
        info = mt5.symbol_info(SYMBOL)
        specs = (float(info.point), float(info.trade_contract_size),
                 info.volume_min, info.volume_max, info.volume_step)
        mt5.shutdown()
        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s")
        df.set_index("time", inplace=True)
        with open("xauusd_m15_frozen.pkl", "wb") as f:
            pickle.dump({"df": df, "specs": specs}, f)
    df = df[["open", "high", "low", "close"]].copy()
    df["atr"] = compute_atr14(df)
    df = precompute(df)
    return df, specs


# ------------------------------------------------------ fast window engine ----
class WindowEngine:
    """Precomputes balance-independent signals + exits once; replays any
    bar range from a fresh balance with the calibrator's exact sizing."""

    def __init__(self, df, specs):
        self.df = df
        self.point, self.contract, self.vol_min, self.vol_max, self.vol_step = specs
        self.index = df.index
        n = len(df)
        opens  = df["open"].to_numpy(float)
        highs  = df["high"].to_numpy(float)
        lows   = df["low"].to_numpy(float)
        closes = df["close"].to_numpy(float)
        hours = df.index.hour
        self.dates = df.index.date

        sig = []   # (i, direction, sl, tp, entry_price, exit_price)
        for i in range(MIN_LOOKBACK, n - 1):
            if hours[i] not in SESSION_HOURS:
                continue
            direction, _, sl, tp = check_entry(df, i, -10**9, 0, 3, 4)
            if direction == "NEUTRAL":
                continue
            entry_price = opens[i + 1]
            sl_dist = (entry_price - sl) if direction == "BUY" else (sl - entry_price)
            if sl_dist <= 0:
                # calibrator's _simulate_trade returns None here: the signal is
                # consumed by nothing (no cooldown, no daily-cap increment)
                continue
            exit_price = None
            for j in range(i + 2, min(i + MAX_HOLD_BARS + 2, n)):
                h, l = highs[j], lows[j]
                if direction == "BUY":
                    if l <= sl:
                        exit_price = sl; break
                    if h >= tp:
                        exit_price = tp; break
                else:
                    if h >= sl:
                        exit_price = sl; break
                    if l <= tp:
                        exit_price = tp; break
            if exit_price is None:
                exit_price = closes[min(i + MAX_HOLD_BARS + 1, n - 1)]
            sig.append((i, direction, sl, tp, entry_price, exit_price))
        self.signals = sig

    def run(self, start_i, end_i, balance0=ACCOUNT_SIZE, risk_pct=RISK_PCT):
        """Replay bars [start_i, end_i) from a fresh balance."""
        point, contract = self.point, self.contract
        trades, balance = [], balance0
        current_date, trades_today, last_trade_bar = None, 0, -3
        for (i, direction, sl, tp, entry_price, exit_price) in self.signals:
            if i < start_i:
                continue
            if i >= end_i:
                break
            d = self.dates[i]
            if d != current_date:
                current_date, trades_today = d, 0
            if trades_today >= 4 or i - last_trade_bar < 3:
                continue
            sl_dist = (entry_price - sl) if direction == "BUY" else (sl - entry_price)
            risk_amt  = balance * (risk_pct / 100)
            sl_points = sl_dist / point
            raw_lot   = risk_amt / (sl_points * contract * point)
            cap       = (balance * rc.MARGIN_BUDGET_PCT * LEVERAGE) / (contract * entry_price)
            lot = min(raw_lot, cap, self.vol_max)
            lot = max(lot, self.vol_min)
            lot = round(round(lot / self.vol_step) * self.vol_step, 2)
            mult = lot * contract
            spread_cost = rc.SPREAD_POINTS * point * mult
            raw_move = (exit_price - entry_price) if direction == "BUY" else (entry_price - exit_price)
            pnl = round(raw_move * mult - spread_cost, 2)
            balance += pnl
            t = self.index[i + 1]
            trades.append({
                "date": t.strftime("%Y-%m-%d"), "time": t.strftime("%H:%M"),
                "direction": direction, "entry": round(entry_price, 2), "exit": round(exit_price, 2),
                "sl": round(sl, 2), "tp": round(tp, 2), "lots": lot,
                "spread_cost": round(spread_cost, 2), "pnl": pnl,
                "result": "WIN" if pnl > 0 else "LOSS",
                "balance_after": round(balance, 2),
                "floor_clamped": raw_lot < self.vol_min,
            })
            last_trade_bar, trades_today = i, trades_today + 1
        return trades


def trade_md5(trades):
    key = [(t["date"], t["time"], t["direction"], t["entry"], t["exit"], t["lots"], t["pnl"]) for t in trades]
    return hashlib.md5(json.dumps(key).encode()).hexdigest()


def summarise(trades, balance0=ACCOUNT_SIZE):
    if not trades:
        return {"trades": 0, "pf": float("nan"), "wr": float("nan"), "net_pct": 0.0, "max_dd": 0.0}
    m = calculate_metrics(trades, balance0)
    gw, gl = m["gross_win"], m["gross_loss"]
    return {
        "trades": m["total_trades"],
        "pf": (gw / gl) if gl > 0 else float("inf"),
        "wr": m["win_rate"],
        "net_pct": (m["final_balance"] - balance0) / balance0 * 100,
        "max_dd": m["max_drawdown_pct"],
    }


def dist(values):
    a = np.array([v for v in values if np.isfinite(v)], dtype=float)
    if len(a) == 0:
        return {}
    return {
        "n": int(len(a)), "mean": float(a.mean()), "median": float(np.median(a)), "std": float(a.std()),
        "min": float(a.min()), "max": float(a.max()),
        "p5": float(np.percentile(a, 5)), "p95": float(np.percentile(a, 95)),
    }


def fmt_dist(name, d, worst_is_max=False):
    if not d:
        return f"  {name:<10} (no data)"
    worst, best = (d["max"], d["min"]) if worst_is_max else (d["min"], d["max"])
    return (f"    {name:<10} mean {d['mean']:8.3f}  median {d['median']:8.3f}  sd {d['std']:7.3f}  "
            f"worst {worst:8.3f}  best {best:8.3f}  5th/95th {d['p5']:8.3f} / {d['p95']:8.3f}")


# ------------------------------------------------------------ experiments ----
def month_bounds(index):
    months = index.to_period("M")
    out = []
    for p in months.unique():
        idx = np.flatnonzero(months == p)
        out.append((str(p), int(idx[0]), int(idx[-1]) + 1))
    return out


def block_reorder_mc(trades, n_reorders, seed, balance0):
    """Shuffle MONTH blocks (internal order preserved), recompound pct returns.
    Same paths as research/backtest/monte_carlo.monte_carlo_block_reorder_pct
    (the calibrator's tail metric); this wrapper additionally reports the
    order-dependent PF/WR/net spread for the report's distribution tables."""
    balances_before = np.array([balance0] + [t["balance_after"] for t in trades[:-1]])
    pct = np.array([t["pnl"] for t in trades]) / balances_before
    blocks, order = defaultdict(list), []
    for k, t in enumerate(trades):
        m = t["date"][:7]
        if m not in blocks:
            order.append(m)
        blocks[m].append(k)
    block_idx = [np.array(blocks[m]) for m in order]
    rng = np.random.default_rng(seed)

    pfs, wrs, nets = [], [], []
    for _ in range(n_reorders):
        perm = rng.permutation(len(block_idx))
        seq = np.concatenate([block_idx[p] for p in perm])
        bal = balance0 * np.cumprod(1 + pct[seq])
        pnl = np.diff(np.concatenate([[balance0], bal]))
        gw, gl = pnl[pnl > 0].sum(), -pnl[pnl <= 0].sum()
        pfs.append(gw / gl if gl > 0 else float("inf"))
        wrs.append((pnl > 0).mean() * 100)
        nets.append((bal[-1] - balance0) / balance0 * 100)
    shared = monte_carlo_block_reorder_pct(trades, balance0, n_reorders=n_reorders, seed=seed, wall_pct=WALL_PCT)
    dds = shared["max_dds"]; nets = np.array(nets)
    return {"pf": dist(pfs), "wr": dist(wrs), "max_dd": dist(dds), "net_pct": dist(nets),
            "losing_month_streak": {"median": shared["losing_month_streak_median"],
                                    "max": shared["losing_month_streak_worst"]},
            "pct_paths_breaching_wall": shared["pct_paths_breaching_wall"],
            "pct_paths_underwater": float((nets < 0).mean() * 100),
            "n": n_reorders, "blocks": len(block_idx)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bars", default=None, help="frozen bars pickle ({'df','specs'})")
    ap.add_argument("--out", default=None, help="write the report here (and .json next to it) as well as stdout")
    ap.add_argument("--reorders", type=int, default=N_REORDERS)
    args = ap.parse_args()

    lines = []
    def say(s=""):
        print(s); lines.append(s)

    df, specs = load_bars(args.bars)
    point, contract, vol_min, vol_max, vol_step = specs
    say("=" * 100)
    say("LSC ROLLING / COMBINATORIAL MONTH-WINDOW ROBUSTNESS TEST")
    say(f"XAUUSD.a M15, {len(df)} bars, {df.index[0]} -> {df.index[-1]} UTC")
    say(f"Framework: ${ACCOUNT_SIZE:,.0f} / 1:{LEVERAGE} / {RISK_PCT}% risk / {rc.MARGIN_BUDGET_PCT*100:.0f}% margin "
        f"budget / {rc.SPREAD_POINTS}pt spread / {WALL_PCT}% trailing wall")
    say("=" * 100)

    # ---- 1. baseline via the calibrator's own harness -----------------------
    rc.INITIAL_BALANCE, rc.LEVERAGE_ASSUMED = ACCOUNT_SIZE, LEVERAGE
    base = rc.run_backtest(df, RISK_PCT, point, contract, vol_min, vol_max, vol_step)
    bs = summarise(base)
    say("\n[1] BASELINE - risk_calibrator.run_backtest on these bars, full contiguous history")
    say(f"    trades={bs['trades']} PF={bs['pf']:.4f} WR={bs['wr']:.2f}% net={bs['net_pct']:+.2f}% "
        f"maxDD={bs['max_dd']:.2f}%  md5={trade_md5(base)}")
    say("    (2026-09-08 validation on the then-current 90k bars: 2649 trades / PF 1.0800 / WR 38.80% / maxDD 6.00%)")

    eng = WindowEngine(df, specs)
    full = eng.run(MIN_LOOKBACK, len(df) - 1)
    ok = trade_md5(full) == trade_md5(base) and len(full) == len(base)
    say(f"    window-engine full-range replay: {len(full)} trades md5={trade_md5(full)}  "
        f"-> {'IDENTICAL to calibrator' if ok else '*** MISMATCH - DO NOT TRUST WINDOW RESULTS ***'}")
    if not ok:
        sys.exit(1)

    # ---- 2. per-month standalone + contribution in the full run -------------
    months = month_bounds(df.index)
    say(f"\n[2] CALENDAR MONTHS - {len(months)} months (first/last are partial). "
        "Standalone = run from a fresh $50K over that month only.")
    say("    Contribution = that month's P&L inside the full contiguous run (compounded balance).")
    say(f"    {'month':<8}{'bars':>6}{'trades':>7}{'PF':>7}{'WR%':>7}{'net%':>8}{'maxDD%':>8}  |"
        f"{'full-run P&L$':>14}{'cum P&L$':>11}")
    full_by_month = defaultdict(float)
    for t in full:
        full_by_month[t["date"][:7]] += t["pnl"]
    month_stats, cum = [], 0.0
    for (label, s, e) in months:
        tr = eng.run(max(s, MIN_LOOKBACK), min(e, len(df) - 1))
        st = summarise(tr)
        cum += full_by_month[label]
        st.update({"month": label, "bars": e - s, "contrib": full_by_month[label]})
        month_stats.append(st)
        pf_s = f"{st['pf']:.2f}" if st["trades"] and np.isfinite(st["pf"]) else "n/a"
        say(f"    {label:<8}{e-s:>6}{st['trades']:>7}{pf_s:>7}{st['wr']:>7.1f}{st['net_pct']:>8.2f}{st['max_dd']:>8.2f}  |"
            f"{full_by_month[label]:>14,.2f}{cum:>11,.2f}")

    complete = [m for m in month_stats if m["bars"] >= MIN_MONTH_BARS]
    pf_ok = [m for m in complete if m["trades"] > 0 and np.isfinite(m["pf"])]
    say(f"\n    Complete months: {len(complete)} | PF>1: {sum(m['pf'] > 1 for m in pf_ok)} | "
        f"PF<1: {sum(m['pf'] < 1 for m in pf_ok)}")
    say(fmt_dist("PF", dist([m["pf"] for m in pf_ok])))
    say(fmt_dist("WR%", dist([m["wr"] for m in pf_ok])))
    say(fmt_dist("net%", dist([m["net_pct"] for m in complete])))
    say(fmt_dist("maxDD%", dist([m["max_dd"] for m in complete]), worst_is_max=True))

    # carry / drag
    net_total = sum(t["pnl"] for t in full)
    ranked = sorted(month_stats, key=lambda m: m["contrib"], reverse=True)
    say("\n    CARRY / DRAG - full-run monthly contribution ranked:")
    say(f"    Full-run net P&L ${net_total:,.2f}. Top 5 months by contribution:")
    for m in ranked[:5]:
        say(f"      {m['month']}  {m['contrib']:>+10,.2f}  = {m['contrib']/abs(net_total)*100:>6.1f}% of net")
    say("    Bottom 5 months by contribution:")
    for m in ranked[-5:]:
        say(f"      {m['month']}  {m['contrib']:>+10,.2f}  = {m['contrib']/abs(net_total)*100:>6.1f}% of net")
    top3 = sum(m["contrib"] for m in ranked[:3])
    say(f"    Sum of best 3 months {top3:+,.2f} vs full-run net {net_total:+,.2f} "
        f"-> best-3 = {top3/abs(net_total)*100:.0f}% of net")
    pos_sum = sum(m["contrib"] for m in month_stats if m["contrib"] > 0)
    neg_sum = sum(m["contrib"] for m in month_stats if m["contrib"] < 0)
    say(f"    Sum of positive months {pos_sum:+,.2f} | sum of negative months {neg_sum:+,.2f} | month-level PF "
        f"{pos_sum/abs(neg_sum):.2f}")

    say("\n    LEAVE-ONE-MONTH-OUT - full-run PF with that month's trades removed (baseline "
        f"PF {bs['pf']:.4f}); a jump UP = that month drags, a drop DOWN = it carries:")
    gw = sum(t["pnl"] for t in full if t["pnl"] > 0)
    gl = -sum(t["pnl"] for t in full if t["pnl"] <= 0)
    loo = []
    for m in month_stats:
        mt = [t for t in full if t["date"][:7] == m["month"]]
        mw = sum(t["pnl"] for t in mt if t["pnl"] > 0)
        ml = -sum(t["pnl"] for t in mt if t["pnl"] <= 0)
        pf_wo = (gw - mw) / (gl - ml) if (gl - ml) > 0 else float("inf")
        loo.append((m["month"], pf_wo, pf_wo - bs["pf"]))
    loo_sorted = sorted(loo, key=lambda x: x[2])
    say("      biggest CARRIERS (PF falls most without them):")
    for mth, pf_wo, dlt in loo_sorted[:5]:
        say(f"        {mth}: PF without it {pf_wo:.4f} ({dlt:+.4f})")
    say("      biggest DRAGS (PF rises most without them):")
    for mth, pf_wo, dlt in loo_sorted[-5:][::-1]:
        say(f"        {mth}: PF without it {pf_wo:.4f} ({dlt:+.4f})")
    say(f"      Months whose removal alone flips full-run PF below 1.0: "
        f"{[m for m, p, _ in loo if p < 1.0] or 'none'}")

    streak = best = 0; best_end = None
    for m in month_stats:
        if m["contrib"] < 0:
            streak += 1
            if streak > best:
                best, best_end = streak, m["month"]
        else:
            streak = 0
    say(f"      Longest run of consecutive losing months in real order: {best} (ending {best_end})")

    # ---- 3. rolling 6-month windows ------------------------------------------
    say(f"\n[3] ROLLING {ROLL_MONTHS}-MONTH WINDOWS, sliding one month, fresh $50K each "
        "(complete months only)")
    cm = [m for m in months if (m[2] - m[1]) >= MIN_MONTH_BARS]
    roll = []
    say(f"    {'window':<19}{'trades':>7}{'PF':>7}{'WR%':>7}{'net%':>8}{'maxDD%':>8}  wall")
    for k in range(len(cm) - ROLL_MONTHS + 1):
        s, e = cm[k][1], cm[k + ROLL_MONTHS - 1][2]
        tr = eng.run(max(s, MIN_LOOKBACK), min(e, len(df) - 1))
        st = summarise(tr); st["label"] = f"{cm[k][0]}..{cm[k+ROLL_MONTHS-1][0]}"
        roll.append(st)
        say(f"    {st['label']:<19}{st['trades']:>7}{st['pf']:>7.3f}{st['wr']:>7.1f}{st['net_pct']:>8.2f}"
            f"{st['max_dd']:>8.2f}  {'BREACH' if st['max_dd'] >= WALL_PCT else ''}")
    say(f"\n    {len(roll)} windows | PF>1: {sum(r['pf'] > 1 for r in roll)} | PF<1: {sum(r['pf'] < 1 for r in roll)} | "
        f"maxDD >= {WALL_PCT}% wall: {sum(r['max_dd'] >= WALL_PCT for r in roll)}")
    say(fmt_dist("PF", dist([r["pf"] for r in roll])))
    say(fmt_dist("WR%", dist([r["wr"] for r in roll])))
    say(fmt_dist("net%", dist([r["net_pct"] for r in roll])))
    say(fmt_dist("maxDD%", dist([r["max_dd"] for r in roll]), worst_is_max=True))
    say(fmt_dist("trades", dist([r["trades"] for r in roll])))

    # ---- 4. every contiguous month-window of every length --------------------
    say(f"\n[4] ALL CONTIGUOUS MONTH-WINDOWS (every start x every end, complete months, fresh $50K each)")
    allw = []
    for a in range(len(cm)):
        for b in range(a, len(cm)):
            s, e = cm[a][1], cm[b][2]
            tr = eng.run(max(s, MIN_LOOKBACK), min(e, len(df) - 1))
            st = summarise(tr); st["len"] = b - a + 1; st["start"] = cm[a][0]; st["end"] = cm[b][0]
            allw.append(st)
    say(f"    {len(allw)} windows total")
    say(f"    {'len(mo)':>7}{'n':>5}{'PF mean':>9}{'PF worst':>9}{'PF best':>8}{'PF<1':>7}"
        f"{'DD mean':>9}{'DD worst':>9}{'DD>=wall':>9}{'net% mean':>10}{'net%<0':>8}")
    for L in sorted(set(w["len"] for w in allw)):
        ws = [w for w in allw if w["len"] == L and w["trades"] > 0]
        pfs = np.array([w["pf"] for w in ws]); dds = np.array([w["max_dd"] for w in ws])
        nets = np.array([w["net_pct"] for w in ws])
        say(f"    {L:>7}{len(ws):>5}{pfs.mean():>9.3f}{pfs.min():>9.3f}{pfs.max():>8.3f}{(pfs < 1).mean()*100:>6.0f}%"
            f"{dds.mean():>9.2f}{dds.max():>9.2f}{(dds >= WALL_PCT).mean()*100:>8.0f}%{nets.mean():>10.2f}{(nets < 0).mean()*100:>7.0f}%")
    ws = [w for w in allw if w["trades"] > 0]
    say("\n    Pooled over all window lengths:")
    say(fmt_dist("PF", dist([w["pf"] for w in ws])))
    say(fmt_dist("WR%", dist([w["wr"] for w in ws])))
    say(fmt_dist("net%", dist([w["net_pct"] for w in ws])))
    say(fmt_dist("maxDD%", dist([w["max_dd"] for w in ws]), worst_is_max=True))
    say(f"    PF<1 in {np.mean([w['pf'] < 1 for w in ws])*100:.1f}% of windows | wall breached in "
        f"{np.mean([w['max_dd'] >= WALL_PCT for w in ws])*100:.1f}% of windows")
    ge12 = [w for w in ws if w["len"] >= 12]
    say(f"    Windows >= 12 months: {len(ge12)} | PF<1 in {np.mean([w['pf'] < 1 for w in ge12])*100:.1f}% | "
        f"worst PF {min(w['pf'] for w in ge12):.3f} | wall breached in {np.mean([w['max_dd'] >= WALL_PCT for w in ge12])*100:.1f}%")
    worst_w = min(ws, key=lambda w: w["pf"]); best_w = max(ws, key=lambda w: w["pf"])
    say(f"    Worst window overall: {worst_w['start']}..{worst_w['end']} ({worst_w['len']} mo) PF {worst_w['pf']:.3f} "
        f"DD {worst_w['max_dd']:.2f}% | best: {best_w['start']}..{best_w['end']} ({best_w['len']} mo) PF {best_w['pf']:.3f}")

    # ---- 5. month-block reorderings -------------------------------------------
    n_blocks = len(set(t["date"][:7] for t in full))
    say(f"\n[5] MONTH-BLOCK REORDERINGS - {args.reorders} random orderings of the {n_blocks} "
        "month blocks, each month's internal trade sequence preserved, percent-return recompounding from $50K")
    mc = block_reorder_mc(full, args.reorders, SEED, ACCOUNT_SIZE)
    say(fmt_dist("PF", mc["pf"]))
    say(fmt_dist("WR%", mc["wr"]))
    say(fmt_dist("net%", mc["net_pct"]))
    say(fmt_dist("maxDD%", mc["max_dd"], worst_is_max=True))
    say(f"    lose-strk  longest run of consecutive losing months along a path: median "
        f"{mc['losing_month_streak']['median']}  worst {mc['losing_month_streak']['max']}")
    say(f"    Paths whose maxDD >= {WALL_PCT}% wall: {mc['pct_paths_breaching_wall']:.1f}% | paths ending underwater: "
        f"{mc['pct_paths_underwater']:.1f}%")
    say(f"    Real-order maxDD for comparison: {bs['max_dd']:.2f}%  (PF/WR are near-invariant under reordering by "
        "construction - only compounding moves them; the reordering test is about drawdown/path)")
    tl = rc.monte_carlo_drawdown_pct(full, ACCOUNT_SIZE, n_shuffles=args.reorders, seed=SEED)
    say(f"    Reference - individual-trade shuffle (calibrator method, same seed): median DD {tl['median_max_dd_pct']}% "
        f"worst-5% {tl['worst_5pct_dd_pct']}% abs-worst {tl['worst_dd_pct']}%")

    say("\n" + "=" * 100)
    say("SCOPE: every run above re-slices the same 2022-11 -> 2026-09 dataset, a single sustained gold-bull regime.")
    say("This measures sensitivity to WHICH part of that regime LSC sees and in WHAT order. It is NOT cross-regime")
    say("robustness. That remains open pending real 2013-2018 data access.")
    say("=" * 100)

    if args.out:
        Path(args.out).write_text("\n".join(lines) + "\n", encoding="utf-8")
        with open(Path(args.out).with_suffix(".json"), "w") as f:
            json.dump({"baseline": bs, "months": month_stats, "rolling6": roll, "all_windows": allw,
                       "reorder_mc": mc, "trade_shuffle": tl}, f, default=float, indent=1)


if __name__ == "__main__":
    main()
