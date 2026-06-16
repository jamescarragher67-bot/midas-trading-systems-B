"""
strategy/multi_timeframe.py

Analyses H1 and M15 to determine the higher timeframe bias.
The M5 signal engine only fires if the bias matches the trade direction.
"""

import MetaTrader5 as mt5
import pandas as pd
import numpy as np
from utils.logger import setup_logger
from config.settings import SYMBOL, MTF_H1, MTF_M15, EMA_TREND, EMA_SLOW

logger = setup_logger("multi_timeframe")

TIMEFRAME_MAP = {
    5:  mt5.TIMEFRAME_M5,
    15: mt5.TIMEFRAME_M15,
    60: mt5.TIMEFRAME_H1,
}


def _ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def _rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta    = series.diff()
    gain     = delta.clip(lower=0)
    loss     = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=period - 1, adjust=False).mean()
    avg_loss = loss.ewm(com=period - 1, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _fetch(timeframe: int, count: int = 100) -> pd.DataFrame:
    tf = TIMEFRAME_MAP.get(timeframe)
    if tf is None:
        return pd.DataFrame()
    rates = mt5.copy_rates_from_pos(SYMBOL, tf, 0, count)
    if rates is None or len(rates) == 0:
        return pd.DataFrame()
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    df.set_index("time", inplace=True)
    return df


def get_htf_bias() -> dict:
    """
    Returns a dict with:
      - bias:       "BUY", "SELL", or "NEUTRAL"
      - h1_trend:   "UP", "DOWN", or "FLAT"
      - m15_momentum: "UP", "DOWN", or "FLAT"
      - score:      0-5 confluence score
      - details:    list of condition descriptions
    """
    result = {
        "bias":         "NEUTRAL",
        "h1_trend":     "FLAT",
        "m15_momentum": "FLAT",
        "score":        0,
        "details":      [],
    }

    # ── H1 trend ──────────────────────────────────────────────────────────────
    h1 = _fetch(MTF_H1, 100)
    if h1.empty:
        logger.warning("Could not fetch H1 data for MTF analysis.")
        return result

    h1["ema50"] = _ema(h1["close"], EMA_TREND)
    h1["ema21"] = _ema(h1["close"], EMA_SLOW)
    h1["rsi"]   = _rsi(h1["close"])

    h1_close  = h1["close"].iloc[-2]
    h1_ema50  = h1["ema50"].iloc[-2]
    h1_ema21  = h1["ema21"].iloc[-2]
    h1_rsi    = h1["rsi"].iloc[-2]

    if h1_close > h1_ema50 and h1_ema21 > h1_ema50:
        result["h1_trend"] = "UP"
    elif h1_close < h1_ema50 and h1_ema21 < h1_ema50:
        result["h1_trend"] = "DOWN"

    # ── M15 momentum ──────────────────────────────────────────────────────────
    m15 = _fetch(MTF_M15, 100)
    if m15.empty:
        logger.warning("Could not fetch M15 data for MTF analysis.")
        return result

    m15["ema9"]  = _ema(m15["close"], 9)
    m15["ema21"] = _ema(m15["close"], EMA_SLOW)
    m15["rsi"]   = _rsi(m15["close"])

    m15_ema9  = m15["ema9"].iloc[-2]
    m15_ema21 = m15["ema21"].iloc[-2]
    m15_rsi   = m15["rsi"].iloc[-2]
    m15_close = m15["close"].iloc[-2]
    m15_ema50 = _ema(m15["close"], EMA_TREND).iloc[-2]

    if m15_ema9 > m15_ema21 and m15_close > m15_ema50:
        result["m15_momentum"] = "UP"
    elif m15_ema9 < m15_ema21 and m15_close < m15_ema50:
        result["m15_momentum"] = "DOWN"

    # ── Confluence scoring ────────────────────────────────────────────────────
    score   = 0
    details = []

    # Condition 1: H1 price above/below EMA50
    if result["h1_trend"] == "UP":
        score += 1
        details.append("✅ H1 price above EMA50 (uptrend)")
    elif result["h1_trend"] == "DOWN":
        score += 1
        details.append("✅ H1 price below EMA50 (downtrend)")
    else:
        details.append("❌ H1 trend flat")

    # Condition 2: H1 RSI in healthy zone
    if 40 < h1_rsi < 70:
        score += 1
        details.append(f"✅ H1 RSI healthy ({h1_rsi:.1f})")
    elif 30 < h1_rsi < 80:
        score += 0.5
        details.append(f"⚠️ H1 RSI ok ({h1_rsi:.1f})")
    else:
        details.append(f"❌ H1 RSI extreme ({h1_rsi:.1f})")

    # Condition 3: M15 momentum aligned with H1
    if result["h1_trend"] == result["m15_momentum"] and result["h1_trend"] != "FLAT":
        score += 1
        details.append("✅ M15 momentum aligned with H1")
    else:
        details.append("❌ M15 momentum not aligned")

    # Condition 4: M15 EMA9 > EMA21
    if m15_ema9 > m15_ema21:
        score += 1
        details.append("✅ M15 EMA9 above EMA21")
    elif m15_ema9 < m15_ema21:
        score += 1
        details.append("✅ M15 EMA9 below EMA21")

    # Condition 5: M15 RSI not extreme
    if 35 < m15_rsi < 65:
        score += 1
        details.append(f"✅ M15 RSI in ideal zone ({m15_rsi:.1f})")
    elif 25 < m15_rsi < 75:
        score += 0.5
        details.append(f"⚠️ M15 RSI acceptable ({m15_rsi:.1f})")
    else:
        details.append(f"❌ M15 RSI extreme ({m15_rsi:.1f})")

    result["score"]   = round(score, 1)
    result["details"] = details

    # ── Final bias ────────────────────────────────────────────────────────────
    if result["h1_trend"] == "UP" and result["m15_momentum"] == "UP":
        result["bias"] = "BUY"
    elif result["h1_trend"] == "DOWN" and result["m15_momentum"] == "DOWN":
        result["bias"] = "SELL"
    else:
        result["bias"] = "NEUTRAL"

    logger.info(
        f"MTF | H1={result['h1_trend']} M15={result['m15_momentum']} "
        f"Bias={result['bias']} Score={result['score']}/5"
    )

    return result