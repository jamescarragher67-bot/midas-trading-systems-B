"""
research/tools/five_gate_replay_live_vs_backtest.py - live-vs-backtest replay
for the 5-gate voting engine, on the same discipline that caught LSC's
session-hour/cooldown/fetch-window bugs (research/tools/replay_live_vs_backtest.py).

The 5-gate engine was never wired into main.py, so there is no existing
"live" code path to audit - Step 1 of this task IS building that path (as it
would have to be built for a real promotion to live) and proving it against
the validated backtest, bar by bar, exactly as MT5 would feed it.

ARCHITECTURE, and why it differs from LSC's replay mechanics:

LSC's strategy/lsc_m15.py::check_entry(df, i, last_trade_bar, ...) takes an
EXPLICIT absolute bar index i, so main.py can hand it a fetched frame that
still has a trailing "forming" bar (MT5's copy_rates_from_pos convention:
the last row is always the currently-forming, not-yet-closed bar) without
confusing the strategy - check_entry only ever touches index i, ignoring
anything after it.

The 5-gate voters (strategy/ema_stack.py etc., faithfully reproduced in
research/strategy/five_gate_voter.py::compute_votes) do NOT take an index -
they read `.iloc[-1]` / `.iloc[-2]` RELATIVE to whatever frame is handed in.
For a live frame to reproduce the backtest's `df.iloc[:i+1]` slice (EMA
Stack/RSI Extreme/ATR Expansion read row i-1 via -2; Prev Day Structure
reads row i itself via -1 - a real, pre-existing one-bar lag mismatch
BETWEEN VOTERS that is faithfully reproduced here, not introduced by this
tool), the frame's LAST ROW must be the just-closed decision bar i with
NOTHING after it. So this replay's fetch window is `raw[i-N+1 : i+1]`
(bounded to at most N rows, ending AT i) - no forming-bar trim needed,
unlike LSC, because there is no forming bar in the frame to begin with.

WHAT THIS TESTS, matching the task's checklist:
  1. Bar-fetch window sufficiency (N): compute_votes needs EMA50/RSI14/
     ATR14/ATR14-MA20 to match their full-history values, AND Prev Day
     Structure needs a COMPLETE previous calendar day inside the window.
     Swept over candidate N until the live (bounded-window) direction
     sequence exactly matches the full-history (unbounded) ground truth.
  2. Cooldown coordinate handling: the 5-gate engine has NO daily-cap
     check (confirmed by reading backtest/engine.py - MAX_TRADES_PER_DAY
     does not exist in it, unlike LSC), so the only stateful gate is
     cooldown (`i - last_trade_bar < COOLDOWN_BARS`). This replay carries
     a private bot-uptime counter (reset to 0 at an arbitrary mid-history
     start, exactly like LSC's replay resetting main._bar_index) and
     tests BOTH a correct implementation (comparing two values in the same
     counter space - no translation needed since 5-gate's cooldown check
     lives outside any frame-indexed callback) and a deliberately naive one
     that mixes the bounded frame's local length into the comparison, to
     prove by demonstration - not just architecture - that the fix matters.
  3. Session-hour timing: uses the decision bar's OWN timestamp throughout
     (this is an offline replay over frozen bars - there is no wall clock
     to accidentally consult, so this class of bug cannot reappear here,
     but the hours actually used are reported for the record).
  4. GBPJPY-specific: digits/point/contract come from mt5.symbol_info via
     the frozen pickle, not hardcoded; smart_sl rounds at that digit count.

A SEPARATE finding, not on the task's checklist but surfaced by building
this: the original backtest computes SL/TP using the ACTUAL next bar's open
(`entry_price = df.iloc[entry_idx+1]["open"]`, passed into smart_sl) - a
price a live bot cannot know until AFTER it has already decided to trade.
A live implementation must estimate entry with the last CLOSED bar's close
instead. This tool's live driver does that, and separately reports how far
that estimate lands from the backtest's true-open based SL/TP - direction
is unaffected (it depends only on closed-bar history), but SL distance,
and rarely whether smart_sl's structural/ATR branch is chosen, can differ.

Usage:
    python research/tools/five_gate_replay_live_vs_backtest.py --symbol XAUUSD.a --m5-dir <dir>
    python research/tools/five_gate_replay_live_vs_backtest.py --symbol GBPJPY.a --m5-dir <dir>
"""
import sys, os, argparse, pickle, time
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
import numpy as np
import pandas as pd
from research.strategy import five_gate_voter as fg

CANDIDATE_N = [300, 400, 500, 600, 800, 1000]
SAFETY_MARGIN_BARS = 200   # added on top of the smallest N that matches, same spirit as LSC's 150->300


def ground_truth_signals(raw, digits):
    """Full-history vectorised backtest: every (direction, sl, tp) the
    validated engine would produce, keyed by the ENTRY bar (i+1) exactly as
    backtest/engine.py records trades. mode='standard' sizing is irrelevant
    to direction/sl/tp - specs are dummy USD numbers so run() doesn't error."""
    dummy_specs = (0.01 if digits <= 2 else 10 ** -digits, 100.0, 0.01, 50.0, 0.01)
    trades, diag = fg.run(raw, dummy_specs, digits, mode="standard", risk_pct=0.1,
                          initial_balance=50000.0, spread_points=0.0)
    sigs = {}
    for t in trades:
        key = f"{t['date']} {t['time']}"
        sigs[key] = (t["direction"], t["sl"], t["tp"])
    return sigs, diag["n_signals"]


def live_replay(raw, digits, N, cooldown_mode="correct", start=None, stop=None):
    """Bar-by-bar incremental replay. cooldown_mode: 'correct' compares the
    bot's own uptime counter against itself; 'naive' deliberately mixes the
    bounded frame's local length in, reproducing the exact class of bug
    found in main.py before 2026-09-15."""
    n = len(raw)
    start = start or max(N + fg.MIN_LOOKBACK, 2000)
    stop = stop or (n - 1)
    highs, lows, opens, closes = (raw[c].to_numpy(float) for c in ("high", "low", "open", "close"))
    idx = raw.index

    bot_bar_index = 0          # increments once per decision bar this "bot" has been running
    last_trade_bar = -fg.COOLDOWN_BARS
    live_signals = {}
    sl_estimates = {}          # key -> (sl_live, tp_live, atr, branch)
    signal_exists_flip = 0
    t0 = time.time()

    for i in range(start, stop):     # i = decision bar (just closed); entry would be bar i+1
        frame = raw.iloc[max(0, i - N + 1): i + 1]        # ends exactly at row i, bounded to N rows
        bot_bar_index += 1

        gap = bot_bar_index - 1 - last_trade_bar          # "correct": both counters in bot-uptime units
        local_i = len(frame) - 1
        gap_naive = local_i - last_trade_bar              # "naive": mixes frame-local length with the global counter
        cooling = (gap < fg.COOLDOWN_BARS) if cooldown_mode == "correct" else (gap_naive < fg.COOLDOWN_BARS)
        if cooling:
            continue

        if idx[i].hour not in fg.SESSION_HOURS:
            continue

        fdf = fg.add_indicators(frame)
        votes = fg.compute_votes(fdf)
        direction = int(votes["direction"].iloc[-1])
        if direction == 0:
            continue

        dirn = "BUY" if direction > 0 else "SELL"
        atr = float(fdf["atr"].iloc[-1])
        entry_estimate = float(closes[i])                  # last closed bar's close - all a live bot can know
        sl_live, method_live = fg.smart_sl(fdf, local_i, dirn, entry_estimate, atr, digits)
        sl_dist_live = (entry_estimate - sl_live) if dirn == "BUY" else (sl_live - entry_estimate)

        entry_real = float(opens[i + 1])                    # what the backtest actually uses (future, unknowable live)
        sl_real, method_real = fg.smart_sl(fdf, local_i, dirn, entry_real, atr, digits)
        sl_dist_real = (entry_real - sl_real) if dirn == "BUY" else (sl_real - entry_real)

        key = f"{idx[i+1].strftime('%Y-%m-%d')} {idx[i+1].strftime('%H:%M')}"
        live_exists = sl_dist_live > 0
        real_exists = sl_dist_real > 0
        if live_exists != real_exists:
            signal_exists_flip += 1
        if live_exists:
            live_signals[key] = dirn
            tp_live = entry_estimate + sl_dist_live * fg.REWARD_RATIO if dirn == "BUY" else entry_estimate - sl_dist_live * fg.REWARD_RATIO
            sl_estimates[key] = (round(sl_live, digits), round(tp_live, digits), method_live,
                                round(sl_real, digits) if real_exists else None, method_real, atr)
            last_trade_bar = bot_bar_index - 1

    return live_signals, sl_estimates, signal_exists_flip, time.time() - t0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--m5-dir", required=True)
    ap.add_argument("--sweep-bars", type=int, default=4000,
                    help="how many decision bars to evaluate per candidate N during the window sweep "
                         "(default 4000 ~ 14-15 M5 days, spans 2+ weekends to stress the prior-day-completeness case)")
    ap.add_argument("--proof-bars", type=int, default=20000,
                    help="how many decision bars to evaluate for the final proof / cooldown ablation / SL fidelity sections")
    args = ap.parse_args()

    blob = pickle.load(open(os.path.join(args.m5_dir, f"frozen_m5_{args.symbol}.pkl"), "rb"))
    raw = blob["df"][["open", "high", "low", "close"]].copy()
    digits = blob["info"]["digits"]
    point, contract = blob["specs"][0], blob["specs"][1]

    report = os.path.join(ROOT, "research", f"five_gate_replay_{args.symbol}_2026-09-16.txt")
    log = open(report, "w", encoding="utf-8")
    def say(s=""): print(s); log.write(s + "\n"); log.flush()

    say("=" * 100)
    say(f"5-GATE LIVE-VS-BACKTEST REPLAY - {args.symbol}")
    say("=" * 100)
    say(f"bars {len(raw)} | {raw.index[0]} -> {raw.index[-1]} | digits={digits} point={point} contract={contract}")
    say(f"session hours used (bar's own timestamp, never wall clock): {sorted(fg.SESSION_HOURS)}")
    say(f"5-gate engine has NO daily trade cap (confirmed absent from backtest/engine.py, unlike LSC) - "
        f"the only stateful live gate is cooldown ({fg.COOLDOWN_BARS} bars).\n")

    gt_signals, n_gt_signals = ground_truth_signals(raw, digits)
    gt_dir_full = {k: v[0] for k, v in gt_signals.items()}
    say(f"Ground truth (full-history vectorised backtest, whole dataset): {n_gt_signals} raw signals, {len(gt_signals)} converted to trades "
        f"({n_gt_signals - len(gt_signals)} dropped for sl_dist<=0)")

    sweep_start = max(CANDIDATE_N[-1] + fg.MIN_LOOKBACK, len(raw) - args.sweep_bars - 200)
    sweep_stop = sweep_start + args.sweep_bars
    say(f"\n1) BAR-FETCH WINDOW SWEEP (correct cooldown handling), decision bars [{sweep_start}:{sweep_stop}] "
        f"({raw.index[sweep_start]} -> {raw.index[sweep_stop]}) - direction-sequence match vs ground truth on that same range:")
    gt_dir = {k: v for k, v in gt_dir_full.items()
             if raw.index[sweep_start] <= pd.Timestamp(k) <= raw.index[min(sweep_stop, len(raw) - 1)]}
    best_n = None
    for N in CANDIDATE_N:
        live_sig, _, flips, dt = live_replay(raw, digits, N, cooldown_mode="correct", start=sweep_start, stop=sweep_stop)
        only_live = set(live_sig) - set(gt_dir)
        only_gt = set(gt_dir) - set(live_sig)
        mismatched_dir = sum(1 for k in set(live_sig) & set(gt_dir) if live_sig[k] != gt_dir[k])
        match = (not only_live) and (not only_gt) and (mismatched_dir == 0)
        say(f"  N={N:>5}  live_signals={len(live_sig):>4}  gt_signals={len(gt_dir):>4}  "
            f"only_live={len(only_live):>3}  only_gt={len(only_gt):>3}  dir_mismatch={mismatched_dir:>3}  "
            f"sl_dist_flip={flips:>3}  {'MATCH' if match else 'diff'}  [{dt:.0f}s]")
        if match and best_n is None:
            best_n = N
    if best_n is None:
        say("  NO candidate N produced an exact match up to 1200 bars - see largest N's discrepancies below.")
    else:
        recommended = best_n  # report the smallest exact match; margin discussed separately
        bars_per_day = len(raw) / max((raw.index[-1] - raw.index[0]).days, 1)
        say(f"  -> smallest N with an exact direction-sequence match: {best_n} bars "
            f"(~{best_n / bars_per_day:.1f} days of M5 at this instrument's bar density, {bars_per_day:.0f} bars/day)")
        say(f"  -> RECOMMENDED live BARS_TO_FETCH (with the same kind of safety margin LSC's 150->300 fix used): "
            f"{best_n + SAFETY_MARGIN_BARS}")

    N_final = (best_n or CANDIDATE_N[-1]) + SAFETY_MARGIN_BARS
    proof_start = max(N_final + fg.MIN_LOOKBACK, len(raw) - args.proof_bars - 200)
    proof_stop = len(raw) - 1
    gt_dir_proof = {k: v for k, v in gt_dir_full.items()
                   if raw.index[proof_start] <= pd.Timestamp(k) <= raw.index[proof_stop]}
    say(f"\n2) COOLDOWN COORDINATE HANDLING at N={N_final}, decision bars [{proof_start}:{proof_stop}] "
        f"({raw.index[proof_start]} -> {raw.index[proof_stop]}) - correct vs deliberately naive (LSC-bug-class) implementation:")
    for mode in ("correct", "naive"):
        live_sig, _, flips, dt = live_replay(raw, digits, N_final, cooldown_mode=mode, start=proof_start, stop=proof_stop)
        only_live = set(live_sig) - set(gt_dir_proof); only_gt = set(gt_dir_proof) - set(live_sig)
        mismatched_dir = sum(1 for k in set(live_sig) & set(gt_dir_proof) if live_sig[k] != gt_dir_proof[k])
        match = (not only_live) and (not only_gt) and (mismatched_dir == 0)
        say(f"  {mode:<8}  live_signals={len(live_sig):>4}  gt_signals={len(gt_dir_proof):>4}  only_live={len(only_live):>3}  "
            f"only_gt={len(only_gt):>3}  dir_mismatch={mismatched_dir:>3}  {'MATCH' if match else 'BROKEN'}  [{dt:.0f}s]")
        if mode == "naive" and not match:
            say(f"    (confirms the mixed-coordinate bug class would have broken this engine too, exactly as it did main.py for LSC)")

    say(f"\n3) FULL PROOF at recommended settings (N={N_final}, correct cooldown, session on bar's own timestamp), "
        f"same decision-bar range as section 2:")
    live_sig, sl_map, flips, dt = live_replay(raw, digits, N_final, cooldown_mode="correct", start=proof_start, stop=proof_stop)
    gt_dir = gt_dir_proof
    identical = live_sig == gt_dir
    say(f"  backtest entries: {len(gt_dir)} | live-replayed entries: {len(live_sig)} | sequence identical (direction): {identical}")
    if not identical:
        for k in sorted(set(gt_dir) - set(live_sig))[:10]: say(f"    backtest-only: {k} {gt_dir[k]}")
        for k in sorted(set(live_sig) - set(gt_dir))[:10]: say(f"    live-only:     {k} {live_sig[k]}")
        for k in sorted(set(live_sig) & set(gt_dir)):
            if live_sig[k] != gt_dir[k]: say(f"    direction differs: {k} live={live_sig[k]} backtest={gt_dir[k]}")

    say(f"\n4) SL/TP FIDELITY (separate finding: backtest sizes stops off the REAL next-bar open, a live bot can only "
        f"estimate with the last closed bar's close):")
    if sl_map:
        sl_diffs, tp_flags, branch_flips = [], 0, 0
        for k, (sl_l, tp_l, m_l, sl_r, m_r, atr) in sl_map.items():
            if sl_r is None: continue
            sl_diffs.append(abs(sl_l - sl_r) / atr if atr > 0 else 0)
            if m_l != m_r: branch_flips += 1
        sl_diffs = np.array(sl_diffs)
        say(f"  matched signals compared: {len(sl_diffs)} | signal-existence flips (sl_dist<=0 boundary): {flips}")
        say(f"  |SL_live - SL_backtest| in ATR units: median {np.median(sl_diffs):.4f}  p90 {np.percentile(sl_diffs,90):.4f}  "
            f"max {np.max(sl_diffs):.4f}")
        say(f"  smart_sl branch (structural vs atr) flipped between live-estimate and real-open: {branch_flips}/{len(sl_diffs)} "
            f"({branch_flips/len(sl_diffs)*100:.1f}%)")
        say(f"  CONCLUSION: direction is entry-price-independent and unaffected; SL/TP levels a live bot would actually use "
            f"differ from the backtest's idealised (real-open) levels by a small, quantified amount - this is a genuine gap "
            f"between what was validated and what live trading can achieve, not a bug to fix in this engine's code.")
    else:
        say("  no matched signals to compare (N sweep did not converge - see section 1)")

    say(f"\n{args.symbol} instrument-specific check: digits={digits}, point={point}, contract={contract} read live from "
        f"mt5.symbol_info via the frozen pickle (not hardcoded); smart_sl and all trade fields round at `digits`.")
    say(f"\nSaved research/five_gate_replay_{args.symbol}_2026-09-16.txt")
    log.close()


if __name__ == "__main__":
    main()
