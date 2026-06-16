"""
utils/dynamic_risk.py

Dynamic position sizing — scales risk % as account balance grows.
Keeps drawdown percentage stable while profits compound faster.

Scaling ladder:
  $0      - $110k  →  0.5%
  $110k   - $125k  →  0.75%
  $125k   - $150k  →  1.0%
  $150k   - $200k  →  1.25%
  $200k+           →  1.5%

For small accounts ($500 - $5k demo):
  $0    - $750    →  0.5%
  $750  - $1,500  →  0.75%
  $1,500 - $3,000 →  1.0%
  $3,000 - $5,000 →  1.25%
  $5,000+         →  1.5%
"""

from utils.logger import setup_logger
from config.settings import DYNAMIC_RISK_ENABLED, RISK_PERCENT

logger = setup_logger("dynamic_risk")

# Scaling ladder — (min_balance, max_balance, risk_pct)
RISK_LADDER = [
    (0,       750,    0.50),
    (750,     1500,   0.60),
    (1500,    3000,   0.70),
    (3000,    5000,   0.80),
    (5000,    110000, 0.50),
    (110000,  120000, 0.52),
    (120000,  132000, 0.55),
    (132000,  148000, 0.58),
    (148000,  168000, 0.61),
    (168000,  195000, 0.64),
    (195000,  999999, 0.65),
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
