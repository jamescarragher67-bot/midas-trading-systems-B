"""
tools/risk_calibrator.py - Prop-firm risk-per-trade calibration for LSC (Stage 4).

Sweeps risk-per-trade levels for strategy/lsc_m15.py through a 1000-path
Monte Carlo drawdown stress test, to find the highest risk-per-trade that
keeps worst-case drawdown comfortably clear of a hard prop-firm
max-total-loss ceiling - a breach of that line ends the account, so this is
solved for margin, not for the wall.

TAIL METRIC - changed 2026-09-15, read this before comparing to older runs:
The decision metric is now MONTH-BLOCK REORDERING
(research/backtest/monte_carlo.monte_carlo_block_reorder_pct): the trade
sequence is cut into calendar-month blocks, the block order is shuffled
with every month's internal sequence preserved, and percent returns are
recompounded along each path. Every earlier calibration (including the
0.045% figure set on 2026-09-01) used an individual-trade shuffle
(monte_carlo_drawdown_pct below, kept for comparison only). That shuffle
destroys LSC's real month-level loss clustering and was shown by
research/tools/rolling_month_backtest.py (research/lsc_rolling_month_2026-09-15.txt)
to understate the tail by roughly 2x: at 0.045% it reported a 95th-pct
max DD of 3.7% / worst 5.3%, while month-block reordering gives 6.4% /
9.1% with 8.6% of paths breaching the 6% wall - and the actual historical
order reached 5.99%. A level now passes only if the block-reordered
95th-pct AND absolute-worst drawdowns clear the wall by the usual safety
ratios AND the real historical order does too (the real order is one
legitimate ordering; a level whose own history breached the wall cannot
be recommended whatever the simulation says).

DATA SOURCE - read this before trusting the numbers:
There is no saved trade log, CSV, or Monte Carlo output anywhere in this
repo from the prior $25k-account testing that found a 46.7% worst-5th-
percentile drawdown at 1.0% risk. That run's exact backtest window and
max-lot-cap value only ever existed in a prior chat session, never
persisted to disk. Rather than guess at that window, this script
re-derives the trade sequence from the unchanged strategy code.

MAX_BARS is set to 90,000 - the terminal's verified true ceiling for this
symbol/timeframe (100,000 returns "Invalid params"). An earlier version of
this script used 75,000 bars (backtest/lsc_engine.py's own hardcoded fetch
cap), because that window reproduces lsc_m15.py's documented validation
numbers almost exactly (2210 vs ~2200 total trades, early half 1105/PF
1.24 vs documented ~1100/1.23, recent half 1105/PF 1.15 vs documented
~1100/1.21). That match only proves the strategy CODE hasn't changed - it
does not mean 75,000 bars is the right window to size real risk on.

Investigated directly (2026-09-01): the extra ~15,000 bars 75,000 excludes
cover 2022-11-08 to 2023-06-29. A monthly PF breakdown of the full 90,000-
bar set shows that period averaging PF 0.82 (a real losing stretch,
dominated by Feb 2023 at PF 0.48/23% win rate) - but the "kept" 75,000-bar
window has its OWN comparably bad stretches scattered throughout its
history (e.g. Nov 2025: PF 0.61, -$8,634, worse than any single month in
the excluded period). Checked bar-level data quality (zero-range bars,
zero volume, spread, tick volume) in the excluded period against the kept
window's own start - no anomalies found. There is no comment anywhere in
lsc_engine.py explaining why 75,000 specifically, and no data-quality
justification found for excluding that period. Conclusion: the 75,000-bar
cap is an incidental engineering safety limit (guarding against
copy_rates_range failing on very large multi-year spans - see that
function's comment), not a deliberate exclusion of a disqualified regime.
The strategy's edge shows real month-to-month variance with multi-month
losing stretches THROUGHOUT its history, not just in the older excluded
data - concerning in the sense that real money should be sized for that
variance, but not a sign the excluded data reveals some different,
disqualifying regime. Given no principled reason to drop it, this script
now calibrates on the full 90,000-bar set as the more complete, more
conservative, and more defensible basis - see run_fidelity_check() for
the comparison this conclusion is based on.

ASSUMPTIONS - revisit if the funded account differs from what's below:
  - Margin-safe lot cap: leverage=50, 25% equity margin budget, matching
    risk/trade_manager.py's live formula and config/settings.py's
    MARGIN_SAFETY_BUDGET_PCT. Leverage 50 is FTMO's Standard-account
    XAUUSD leverage, confirmed directly from FTMO's own trading-update
    posts (Standard 1:50 / Swing 1:15, effective 1 Feb 2026; checked all
    posts through 27 Aug 2026 - the most recent at the time of writing -
    for any later change, found none). Standard, not Swing, matches LSC's
    behavior: it force-exits within ~1 day (MAX_HOLD_BARS) and flattens
    before the weekend, it never actually holds swing positions. The
    report states whether this cap actually bound any trades at the
    tested risk levels; if it never binds, the exact leverage value is
    moot.
  - Spread: 18 points, per config/settings.py's comment ("LSC backtests
    used an 18pt assumption").
  - Backtest window: the full 90,000 bars the terminal will serve for
    this symbol/timeframe (see above) - not the narrower window that
    happens to match the originally-quoted 46.7% figure, since that
    window's exact bounds were never recoverable (see DATA SOURCE above).
"""

import argparse
import pickle
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import numpy as np
import pandas as pd
import MetaTrader5 as mt5

from strategy.lsc_m15 import precompute, check_entry
from backtest.lsc_engine import compute_atr14, get_symbol_specs, SESSION_HOURS, MAX_HOLD_BARS, MIN_LOOKBACK
from research.backtest.metrics import calculate_metrics
from research.backtest.monte_carlo import monte_carlo_block_reorder_pct

SYMBOL              = "XAUUSD.a"
MAX_BARS            = 90000     # verified true ceiling for this symbol/TF - see module docstring
INITIAL_BALANCE     = 25000.0
SPREAD_POINTS       = 18        # config/settings.py's documented backtest assumption
COOLDOWN_BARS       = 3
MAX_TRADES_PER_DAY  = 4
MARGIN_BUDGET_PCT   = 0.25      # matches config.settings.MARGIN_SAFETY_BUDGET_PCT
LEVERAGE_ASSUMED    = 50        # FTMO Standard XAUUSD leverage, confirmed - see module docstring
N_SHUFFLES          = 1000
SEED                = 42
# The absolute-worst-of-1000 is a single order statistic and moves by up to
# ~1 point with the seed (checked 2026-09-15: 0.0175% gave worst 4.94-6.05%
# across 8 seeds). The pass/fail decision therefore takes the WORST value
# of each tail statistic over SEED plus these extra seeds; the table shows
# SEED's own distribution and the max-over-seeds columns side by side.
EXTRA_SEEDS         = (1, 7, 99, 123)

RISK_LEVELS = [0.1, 0.075, 0.065, 0.06, 0.05, 0.045, 0.04, 0.035, 0.03, 0.0275, 0.025, 0.0225, 0.02, 0.0175, 0.015, 0.01, 0.0075, 0.005]

# Safety-margin ratios applied to whatever total-loss wall is passed via
# --total-wall-pct (not hardcoded to one firm's number). Same ratios used
# throughout this tool's history (7/10=0.70, 9.5/10=0.95 for the original
# 10%-wall pass): worst-5th-pct should sit comfortably inside the wall, the
# rare 1-in-1000 case should still clear it with some margin, not ride
# right up against the line.
WORST5_MARGIN_RATIO    = 0.70
ABS_WORST_MARGIN_RATIO = 0.95


def load_frozen(path: str):
    """Frozen bars pickle ({'df', 'specs'}) as written by rolling_month_backtest.py -
    lets a calibration be re-run on the exact bars of a saved validation
    instead of whatever the terminal serves today."""
    with open(path, "rb") as f:
        blob = pickle.load(f)
    df = blob["df"][["open", "high", "low", "close"]].copy()
    df["atr"] = compute_atr14(df)
    df = precompute(df)
    return df, blob["specs"]


def fetch_history(symbol: str) -> pd.DataFrame:
    if not mt5.initialize():
        raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")
    if not mt5.symbol_select(symbol, True):
        raise RuntimeError(f"symbol_select({symbol}) failed: {mt5.last_error()}")
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M15, 0, MAX_BARS)
    if rates is None or len(rates) == 0:
        raise RuntimeError(f"No data returned for {symbol}: {mt5.last_error()}")
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    df.set_index("time", inplace=True)
    df["atr"] = compute_atr14(df)
    df = precompute(df)
    return df


def margin_safe_max_lot(balance: float, price: float, contract_size: float) -> float:
    return (balance * MARGIN_BUDGET_PCT * LEVERAGE_ASSUMED) / (contract_size * price)


def _simulate_trade(df: pd.DataFrame, entry_idx: int, direction: str, sl: float, tp: float,
                     balance: float, risk_pct: float, point: float, contract_size: float,
                     vol_min: float, vol_max: float, vol_step: float) -> dict | None:
    if entry_idx + 1 >= len(df):
        return None

    entry_candle = df.iloc[entry_idx + 1]
    entry_price  = float(entry_candle["open"])
    sl_dist = (entry_price - sl) if direction == "BUY" else (sl - entry_price)
    if sl_dist <= 0:
        return None

    risk_amt  = balance * (risk_pct / 100)
    sl_points = sl_dist / point
    raw_lot   = risk_amt / (sl_points * contract_size * point)
    cap       = margin_safe_max_lot(balance, entry_price, contract_size)
    lot       = min(raw_lot, cap, vol_max)
    lot       = max(lot, vol_min)
    lot       = round(round(lot / vol_step) * vol_step, 2)
    margin_capped = raw_lot > cap
    # floor_clamped: the broker's minimum tradable lot forced the position
    # UP from what the risk% formula actually wanted - the account is
    # taking MORE risk on this trade than risk_pct intends. Distinct from
    # margin_capped, which forces lot DOWN (less risk than intended).
    floor_clamped = raw_lot < vol_min
    actual_risk_pct_this_trade = (lot * contract_size * sl_dist) / balance * 100 if balance > 0 else 0.0

    mult        = lot * contract_size
    spread_cost = SPREAD_POINTS * point * mult

    result, exit_price = None, None
    for j in range(entry_idx + 2, min(entry_idx + MAX_HOLD_BARS + 2, len(df))):
        c = df.iloc[j]
        high, low = float(c["high"]), float(c["low"])
        if direction == "BUY":
            if low <= sl:
                result, exit_price = "LOSS", sl; break
            if high >= tp:
                result, exit_price = "WIN", tp; break
        else:
            if high >= sl:
                result, exit_price = "LOSS", sl; break
            if low <= tp:
                result, exit_price = "WIN", tp; break

    if result is None:
        last = df.iloc[min(entry_idx + MAX_HOLD_BARS + 1, len(df) - 1)]
        exit_price = float(last["close"])

    raw_move = (exit_price - entry_price) if direction == "BUY" else (entry_price - exit_price)
    pnl    = raw_move * mult - spread_cost
    result = "WIN" if pnl > 0 else "LOSS"

    entry_time = df.index[entry_idx + 1]
    return {
        "date": entry_time.strftime("%Y-%m-%d"), "time": entry_time.strftime("%H:%M"),
        "direction": direction, "entry": round(entry_price, 2), "exit": round(exit_price, 2),
        "sl": round(sl, 2), "tp": round(tp, 2), "lots": lot,
        "spread_cost": round(spread_cost, 2), "pnl": round(pnl, 2), "result": result,
        "balance_after": round(balance + pnl, 2), "margin_capped": margin_capped,
        "floor_clamped": floor_clamped, "intended_risk_pct": risk_pct,
        "actual_risk_pct": round(actual_risk_pct_this_trade, 4),
    }


def run_backtest(df: pd.DataFrame, risk_pct: float, point: float, contract_size: float,
                  vol_min: float, vol_max: float, vol_step: float) -> list:
    trades         = []
    balance        = INITIAL_BALANCE
    current_date   = None
    trades_today   = 0
    last_trade_bar = -COOLDOWN_BARS

    for i in range(MIN_LOOKBACK, len(df) - 1):
        bar_time = df.index[i]
        bar_date = bar_time.date()
        if bar_date != current_date:
            current_date = bar_date
            trades_today = 0
        if bar_time.hour not in SESSION_HOURS:
            continue

        direction, _, sl, tp = check_entry(df, i, last_trade_bar, trades_today,
                                            COOLDOWN_BARS, MAX_TRADES_PER_DAY)
        if direction == "NEUTRAL":
            continue

        trade = _simulate_trade(df, i, direction, sl, tp, balance, risk_pct, point, contract_size,
                                 vol_min, vol_max, vol_step)
        if trade is None:
            continue

        balance               += trade["pnl"]
        trade["balance_after"] = round(balance, 2)
        trades.append(trade)
        last_trade_bar  = i
        trades_today   += 1

    return trades


DOC_TRADE_COUNT_APPROX = 2200   # documented in lsc_m15.py's docstring (the 75k-bar-equivalent window)


def run_fidelity_check(trades: list) -> dict:
    """Code-drift check, NOT a check on whether the full window is 'correct'.

    Compares the TRAILING ~2200 trades of this run (the same trade count as
    the 75,000-bar window that reproduces lsc_m15.py's documented validation
    numbers) against those documented early/recent-half figures (PF 1.23 /
    1.21). If strategy code were unchanged and MT5 serves the same recent
    history, this subset should closely match regardless of how much OLDER
    history this run additionally includes ahead of it.

    Separately reports the full-set early/recent split, which is expected
    to diverge from the documented numbers once older data is included -
    see the module docstring's investigation of why, and by how much.
    """
    n = len(trades)
    tail = trades[-DOC_TRADE_COUNT_APPROX:] if n >= DOC_TRADE_COUNT_APPROX else trades
    tail_half = len(tail) // 2
    tail_early, tail_recent = tail[:tail_half], tail[tail_half:]

    half = n // 2
    full_early, full_recent = trades[:half], trades[half:]

    m_tail_early  = calculate_metrics(tail_early, INITIAL_BALANCE)
    m_tail_recent = calculate_metrics(tail_recent, INITIAL_BALANCE)
    m_full_early  = calculate_metrics(full_early, INITIAL_BALANCE)
    m_full_recent = calculate_metrics(full_recent, INITIAL_BALANCE)
    m_all         = calculate_metrics(trades, INITIAL_BALANCE)

    return {
        "total_trades": n, "pf_all": m_all["profit_factor"], "win_rate": m_all["win_rate"],
        "tail_trades": len(tail),
        "pf_tail_early": m_tail_early["profit_factor"], "pf_tail_recent": m_tail_recent["profit_factor"],
        "pf_full_early": m_full_early["profit_factor"], "full_early_trades": len(full_early),
        "pf_full_recent": m_full_recent["profit_factor"], "full_recent_trades": len(full_recent),
        "margin_capped_count": sum(1 for t in trades if t["margin_capped"]),
    }


def monte_carlo_drawdown_pct(trades: list, initial_balance: float,
                              n_shuffles: int = N_SHUFFLES, seed: int = SEED) -> dict:
    """Percent-return, recompounding Monte Carlo - the correct method for a
    strategy that sizes positions as a % of a compounding balance.

    backtest/monte_carlo.py shuffles fixed DOLLAR P&L amounts. That is exact
    for a strategy with static position sizing, but LSC's position size
    (and therefore each trade's dollar P&L) is a function of the balance
    AT THE TIME the trade actually happened, which grew ~6x over this
    backtest. Reshuffling those dollar amounts effectively lets a
    late-sequence, large-balance-sized loss land against an early-sequence,
    small starting balance in unlucky shuffles - inflating tail drawdown
    with a disparity that never happens live, because live position size
    always recomputes from whatever the CURRENT balance actually is.

    This function instead shuffles each trade's REALISED PERCENT return
    (pnl / balance immediately before that trade) and recompounds
    multiplicatively along each shuffled path, matching how a live
    percent-risk account actually behaves under reordering.
    """
    balances_before = np.array([initial_balance] + [t["balance_after"] for t in trades[:-1]])
    pct_returns = np.array([t["pnl"] for t in trades]) / balances_before
    rng = np.random.default_rng(seed)

    max_dds = np.zeros(n_shuffles)
    final_balances = np.zeros(n_shuffles)

    for s in range(n_shuffles):
        shuffled = rng.permutation(pct_returns)
        balance  = initial_balance
        peak     = initial_balance
        max_dd_pct = 0.0
        for r in shuffled:
            balance *= (1 + r)
            if balance > peak:
                peak = balance
            dd_pct = (peak - balance) / peak * 100 if peak > 0 else 0.0
            if dd_pct > max_dd_pct:
                max_dd_pct = dd_pct
        max_dds[s] = max_dd_pct
        final_balances[s] = balance

    return {
        "n_shuffles":              n_shuffles,
        "median_max_dd_pct":       round(float(np.median(max_dds)), 1),
        "worst_5pct_dd_pct":       round(float(np.percentile(max_dds, 95)), 1),
        "worst_1pct_dd_pct":       round(float(np.percentile(max_dds, 99)), 1),
        "best_dd_pct":             round(float(np.min(max_dds)), 1),
        "worst_dd_pct":            round(float(np.max(max_dds)), 1),
        "median_final_balance":    round(float(np.median(final_balances)), 2),
        "pct_shuffles_underwater": round(float(np.mean(final_balances < initial_balance) * 100), 1),
    }


def verify_order_invariance(trades: list) -> bool:
    """PF/expectancy are sums/counts over the trade set - shuffling order cannot
    change them. Confirms that programmatically rather than asserting it."""
    base = calculate_metrics(trades, INITIAL_BALANCE)
    rng = np.random.default_rng(SEED)
    idx = np.arange(len(trades))
    for _ in range(5):
        shuffled_idx = rng.permutation(idx)
        shuffled = [trades[i] for i in shuffled_idx]
        m = calculate_metrics(shuffled, INITIAL_BALANCE)
        if m["profit_factor"] != base["profit_factor"] or m["expectancy"] != base["expectancy"]:
            return False
    return True


def main():
    global INITIAL_BALANCE, LEVERAGE_ASSUMED

    parser = argparse.ArgumentParser(description="LSC risk-per-trade calibration for a prop-firm funded account.")
    parser.add_argument("--account-size", type=float, default=INITIAL_BALANCE,
                        help="Account size in USD. Drives the actual simulation (position sizing, "
                             "lot floor/margin effects), not just the final dollar-scaling display.")
    parser.add_argument("--leverage", type=float, default=LEVERAGE_ASSUMED,
                        help="Leverage for the margin-safe lot cap, e.g. the broker/firm's confirmed XAUUSD leverage.")
    parser.add_argument("--total-wall-pct", type=float, default=10.0,
                        help="Hard total-loss ceiling to solve backward from (trailing peak-to-trough).")
    parser.add_argument("--bars", default=None,
                        help="Frozen bars pickle ({'df','specs'}); skips the MT5 fetch so the run is reproducible.")
    args = parser.parse_args()

    INITIAL_BALANCE  = args.account_size
    LEVERAGE_ASSUMED = args.leverage
    PROP_MAX_LOSS_PCT    = args.total_wall_pct
    TARGET_WORST5_PCT    = round(PROP_MAX_LOSS_PCT * WORST5_MARGIN_RATIO, 2)
    ABS_WORST_TARGET_PCT = round(PROP_MAX_LOSS_PCT * ABS_WORST_MARGIN_RATIO, 2)

    print("=" * 70)
    if args.bars:
        print(f"LSC RISK CALIBRATOR - frozen bars from {args.bars}")
        print("=" * 70)
        df, (point, contract_size, vol_min, vol_max, vol_step) = load_frozen(args.bars)
    else:
        print("LSC RISK CALIBRATOR - fetching full XAUUSD.a M15 history from MT5")
        print("=" * 70)
        df = fetch_history(SYMBOL)
        point, contract_size = get_symbol_specs(SYMBOL)
        info = mt5.symbol_info(SYMBOL)
        vol_min, vol_max, vol_step = info.volume_min, info.volume_max, info.volume_step
    print(f"Bars: {len(df)} | {df.index[0]}  ->  {df.index[-1]}")
    print(f"Account size: ${INITIAL_BALANCE:,.0f}  |  Leverage: {LEVERAGE_ASSUMED}:1  |  "
          f"Total-loss wall: {PROP_MAX_LOSS_PCT}%  (target 95th-pct: {TARGET_WORST5_PCT}%, "
          f"target abs-worst and real order: {ABS_WORST_TARGET_PCT}%)")
    print(f"point={point} contract_size={contract_size} vol_min={vol_min} vol_max={vol_max} vol_step={vol_step}")
    print(f"Margin-safe cap: leverage={LEVERAGE_ASSUMED}:1, "
          f"{MARGIN_BUDGET_PCT*100:.0f}% equity budget\n")

    # ---- Fidelity check at 1.0% risk before trusting anything further ----
    print("-" * 70)
    print("FIDELITY CHECK (1.0% risk) - two separate comparisons:")
    print("  (a) code-drift check: trailing ~2200 trades vs. lsc_m15.py's")
    print("      documented ~1100/~1100 halves, PF 1.23 early / 1.21 recent")
    print("  (b) full-set early/recent halves - EXPECTED to diverge from (a)")
    print("      once older data is included; see module docstring for why")
    print("-" * 70)
    baseline_trades = run_backtest(df, 1.0, point, contract_size, vol_min, vol_max, vol_step)
    fc = run_fidelity_check(baseline_trades)
    print(f"  Total trades (full {MAX_BARS}-bar set): {fc['total_trades']}")
    print(f"  Overall PF (full set):  {fc['pf_all']}   Win rate: {fc['win_rate']}%")
    print(f"  (a) Trailing {fc['tail_trades']} trades: early-half PF {fc['pf_tail_early']} "
          f"(documented ~1100, PF 1.23) | recent-half PF {fc['pf_tail_recent']} (documented ~1100, PF 1.21)")
    print(f"  (b) Full-set halves:    {fc['full_early_trades']} trades PF {fc['pf_full_early']} (early) | "
          f"{fc['full_recent_trades']} trades PF {fc['pf_full_recent']} (recent)")
    print(f"  Margin-capped trades: {fc['margin_capped_count']} / {fc['total_trades']}")

    DOC_PF_EARLY, DOC_PF_RECENT, DOC_TOLERANCE = 1.23, 1.21, 0.15
    code_drift = (
        abs(fc["pf_tail_early"] - DOC_PF_EARLY) > DOC_TOLERANCE
        or abs(fc["pf_tail_recent"] - DOC_PF_RECENT) > DOC_TOLERANCE
        or (fc["pf_tail_early"] < 1.0) != (DOC_PF_EARLY < 1.0)
        or (fc["pf_tail_recent"] < 1.0) != (DOC_PF_RECENT < 1.0)
    )
    if code_drift:
        print("\n*** CODE-DRIFT CHECK FAILED ***")
        print("The trailing-trades subset (same trade count as the historically-")
        print("validated window) no longer matches the documented PF ballpark.")
        print("This means the strategy is behaving differently than what was")
        print("validated - possibly a code or data change since Aug 2 2026.")
        print("STOPPING before the risk sweep. Investigate before re-running.")
        return

    print("\nCode-drift check passed (trailing-trades subset matches documented")
    print("PF ballpark) - strategy code and recent data are consistent with the")
    print("Aug 2 2026 validation. Calibrating on the full set. Proceeding.\n")

    # ---- Order-invariance sanity check ----
    invariant = verify_order_invariance(baseline_trades)
    print(f"Order-invariance check (PF/expectancy unchanged across 5 random shuffles): "
          f"{'PASS' if invariant else 'FAIL'}\n")

    # ---- Full sweep ----
    print("=" * 70)
    print(f"MONTE CARLO SWEEP - {N_SHUFFLES} month-block reorderings per risk level, ${INITIAL_BALANCE:,.0f} start")
    print("=" * 70)

    print("Decision metric: MONTH-BLOCK REORDERING (calendar-month blocks shuffled,")
    print("each month's internal trade sequence preserved, percent-return")
    print("recompounding). The individual-trade shuffle every earlier calibration")
    print("used is printed alongside as 'trade-shuffle' for comparison only - it")
    print("understates the tail (see module docstring). 'histDD' is the real")
    print("historical order's max drawdown at that risk level.\n")

    results = []
    for risk_pct in RISK_LEVELS:
        trades = baseline_trades if risk_pct == 1.0 else run_backtest(
            df, risk_pct, point, contract_size, vol_min, vol_max, vol_step)
        metrics = calculate_metrics(trades, INITIAL_BALANCE)
        mc_block = monte_carlo_block_reorder_pct(trades, INITIAL_BALANCE, n_reorders=N_SHUFFLES,
                                                 seed=SEED, wall_pct=PROP_MAX_LOSS_PCT)
        extra = [monte_carlo_block_reorder_pct(trades, INITIAL_BALANCE, n_reorders=N_SHUFFLES,
                                               seed=s, wall_pct=PROP_MAX_LOSS_PCT) for s in EXTRA_SEEDS]
        worst5_maxseed = max(m["worst_5pct_dd_pct"] for m in [mc_block] + extra)
        worst_maxseed  = max(m["worst_dd_pct"] for m in [mc_block] + extra)
        mc_trade = monte_carlo_drawdown_pct(trades, INITIAL_BALANCE, n_shuffles=N_SHUFFLES, seed=SEED)
        floor_clamped = [t for t in trades if t["floor_clamped"]]
        row = {
            "risk_pct": risk_pct,
            "trades": len(trades),
            "win_rate": metrics["win_rate"],
            "pf": metrics["profit_factor"],
            "expectancy": metrics["expectancy"],
            "actual_final_balance": metrics["final_balance"],
            "actual_total_return_pct": metrics["total_return"],
            "hist_dd_pct": metrics["max_drawdown_pct"],
            "mean_dd_pct": mc_block["mean_max_dd_pct"],
            "median_dd_pct": mc_block["median_max_dd_pct"],
            "worst5pct_dd_pct": mc_block["worst_5pct_dd_pct"],
            "worst1pct_dd_pct": mc_block["worst_1pct_dd_pct"],
            "absolute_worst_dd_pct": mc_block["worst_dd_pct"],
            "pct_paths_breaching_wall": mc_block["pct_paths_breaching_wall"],
            "worst5pct_dd_pct_maxseed": worst5_maxseed,
            "absolute_worst_dd_pct_maxseed": worst_maxseed,
            "trade_shuffle_worst5pct_dd_pct": mc_trade["worst_5pct_dd_pct"],
            "trade_shuffle_absolute_worst_dd_pct": mc_trade["worst_dd_pct"],
            "floor_clamped_count": len(floor_clamped),
            "floor_clamped_worst_actual_risk_pct": max((t["actual_risk_pct"] for t in floor_clamped), default=0.0),
            "margin_capped_count": sum(1 for t in trades if t["margin_capped"]),
        }
        results.append(row)
        print(f"  risk={risk_pct:>6}%  trades={row['trades']:>5}  PF={row['pf']:>5}  "
              f"histDD={row['hist_dd_pct']:>4}%  medianDD={row['median_dd_pct']:>5}%  "
              f"95thDD={row['worst5pct_dd_pct']:>5}% (max-seed {row['worst5pct_dd_pct_maxseed']:>5}%)  "
              f"worstDD={row['absolute_worst_dd_pct']:>5}% (max-seed {row['absolute_worst_dd_pct_maxseed']:>5}%)  "
              f"breach={row['pct_paths_breaching_wall']:>4}%  "
              f"floorClamped={row['floor_clamped_count']:>4}/{row['trades']}  "
              f"finalBal=${row['actual_final_balance']:,.0f}")

    # ---- Table ----
    print("\n" + "=" * 150)
    print(f"{'Risk%':>7} {'Trades':>6} {'WinRate':>8} {'PF':>6} {'Expect$':>8} "
          f"{'HistDD%':>8} {'MeanDD%':>8} {'MedDD%':>7} {'95thDD%':>8} {'99thDD%':>8} {'WorstDD%':>9} "
          f"{'95thMax':>8} {'WorstMax':>9} "
          f"{'Breach%':>8} {'tsh95th':>8} {'tshWorst':>9} {'Floor':>10} {'FinalBal$':>10}")
    for r in results:
        print(f"{r['risk_pct']:>7} {r['trades']:>6} {r['win_rate']:>7}% {r['pf']:>6} "
              f"{r['expectancy']:>8.2f} {r['hist_dd_pct']:>8} {r['mean_dd_pct']:>8} {r['median_dd_pct']:>7} "
              f"{r['worst5pct_dd_pct']:>8} {r['worst1pct_dd_pct']:>8} {r['absolute_worst_dd_pct']:>9} "
              f"{r['worst5pct_dd_pct_maxseed']:>8} {r['absolute_worst_dd_pct_maxseed']:>9} "
              f"{r['pct_paths_breaching_wall']:>8} {r['trade_shuffle_worst5pct_dd_pct']:>8} "
              f"{r['trade_shuffle_absolute_worst_dd_pct']:>9} "
              f"{str(r['floor_clamped_count']) + '/' + str(r['trades']):>10} {r['actual_final_balance']:>10,.0f}")
    print("=" * 150)
    print(f"HistDD = real historical order. MeanDD..WorstDD, Breach% = month-block reordering, seed {SEED}.")
    print(f"95thMax/WorstMax = worst of those two statistics over seeds {(SEED,) + EXTRA_SEEDS} - the DECISION columns.")
    print("tsh95th/tshWorst = individual-trade shuffle (superseded 2026-09-15, comparison only).")
    print("Floor = trades forced UP to the broker's minimum lot.")

    # ---- Solve backward ----
    print("\n" + "-" * 70)
    print(f"RECOMMENDATION - highest tested risk% where, on EVERY seed, the month-block-")
    print(f"reordered 95th-pct DD <= {TARGET_WORST5_PCT}% AND the absolute worst reordering "
          f"(1-in-{N_SHUFFLES}) <= {ABS_WORST_TARGET_PCT}%,")
    print(f"and the real historical order's DD <= {ABS_WORST_TARGET_PCT}%")
    print(f"(every tested tail case must clear the {PROP_MAX_LOSS_PCT}% hard wall with real")
    print("margin, not just the median - a breach at any probability ends the")
    print("account, so the rare 1-in-1000 case and the one ordering that actually")
    print("happened both matter)")
    print("-" * 70)

    passing = [r for r in results
               if r["worst5pct_dd_pct_maxseed"] <= TARGET_WORST5_PCT
               and r["absolute_worst_dd_pct_maxseed"] <= ABS_WORST_TARGET_PCT
               and r["hist_dd_pct"] <= ABS_WORST_TARGET_PCT]
    all_levels_fail_hard_wall = all(r["absolute_worst_dd_pct_maxseed"] > PROP_MAX_LOSS_PCT for r in results)

    if all_levels_fail_hard_wall:
        lowest = min(results, key=lambda r: r["risk_pct"])
        print(f"*** STRUCTURAL FLAG ***")
        print(f"Even the lowest tested risk level ({lowest['risk_pct']}%) has an absolute-worst")
        print(f"reordering of {lowest['absolute_worst_dd_pct_maxseed']}%, which does not clear the "
              f"{PROP_MAX_LOSS_PCT}% hard wall at all, let alone with margin.")
        print("This is NOT a sizing problem - it means LSC's month-to-month P&L variance")
        print("is too large for a standard hard-max-loss prop-firm account regardless of")
        print("risk-per-trade. Sizing down further only delays the breach; it doesn't fix it.")
        recommended = None
    elif not passing:
        best = min(results, key=lambda r: r["absolute_worst_dd_pct_maxseed"])
        print(f"No tested level reaches all targets simultaneously.")
        print(f"Best available: {best['risk_pct']}% risk -> 95th-pct {best['worst5pct_dd_pct_maxseed']}%, "
              f"absolute-worst {best['absolute_worst_dd_pct_maxseed']}%, real order {best['hist_dd_pct']}% (max over seeds).")
        if best["absolute_worst_dd_pct_maxseed"] <= PROP_MAX_LOSS_PCT:
            print(f"This clears the {PROP_MAX_LOSS_PCT}% hard wall but without the requested "
                  f"safety margin - consider testing levels below {min(RISK_LEVELS)}% or "
                  "treat this as the practical floor.")
        else:
            print(f"This still does not clear the {PROP_MAX_LOSS_PCT}% hard wall. See structural flag above.")
        recommended = None
    else:
        recommended = max(passing, key=lambda r: r["risk_pct"])
        print(f"Recommended: {recommended['risk_pct']}% risk per trade")
        print(f"  -> 95th-pct block-reordered DD: {recommended['worst5pct_dd_pct']}% on seed {SEED}, "
              f"{recommended['worst5pct_dd_pct_maxseed']}% worst over seeds  "
              f"(target <= {TARGET_WORST5_PCT}%, hard wall {PROP_MAX_LOSS_PCT}%)")
        print(f"  -> absolute worst reordering (1-in-{N_SHUFFLES}): "
              f"{recommended['absolute_worst_dd_pct']}% on seed {SEED}, "
              f"{recommended['absolute_worst_dd_pct_maxseed']}% worst over seeds  (target <= {ABS_WORST_TARGET_PCT}%)")
        print(f"  -> real historical order: {recommended['hist_dd_pct']}%  (target <= {ABS_WORST_TARGET_PCT}%)")
        print(f"  -> paths breaching the {PROP_MAX_LOSS_PCT}% wall: {recommended['pct_paths_breaching_wall']}%")
        print(f"  -> mean / median DD: {recommended['mean_dd_pct']}% / {recommended['median_dd_pct']}%")
        print(f"  -> PF {recommended['pf']}, expectancy ${recommended['expectancy']:.2f}/trade, "
              f"{recommended['trades']} trades")

    # ---- Lot-size feasibility check (the 0.01-lot-floor problem) ----
    print("\n" + "-" * 70)
    print(f"LOT-SIZE FEASIBILITY CHECK - ${INITIAL_BALANCE:,.0f} account, "
          f"broker minimum lot {vol_min}")
    print("-" * 70)
    print("floor_clamped = risk% formula wanted LESS than the broker's minimum")
    print("tradable lot, so the position was forced UP to that minimum - meaning")
    print("the account risked MORE than the intended risk% on that trade. If this")
    print("happens on most/all trades at the recommended risk%, the account is too")
    print("small for that risk% to actually mean anything: it's really trading at")
    print("whatever risk% the 0.01-lot floor imposes, not the calibrated number.")
    for r in results:
        flag = "  <-- FLOOR BINDS ON EVERY TRADE" if r["floor_clamped_count"] == r["trades"] else (
               "  <-- floor binds on some trades" if r["floor_clamped_count"] > 0 else "")
        print(f"  risk={r['risk_pct']:>6}%  floor_clamped={r['floor_clamped_count']:>5}/{r['trades']:<5}  "
              f"worst actual risk on a clamped trade: {r['floor_clamped_worst_actual_risk_pct']:.3f}%{flag}")

    if recommended is not None:
        rec_clamped_pct = recommended["floor_clamped_count"] / recommended["trades"] * 100
        print(f"\nAt the recommended {recommended['risk_pct']}% risk level: "
              f"{recommended['floor_clamped_count']}/{recommended['trades']} trades "
              f"({rec_clamped_pct:.1f}%) hit the {vol_min}-lot floor.")
        if rec_clamped_pct >= 99.0:
            print(f"*** STRUCTURAL FLAG: LOT FLOOR, NOT RISK FORMULA, IS SIZING EVERY TRADE ***")
            print(f"The calibrated {recommended['risk_pct']}% risk-per-trade number is not what")
            print(f"actually executes - essentially every trade is forced to the {vol_min}-lot")
            print(f"minimum, which risks {recommended['floor_clamped_worst_actual_risk_pct']:.3f}% "
                  f"(or more) per trade regardless of the formula. This is the same category of")
            print(f"problem that made $10-500 accounts unworkable for LSC before $500-750 fixed")
            print(f"it. ${INITIAL_BALANCE:,.0f} is very likely too small for LSC's position sizing")
            print(f"to function as calibrated - consider a larger account tier.")
        elif rec_clamped_pct > 10.0:
            print(f"Floor binds on a meaningful minority of trades ({rec_clamped_pct:.1f}%) - "
                  f"the effective realized risk on those trades runs above the {recommended['risk_pct']}%")
            print(f"target (up to {recommended['floor_clamped_worst_actual_risk_pct']:.3f}%), which "
                  f"partially undermines the calibration. Worth a larger account tier if avoidable.")
        else:
            print(f"Floor clamping is rare enough ({rec_clamped_pct:.1f}% of trades) not to "
                  f"materially undermine the calibration.")

    # ---- Dollar sanity check ----
    print("\n" + "-" * 70)
    print(f"DOLLAR SANITY CHECK - ${INITIAL_BALANCE:,.0f} ACCOUNT")
    print("-" * 70)
    if recommended is not None:
        dollar_risk = INITIAL_BALANCE * recommended["risk_pct"] / 100
        worst5_dollars = INITIAL_BALANCE * recommended["worst5pct_dd_pct"] / 100
        abs_worst_dollars = INITIAL_BALANCE * recommended["absolute_worst_dd_pct"] / 100
        print(f"  ${INITIAL_BALANCE:,.0f} account @ {recommended['risk_pct']}% risk: "
              f"${dollar_risk:,.2f} risked/trade | "
              f"worst-5%-case DD ~${worst5_dollars:,.2f} | "
              f"absolute-worst-shuffle DD ~${abs_worst_dollars:,.2f}")
        print(f"  (minimum lot {vol_min} at this account size actually risks up to "
              f"${INITIAL_BALANCE * recommended['floor_clamped_worst_actual_risk_pct'] / 100:,.2f} "
              f"on a floor-clamped trade)")
    else:
        print("  (skipped - no recommended risk level to scale)")

    print("\nDone.")


if __name__ == "__main__":
    main()
