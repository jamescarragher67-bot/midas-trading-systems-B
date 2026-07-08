"""
strategy/m5_highfreq_engine.py — Bot 3: High-Frequency M5 Engine

Daily bias gate: 3/3 unanimous voters (EMA Stack + ATR Expansion + Prev Day Structure).
M5 entry: proximity-based EMA21 pullback in the bias direction.
  - Wick-test: low within proximity_atr_mult x ATR of EMA21 (BUY) / high (SELL)
  - Close on correct side of EMA21 (bounce confirmed)
  - Body >= body_atr_mult x ATR14
  - Close in top/bottom (1 - close_range_thresh) of range
  - ATR floor filter (optional)
  - RSI filter (optional)
"""

import pandas as pd
from utils.logger import setup_logger

logger = setup_logger("m5_highfreq_engine")

EMA21_COL          = "ema_slow"
ATR_FLOOR_LOOKBACK = 10


def get_daily_bias(df: pd.DataFrame) -> str:
    """
    3/3 unanimous structural voters: EMA Stack + ATR Expansion + Prev Day Structure.
    Returns 'BUY', 'SELL', or 'NONE'.
    """
    from strategy.ema_stack import get_signal as ema_signal
    from strategy.atr_expansion import get_signal as atr_expansion_signal
    from strategy.prev_day_structure import get_signal as prev_day_signal

    score = 0
    for fn in [ema_signal, atr_expansion_signal, prev_day_signal]:
        try:
            vote, _ = fn(df)
        except Exception:
            vote = 0
        score += vote

    if score == 3:
        return "BUY"
    if score == -3:
        return "SELL"
    return "NONE"


def check_entry(df: pd.DataFrame,
                daily_bias: str,
                proximity_atr_mult: float = 1.5,
                body_atr_mult: float = 0.4,
                close_range_thresh: float = 0.70,
                rsi_filter: bool = False,
                atr_floor_filter: bool = False) -> tuple[str, str]:
    """
    Evaluate M5 entry on the last bar of df.

    Direction is constrained by daily_bias ('BUY', 'SELL', or 'NONE').
    Proximity uses a wick-test: the candle's low (BUY) or high (SELL) must
    have gotten within proximity_atr_mult x ATR of EMA21, and the close must
    be on the correct side (confirming the bounce).

    close_range_thresh=0.70  top/bottom 30%
    close_range_thresh=0.75  top/bottom 25%
    """
    if daily_bias == "NONE":
        return "NEUTRAL", "no_daily_bias"

    bar   = df.iloc[-1]
    close = float(bar["close"])
    open_ = float(bar["open"])
    high  = float(bar["high"])
    low   = float(bar["low"])
    ema21 = float(bar[EMA21_COL])
    atr   = float(bar["atr"])
    rng   = high - low
    buf   = proximity_atr_mult * atr

    if rng <= 0 or atr <= 0:
        return "NEUTRAL", "zero_range_or_atr"

    # ── Momentum body ────────────────────────────────────────────────────────────
    body = abs(close - open_)
    if body < body_atr_mult * atr:
        return "NEUTRAL", f"weak_body_{body:.3f}_need_{body_atr_mult * atr:.3f}"

    # ── ATR floor filter ─────────────────────────────────────────────────────────
    if atr_floor_filter:
        atr_window = df["atr"].iloc[-(ATR_FLOOR_LOOKBACK + 1):-1]
        if len(atr_window) >= ATR_FLOOR_LOOKBACK:
            atr_min = float(atr_window.min())
            if atr <= atr_min:
                return "NEUTRAL", f"atr_at_floor_{atr:.3f}_min_{atr_min:.3f}"

    # ── RSI filter ───────────────────────────────────────────────────────────────
    if rsi_filter:
        rsi_val = float(bar.get("rsi", 50.0))
        if daily_bias == "BUY" and rsi_val < 50.0:
            return "NEUTRAL", f"rsi_below_50_{rsi_val:.1f}"
        if daily_bias == "SELL" and rsi_val > 50.0:
            return "NEUTRAL", f"rsi_above_50_{rsi_val:.1f}"

    # ── Wick-test EMA21 proximity + direction ────────────────────────────────────
    if daily_bias == "BUY":
        if not (low <= ema21 + buf and close > ema21):
            return "NEUTRAL", f"buy_wick_fail low={low:.2f} ema21+buf={ema21+buf:.2f} close={close:.2f}"
        close_pos = (close - low) / rng
        if close_pos < close_range_thresh:
            return "NEUTRAL", f"buy_close_pos_{close_pos:.2f}_need_{close_range_thresh}"
        return "BUY", f"hf_buy ema21={ema21:.2f} body={body:.2f} atr={atr:.2f}"

    else:  # SELL
        if not (high >= ema21 - buf and close < ema21):
            return "NEUTRAL", f"sell_wick_fail high={high:.2f} ema21-buf={ema21-buf:.2f} close={close:.2f}"
        close_pos = (high - close) / rng
        if close_pos < close_range_thresh:
            return "NEUTRAL", f"sell_close_pos_{close_pos:.2f}_need_{close_range_thresh}"
        return "SELL", f"hf_sell ema21={ema21:.2f} body={body:.2f} atr={atr:.2f}"
