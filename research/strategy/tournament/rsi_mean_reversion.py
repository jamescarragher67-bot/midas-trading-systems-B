"""
strategy/tournament/rsi_mean_reversion.py - RSI(14) mean reversion, D1.

Candidate #3. Concept + exact published parameters from the Quant-Signals
8,693-trade XAUUSD study ("RSI Basic", D1: 56 trades, 23.2% WR, 0.60 PF,
24.6% max DD, -0.304R expectancy) - quant-signals.com/xauusd-trading-strategies/
(archived 2026-07-21). Article states: "triggers buy signals when the
14-period RSI drops below 30 (oversold) and sell signals when RSI exceeds 70
(overbought)", with the same 1.5x ATR stop / 2:1 reward:risk framework used
across their whole study. Entry timing (cross INTO the zone vs. already-in-
zone) isn't specified beyond that - we use "RSI crosses below 30 -> BUY" /
"RSI crosses above 70 -> SELL" (a single discrete trigger per excursion,
not a re-trigger every bar RSI stays extreme), the natural reading of
"triggers ... when RSI drops below 30". Expect this to lose - the article's
own conclusion is gold is trend-following, not mean-reverting.
"""

import pandas as pd
import MetaTrader5 as mt5

TIMEFRAME_MT5   = mt5.TIMEFRAME_D1
TIMEFRAME_LABEL = "D1"
MAX_HOLD_BARS   = 30
SESSION_HOURS   = None
COOLDOWN_BARS   = 1
MAX_TRADES_PER_DAY = 1

RSI_PERIOD   = 14
RSI_OVERSOLD = 30
RSI_OVERBOUGHT = 70
SL_ATR_MULT  = 1.5
REWARD_RATIO = 2.0


def _rsi(close: pd.Series, period: int) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, float("nan"))
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(100).where(avg_loss != 0, 100).mask((avg_gain == 0) & (avg_loss == 0), 50)


def precompute(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["rsi"] = _rsi(df["close"], RSI_PERIOD)
    return df


def check_entry(df: pd.DataFrame, i: int, last_trade_bar: int, trades_today: int,
                cooldown_bars: int, max_trades_per_day: int):
    if trades_today >= max_trades_per_day or i - last_trade_bar < cooldown_bars:
        return "NEUTRAL", "gated", None, None

    prev, bar = df.iloc[i - 1], df.iloc[i]
    if pd.isna(prev["rsi"]) or pd.isna(bar["rsi"]) or pd.isna(bar["atr"]):
        return "NEUTRAL", "warmup", None, None

    close, atr = bar["close"], bar["atr"]
    if prev["rsi"] >= RSI_OVERSOLD and bar["rsi"] < RSI_OVERSOLD:
        sl = close - SL_ATR_MULT * atr
        tp = close + (close - sl) * REWARD_RATIO
        return "BUY", "rsi_cross_oversold", sl, tp
    if prev["rsi"] <= RSI_OVERBOUGHT and bar["rsi"] > RSI_OVERBOUGHT:
        sl = close + SL_ATR_MULT * atr
        tp = close - (sl - close) * REWARD_RATIO
        return "SELL", "rsi_cross_overbought", sl, tp

    return "NEUTRAL", "no_signal", None, None
