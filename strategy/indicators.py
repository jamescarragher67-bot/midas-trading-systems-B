"""
strategy/indicators.py
Calculates all technical indicators used by the signal engine.
Uses pandas only — no TA-lib dependency required.
"""

import pandas as pd
import numpy as np
from utils.logger import setup_logger

logger = setup_logger("indicators")


def ema(series: pd.Series, period: int) -> pd.Series:
    """Exponential Moving Average."""
    return series.ewm(span=period, adjust=False).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Relative Strength Index."""
    delta = series.diff()
    gain  = delta.clip(lower=0)
    loss  = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=period - 1, adjust=False).mean()
    avg_loss = loss.ewm(com=period - 1, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range."""
    high  = df["high"]
    low   = df["low"]
    close = df["close"]
    prev_close = close.shift(1)

    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low  - prev_close).abs()
    ], axis=1).max(axis=1)

    return tr.ewm(com=period - 1, adjust=False).mean()


def add_indicators(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """
    Add all indicators to the DataFrame.
    cfg should contain keys matching config/settings.py indicator params.
    """
    df = df.copy()

    df["ema_fast"]  = ema(df["close"], cfg["EMA_FAST"])
    df["ema_slow"]  = ema(df["close"], cfg["EMA_SLOW"])
    df["ema_trend"] = ema(df["close"], cfg["EMA_TREND"])
    df["rsi"]       = rsi(df["close"], cfg["RSI_PERIOD"])
    df["atr"]       = atr(df,          cfg["ATR_PERIOD"])

    return df
