"""
strategy/regime_classifier.py - MIDAS-B Regime Classifier

Three regimes, classified by volatility fingerprint ratios:
  A — Compression  (coiling, breakout building)
  B — Expansion    (trend running, normal volatility)
  C — Exhaustion   (spike, mean reversion imminent)

Priority: C > A > B > UNKNOWN
"""

# ── Regime thresholds (ATR-relative — no absolute price references) ────────────

# Regime A: compression
A_ATR_RATIO_MAX    = 0.80
A_STDDEV_RATIO_MAX = 0.80
A_HL_COMP_MAX      = 0.75

# Regime B: expansion (normal)
B_ATR_RATIO_MIN    = 0.80
B_ATR_RATIO_MAX    = 1.50
B_STDDEV_RATIO_MIN = 0.80
B_STDDEV_RATIO_MAX = 1.50

# Regime C: exhaustion / spike
C_ATR_RATIO_MIN    = 1.50
C_VOV_RATIO_MIN    = 1.30


def classify_regime(fingerprint: dict) -> str:
    """
    Classify current market regime from the volatility fingerprint.
    Returns 'A', 'B', 'C', or 'UNKNOWN'.
    """
    atr_ratio   = fingerprint["atr_ratio"]
    std_ratio   = fingerprint["stddev_ratio"]
    hl_comp     = fingerprint["hl_compression"]
    vov_ratio   = fingerprint["vov_ratio"]
    body_ratio  = fingerprint["body_size_ratio"]
    wick_ratio  = fingerprint["wick_ratio"]

    # Regime C — exhaustion (check first; highest priority)
    if atr_ratio > C_ATR_RATIO_MIN and vov_ratio > C_VOV_RATIO_MIN:
        return "C"

    # Regime A — compression (second priority)
    if (atr_ratio  < A_ATR_RATIO_MAX and
        std_ratio  < A_STDDEV_RATIO_MAX and
        hl_comp    < A_HL_COMP_MAX):
        return "A"

    # Regime B — expansion (default normal state)
    if (B_ATR_RATIO_MIN  <= atr_ratio <= B_ATR_RATIO_MAX and
        B_STDDEV_RATIO_MIN <= std_ratio <= B_STDDEV_RATIO_MAX):
        return "B"

    return "UNKNOWN"
