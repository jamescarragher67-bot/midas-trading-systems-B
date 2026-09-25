"""
strategy/tournament2/ma_crossover.py - Candidate 1: EMA 20/60 crossover with
EMA 100 trend filter, M15.

Source description: BUY when EMA20 crosses above EMA60 AND price is above
EMA100; SELL when EMA20 crosses below EMA60 AND price is below EMA100; exit
on the opposite crossover. The source has no SL/TP, so per the tournament
brief the framework's ATR-based stop is applied (0.5x and 1.0x ATR14 as two
sub-variants) with a 2:1 target; a trade therefore closes on whichever comes
first: SL, TP, opposite crossover (filled at the next bar's open, same
no-lookahead convention as entries), or the hold cap.

Interpretation choices (not stated in the source, flagged here):
  - "price" for the EMA100 filter = the signal bar's close.
  - The exit crossover is the raw EMA20/EMA60 cross, NOT re-filtered by
    EMA100 (the source says "opposite crossover", not "opposite signal").
  - MAX_HOLD_BARS is raised from the framework's 96 to 960 (~2 trading
    weeks): with an explicit crossover exit the 1-day cap would otherwise
    override the source's exit rule on most trades. Reported in the exit-
    reason breakdown so the effect is visible.
  - SL/TP are measured from the signal bar's close (as every tournament-1
    candidate did); entry is the next bar's open.
"""

import pandas as pd

from research.strategy.tournament2._base import M15Candidate


class MACrossover(M15Candidate):
    MAX_HOLD_BARS = 960
    FAST, SLOW, TREND = 20, 60, 100

    def __init__(self, sl_atr_mult: float):
        self.sl_atr_mult = sl_atr_mult
        self.name  = f"sl{sl_atr_mult}atr"
        self.label = (f"EMA20/EMA60 cross + EMA100 filter, SL {sl_atr_mult}xATR14, "
                      f"TP 2:1, exit on opposite cross")

    def precompute(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        c = df["close"]
        df["ema_fast"]  = c.ewm(span=self.FAST,  adjust=False).mean()
        df["ema_slow"]  = c.ewm(span=self.SLOW,  adjust=False).mean()
        df["ema_trend"] = c.ewm(span=self.TREND, adjust=False).mean()
        above = (df["ema_fast"] > df["ema_slow"]).astype(int)
        d = above.diff()
        df["x_up"] = d == 1
        df["x_dn"] = d == -1
        return df

    def check_entry(self, df, i, last_trade_bar, trades_today, cooldown_bars, max_trades_per_day):
        if self.gated(i, last_trade_bar, trades_today, cooldown_bars, max_trades_per_day):
            return "NEUTRAL", "gated", None, None
        bar = df.iloc[i]
        if pd.isna(bar["atr"]) or pd.isna(bar["ema_trend"]) or i < self.TREND:
            return "NEUTRAL", "warmup", None, None
        close, atr = float(bar["close"]), float(bar["atr"])
        if bar["x_up"] and close > bar["ema_trend"]:
            sl = close - self.sl_atr_mult * atr
            tp = close + (close - sl) * self.REWARD_RATIO
            return "BUY", "ema_cross_up", sl, tp
        if bar["x_dn"] and close < bar["ema_trend"]:
            sl = close + self.sl_atr_mult * atr
            tp = close - (sl - close) * self.REWARD_RATIO
            return "SELL", "ema_cross_down", sl, tp
        return "NEUTRAL", "no_signal", None, None

    def check_exit(self, df, j, direction) -> bool:
        bar = df.iloc[j]
        return bool(bar["x_dn"]) if direction == "BUY" else bool(bar["x_up"])
