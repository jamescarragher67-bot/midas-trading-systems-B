"""
strategy/lsc_m15.py - Liquidity-Sweep Continuation (M15)

Midas Stage 4 strategy. Validated 2026-08-02: in-sample + OOS positive,
held up independently across two non-overlapping ~1100-trade halves
(early PF 1.23 / recent PF 1.21), and PF/expectancy confirmed invariant
under 1000-shuffle Monte Carlo reordering. See project memory for the
full validation history.

When price sweeps beyond the prior UTC day's high/low (a liquidity grab)
and then closes convincingly beyond that level in the sweep direction,
treat it as continuation, not exhaustion - the corrected version of the
Asian Range concept (strategy/asian_range_reversal.py, a53504f, never
wired to anything) that failed because it faded the sweep instead.

Exposes pure functions taking a pre-fetched, indicator-added M15
DataFrame (needs 'atr', 'high', 'low', 'close'), so the exact same code
path runs in both live trading and backtesting - see backtest/lsc_engine.py.
"""

import pandas as pd

CLOSE_BEYOND_ATR_MULT = 0.2   # how far past the prior level the close must be, to confirm
SL_BUFFER_ATR_MULT    = 0.3
REWARD_RATIO          = 2.0
COOLDOWN_BARS          = 3     # 45 minutes = 3 x M15 bars
MAX_TRADES_PER_DAY     = 4


def compute_prior_session_range(df: pd.DataFrame):
    """Prior UTC day's high/low, mapped onto every bar of the current day."""
    day = df.index.normalize()
    tmp = pd.DataFrame({"high": df["high"], "low": df["low"], "day": day})
    agg = tmp.groupby("day").agg(day_high=("high", "max"), day_low=("low", "min"))
    agg_prior = agg.shift(1)
    prior_high = day.to_series(index=df.index).map(agg_prior["day_high"])
    prior_low  = day.to_series(index=df.index).map(agg_prior["day_low"])
    return prior_high, prior_low


def precompute(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["prior_high"], df["prior_low"] = compute_prior_session_range(df)
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
    prior_high, prior_low, atr = bar["prior_high"], bar["prior_low"], bar["atr"]
    high, low, close = bar["high"], bar["low"], bar["close"]

    if any(pd.isna(x) for x in (prior_high, prior_low, atr)):
        return "NEUTRAL", "warmup", None, None

    confirm = atr * CLOSE_BEYOND_ATR_MULT
    buffer  = atr * SL_BUFFER_ATR_MULT

    # Swept above prior high, closed convincingly above it - continuation BUY
    if high > prior_high and close > prior_high + confirm:
        sl = prior_high - buffer
        sl_dist = close - sl
        tp = close + sl_dist * REWARD_RATIO
        return "BUY", f"sweep_continuation_above={prior_high:.2f}", sl, tp

    # Swept below prior low, closed convincingly below it - continuation SELL
    if low < prior_low and close < prior_low - confirm:
        sl = prior_low + buffer
        sl_dist = sl - close
        tp = close - sl_dist * REWARD_RATIO
        return "SELL", f"sweep_continuation_below={prior_low:.2f}", sl, tp

    return "NEUTRAL", "no_sweep_continuation", None, None
