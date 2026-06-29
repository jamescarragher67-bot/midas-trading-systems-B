"""
strategy/volatility_metrics.py - MIDAS-B Volatility Metrics

Two modes:
  - Live:     get_volatility_fingerprint(bars) — full computation on a df slice
  - Backtest: precompute_volatility_series(df) once, then fingerprint_at(series, i) per bar

All ratios are expressed relative to 50-bar rolling ATR mean so thresholds
work at any Gold price level.
"""

import numpy as np
import pandas as pd


# ── Low-level helpers ──────────────────────────────────────────────────────────

def _true_range(df: pd.DataFrame) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    prev = close.shift(1)
    return pd.concat([high - low, (high - prev).abs(), (low - prev).abs()], axis=1).max(axis=1)


def _atr_series(df: pd.DataFrame, period: int = 14) -> pd.Series:
    return _true_range(df).ewm(com=period - 1, adjust=False).mean()


# ── Live API (used by main.py each cycle) ─────────────────────────────────────

def calculate_atr(bars: pd.DataFrame, period: int = 14) -> float:
    return float(_atr_series(bars, period).iloc[-1])


def calculate_stddev_returns(bars: pd.DataFrame, period: int = 20) -> float:
    log_ret = np.log(bars["close"] / bars["close"].shift(1)).dropna()
    if len(log_ret) < period:
        return 0.0
    return float(log_ret.iloc[-period:].std())


def calculate_hl_compression(bars: pd.DataFrame, period: int = 20) -> float:
    hl = bars["high"] - bars["low"]
    if len(hl) < period:
        return 1.0
    avg = float(hl.iloc[-period:].mean())
    return float(hl.iloc[-1]) / avg if avg > 0 else 1.0


def calculate_body_size_ratio(bars: pd.DataFrame, period: int = 20) -> float:
    bodies = (bars["close"] - bars["open"]).abs()
    hl     = bars["high"] - bars["low"]
    if len(bodies) < period:
        return 0.5
    avg_body = float(bodies.iloc[-period:].mean())
    avg_hl   = float(hl.iloc[-period:].mean())
    return avg_body / avg_hl if avg_hl > 0 else 0.5


def calculate_vol_of_vol(bars: pd.DataFrame, atr_period: int = 14, vov_period: int = 20) -> float:
    atr  = _atr_series(bars, atr_period)
    diff = atr.diff().abs()
    vov  = diff.ewm(com=vov_period - 1, adjust=False).mean()
    return float(vov.iloc[-1])


def get_volatility_fingerprint(bars: pd.DataFrame) -> dict:
    """Full computation — for live use. Returns all metrics as a dict."""
    atr    = _atr_series(bars, 14)
    atr_v  = float(atr.iloc[-1])
    atr_ma50 = float(atr.iloc[-50:].mean()) if len(atr) >= 50 else atr_v

    log_ret  = np.log(bars["close"] / bars["close"].shift(1)).dropna()
    std_v    = float(log_ret.iloc[-20:].std()) if len(log_ret) >= 20 else 0.0
    std_ma50 = float(log_ret.rolling(20).std().iloc[-50:].mean()) if len(log_ret) >= 70 else std_v

    hl           = bars["high"] - bars["low"]
    hl_ma20      = float(hl.iloc[-20:].mean()) if len(hl) >= 20 else float(hl.iloc[-1])
    hl_comp      = float(hl.iloc[-1]) / hl_ma20 if hl_ma20 > 0 else 1.0

    bodies       = (bars["close"] - bars["open"]).abs()
    avg_body     = float(bodies.iloc[-20:].mean()) if len(bodies) >= 20 else float(bodies.iloc[-1])
    body_ratio   = avg_body / hl_ma20 if hl_ma20 > 0 else 0.5

    vov_series   = atr.diff().abs().ewm(com=19, adjust=False).mean()
    vov_v        = float(vov_series.iloc[-1])
    vov_ma       = float(vov_series.iloc[-20:].mean()) if len(vov_series) >= 20 else vov_v

    cur_bar      = bars.iloc[-1]
    cur_body     = abs(float(cur_bar["close"]) - float(cur_bar["open"]))
    cur_hl       = float(cur_bar["high"]) - float(cur_bar["low"])
    wick_ratio   = (cur_hl - cur_body) / cur_hl if cur_hl > 0 else 0.0

    return {
        "atr":            atr_v,
        "atr_ma50":       atr_ma50,
        "atr_ratio":      atr_v / atr_ma50 if atr_ma50 > 0 else 1.0,
        "stddev":         std_v,
        "stddev_ma50":    std_ma50,
        "stddev_ratio":   std_v / std_ma50 if std_ma50 > 0 else 1.0,
        "hl_compression": hl_comp,
        "body_size_ratio": body_ratio,
        "vov":            vov_v,
        "vov_ma":         vov_ma,
        "vov_ratio":      vov_v / vov_ma if vov_ma > 0 else 1.0,
        "wick_ratio":     wick_ratio,
        "cur_body":       cur_body,
        "cur_hl":         cur_hl,
    }


# ── Backtest API (precompute once, then O(1) lookups) ─────────────────────────

def precompute_volatility_series(df: pd.DataFrame) -> dict:
    """
    Precompute all indicator series on the full dataframe.
    Call once before the simulation loop, then use fingerprint_at(series, i).
    """
    atr  = _true_range(df).ewm(com=13, adjust=False).mean()
    atr_ma50 = atr.rolling(50).mean()

    log_ret  = np.log(df["close"] / df["close"].shift(1))
    stddev   = log_ret.rolling(20).std()
    std_ma50 = stddev.rolling(50).mean()

    hl      = df["high"] - df["low"]
    hl_ma20 = hl.rolling(20).mean()
    hl_comp = hl / hl_ma20

    bodies     = (df["close"] - df["open"]).abs()
    body_ma20  = bodies.rolling(20).mean()
    body_ratio = body_ma20 / hl_ma20

    vov_raw = atr.diff().abs()
    vov     = vov_raw.ewm(com=19, adjust=False).mean()
    vov_ma  = vov.rolling(20).mean()

    wick_size  = hl - bodies
    wick_ratio = wick_size / hl.replace(0, np.nan)

    return {
        "atr":            atr,
        "atr_ma50":       atr_ma50,
        "atr_ratio":      atr / atr_ma50,
        "stddev":         stddev,
        "stddev_ma50":    std_ma50,
        "stddev_ratio":   stddev / std_ma50,
        "hl_compression": hl_comp,
        "body_size_ratio": body_ratio,
        "vov":            vov,
        "vov_ma":         vov_ma,
        "vov_ratio":      vov / vov_ma,
        "wick_ratio":     wick_ratio,
        "body":           bodies,
        "hl":             hl,
    }


def fingerprint_at(series: dict, i: int) -> dict:
    """Extract scalar fingerprint at bar index i from precomputed series."""
    def _f(key):
        v = series[key].iloc[i]
        return float(v) if not (v != v) else 0.0   # NaN → 0.0

    atr    = _f("atr")
    atr_ma = _f("atr_ma50")
    std    = _f("stddev")
    s_ma   = _f("stddev_ma50")
    vov    = _f("vov")
    v_ma   = _f("vov_ma")
    hl     = _f("hl")
    body   = _f("body")

    return {
        "atr":            atr,
        "atr_ma50":       atr_ma,
        "atr_ratio":      atr / atr_ma  if atr_ma  > 0 else 1.0,
        "stddev":         std,
        "stddev_ma50":    s_ma,
        "stddev_ratio":   std / s_ma    if s_ma    > 0 else 1.0,
        "hl_compression": _f("hl_compression"),
        "body_size_ratio": _f("body_size_ratio"),
        "vov":            vov,
        "vov_ma":         v_ma,
        "vov_ratio":      vov / v_ma    if v_ma    > 0 else 1.0,
        "wick_ratio":     _f("wick_ratio"),
        "cur_body":       body,
        "cur_hl":         hl,
    }
