"""
backtest/tournament/monte_carlo_pct.py - percent-return recompounding Monte
Carlo, the corrected methodology referenced in config/settings.py's
RISK_PERCENT comment ("1000-shuffle Monte Carlo ... percent-return
recompounding method").

backtest/monte_carlo.py (the original LSC tool) shuffles raw DOLLAR pnl
values from one fixed historical trade sequence. That's inconsistent with
risk-% position sizing: each trade's dollar pnl was sized off the ACCOUNT
BALANCE AT THE POINT IT ORIGINALLY OCCURRED. Replaying those same dollar
amounts in a different (shuffled) order silently assumes the account had
the same balance at every point along the new path too - wrong whenever the
shuffle front-loads losses or wins differently than the original sequence,
which is exactly the scenario Monte Carlo exists to explore.

Fix: convert each trade's dollar pnl to a PERCENT return on the balance it
actually occurred against (pnl / balance_before_trade), shuffle the order of
those percent returns, then recompound multiplicatively along the new path
(balance *= 1 + pct). This keeps position sizing internally consistent
regardless of shuffle order - the corrected method already applied when
LSC's own RISK_PERCENT=0.045% was calibrated.
"""

import numpy as np


def trades_to_pct_returns(trades: list) -> np.ndarray:
    pct_returns = []
    for t in trades:
        balance_after = t["balance_after"]
        pnl = t["pnl"]
        balance_before = balance_after - pnl
        if balance_before <= 0:
            continue
        pct_returns.append(pnl / balance_before)
    return np.array(pct_returns)


def monte_carlo_pct_drawdown(trades: list, initial_balance: float,
                              n_shuffles: int = 1000, seed: int = 42) -> dict:
    pct_returns = trades_to_pct_returns(trades)
    rng = np.random.default_rng(seed)

    max_dds        = np.zeros(n_shuffles)
    final_balances = np.zeros(n_shuffles)

    for s in range(n_shuffles):
        shuffled   = rng.permutation(pct_returns)
        balance    = initial_balance
        peak       = initial_balance
        max_dd_pct = 0.0
        for pct in shuffled:
            balance *= (1 + pct)
            if balance > peak:
                peak = balance
            dd_pct = (peak - balance) / peak * 100 if peak > 0 else 0.0
            if dd_pct > max_dd_pct:
                max_dd_pct = dd_pct
        max_dds[s]        = max_dd_pct
        final_balances[s] = balance

    return {
        "n_shuffles":              n_shuffles,
        "n_trades":                len(pct_returns),
        "mean_max_dd_pct":         round(float(np.mean(max_dds)), 2),
        "median_max_dd_pct":       round(float(np.median(max_dds)), 2),
        "worst_5pct_dd_pct":       round(float(np.percentile(max_dds, 95)), 2),
        "worst_1pct_dd_pct":       round(float(np.percentile(max_dds, 99)), 2),
        "best_dd_pct":             round(float(np.min(max_dds)), 2),
        "worst_dd_pct":            round(float(np.max(max_dds)), 2),
        "pct_shuffles_underwater": round(float(np.mean(final_balances < initial_balance) * 100), 1),
        "mean_final_balance":      round(float(np.mean(final_balances)), 2),
    }
