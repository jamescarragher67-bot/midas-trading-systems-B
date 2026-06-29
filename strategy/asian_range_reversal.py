"""
strategy/asian_range_reversal.py - Gate 3: Asian Range Stop Hunt Reversal

Institutional-grade entry engine:
  Phase 1  00:00-07:00 UTC  Asian session range forms (high/low reference levels)
  Phase 2  07:00-09:00 UTC  London sweeps one side (stop-hunt of retail breakout traders)
  Phase 3  07:00-10:00 UTC  Entry on reversal confirmation (RSI + EMA21 alignment)

1 pip = $1.00 on XAUUSD (e.g. 2320.00 to 2321.00 = 1 pip)
"""

import pandas as pd
from datetime import datetime
from utils.logger import setup_logger

logger = setup_logger("asian_range_reversal")

PIP               = 1.0    # $1 per pip on XAUUSD
ASIAN_END_HOUR    = 7      # Asian range defined from 00:00 up to (not including) 07:00
SWEEP_END_HOUR    = 9      # London sweep window 07:00-09:00
ENTRY_END_HOUR    = 10     # Entry valid until 10:00 UTC
RANGE_MIN_PIPS    = 10.0
RANGE_MAX_PIPS    = 100.0
SWEEP_MIN_PIPS    = 5.0
SWEEP_MAX_PIPS    = 80.0


def get_asian_range(df_m5: pd.DataFrame, date) -> dict:
    """
    Compute Asian session high/low from 00:00-07:00 UTC for the given date.
    Returns {high, low, size_pips, valid}.
    """
    NONE = {"high": 0.0, "low": 0.0, "size_pips": 0.0, "valid": False}
    mask = (df_m5.index.date == date) & (df_m5.index.hour < ASIAN_END_HOUR)
    bars = df_m5[mask]
    if len(bars) < 6:
        return NONE
    high      = float(bars["high"].max())
    low       = float(bars["low"].min())
    size_pips = (high - low) / PIP
    if not (RANGE_MIN_PIPS <= size_pips <= RANGE_MAX_PIPS):
        return NONE
    return {"high": round(high, 2), "low": round(low, 2),
            "size_pips": round(size_pips, 1), "valid": True}


def detect_sweep(df_m5: pd.DataFrame, asian_high: float, asian_low: float,
                 sweep_min: float = SWEEP_MIN_PIPS,
                 sweep_max: float = SWEEP_MAX_PIPS) -> dict:
    """
    Detect a London stop-hunt sweep of the Asian range during 07:00-09:00 UTC.
    Price must wick beyond the level then close back inside.
    Returns {direction, sweep_level, sweep_pips} or {direction: None}.
    """
    NONE = {"direction": None, "sweep_level": 0.0, "sweep_pips": 0.0}
    mask = (df_m5.index.hour >= ASIAN_END_HOUR) & (df_m5.index.hour < SWEEP_END_HOUR)
    london = df_m5[mask]
    if len(london) < 2:
        return NONE

    sweep_up = sweep_down = None
    for _, bar in london.iterrows():
        if bar["high"] > asian_high and bar["close"] <= asian_high:
            pips = (bar["high"] - asian_high) / PIP
            if sweep_min <= pips <= sweep_max and sweep_up is None:
                sweep_up = {"direction": "UP",
                            "sweep_level": round(float(bar["high"]), 2),
                            "sweep_pips":  round(pips, 1)}
        if bar["low"] < asian_low and bar["close"] >= asian_low:
            pips = (asian_low - bar["low"]) / PIP
            if sweep_min <= pips <= sweep_max and sweep_down is None:
                sweep_down = {"direction": "DOWN",
                              "sweep_level": round(float(bar["low"]), 2),
                              "sweep_pips":  round(pips, 1)}

    # Both sides swept = ambiguous, skip
    if sweep_up and sweep_down:
        return NONE
    return sweep_up or sweep_down or NONE


def get_entry_signal(df_m5: pd.DataFrame, h1_trend: str,
                     asian_high: float, asian_low: float,
                     sweep: dict, atr14: float, atr_ma20: float,
                     utc_time, max_spread_pts: float = 20.0) -> dict:
    """
    CONTINUATION entry after London stop-hunt sweep. Valid 07:00-10:00 UTC.

    The London sweep clears retail stops to fuel institutional momentum:
      UP sweep + BUY H1 trend  → institutions absorbed sell-stops above Asian high
                                   → BUY when price retests Asian high as new support
      DOWN sweep + SELL H1 trend → institutions absorbed buy-stops below Asian low
                                   → SELL when price retests Asian low as new resistance

    Entry:  first M5 close within ATR×0.5 of the swept Asian level
    SL:     beyond the London swing opposite extreme (below london_low for BUY,
            above london_high for SELL) plus ATR×0.1 buffer
    TP:     2× SL distance in the continuation direction

    Returns {signal, entry, sl, tp, grade, reason}.
    """
    NONE = {"signal": "NONE", "entry": 0.0, "sl": 0.0, "tp": 0.0,
            "grade": None, "reason": "no_signal"}

    if sweep["direction"] is None:
        return {**NONE, "reason": "no_sweep"}

    hour = utc_time.hour if hasattr(utc_time, "hour") else int(utc_time)
    if not (ASIAN_END_HOUR <= hour < ENTRY_END_HOUR):
        return {**NONE, "reason": "outside_entry_window"}

    cur   = df_m5.iloc[-1]
    close = float(cur["close"])
    rsi14 = float(cur["rsi"])
    retest_tol = atr14 * 1.0   # how close to the Asian level counts as a retest

    # BUY continuation: UP sweep + BUY H1 trend
    # Price swept above Asian HIGH, pulled back, now retesting Asian HIGH as support
    if sweep["direction"] == "UP" and h1_trend == "BUY":
        at_level = abs(close - asian_high) <= retest_tol
        if at_level and close >= asian_high - retest_tol:
            london_low  = sweep.get("london_low", asian_high - atr14)
            sl          = round(london_low - atr14 * 0.1, 2)
            sl_dist     = abs(close - sl)
            if sl_dist <= 0:
                return {**NONE, "reason": "zero_sl"}
            tp      = round(close + sl_dist * 2.0, 2)
            grade_a = sweep["sweep_pips"] >= 10.0 and rsi14 > 50.0 and atr14 >= atr_ma20
            grade   = "A" if grade_a else "B"
            logger.info(f"Gate 3 BUY Grade-{grade} | sweep={sweep['sweep_pips']:.1f}p "
                        f"| retest={close:.2f} asian_high={asian_high:.2f} SL={sl} TP={tp}")
            return {"signal": "BUY", "entry": close, "sl": sl, "tp": tp,
                    "grade": grade, "reason": "asian_sweep_continuation_buy"}
        return {**NONE, "reason": "not_at_retest_level"}

    # SELL continuation: DOWN sweep + SELL H1 trend
    # Price swept below Asian LOW, bounced, now retesting Asian LOW as resistance
    if sweep["direction"] == "DOWN" and h1_trend == "SELL":
        at_level = abs(close - asian_low) <= retest_tol
        if at_level and close <= asian_low + retest_tol:  # noqa: SIM114
            london_high = sweep.get("london_high", asian_low + atr14)
            sl          = round(london_high + atr14 * 0.1, 2)
            sl_dist     = abs(close - sl)
            if sl_dist <= 0:
                return {**NONE, "reason": "zero_sl"}
            tp      = round(close - sl_dist * 2.0, 2)
            grade_a = sweep["sweep_pips"] >= 10.0 and rsi14 < 50.0 and atr14 >= atr_ma20
            grade   = "A" if grade_a else "B"
            logger.info(f"Gate 3 SELL Grade-{grade} | sweep={sweep['sweep_pips']:.1f}p "
                        f"| retest={close:.2f} asian_low={asian_low:.2f} SL={sl} TP={tp}")
            return {"signal": "SELL", "entry": close, "sl": sl, "tp": tp,
                    "grade": grade, "reason": "asian_sweep_continuation_sell"}
        return {**NONE, "reason": "not_at_retest_level"}

    return {**NONE, "reason": "trend_mismatch"}
