"""
utils/dynamic_risk.py

Dynamic position sizing — scales risk % as account balance grows.
Keeps drawdown percentage stable while profits compound faster.

Scaling ladder:
  $0       - $750    →  0.50%
  $750     - $1,500  →  0.60%
  $1,500   - $3,000  →  0.70%
  $3,000   - $5,000  →  0.80%
  $5,000   - $10,000 →  0.90%
  $10,000  - $25,000 →  1.00%
  $25,000  - $50,000 →  1.10%
  $50,000  - $100,000→  1.25%
  $100,000+          →  1.50%
"""

from utils.logger import setup_logger
from config.settings import DYNAMIC_RISK_ENABLED, RISK_PERCENT

logger = setup_logger("dynamic_risk")

# Scaling ladder — (min_balance, max_balance, risk_pct) — monotonically increasing
RISK_LADDER = [
    (0,        750,    0.50),
    (750,      1_500,  0.60),
    (1_500,    3_000,  0.70),
    (3_000,    5_000,  0.80),
    (5_000,    10_000, 0.90),
    (10_000,   25_000, 1.00),
    (25_000,   50_000, 1.10),
    (50_000,   100_000, 1.25),
    (100_000,  999_999, 1.50),
]


def get_risk_pct(balance: float) -> float:
    """
    Returns the appropriate risk % for the current balance.
    Falls back to settings.RISK_PERCENT if dynamic risk is disabled.
    """
    if not DYNAMIC_RISK_ENABLED:
        return RISK_PERCENT

    for min_bal, max_bal, risk in RISK_LADDER:
        if min_bal <= balance < max_bal:
            logger.debug(f"Dynamic risk: ${balance:.0f} → {risk}% risk")
            return risk

    return 1.50   # max tier
