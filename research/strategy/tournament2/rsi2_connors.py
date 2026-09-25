"""
strategy/tournament2/rsi2_connors.py - Candidate 5: 2-period RSI, M15.

Source description: BUY when RSI(2) moves/crosses above 90; SELL when RSI(2)
crosses below 10. Tight, aggressive stop (tested at 0.5x ATR14). The source
warns it gets "caught out by a ranging market" without a trend filter, so
the brief asks for with/without a longer-EMA filter.

IMPORTANT discrepancy, flagged rather than silently "fixed": the source's
orientation is the REVERSE of Larry Connors' published RSI(2) rule. Connors
BUYS when RSI(2) is below 10 (an oversold dip) with price above the 200-day
MA, and SELLS when RSI(2) is above 90 with price below it - a pullback
strategy. The source as written BUYS the >90 spike and SELLS the <10 dip,
which is a momentum/exhaustion-continuation entry, not mean reversion. The
tournament candidates are coded AS THE SOURCE STATES. A single true-Connors-
orientation instance (invert=True) is run alongside as a labelled
diagnostic only - it is not a candidate and is not ranked.

Sub-variants (all four are ranked; the best in-sample one carries the
candidate forward):
  trigger "cross": RSI(2) crosses above 90 (prev <= 90 < now) / below 10.
  trigger "level": RSI(2) is above 90 / below 10 on the bar (the source's
                   literal "moves above"); re-triggers on consecutive bars,
                   throttled only by the framework's 3-bar cooldown and
                   4-trades/day cap.
  filter None / "ema200": BUY only above EMA200, SELL only below.

Interpretation choices (flagged):
  - Trend-filter length is not given; EMA200 (Connors' own 200-period MA)
    is used, on the same M15 bars.
  - No target is given; the framework's 2:1 is applied from the signal
    bar's close, SL = 0.5x ATR14.
  - RSI is Wilder's (EWM alpha=1/2), the same _rsi() tournament 1 used.
"""

import pandas as pd

from research.strategy.tournament2._base import M15Candidate
from research.strategy.tournament.rsi_mean_reversion import _rsi


class RSI2(M15Candidate):
    RSI_PERIOD  = 2
    UPPER, LOWER = 90, 10
    SL_ATR_MULT = 0.5
    TREND_EMA   = 200

    def __init__(self, trigger: str, trend_filter: bool, invert: bool = False):
        assert trigger in ("cross", "level")
        self.trigger, self.trend_filter, self.invert = trigger, trend_filter, invert
        self.name  = f"{trigger}_{'ema200' if trend_filter else 'nofilter'}" + ("_TRUE_CONNORS_DIAG" if invert else "")
        self.label = ((f"RSI(2) {'crosses' if trigger == 'cross' else 'is'} "
                       + ("<10 BUY / >90 SELL (true Connors orientation - DIAGNOSTIC)" if invert
                          else ">90 BUY / <10 SELL (as the source states)"))
                      + (", EMA200 trend filter" if trend_filter else ", no filter")
                      + ", SL 0.5xATR14, TP 2:1")

    def precompute(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df["rsi2"]   = _rsi(df["close"], self.RSI_PERIOD)
        df["ema200"] = df["close"].ewm(span=self.TREND_EMA, adjust=False).mean()
        return df

    def check_entry(self, df, i, last_trade_bar, trades_today, cooldown_bars, max_trades_per_day):
        if self.gated(i, last_trade_bar, trades_today, cooldown_bars, max_trades_per_day):
            return "NEUTRAL", "gated", None, None
        if i < self.TREND_EMA:
            return "NEUTRAL", "warmup", None, None
        bar, prev = df.iloc[i], df.iloc[i - 1]
        r0, r1 = bar["rsi2"], prev["rsi2"]
        if pd.isna(r0) or pd.isna(r1) or pd.isna(bar["atr"]):
            return "NEUTRAL", "warmup", None, None
        if self.trigger == "cross":
            up, dn = (r1 <= self.UPPER < r0), (r1 >= self.LOWER > r0)
        else:
            up, dn = (r0 > self.UPPER), (r0 < self.LOWER)
        if self.invert:
            up, dn = dn, up
        close, atr = float(bar["close"]), float(bar["atr"])
        if self.trend_filter:
            up = up and close > bar["ema200"]
            dn = dn and close < bar["ema200"]
        if up:
            sl = close - self.SL_ATR_MULT * atr
            tp = close + (close - sl) * self.REWARD_RATIO
            return "BUY", f"rsi2_{self.trigger}_up", sl, tp
        if dn:
            sl = close + self.SL_ATR_MULT * atr
            tp = close - (sl - close) * self.REWARD_RATIO
            return "SELL", f"rsi2_{self.trigger}_down", sl, tp
        return "NEUTRAL", "no_signal", None, None
