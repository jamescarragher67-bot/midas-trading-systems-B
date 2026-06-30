"""
utils/performance_monitor.py

Performance degradation detector.

Monitors rolling win rate over the last N trades.
If it drops below the threshold:
  - Automatically switches to conservative risk settings
  - Sends a WhatsApp alert
  - Logs the degradation event

If performance recovers above the recovery threshold:
  - Switches back to normal risk settings
  - Sends a WhatsApp alert

This protects funded accounts during bad streaks without
requiring manual monitoring.
"""

import json
import os
from datetime import datetime, timezone
from utils.logger import setup_logger
from utils.notifications import _send
from config.settings import (
    PERF_MONITOR_ENABLED,
    PERF_MONITOR_LOOKBACK,
    PERF_MONITOR_MIN_WIN_RATE,
    PERF_MONITOR_RECOVERY_WIN_RATE,
    PERF_MONITOR_MIN_TRADES,
    RISK_PERCENT,
    PERF_MONITOR_REDUCED_RISK,
)

logger = setup_logger("performance_monitor")

TRADES_FILE    = "jasons/trades.json"
STATE_FILE     = "jasons/monitor_state.json"


def _load_recent_trades(n: int) -> list:
    """Load the last N closed trades from trades.json."""
    if not os.path.exists(TRADES_FILE):
        return []
    try:
        with open(TRADES_FILE) as f:
            trades = json.load(f)
        return trades[-n:] if len(trades) >= n else trades
    except Exception:
        return []


def _load_state() -> dict:
    """Load monitor state — tracks whether we're in degraded mode."""
    default = {
        "degraded":       False,
        "degraded_since": None,
        "last_win_rate":  None,
    }
    if not os.path.exists(STATE_FILE):
        return default
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return default


def _save_state(state: dict):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def get_current_risk_pct() -> float:
    """
    Returns the appropriate risk % based on current performance state.
    Called by trade_manager.py before every trade.
    """
    if not PERF_MONITOR_ENABLED:
        return RISK_PERCENT

    state = _load_state()
    if state["degraded"]:
        return PERF_MONITOR_REDUCED_RISK
    return RISK_PERCENT


def check_performance() -> dict:
    """
    Check recent performance and update degradation state.
    Call this every loop cycle from main.py.

    Returns:
        {
            "degraded":   bool,
            "win_rate":   float,
            "trades":     int,
            "action":     str,  — "degraded", "recovered", "normal", "insufficient_data"
        }
    """
    if not PERF_MONITOR_ENABLED:
        return {"degraded": False, "win_rate": 0, "trades": 0, "action": "disabled"}

    trades = _load_recent_trades(PERF_MONITOR_LOOKBACK)
    state  = _load_state()

    if len(trades) < PERF_MONITOR_MIN_TRADES:
        return {
            "degraded": state["degraded"],
            "win_rate": 0,
            "trades":   len(trades),
            "action":   "insufficient_data",
        }

    wins     = sum(1 for t in trades if t.get("result") == "WIN")
    win_rate = wins / len(trades) * 100

    logger.debug(f"Performance check: {win_rate:.1f}% WR ({wins}W/{len(trades)-wins}L) over last {len(trades)} trades")

    # ── Degradation detected ──────────────────────────────────────────────────
    if not state["degraded"] and win_rate < PERF_MONITOR_MIN_WIN_RATE:
        state["degraded"]       = True
        state["degraded_since"] = datetime.now(timezone.utc).isoformat()
        state["last_win_rate"]  = round(win_rate, 1)
        _save_state(state)

        msg = (
            f"⚠️ MIDAS — PERFORMANCE ALERT\n"
            f"Win rate dropped to {win_rate:.1f}% over last {len(trades)} trades\n"
            f"Switching to reduced risk: {PERF_MONITOR_REDUCED_RISK}%\n"
            f"Normal risk resumes when WR recovers above {PERF_MONITOR_RECOVERY_WIN_RATE}%"
        )
        _send(msg)
        logger.warning(f"Performance degraded — switching to {PERF_MONITOR_REDUCED_RISK}% risk")

        return {"degraded": True, "win_rate": win_rate, "trades": len(trades), "action": "degraded"}

    # ── Recovery detected ─────────────────────────────────────────────────────
    if state["degraded"] and win_rate >= PERF_MONITOR_RECOVERY_WIN_RATE:
        state["degraded"]       = False
        state["degraded_since"] = None
        state["last_win_rate"]  = round(win_rate, 1)
        _save_state(state)

        msg = (
            f"✅ MIDAS — PERFORMANCE RECOVERED\n"
            f"Win rate back to {win_rate:.1f}% over last {len(trades)} trades\n"
            f"Resuming normal risk: {RISK_PERCENT}%"
        )
        _send(msg)
        logger.info(f"Performance recovered — resuming {RISK_PERCENT}% risk")

        return {"degraded": False, "win_rate": win_rate, "trades": len(trades), "action": "recovered"}

    # ── Normal ────────────────────────────────────────────────────────────────
    return {
        "degraded": state["degraded"],
        "win_rate": round(win_rate, 1),
        "trades":   len(trades),
        "action":   "normal",
    }
