"""
tools/tournament_final_mc.py - Final stage: 1000-shuffle percent-recompounding
Monte Carlo on the 2 tournament survivors, combining each one's IS+OOS trade
sequence (maximizes sample size given how thin the OOS-only samples are;
mirrors how LSC's own MC was run on "the full available history", not an
OOS-only slice). Same $50k/6%-wall framing as LSC's own calibration.
"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from research.backtest.tournament.monte_carlo_pct import monte_carlo_pct_drawdown

base = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(base, "..", "tournament_is_trades.json")) as f:
    is_trades = json.load(f)
with open(os.path.join(base, "..", "tournament_oos_trades.json")) as f:
    oos_trades = json.load(f)

SURVIVORS = ["10_ndays_control", "1_sunrise_ogle"]
INITIAL_BALANCE = 50_000.0
WALL_PCT = 6.0

for key in SURVIVORS:
    combined = is_trades[key] + oos_trades[key]
    print(f"\n=== {key}: {len(combined)} trades (IS {len(is_trades[key])} + OOS {len(oos_trades[key])}) ===")
    mc = monte_carlo_pct_drawdown(combined, INITIAL_BALANCE, n_shuffles=1000, seed=42)
    for k, v in mc.items():
        print(f"  {k}: {v}")
    clears = mc["worst_dd_pct"] < WALL_PCT
    print(f"  Clears {WALL_PCT}% wall on worst-of-1000 shuffle: {clears} "
          f"(worst={mc['worst_dd_pct']}%, worst-5th-pctile={mc['worst_5pct_dd_pct']}%)")
