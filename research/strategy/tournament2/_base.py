"""
strategy/tournament2/_base.py - shared framework attributes for the five
tournament-2 candidates (2026-09-16), all XAUUSD.a M15.

Every value here is the SAME common footing the tournament-1 M15 candidates
ran on (killzone_overlap / msb_trend_breakout): 1-trading-day safety-valve
hold cap, no session filter (none of the five source descriptions mentions
one), LSC's 3-bar cooldown and 4-trades/day cap, 2:1 reward:risk. Candidates
are class instances rather than modules so a sub-variant (e.g. SL 0.5 vs 1.0
ATR) is just a second instance; the engine only needs attribute access.
"""

import MetaTrader5 as mt5


class M15Candidate:
    TIMEFRAME_MT5      = mt5.TIMEFRAME_M15
    TIMEFRAME_LABEL    = "M15"
    MAX_HOLD_BARS      = 96      # 1 trading day of M15 bars, as tournament 1's M15 candidates
    SESSION_HOURS      = None
    COOLDOWN_BARS      = 3
    MAX_TRADES_PER_DAY = 4
    REWARD_RATIO       = 2.0
    name  = "base"
    label = "base"

    @staticmethod
    def gated(i, last_trade_bar, trades_today, cooldown_bars, max_trades_per_day) -> bool:
        return trades_today >= max_trades_per_day or i - last_trade_bar < cooldown_bars
