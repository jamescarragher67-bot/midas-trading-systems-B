"""
strategy/gold_compression_breakout.py - Compression Breakout (M5)

Candidate 5 from the 2026-09-01 strategy scoping pass. Stays on XAUUSD.a
(no new-instrument risk) but replaces LSC's day-range-derived stop with a
tight, purely ATR-relative stop, on a shorter timeframe - designed
specifically to attack the two problems LSC hit at small-account sizes:
the 0.01-lot floor and the wide tail-variance of day-range-sized stops.

Concept: identify a volatility SQUEEZE (recent ATR compressed well below
its longer-run average, price coiled in a tight range), then trade the
BREAKOUT when price closes decisively outside that squeeze range, with a
stop pinned to *local* short-horizon ATR (0.5x) rather than whatever the
prior day's whole range happened to be. This is a genuinely different
signal from LSC's liquidity-sweep-continuation, not a re-parameterization
of it - it needs its own edge validation, no assumption LSC's edge
transfers.

Exposes the same pure-function interface as strategy/lsc_m15.py
(precompute(df), check_entry(df, i, ...)) so backtest/gold_compression_engine.py
and any future live wiring can follow the identical LSC pattern.
"""

import pandas as pd

ATR_PERIOD              = 14
ATR_MA_PERIOD           = 50    # baseline to measure "compressed relative to what"
COMPRESSION_RATIO_MAX   = 0.75  # squeeze window's own ATR must be < 75% of the 50-bar ATR average
RANGE_LOOKBACK          = 12    # bars forming the squeeze range (1 hour on M5)
BREAKOUT_CONFIRM_ATR_MULT = 0.15  # close must clear the squeeze boundary by this many ATRs
SL_ATR_MULT             = 0.5   # tight stop - the deliberate change from LSC's day-range stop
REWARD_RATIO            = 2.0   # same discipline as LSC
COOLDOWN_BARS           = 9     # 45 minutes on M5 (9 x 5min) - matches LSC's 45-min cooldown in wall-clock terms
MAX_TRADES_PER_DAY      = 4     # inherited from LSC as a starting assumption, not re-derived


def precompute(df: pd.DataFrame) -> pd.DataFrame:
    """Adds squeeze-range and compression-ratio columns. Expects df to
    already have an 'atr' column (ATR14) - see backtest engine for that,
    matching lsc_m15.py's convention of taking a pre-fetched, indicator-
    added DataFrame."""
    df = df.copy()
    atr_ma = df["atr"].rolling(ATR_MA_PERIOD).mean()
    df["compression_ratio"] = df["atr"] / atr_ma

    # Squeeze range formed by the RANGE_LOOKBACK bars strictly BEFORE the
    # current bar (shift(1) before the rolling window) - never includes the
    # current (potential breakout) bar itself, avoiding lookahead.
    prior_high = df["high"].shift(1)
    prior_low  = df["low"].shift(1)
    df["squeeze_high"] = prior_high.rolling(RANGE_LOOKBACK).max()
    df["squeeze_low"]  = prior_low.rolling(RANGE_LOOKBACK).min()
    # Was the squeeze window itself actually compressed? Average compression
    # ratio over the same lookback window, evaluated on the PRIOR bars only.
    df["squeeze_compression"] = df["compression_ratio"].shift(1).rolling(RANGE_LOOKBACK).mean()

    return df


def check_entry(df: pd.DataFrame, i: int, last_trade_bar: int, trades_today: int,
                 cooldown_bars: int = COOLDOWN_BARS,
                 max_trades_per_day: int = MAX_TRADES_PER_DAY):
    """Returns (direction, reason, sl, tp). direction is 'BUY'/'SELL'/'NEUTRAL'."""
    if trades_today >= max_trades_per_day:
        return "NEUTRAL", "max_trades_today", None, None
    if i - last_trade_bar < cooldown_bars:
        return "NEUTRAL", "cooldown", None, None

    bar = df.iloc[i]
    atr = bar["atr"]
    squeeze_high, squeeze_low = bar["squeeze_high"], bar["squeeze_low"]
    squeeze_compression       = bar["squeeze_compression"]
    close = bar["close"]

    if any(pd.isna(x) for x in (atr, squeeze_high, squeeze_low, squeeze_compression)):
        return "NEUTRAL", "warmup", None, None

    if squeeze_compression >= COMPRESSION_RATIO_MAX:
        return "NEUTRAL", "no_squeeze", None, None

    confirm = atr * BREAKOUT_CONFIRM_ATR_MULT
    sl_dist = atr * SL_ATR_MULT

    # Breakout above the squeeze - continuation BUY
    if close > squeeze_high + confirm:
        sl = close - sl_dist
        tp = close + sl_dist * REWARD_RATIO
        return "BUY", f"compression_breakout_above={squeeze_high:.2f}", sl, tp

    # Breakout below the squeeze - continuation SELL
    if close < squeeze_low - confirm:
        sl = close + sl_dist
        tp = close - sl_dist * REWARD_RATIO
        return "SELL", f"compression_breakout_below={squeeze_low:.2f}", sl, tp

    return "NEUTRAL", "no_breakout", None, None
