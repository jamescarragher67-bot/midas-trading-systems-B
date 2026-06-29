"""
strategy/spread_calculator.py — Gold/Silver Statistical Arbitrage Math

Rolling OLS hedge ratio, spread, z-score, and correlation functions.
All functions operate on plain lists or numpy arrays.
"""

import numpy as np


def calculate_beta(gold_closes, silver_closes, lookback: int = 50):
    """
    Rolling OLS hedge ratio.
    Returns (beta, alpha) where: Gold = beta × Silver + alpha
    """
    g = np.array(gold_closes[-lookback:], dtype=float)
    s = np.array(silver_closes[-lookback:], dtype=float)
    var_s = np.var(s)
    if var_s < 1e-10:
        return 1.0, 0.0
    beta  = np.cov(g, s)[0, 1] / var_s
    alpha = np.mean(g) - beta * np.mean(s)
    return float(beta), float(alpha)


def calculate_spread_series(gold_closes, silver_closes, beta: float, alpha: float) -> np.ndarray:
    """Spread for every bar: gold - (beta × silver + alpha)"""
    g = np.array(gold_closes, dtype=float)
    s = np.array(silver_closes, dtype=float)
    return g - (beta * s + alpha)


def calculate_zscore(spread_series, lookback: int = 50) -> float:
    """Z-score of the last element relative to the trailing lookback window."""
    arr  = np.array(spread_series, dtype=float)
    win  = arr[-lookback:]
    mean = np.mean(win)
    std  = np.std(win)
    if std < 1e-10:
        return 0.0
    return float((arr[-1] - mean) / std)


def calculate_correlation(gold_closes, silver_closes, lookback: int = 50) -> float:
    """Pearson correlation over the trailing lookback window."""
    g = np.array(gold_closes[-lookback:], dtype=float)
    s = np.array(silver_closes[-lookback:], dtype=float)
    if np.std(g) < 1e-10 or np.std(s) < 1e-10:
        return 0.0
    mat = np.corrcoef(g, s)
    val = mat[0, 1]
    return float(val) if not np.isnan(val) else 0.0


def check_cointegration(gold_closes, silver_closes,
                        lookback: int = 200, p_threshold: float = 0.05) -> tuple:
    """
    Engle-Granger two-step cointegration test on a rolling window.
    Step 1: OLS  Gold = beta * Silver + alpha  → get residuals (spread)
    Step 2: ADF test on residuals — if p < threshold, residuals are stationary
            and the pair IS cointegrated at this moment.

    Returns (is_cointegrated: bool, p_value: float)
    """
    from statsmodels.tsa.stattools import adfuller

    g = np.array(gold_closes[-lookback:], dtype=float)
    s = np.array(silver_closes[-lookback:], dtype=float)

    if len(g) < lookback or np.var(s) < 1e-10:
        return False, 1.0

    beta     = np.cov(g, s)[0, 1] / np.var(s)
    alpha    = np.mean(g) - beta * np.mean(s)
    residuals = g - (beta * s + alpha)

    try:
        adf_stat, p_value = adfuller(residuals, autolag="AIC")[:2]
        return bool(p_value < p_threshold), float(p_value)
    except Exception:
        return False, 1.0


def get_stat_arb_signal(gold_bars, silver_bars, atr14: float, atr_ma20: float,
                        config: dict, prev_zscores: list = None) -> dict:
    """
    Master stat arb signal function.

    gold_bars / silver_bars: aligned DataFrames with a 'close' column.
    prev_zscores:            list of z-scores from the last N bars (for age check).

    Returns:
        {signal, zscore, spread, beta, correlation, entry, sl, tp, reason}
    """
    lookback  = config.get("lookback_bars", 50)
    z_entry   = config.get("zscore_entry", 2.0)
    min_corr  = config.get("min_correlation", 0.60)
    atr_mult  = config.get("atr_regime_multiplier", 0.8)
    sl_mult   = config.get("sl_atr_multiplier", 1.5)
    rr        = config.get("reward_ratio", 2.0)
    min_age   = config.get("min_zscore_age_bars", 2)

    NONE = {
        "signal": "NONE", "zscore": 0.0, "spread": 0.0,
        "beta": 0.0, "correlation": 0.0,
        "entry": 0.0, "sl": 0.0, "tp": 0.0, "reason": "init",
    }

    if len(gold_bars) < lookback + 5 or len(silver_bars) < lookback + 5:
        return {**NONE, "reason": "insufficient_data"}

    gold_closes   = gold_bars["close"].tolist()
    silver_closes = silver_bars["close"].tolist()

    # Regime: correlation
    corr = calculate_correlation(gold_closes, silver_closes, lookback)
    if abs(corr) < min_corr:
        return {**NONE, "correlation": corr, "reason": f"low_corr_{corr:.2f}"}

    # Regime: ATR (dead market filter)
    if atr_ma20 > 0 and atr14 < atr_ma20 * atr_mult:
        return {**NONE, "correlation": corr, "reason": "dead_market"}

    beta, alpha = calculate_beta(gold_closes, silver_closes, lookback)
    spread_arr  = calculate_spread_series(gold_closes, silver_closes, beta, alpha)
    zscore      = calculate_zscore(spread_arr, lookback)
    spread      = float(spread_arr[-1])

    # Z-score maturity: must have been beyond threshold for ≥ min_age bars
    if prev_zscores and len(prev_zscores) >= min_age - 1:
        if zscore > z_entry:
            if not all(z > z_entry for z in prev_zscores[-(min_age - 1):]):
                return {**NONE, "zscore": zscore, "spread": spread, "beta": beta,
                        "correlation": corr, "reason": "zscore_not_mature"}
        elif zscore < -z_entry:
            if not all(z < -z_entry for z in prev_zscores[-(min_age - 1):]):
                return {**NONE, "zscore": zscore, "spread": spread, "beta": beta,
                        "correlation": corr, "reason": "zscore_not_mature"}

    entry   = float(gold_closes[-1])
    sl_dist = atr14 * sl_mult

    if zscore > z_entry:
        sl = round(entry + sl_dist, 2)
        tp = round(entry - sl_dist * rr, 2)
        return {
            "signal": "SELL", "zscore": zscore, "spread": spread,
            "beta": beta, "correlation": corr,
            "entry": entry, "sl": sl, "tp": tp,
            "reason": f"z={zscore:.2f}_SELL",
        }

    if zscore < -z_entry:
        sl = round(entry - sl_dist, 2)
        tp = round(entry + sl_dist * rr, 2)
        return {
            "signal": "BUY", "zscore": zscore, "spread": spread,
            "beta": beta, "correlation": corr,
            "entry": entry, "sl": sl, "tp": tp,
            "reason": f"z={zscore:.2f}_BUY",
        }

    return {
        **NONE,
        "zscore": zscore, "spread": spread, "beta": beta, "correlation": corr,
        "reason": f"no_signal_z={zscore:.2f}",
    }
