"""
risk/circuit_breaker.py

Pauses the bot when:
  1. MAX_CONSECUTIVE_LOSSES losses in a row
  2. Daily loss exceeds MAX_DAILY_LOSS_PCT % of balance

Resets at midnight UTC.
"""

import MetaTrader5 as mt5
from datetime import datetime, timezone
from utils.logger import setup_logger
from config.settings import (
    CIRCUIT_BREAKER_ENABLED,
    MAX_CONSECUTIVE_LOSSES,
    MAX_DAILY_LOSS_PCT,
)

logger = setup_logger("circuit_breaker")


class CircuitBreaker:
    def __init__(self):
        self._consecutive_losses = 0
        self._daily_loss         = 0.0
        self._tripped            = False
        self._trip_reason        = ""
        self._last_reset_date    = None
        self._starting_balance   = None

    def _reset_if_new_day(self):
        today = datetime.now(timezone.utc).date()
        if self._last_reset_date != today:
            self._consecutive_losses = 0
            self._daily_loss         = 0.0
            self._tripped            = False
            self._trip_reason        = ""
            self._last_reset_date    = today
            account = mt5.account_info()
            if account:
                self._starting_balance = account.balance
            logger.info("Circuit breaker reset for new day.")

    def record_trade(self, pnl: float):
        """Call after every trade closes."""
        if not CIRCUIT_BREAKER_ENABLED:
            return

        self._reset_if_new_day()

        if pnl < 0:
            self._consecutive_losses += 1
            self._daily_loss         += abs(pnl)
            logger.info(
                f"Circuit breaker | Consecutive losses: {self._consecutive_losses}/{MAX_CONSECUTIVE_LOSSES} | "
                f"Daily loss: ${self._daily_loss:.2f}"
            )
        else:
            self._consecutive_losses = 0
            logger.info(f"Circuit breaker | Win recorded. Consecutive losses reset to 0.")

        # Check trip conditions
        if self._consecutive_losses >= MAX_CONSECUTIVE_LOSSES:
            self._trip(f"{MAX_CONSECUTIVE_LOSSES} consecutive losses")

        if self._starting_balance and self._starting_balance > 0:
            loss_pct = (self._daily_loss / self._starting_balance) * 100
            if loss_pct >= MAX_DAILY_LOSS_PCT:
                self._trip(f"Daily loss limit reached ({loss_pct:.1f}% >= {MAX_DAILY_LOSS_PCT}%)")

    def _trip(self, reason: str):
        if not self._tripped:
            self._tripped     = True
            self._trip_reason = reason
            logger.warning(f"🚨 CIRCUIT BREAKER TRIPPED: {reason}. Bot paused until midnight UTC.")

    def is_tripped(self) -> bool:
        if not CIRCUIT_BREAKER_ENABLED:
            return False
        self._reset_if_new_day()
        return self._tripped

    def status(self) -> str:
        if self._tripped:
            return f"TRIPPED — {self._trip_reason}"
        return f"OK | Losses: {self._consecutive_losses}/{MAX_CONSECUTIVE_LOSSES} | Daily loss: ${self._daily_loss:.2f}"


# Singleton instance
circuit_breaker = CircuitBreaker()