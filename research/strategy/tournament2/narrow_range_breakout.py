"""
strategy/tournament2/narrow_range_breakout.py - Candidate 4: narrow-range
candle breakout (NR4 / NR7 sub-variants), M15.

Source description: identify the narrowest-range candle (high - low) of the
last 4 bars (and separately 7). BUY on a break above that candle's high,
SELL on a break below its low. SL at the narrow candle's opposite extreme,
2:1 target.

Interpretation choices (flagged):
  - "Last N bars" = the N COMPLETED bars before the breakout candle
    (i-N .. i-1). The narrowest may be any of them (the source does not
    require it to be the most recent bar, unlike the classic NR4/NR7
    definition, which is a rarer setup). Ties go to the earliest bar.
  - "Break above" is taken on a CLOSE beyond the level (every tournament
    candidate signals on closed bars and enters at the next open; the
    engine has no stop-order fill), and only a FRESH break counts: the
    previous close must not already have been beyond the same level,
    otherwise a trend produces an entry on every bar until the daily cap.
  - SL at the narrow candle's opposite extreme, TP 2:1 from the signal
    bar's close.
"""

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view

from research.strategy.tournament2._base import M15Candidate


class NarrowRangeBreakout(M15Candidate):
    def __init__(self, n: int):
        self.n = n
        self.name  = f"nr{n}"
        self.label = (f"Narrowest-range bar of the prior {n}, close-through of its high/low, "
                      f"SL at its opposite extreme, TP 2:1")

    def precompute(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        n, N = self.n, len(df)
        high = df["high"].to_numpy(float); low = df["low"].to_numpy(float)
        rng = high - low
        nr_high = np.full(N, np.nan); nr_low = np.full(N, np.nan)
        win = sliding_window_view(rng, n)                 # win[k] = rng[k .. k+n-1]
        idx = np.arange(win.shape[0]) + win.argmin(axis=1)  # absolute index of narrowest bar
        # bar i uses the window starting at k = i - n, valid for i in [n, N-1]
        nr_high[n:] = high[idx[:N - n]]
        nr_low[n:]  = low[idx[:N - n]]
        df["nr_high"] = nr_high
        df["nr_low"]  = nr_low
        return df

    def check_entry(self, df, i, last_trade_bar, trades_today, cooldown_bars, max_trades_per_day):
        if self.gated(i, last_trade_bar, trades_today, cooldown_bars, max_trades_per_day):
            return "NEUTRAL", "gated", None, None
        if i < self.n + 1:
            return "NEUTRAL", "warmup", None, None
        bar, prev = df.iloc[i], df.iloc[i - 1]
        nh, nl = bar["nr_high"], bar["nr_low"]
        if pd.isna(nh) or pd.isna(bar["atr"]):
            return "NEUTRAL", "warmup", None, None
        close, pclose = float(bar["close"]), float(prev["close"])
        nh, nl = float(nh), float(nl)
        if close > nh and pclose <= nh:
            tp = close + (close - nl) * self.REWARD_RATIO
            return "BUY", f"nr{self.n}_break_up", nl, tp
        if close < nl and pclose >= nl:
            tp = close - (nh - close) * self.REWARD_RATIO
            return "SELL", f"nr{self.n}_break_down", nh, tp
        return "NEUTRAL", "no_signal", None, None
