"""
strategy/volatility_signal_engine.py - MIDAS-B Signal Engine

Generates trade parameters from a detected regime transition.

  A→B  Breakout:      SL = ATR × 2.0, Trail = ATR × 1.0 (room for breakout)
  B→C  Mean reversion: SL = ATR × 1.0, Trail = ATR × 0.5 (tight, fast exit)
  C→A  Cooling:        NO TRADE — handled by caller
"""

from utils.logger import setup_logger

logger = setup_logger("volatility_signal_engine")

SESSION_HOURS = set(range(0, 15)) | {20, 21, 22, 23}   # block 15:00-19:59 UTC


def get_signal(transition: dict, fingerprint: dict, config: dict) -> dict | None:
    """
    Convert a detected transition into a trade signal with entry params.
    Returns None if no actionable signal.
    """
    t_type    = transition["transition"]
    direction = transition["direction"]

    if t_type == "NONE" or t_type == "C_to_A" or direction is None:
        return None

    atr = fingerprint["atr"]

    if t_type == "A_to_B":
        sl_mult    = config.get("ab_sl_atr_mult",    2.0)
        trail_mult = config.get("ab_trail_atr_mult", 1.0)
    elif t_type == "B_to_C":
        sl_mult    = config.get("bc_sl_atr_mult",    1.0)
        trail_mult = config.get("bc_trail_atr_mult", 0.5)
    else:
        return None

    rr = config.get("reward_ratio", 2.0)

    return {
        "direction":        direction,
        "transition":       t_type,
        "confidence":       transition["confidence"],
        "reason":           transition["reason"],
        "atr":              atr,
        "sl_atr_mult":      sl_mult,
        "trail_atr_mult":   trail_mult,
        "reward_ratio":     rr,
    }


def passes_filters(bar_time, config: dict, trades_today: int,
                   last_trade_bar: int, current_bar: int,
                   spread_points: float = 0.0) -> tuple[bool, str]:
    """
    Check session, daily limit, cooldown, and spread filters.
    Returns (passes: bool, reason: str).
    """
    hour = bar_time.hour

    if config.get("session_filter", True) and hour not in SESSION_HOURS:
        return False, f"session_blocked_hour={hour}"

    if trades_today >= config.get("max_trades_per_day", 6):
        return False, "max_trades_per_day"

    cooldown = config.get("cooldown_bars", 3)
    if current_bar - last_trade_bar < cooldown:
        return False, "cooldown"

    max_spread = config.get("max_spread_points", 20)
    if spread_points > max_spread:
        return False, f"spread_too_wide_{spread_points:.1f}"

    return True, "ok"
