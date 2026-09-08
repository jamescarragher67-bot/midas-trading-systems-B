"""
tools/tournament_regime_test.py - Regime test: 2013-2018 gold (flat-to-down),
requested as the missing "real test" after the CC knockout tournament found
every survivor's edge was inseparable from one continuous 2019-2026 bull
regime.

DATA AVAILABILITY CHECK (see conversation): confirmed via mt5.copy_rates_range
directly against the terminal - XAUUSD.a (and every other XAU-denominated
symbol variant on this broker) has NO M15 or M5 history before ~2022-11 /
2025-04 respectively. D1 goes back to 1998 (full coverage). So:
  - #10 (50-day breakout, D1)  -> TESTABLE
  - #1  (Sunrise Ogle, M5)     -> NOT TESTABLE, no data - not run
  - LSC (Liquidity-Sweep Continuation, M15) -> NOT TESTABLE, no data - not run
This is reported honestly rather than forcing a partial/misleading test.
"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import MetaTrader5 as mt5
from research.backtest.tournament import engine
from research.backtest.metrics import calculate_metrics
from research.strategy.tournament import ndays_breakout

SYMBOL = "XAUUSD.a"
REGIME_START, REGIME_END = "2013-01-01", "2018-12-31"

# Rough linear-scaling estimate from the tournament's final MC (NOT a real
# calibration - flagged as such in the report): worst-of-1000-shuffle DD
# scaled down from 13.79% at 1.0% risk to clear the 6% wall.
NDAYS_RISK_PCT_ESTIMATE = 0.43


def main():
    mt5.initialize()
    mt5.symbol_select(SYMBOL, True)

    print(f"=== #10 50-day breakout, D1, {REGIME_START}..{REGIME_END}, risk={NDAYS_RISK_PCT_ESTIMATE}% (estimate) ===")
    trades = engine.run_backtest(SYMBOL, ndays_breakout, REGIME_START, REGIME_END,
                                  risk_pct=NDAYS_RISK_PCT_ESTIMATE)
    m = calculate_metrics(trades, initial_balance=engine.DEFAULT_INITIAL_BALANCE)
    eq = m.pop("equity_curve", None)
    print(json.dumps(m, indent=2, default=str))

    base = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(base, "..", "tournament_regime2013_18_ndays_trades.json"), "w") as f:
        json.dump(trades, f, indent=2, default=str)
    with open(os.path.join(base, "..", "tournament_regime2013_18_ndays_metrics.json"), "w") as f:
        json.dump(m, f, indent=2, default=str)
    print("Saved.")


if __name__ == "__main__":
    main()
