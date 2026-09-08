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
