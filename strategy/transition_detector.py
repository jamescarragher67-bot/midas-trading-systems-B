"""
strategy/transition_detector.py - MIDAS-B Regime Transition Detector

Where the edge lives. Fires on regime changes:
  A→B  Breakout entry    (compression → expansion)
  B→C  Reversion entry   (expansion → exhaustion)
  C→A  Cooling — NO TRADE

Only fires once at the transition bar (when regime just changed).
"""

import pandas as pd

# ── Transition thresholds ──────────────────────────────────────────────────────

# A→B
AB_MIN_BARS_IN_A    = 3      # must have been in A for ≥3 consecutive bars
AB_BODY_ATR_MULT    = 0.6    # breakout candle body >= 60% of ATR

# B→C (original tight thresholds — 60% WR validated)
BC_ATR_RATIO_MIN    = 1.50
BC_VOV_RATIO_MIN    = 1.30
BC_WICK_RATIO_MIN   = 0.60

# C→A
CA_ATR_BELOW        = 1.20   # ATR dropped below this × ATR_MA50 → cooling done


def _count_consecutive_trailing(history: list, regime: str) -> int:
    """Count consecutive occurrences of `regime` at the tail of history."""
    count = 0
    for r in reversed(history):
        if r == regime:
            count += 1
        else:
            break
    return count


def detect_transition(regime_history: list, fingerprint: dict,
                      current_bar: pd.Series) -> dict:
    """
    Detect regime transition at the current bar.

    Args:
        regime_history: list of regime strings (oldest first, most recent LAST).
                        history[-1] = current regime, history[-2] = previous.
        fingerprint:    current bar's volatility fingerprint dict.
        current_bar:    current bar as a pd.Series (with open/high/low/close).

    Returns:
        {
          transition: 'A_to_B' | 'B_to_C' | 'C_to_A' | 'NONE',
          direction:  'BUY' | 'SELL' | None,
          confidence: float 0.0-1.0,
          reason:     str
        }
    """
    _null = {"transition": "NONE", "direction": None, "confidence": 0.0, "reason": "no_transition"}

    if len(regime_history) < 2:
        return {**_null, "reason": "insufficient_history"}

    current  = regime_history[-1]
    previous = regime_history[-2]

    # No regime change → no transition to detect
    if current == previous:
        return _null

    atr       = fingerprint["atr"]
    atr_ratio = fingerprint["atr_ratio"]
    vov_ratio = fingerprint["vov_ratio"]

    close = float(current_bar["close"])
    open_ = float(current_bar["open"])
    high  = float(current_bar["high"])
    low   = float(current_bar["low"])
    body  = abs(close - open_)
    hl    = high - low
    wick_ratio = (hl - body) / hl if hl > 0 else 0.0

    # ── A→B: DISABLED — 37.1% WR, money furnace. B→C only. ──────────────────
    # if previous == "A" and current in ("B", "UNKNOWN"):
    #     ... (breakout entry disabled)

    # ── B→C: Mean reversion entry ─────────────────────────────────────────────
    if previous in ("B", "UNKNOWN") and current == "C":
        if atr_ratio <= BC_ATR_RATIO_MIN:
            return {**_null, "reason": f"B_to_C_atr_ratio_too_low_{atr_ratio:.2f}"}
        if vov_ratio <= BC_VOV_RATIO_MIN:
            return {**_null, "reason": f"B_to_C_vov_not_spiking_{vov_ratio:.2f}"}
        if wick_ratio <= BC_WICK_RATIO_MIN:
            return {**_null, "reason": f"B_to_C_wicks_not_dominant_{wick_ratio:.2f}"}

        # Direction: AGAINST the move that caused exhaustion
        direction  = "SELL" if close > open_ else "BUY"
        atr_score  = min(1.0, (atr_ratio - BC_ATR_RATIO_MIN) / 0.5)
        vov_score  = min(1.0, (vov_ratio - BC_VOV_RATIO_MIN) / 0.4)
        wick_score = min(1.0, (wick_ratio - BC_WICK_RATIO_MIN) / 0.3)
        confidence = round((atr_score + vov_score + wick_score) / 3, 2)

        return {
            "transition": "B_to_C",
            "direction":  direction,
            "confidence": confidence,
            "reason":     f"B_to_C_{direction}_atr_ratio={atr_ratio:.2f}_vov={vov_ratio:.2f}_wick={wick_ratio:.2f}",
        }

    # ── C→A: Cooling period — no trade ───────────────────────────────────────
    if previous == "C" and current in ("A", "B") and atr_ratio < CA_ATR_BELOW:
        return {
            "transition": "C_to_A",
            "direction":  None,
            "confidence": 1.0,
            "reason":     f"C_to_A_cooling_atr_ratio={atr_ratio:.2f}",
        }

    return _null
