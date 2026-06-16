"""
strategy/voting_engine.py

Aggregates votes from all 8 strategies.
Requires VOTE_THRESHOLD out of 8 to agree before firing a signal.

Score: +8 (all bullish) to -8 (all bearish)
Default threshold: 6/8
"""

import pandas as pd
from utils.logger import setup_logger
from strategy.ema_stack            import get_signal as ema_signal
from strategy.rsi_divergence       import get_signal as rsi_signal
from strategy.bollinger_bands      import get_signal as bb_signal
from strategy.vwap_strategy        import get_signal as vwap_signal
from strategy.candlestick_patterns import get_signal as candle_signal
# from strategy.macd_strategy        import get_signal as macd_signal
# from strategy.stochastic_strategy  import get_signal as stoch_signal
# from strategy.market_structure     import get_signal as structure_signal
from config.settings import VOTE_THRESHOLD

logger = setup_logger("voting_engine")

STRATEGIES = [
    ("EMA Stack",         ema_signal),
    ("RSI Divergence",    rsi_signal),
    ("Bollinger Bands",   bb_signal),
    ("VWAP",              vwap_signal),
    ("Candlestick",       candle_signal),
    # ("MACD",              macd_signal),
    # ("Stochastic",        stoch_signal),
    # ("Market Structure",  structure_signal),
]


def get_vote(df: pd.DataFrame) -> dict:
    """
    Run all 8 strategies and aggregate votes.

    Returns:
        {
            "direction": "BUY" / "SELL" / "NEUTRAL",
            "score":     int (-8 to +8),
            "votes":     {"BUY": int, "SELL": int, "NEUTRAL": int},
            "details":   [{"strategy": str, "vote": int, "reason": str}],
            "threshold": int,
        }
    """
    total_score = 0
    details     = []
    vote_counts = {"BUY": 0, "SELL": 0, "NEUTRAL": 0}

    for name, fn in STRATEGIES:
        try:
            vote, reason = fn(df)
            total_score += vote
            if vote == 1:
                vote_counts["BUY"]     += 1
                icon = "🟢"
            elif vote == -1:
                vote_counts["SELL"]    += 1
                icon = "🔴"
            else:
                vote_counts["NEUTRAL"] += 1
                icon = "⚪"
            details.append({"strategy": name, "vote": vote, "reason": reason})
            logger.info(f"{icon} {name}: {reason}")
        except Exception as e:
            logger.warning(f"{name} error: {e}")
            details.append({"strategy": name, "vote": 0, "reason": f"error: {e}"})

    if total_score >= VOTE_THRESHOLD:
        direction = "BUY"
    elif total_score <= -VOTE_THRESHOLD:
        direction = "SELL"
    else:
        direction = "NEUTRAL"

    logger.info(
        f"Vote: {direction} | Score={total_score}/8 | "
        f"BUY={vote_counts['BUY']} SELL={vote_counts['SELL']} NEUTRAL={vote_counts['NEUTRAL']}"
    )

    return {
        "direction": direction,
        "score":     total_score,
        "votes":     vote_counts,
        "details":   details,
        "threshold": VOTE_THRESHOLD,
    }
