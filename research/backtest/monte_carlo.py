"""
backtest/monte_carlo.py - Monte Carlo trade-sequence shuffle stress test.

Shuffles the ORDER of a strategy's actual trade P&L sequence (same trades,
same win/loss magnitudes, different order) many times, to see how much of
the equity curve's shape - especially max drawdown - depends on the
specific sequence in-sample luck happened to produce, versus the
underlying trade distribution itself.
"""

import numpy as np


def monte_carlo_drawdown(trades: list, initial_balance: float,
                         n_shuffles: int = 1000, seed: int = 42) -> dict:
    pnls = np.array([t["pnl"] for t in trades])
    rng  = np.random.default_rng(seed)

    max_dds        = np.zeros(n_shuffles)
    final_balances = np.zeros(n_shuffles)

    for s in range(n_shuffles):
        shuffled   = rng.permutation(pnls)
        balance    = initial_balance
        peak       = initial_balance
        max_dd_pct = 0.0
        for pnl in shuffled:
            balance += pnl
            if balance > peak:
                peak = balance
            dd_pct = (peak - balance) / peak * 100 if peak > 0 else 0.0
            if dd_pct > max_dd_pct:
                max_dd_pct = dd_pct
        max_dds[s]        = max_dd_pct
        final_balances[s] = balance

    return {
        "n_shuffles":               n_shuffles,
        "mean_max_dd_pct":          round(float(np.mean(max_dds)), 1),
        "median_max_dd_pct":        round(float(np.median(max_dds)), 1),
        "worst_5pct_dd_pct":        round(float(np.percentile(max_dds, 95)), 1),
        "worst_1pct_dd_pct":        round(float(np.percentile(max_dds, 99)), 1),
        "best_dd_pct":              round(float(np.min(max_dds)), 1),
        "worst_dd_pct":             round(float(np.max(max_dds)), 1),
        "pct_shuffles_underwater":  round(float(np.mean(final_balances < initial_balance) * 100), 1),
    }


def monte_carlo_block_reorder_pct(trades: list, initial_balance: float,
                                  n_reorders: int = 1000, seed: int = 42,
                                  wall_pct: float | None = None) -> dict:
    """Month-block reordering Monte Carlo - the tail metric LSC's risk is
    calibrated against since 2026-09-15.

    Shuffling individual trades (monte_carlo_drawdown above, and the
    percent-return variant in research/tools/risk_calibrator.py) destroys the
    real month-level clustering of LSC's losses, and was shown on 2026-09-15
    (research/lsc_rolling_month_2026-09-15.txt) to understate the drawdown
    tail by roughly 2x: at 0.045% risk the trade shuffle gave a 95th-pct max
    DD of 3.7% / worst 5.3%, while the historical order itself reached 5.99%.

    This function cuts the trade sequence into calendar-month blocks (by each
    trade's 'date'), shuffles the ORDER of the blocks while preserving every
    month's internal trade sequence, and recompounds each trade's realised
    percent return (pnl / balance before the trade) along the new path - so
    losing months still land as losing months, just in a different order.

    Returns the max-drawdown distribution, the share of paths breaching
    wall_pct (if given), and the longest run of consecutive losing months.
    PF, win rate and the final balance are order-invariant and not reported.
    """
    balances_before = np.array([initial_balance] + [t["balance_after"] for t in trades[:-1]])
    pct_returns = np.array([t["pnl"] for t in trades]) / balances_before

    blocks, order = {}, []
    for k, t in enumerate(trades):
        m = t["date"][:7]
        if m not in blocks:
            blocks[m] = []
            order.append(m)
        blocks[m].append(k)
    block_idx = [np.array(blocks[m]) for m in order]
    rng = np.random.default_rng(seed)

    max_dds = np.zeros(n_reorders)
    streaks = np.zeros(n_reorders, dtype=int)
    for s in range(n_reorders):
        perm = rng.permutation(len(block_idx))
        seq  = np.concatenate([block_idx[p] for p in perm])
        path = np.concatenate([[initial_balance], initial_balance * np.cumprod(1 + pct_returns[seq])])
        peak = np.maximum.accumulate(path)
        max_dds[s] = ((peak - path) / peak * 100).max()
        pnl = np.diff(path)
        pos = streak = best = 0
        for p in perm:
            k = len(block_idx[p])
            if pnl[pos:pos + k].sum() < 0:
                streak += 1
                best = max(best, streak)
            else:
                streak = 0
            pos += k
        streaks[s] = best

    out = {
        "n_reorders":            n_reorders,
        "n_blocks":              len(block_idx),
        "mean_max_dd_pct":       round(float(np.mean(max_dds)), 2),
        "median_max_dd_pct":     round(float(np.median(max_dds)), 2),
        "worst_5pct_dd_pct":     round(float(np.percentile(max_dds, 95)), 2),
        "worst_1pct_dd_pct":     round(float(np.percentile(max_dds, 99)), 2),
        "best_dd_pct":           round(float(np.min(max_dds)), 2),
        "worst_dd_pct":          round(float(np.max(max_dds)), 2),
        "losing_month_streak_median": int(np.median(streaks)),
        "losing_month_streak_worst":  int(streaks.max()),
        "max_dds":               max_dds,
    }
    if wall_pct is not None:
        out["pct_paths_breaching_wall"] = round(float(np.mean(max_dds >= wall_pct) * 100), 1)
    return out
