"""
strategy/tournament2/heikin_ashi_reversal.py - Candidate 2: Heikin-Ashi
reversal confirmed by Stochastic(14,7,3), M15.

Source description: LONG = 2 consecutive bearish HA candles followed by a
reversal, confirmed by Stochastic(14,7,3) below 30 crossing back up; SHORT
mirrored above 70. SL beyond the second reversal candle's extreme, 2:1 target.

Interpretation choices (the source leaves these open - flagged, not guessed
silently; the two readings of the stochastic confirmation are run as
sub-variants because they differ materially):
  - "Reversal" = the first bullish HA candle (HA close > HA open) after AT
    LEAST two consecutive bearish HA candles (bars i-1 and i-2 bearish).
  - Stochastic(14,7,3) is read in MT5's parameter order: %K period 14,
    %D period 7, slowing 3 (SMA smoothing of raw %K, SMA %D of the slowed %K).
  - Confirmation reading A ("stoch_level30"): slowed %K was below 30 on the
    previous bar and is at/above 30 on the signal bar (crosses back up
    through the 30 level).
    Confirmation reading B ("stoch_kd_cross"): slowed %K crosses above %D on
    the signal bar with %K below 30 on the previous bar (a %K/%D cross
    inside the oversold zone).
    Both must occur ON the reversal candle itself - the source gives no
    look-back window for the confirmation.
  - "Second reversal candle's extreme": the pattern is bearish #1, bearish
    #2, reversal. Taken as the lowest REAL low (not HA low - HA lows are not
    tradeable prices) across bearish #2 and the reversal candle, i.e. the
    extreme of the turn; SELL mirrored on real highs.
  - SL/TP measured from the signal bar's close; entry at the next bar's open.
"""

import numpy as np
import pandas as pd

from research.strategy.tournament2._base import M15Candidate


class HeikinAshiReversal(M15Candidate):
    K_PERIOD, D_PERIOD, SLOWING = 14, 7, 3
    OVERSOLD, OVERBOUGHT = 30, 70

    def __init__(self, confirm: str):
        assert confirm in ("level", "kd")
        self.confirm = confirm
        self.name  = "stoch_level30" if confirm == "level" else "stoch_kd_cross"
        self.label = ("HA reversal after >=2 opposite HA candles, Stoch(14,7,3) "
                      + ("%K crosses back through 30/70" if confirm == "level"
                         else "%K/%D cross inside <30 / >70")
                      + ", SL beyond turn extreme, TP 2:1")

    def precompute(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        o = df["open"].to_numpy(float); h = df["high"].to_numpy(float)
        l = df["low"].to_numpy(float);  c = df["close"].to_numpy(float)
        ha_c = (o + h + l + c) / 4.0
        ha_o = np.empty(len(df))
        ha_o[0] = (o[0] + c[0]) / 2.0
        for k in range(1, len(df)):
            ha_o[k] = (ha_o[k - 1] + ha_c[k - 1]) / 2.0
        df["ha_bull"] = ha_c > ha_o
        df["ha_bear"] = ha_c < ha_o

        ll = df["low"].rolling(self.K_PERIOD).min()
        hh = df["high"].rolling(self.K_PERIOD).max()
        raw_k = 100.0 * (df["close"] - ll) / (hh - ll).replace(0.0, np.nan)
        df["stoch_k"] = raw_k.rolling(self.SLOWING).mean()
        df["stoch_d"] = df["stoch_k"].rolling(self.D_PERIOD).mean()
        return df

    def _confirm_up(self, k0, k1, d0, d1) -> bool:
        if self.confirm == "level":
            return k1 < self.OVERSOLD <= k0
        return k1 <= d1 and k0 > d0 and k1 < self.OVERSOLD

    def _confirm_down(self, k0, k1, d0, d1) -> bool:
        if self.confirm == "level":
            return k1 > self.OVERBOUGHT >= k0
        return k1 >= d1 and k0 < d0 and k1 > self.OVERBOUGHT

    def check_entry(self, df, i, last_trade_bar, trades_today, cooldown_bars, max_trades_per_day):
        if self.gated(i, last_trade_bar, trades_today, cooldown_bars, max_trades_per_day):
            return "NEUTRAL", "gated", None, None
        if i < 2:
            return "NEUTRAL", "warmup", None, None
        bar, p1, p2 = df.iloc[i], df.iloc[i - 1], df.iloc[i - 2]
        k0, k1, d0, d1 = bar["stoch_k"], p1["stoch_k"], bar["stoch_d"], p1["stoch_d"]
        if any(pd.isna(x) for x in (k0, k1, d0, d1, bar["atr"])):
            return "NEUTRAL", "warmup", None, None
        close = float(bar["close"])

        if bar["ha_bull"] and p1["ha_bear"] and p2["ha_bear"] and self._confirm_up(k0, k1, d0, d1):
            sl = min(float(p1["low"]), float(bar["low"]))
            tp = close + (close - sl) * self.REWARD_RATIO
            return "BUY", "ha_reversal_up", sl, tp
        if bar["ha_bear"] and p1["ha_bull"] and p2["ha_bull"] and self._confirm_down(k0, k1, d0, d1):
            sl = max(float(p1["high"]), float(bar["high"]))
            tp = close - (sl - close) * self.REWARD_RATIO
            return "SELL", "ha_reversal_down", sl, tp
        return "NEUTRAL", "no_signal", None, None
