"""
strategy/tournament2/bb_squeeze_sma.py - Candidate 3: Bollinger Band squeeze
then SMA(20) close-through, M15.

Source description: BB(20, 2 sd) + band width. Compression = width in the
bottom 10% of its own rolling 100-bar range. BUY when a full candle closes
above the SMA(20) after compression; SELL when one closes below. SL at the
preceding candle's high/low, 2:1 target, OR max 3% account risk, whichever
is smaller.

Interpretation choices (flagged):
  - "Bottom 10% of its own rolling 100-bar range" is read literally as a
    RANGE: width <= min100 + 0.10 * (max100 - min100). (Tournament 1's BB
    candidate used a 20th-PERCENTILE rule from a different source; that is
    not this source's wording.)
  - Width = (upper - lower) / middle; bands use population std (ddof=0),
    the textbook Bollinger definition and what MT5/TradingView compute.
  - "After compression" = the band was compressed on the bar BEFORE the
    breakout candle (the breakout candle itself starts expanding the band).
  - "A full candle closes above the SMA(20)" = a completed candle whose
    close is above the SMA while the previous close was at/below it (a
    close-through of the mid-band, one discrete trigger), not every bar
    that happens to sit above the mid-band during a squeeze.
  - The "max 3% account risk" clause is a NO-OP in this framework: Rounds
    1-2 run at the tournament-wide 1.0% footing and Round 3 calibrates far
    below that, so the smaller of the two is always the framework's risk%.
  - SL = preceding candle's low (BUY) / high (SELL); TP 2:1 from the signal
    bar's close; entry at the next bar's open. If the next open gaps past
    the SL the engine skips the trade (its standing sl_dist <= 0 rule).
"""

import numpy as np
import pandas as pd

from research.strategy.tournament2._base import M15Candidate


class BBSqueezeSMA(M15Candidate):
    BB_PERIOD, BB_STD   = 20, 2.0
    RANGE_LOOKBACK      = 100
    SQUEEZE_FRACTION    = 0.10
    name  = "base"
    label = ("BB(20,2) width in bottom 10% of rolling 100-bar range, then close-through "
             "of SMA20, SL prior candle extreme, TP 2:1")

    def precompute(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        mid = df["close"].rolling(self.BB_PERIOD).mean()
        std = df["close"].rolling(self.BB_PERIOD).std(ddof=0)
        upper, lower = mid + self.BB_STD * std, mid - self.BB_STD * std
        width = (upper - lower) / mid
        wmin = width.rolling(self.RANGE_LOOKBACK).min()
        wmax = width.rolling(self.RANGE_LOOKBACK).max()
        df["bb_mid"]   = mid
        df["bb_width"] = width
        df["squeeze"]  = width <= wmin + self.SQUEEZE_FRACTION * (wmax - wmin)
        df.loc[wmax.isna(), "squeeze"] = False
        return df

    def check_entry(self, df, i, last_trade_bar, trades_today, cooldown_bars, max_trades_per_day):
        if self.gated(i, last_trade_bar, trades_today, cooldown_bars, max_trades_per_day):
            return "NEUTRAL", "gated", None, None
        if i < 1:
            return "NEUTRAL", "warmup", None, None
        bar, prev = df.iloc[i], df.iloc[i - 1]
        if pd.isna(bar["bb_mid"]) or pd.isna(prev["bb_mid"]) or pd.isna(bar["atr"]):
            return "NEUTRAL", "warmup", None, None
        if not prev["squeeze"]:
            return "NEUTRAL", "no_squeeze", None, None
        close, mid = float(bar["close"]), float(bar["bb_mid"])
        pclose, pmid = float(prev["close"]), float(prev["bb_mid"])
        if close > mid and pclose <= pmid:
            sl = float(prev["low"])
            tp = close + (close - sl) * self.REWARD_RATIO
            return "BUY", "squeeze_close_above_sma", sl, tp
        if close < mid and pclose >= pmid:
            sl = float(prev["high"])
            tp = close - (sl - close) * self.REWARD_RATIO
            return "SELL", "squeeze_close_below_sma", sl, tp
        return "NEUTRAL", "no_signal", None, None
