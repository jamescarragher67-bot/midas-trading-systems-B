"""
strategy/tournament/goldmine.py - "Goldmine Strategy" concept: killzone +
liquidity sweep + retest, M15. Candidate #7.

SOURCE CAVEAT (per standing instructions - reporting this plainly rather
than guessing at precision that doesn't exist): "Goldmine Strategy" is not
a single canonical, code-published system. It's retail marketing content
(Medium/Coinmonks posts by "FXM Brand", plus assorted PDF/blog variants)
describing a qualitative playbook: consolidate during the Asian session,
sweep (false-break) that range during the London killzone, then enter on a
retest of the broken level with the reversal, 1-2% risk, 1:2-1:3 R:R. There
is no published indicator code, no exact range-detection threshold, and no
formal "retest" definition (how many bars, how close a re-test counts).
What follows is OUR mechanical interpretation of the qualitative rules,
built to be testable - not a verified replica of any specific trader's
actual system. In particular we collapse "sweep then retest" into a single
bar's wick-beyond-range-then-close-back-inside pattern (a same-bar
sweep-and-reclaim) rather than modeling a separate multi-bar retest leg,
since the source material never specifies retest timing precisely enough
to code literally.

Mechanics: Asian range = high/low of 00:00-07:00 UTC. During the London
killzone (07:00-10:00 UTC), a bar that wicks beyond the Asian high but
closes back inside the range is read as a swept-and-rejected false
breakout -> fade it (SELL). Mirror for a swept Asian low -> BUY. SL beyond
the sweep extreme + ATR buffer, TP at 2.5R (midpoint of the source's
stated 1:2-1:3 range).
"""

import pandas as pd
import MetaTrader5 as mt5

TIMEFRAME_MT5   = mt5.TIMEFRAME_M15
TIMEFRAME_LABEL = "M15"
MAX_HOLD_BARS   = 48
SESSION_HOURS   = {7, 8, 9}   # London killzone, UTC
COOLDOWN_BARS   = 3
MAX_TRADES_PER_DAY = 2

ASIAN_START_HOUR, ASIAN_END_HOUR = 0, 7   # UTC, [start, end)
SL_BUFFER_ATR_MULT = 0.3
REWARD_RATIO = 2.5


def precompute(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    day = df.index.normalize()
    in_asian = df.index.hour < ASIAN_END_HOUR
    tmp = pd.DataFrame({"high": df["high"], "low": df["low"], "day": day, "in_asian": in_asian})
    asian = tmp[tmp["in_asian"]].groupby("day").agg(a_high=("high", "max"), a_low=("low", "min"))
    df["asian_high"] = day.map(asian["a_high"])
    df["asian_low"]  = day.map(asian["a_low"])
    return df


def check_entry(df: pd.DataFrame, i: int, last_trade_bar: int, trades_today: int,
                cooldown_bars: int, max_trades_per_day: int):
    if trades_today >= max_trades_per_day or i - last_trade_bar < cooldown_bars:
        return "NEUTRAL", "gated", None, None

    bar = df.iloc[i]
    a_high, a_low, atr = bar["asian_high"], bar["asian_low"], bar["atr"]
    if any(pd.isna(x) for x in (a_high, a_low, atr)):
        return "NEUTRAL", "warmup", None, None

    high, low, close = bar["high"], bar["low"], bar["close"]
    buffer = atr * SL_BUFFER_ATR_MULT

    # Swept above Asian high, closed back inside -> false breakout, fade it SHORT
    if high > a_high and close < a_high:
        sl = high + buffer
        sl_dist = sl - close
        if sl_dist > 0:
            tp = close - sl_dist * REWARD_RATIO
            return "SELL", "sweep_asian_high_reject", sl, tp

    # Swept below Asian low, closed back inside -> false breakout, fade it LONG
    if low < a_low and close > a_low:
        sl = low - buffer
        sl_dist = close - sl
        if sl_dist > 0:
            tp = close + sl_dist * REWARD_RATIO
            return "BUY", "sweep_asian_low_reject", sl, tp

    return "NEUTRAL", "no_sweep", None, None
