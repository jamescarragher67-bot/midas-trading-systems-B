"""
strategy/h1_trend.py
Gate 1: H1 trend confirmation.

Requires clean EMA9 > EMA21 > EMA50 alignment on H1 with price above/below EMA50.
Exposes check_h1_trend_df() for backtesting and get_h1_trend() for live trading.
"""

import MetaTrader5 as mt5
import pandas as pd
from strategy.indicators import add_indicators
from utils.logger import setup_logger
from config.settings import SYMBOL

logger = setup_logger("h1_trend")

_INDICATOR_CFG = {
    "EMA_FAST": 9, "EMA_SLOW": 21, "EMA_TREND": 50,
    "RSI_PERIOD": 14, "ATR_PERIOD": 14,
}


from config.settings import H1_EMA_MIN_SPREAD_ATR


def check_h1_trend_df(df_h1: pd.DataFrame) -> dict:
    """
    Check H1 EMA alignment on a pre-loaded, pre-indicator DataFrame.
    Used by both live trading (via get_h1_trend) and backtesting.

    Expects columns: ema_fast (EMA9), ema_slow (EMA21), ema_trend (EMA50), close, atr.
    Uses df_h1.iloc[-2] — last fully closed H1 candle.
    """
    if len(df_h1) < 3:
        return {"direction": "NEUTRAL", "reason": "Insufficient H1 data"}

    last  = df_h1.iloc[-2]
    ema9  = last["ema_fast"]
    ema21 = last["ema_slow"]
    ema50 = last["ema_trend"]
    close = last["close"]
    atr   = last["atr"]

    min_spread = atr * H1_EMA_MIN_SPREAD_ATR

    if (ema9 > ema21 > ema50 and close > ema50
            and (ema9 - ema21) >= min_spread and (ema21 - ema50) >= min_spread):
        return {
            "direction": "BUY",
            "reason":    f"H1 uptrend | EMA9={ema9:.2f} > EMA21={ema21:.2f} > EMA50={ema50:.2f} | spread OK",
        }
    if (ema9 < ema21 < ema50 and close < ema50
            and (ema21 - ema9) >= min_spread and (ema50 - ema21) >= min_spread):
        return {
            "direction": "SELL",
            "reason":    f"H1 downtrend | EMA9={ema9:.2f} < EMA21={ema21:.2f} < EMA50={ema50:.2f} | spread OK",
        }
    return {
        "direction": "NEUTRAL",
        "reason":    f"H1 trend weak or unaligned | EMA9={ema9:.2f} EMA21={ema21:.2f} EMA50={ema50:.2f}",
    }


def get_h1_trend() -> dict:
    """Fetches H1 data from MT5 and checks trend. Used by live signal engine."""
    rates = mt5.copy_rates_from_pos(SYMBOL, mt5.TIMEFRAME_H1, 0, 60)
    if rates is None or len(rates) < 55:
        return {"direction": "NEUTRAL", "reason": "Insufficient H1 data from MT5"}

    df_h1 = pd.DataFrame(rates)
    df_h1 = add_indicators(df_h1, _INDICATOR_CFG)
    return check_h1_trend_df(df_h1)
