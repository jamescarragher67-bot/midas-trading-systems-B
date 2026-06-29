"""
strategy/fvg_entry.py — Gate 3: ICT Silver Bullet / FVG Entry Engine

Replaces the reversal candle gate with an ICT-based institutional entry system.
Sequential logic within three high-probability time windows:
  1. Time window check  (Silver Bullet windows only)
  2. ATR regime filter  (trending market, not compressed)
  3. Liquidity sweep    (Grade A if sweep detected, Grade B if FVG only)
  4. FVG detection      (3-candle imbalance with strong displacement)
  5. Entry trigger      (price retracing into an unmitigated FVG zone)
"""

import pandas as pd
from utils.logger import setup_logger

logger = setup_logger("fvg_entry")

# ── Silver Bullet windows (UTC) ────────────────────────────────────────────────
# (start_hour, start_min, end_hour, end_min)
SILVER_BULLET_WINDOWS = [
    (8,  0, 9,  0),   # London Open
    (15, 0, 16, 0),   # NY AM — Golden Hour
    (19, 0, 20, 0),   # NY PM
]

SWING_LOOKBACK            = 5     # bars each side to confirm a swing high/low
SWEEP_SCAN_BARS           = 4     # completed bars to scan for a liquidity sweep
FVG_MIN_SIZE_PIPS         = 5     # minimum FVG size (Gold: 1 pip = 0.1 price)
FVG_MAX_AGE_BARS          = 10    # bars before an unretested FVG expires
FVG_DISPLACEMENT_ATR_MULT = 1.0   # displacement candle body must be >= ATR × this


# ── Time window ───────────────────────────────────────────────────────────────

def is_silver_bullet_window(hour: int, minute: int) -> bool:
    """True if the given UTC hour:minute falls inside a Silver Bullet window."""
    t = hour * 60 + minute
    for sh, sm, eh, em in SILVER_BULLET_WINDOWS:
        if sh * 60 + sm <= t < eh * 60 + em:
            return True
    return False


# ── Swing high/low detection ──────────────────────────────────────────────────

def detect_swing_highs_lows(df: pd.DataFrame, lookback: int = SWING_LOOKBACK) -> dict:
    """
    Return the most recent confirmed swing high and swing low in df.
    Only scans completed bars (excludes df.iloc[-1] which is forming).
    """
    n      = len(df)
    highs  = []
    lows   = []

    for i in range(lookback, n - lookback - 1):
        bar = df.iloc[i]
        neighbors = list(range(i - lookback, i)) + list(range(i + 1, i + lookback + 1))
        if all(bar["high"] >= df.iloc[j]["high"] for j in neighbors):
            highs.append({"idx": i, "price": float(bar["high"])})
        if all(bar["low"] <= df.iloc[j]["low"] for j in neighbors):
            lows.append({"idx": i, "price": float(bar["low"])})

    return {
        "swing_highs": highs,
        "swing_lows":  lows,
        "last_high":   highs[-1] if highs else None,
        "last_low":    lows[-1]  if lows  else None,
    }


# ── Liquidity sweep ───────────────────────────────────────────────────────────

def detect_liquidity_sweep(df: pd.DataFrame, trend_direction: str) -> dict | None:
    """
    Detect a stop-hunt: price wicks beyond a recent swing level then closes back.

    BUY  sweep: wick below most recent swing low, closes back above it
    SELL sweep: wick above most recent swing high, closes back below it

    Returns sweep details dict or None.
    """
    if len(df) < SWING_LOOKBACK * 2 + 3:
        return None

    swings = detect_swing_highs_lows(df)
    n      = len(df)

    for offset in range(2, min(SWEEP_SCAN_BARS + 2, n)):
        bar = df.iloc[-offset]
        if trend_direction == "BUY":
            ref = swings.get("last_low")
            if ref and bar["low"] < ref["price"] and bar["close"] > ref["price"]:
                return {"type": "BUY_SWEEP", "level": ref["price"], "bar_idx": n - offset}
        else:
            ref = swings.get("last_high")
            if ref and bar["high"] > ref["price"] and bar["close"] < ref["price"]:
                return {"type": "SELL_SWEEP", "level": ref["price"], "bar_idx": n - offset}

    return None


# ── FVG detection ─────────────────────────────────────────────────────────────

def detect_fvg(df: pd.DataFrame, trend_direction: str, atr: float,
               max_age_bars: int = FVG_MAX_AGE_BARS,
               displacement_mult: float = FVG_DISPLACEMENT_ATR_MULT,
               min_size_pips: float = FVG_MIN_SIZE_PIPS) -> list:
    """
    Scan the last max_age_bars bars for Fair Value Gaps aligned with trend_direction.

    Bullish FVG (3-candle sequence A, B, C):
        A.high < C.low  AND  B is strong bullish (body > ATR × mult)
        Zone: A.high → C.low

    Bearish FVG:
        A.low > C.high  AND  B is strong bearish
        Zone: C.high → A.low

    Only returns unmitigated FVGs — mitigation is checked on the current bar.
    """
    fvgs    = []
    min_gap = min_size_pips * 0.1   # Gold: 1 pip = $0.10
    n       = len(df)
    # Third candle (C) can range from index 2 to n-2 (n-1 is forming)
    start   = max(2, n - max_age_bars)

    current = df.iloc[-1]   # forming bar — used for mitigation check

    for i in range(start, n - 1):
        c_a    = df.iloc[i - 2]   # first candle
        c_b    = df.iloc[i - 1]   # displacement candle
        c_c    = df.iloc[i]       # third candle sealing the gap
        body_b = abs(c_b["close"] - c_b["open"])

        if trend_direction == "BUY" and c_b["close"] > c_b["open"]:
            gap_low  = float(c_a["high"])
            gap_high = float(c_c["low"])
            gap_size = gap_high - gap_low
            if gap_size >= min_gap and body_b >= atr * displacement_mult:
                # Skip if already mitigated by current price action
                if current["close"] < gap_low:
                    continue
                fvgs.append({
                    "direction": "BUY",
                    "zone_low":  round(gap_low,  2),
                    "zone_high": round(gap_high, 2),
                    "midpoint":  round((gap_low + gap_high) / 2, 2),
                    "bar_idx":   i,
                })

        elif trend_direction == "SELL" and c_b["close"] < c_b["open"]:
            gap_high = float(c_a["low"])
            gap_low  = float(c_c["high"])
            gap_size = gap_high - gap_low
            if gap_size >= min_gap and body_b >= atr * displacement_mult:
                if current["close"] > gap_high:
                    continue
                fvgs.append({
                    "direction": "SELL",
                    "zone_low":  round(gap_low,  2),
                    "zone_high": round(gap_high, 2),
                    "midpoint":  round((gap_low + gap_high) / 2, 2),
                    "bar_idx":   i,
                })

    return fvgs


# ── Main Gate 3 function ──────────────────────────────────────────────────────

def get_fvg_signal(df: pd.DataFrame, trend_direction: str, atr: float, utc_time) -> dict:
    """
    Main Gate 3 entry function. Called with the M5 candle slice, H1 trend direction,
    current ATR, and current UTC time (datetime or pd.Timestamp).

    Returns:
        {
            "signal":   "BUY" | "SELL" | "NONE",
            "entry":    float,   # FVG midpoint (optimal RR entry)
            "sl":       float,   # FVG-derived stop loss
            "fvg_zone": (low, high) | None,
            "grade":    "A" | "B" | None,
        }
    """
    NONE = {"signal": "NONE", "entry": 0.0, "sl": 0.0, "fvg_zone": None, "grade": None}

    if len(df) < FVG_MAX_AGE_BARS + 10:
        return NONE

    # ── Step 1: Time window ───────────────────────────────────────────────────
    if not is_silver_bullet_window(int(utc_time.hour), int(utc_time.minute)):
        return NONE

    # ── Step 2: ATR regime filter ─────────────────────────────────────────────
    # Skip compressed/choppy markets where ATR is below its 20-bar average
    atr_series = df["atr"].iloc[-22:-1]
    if len(atr_series) >= 10:
        atr_ma = float(atr_series.mean())
        if atr < atr_ma:
            logger.debug(f"ATR regime blocked: ATR={atr:.2f} < MA={atr_ma:.2f}")
            return NONE

    # ── Step 3: Liquidity sweep (determines grade) ────────────────────────────
    sweep = detect_liquidity_sweep(df, trend_direction)
    grade = "A" if sweep else "B"

    # ── Step 4: FVG scan ──────────────────────────────────────────────────────
    fvgs = detect_fvg(df, trend_direction, atr)
    if not fvgs:
        logger.debug(f"No valid {trend_direction} FVG in window")
        return NONE

    # ── Step 5: Entry trigger — price inside an unmitigated FVG ──────────────
    # Use the midpoint of the forming bar as current price approximation
    forming    = df.iloc[-1]
    mid_price  = (float(forming["high"]) + float(forming["low"])) / 2

    # Check most recent FVGs first
    for fvg in reversed(fvgs):
        if fvg["zone_low"] <= mid_price <= fvg["zone_high"]:
            buf = atr * 0.2
            if fvg["direction"] == "BUY":
                sl = round(fvg["zone_low"] - buf, 2)
            else:
                sl = round(fvg["zone_high"] + buf, 2)

            logger.info(
                f"Gate 3 FVG {fvg['direction']} | Grade {grade} | "
                f"Zone {fvg['zone_low']}–{fvg['zone_high']} | "
                f"Entry {fvg['midpoint']} | SL {sl}"
            )
            return {
                "signal":   fvg["direction"],
                "entry":    fvg["midpoint"],
                "sl":       sl,
                "fvg_zone": (fvg["zone_low"], fvg["zone_high"]),
                "grade":    grade,
            }

    logger.debug(f"FVGs found but price not inside any zone | {trend_direction}")
    return NONE
