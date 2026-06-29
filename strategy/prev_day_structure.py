"""
strategy/prev_day_structure.py - Voter 4: Price Structure (Previous Day H/L)

Completely independent of any moving average or oscillator.
BUY  if current price is above the previous calendar day's high (PDH).
SELL if current price is below the previous calendar day's low  (PDL).
Abstains (0) if price is inside the prior day's range.

Previous day is the full UTC calendar day before today's date.
"""

import pandas as pd
import numpy as np
from utils.logger import setup_logger

logger = setup_logger("prev_day_structure")


def get_signal(df: pd.DataFrame) -> tuple[int, str]:
    try:
        current_bar  = df.iloc[-1]
        current_date = df.index[-1].date()
        current_close = float(current_bar["close"])

        # Get all bars from strictly before today
        prev_mask  = df.index.date < current_date
        prev_bars  = df[prev_mask]

        if len(prev_bars) < 12:
            return 0, "insufficient_prior_data"

        # Previous calendar day only
        prev_date  = prev_bars.index.date[-1]
        day_bars   = prev_bars[prev_bars.index.date == prev_date]

        if len(day_bars) == 0:
            return 0, "no_prev_day_bars"

        pdh = float(day_bars["high"].max())
        pdl = float(day_bars["low"].min())

        if current_close > pdh:
            return 1, f"above_pdh_{pdh:.2f}_price_{current_close:.2f}"
        if current_close < pdl:
            return -1, f"below_pdl_{pdl:.2f}_price_{current_close:.2f}"
        return 0, f"inside_pd_range_{pdl:.2f}-{pdh:.2f}"

    except Exception as e:
        logger.debug(f"Prev day structure error: {e}")
        return 0, "prev_day_structure_error"
