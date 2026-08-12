MIDAS CAPITAL — Algorithmic Gold Trading System











Overview

MIDAS Capital is an institutional-grade algorithmic trading framework engineered specifically for XAUUSD (Gold) on MetaTrader 5 . Moving away from multi-bot complexities, the current Stage 4 architecture relies entirely on a single validated strategy: the Liquidity-Sweep Continuation (LSC) engine .

The LSC strategy operates on the premise that institutional order flow often "sweeps" prior session liquidity pools before continuing in the direction of conviction. By requiring a structured price closure beyond prior session extremes confirmed by Average True Range (ATR) metrics, the system captures high-probability continuation moves while systematically avoiding false reversals .




System Architecture

The following diagram illustrates the execution and risk management pipeline of the Midas framework:














Strategy Logic: Liquidity-Sweep Continuation (LSC M15)

The core execution path is executed on the 15-minute timeframe (M15) and is governed by strict mathematical rules validated through extensive Monte Carlo stress testing and split-window out-of-sample evaluations  .

Entry Mechanics

1.
Prior Session Extremes: The system computes the prior UTC day's high and low from historical M15 price bars .

2.
The Sweep: When current price action breaches the prior high or low, it identifies a potential liquidity grab.

3.
Continuation Confirmation: A trade is triggered only when the bar closes convincingly beyond the extreme by at least 0.2 * ATR (CLOSE_BEYOND_ATR_MULT = 0.2) .

4.
Risk Parameters: Position sizing incorporates a fixed risk model (RISK_PERCENT = 1.0%) paired with a dynamic margin safety ceiling (MARGIN_SAFETY_BUDGET_PCT = 25%) to protect account equity during volatility spikes  .

Parameter
Value
Description
Timeframe
M15
Primary signal generation timeframe
ATR Period
14
Volatility baseline measurement
Close Confirmation
0.2 ATR
Minimum breakout distance required
SL Buffer
0.3 ATR
Protection buffer beyond extreme
Reward-to-Risk
2.0 : 1
Fixed structural target
Max Trades/Day
4
Daily frequency cap







Risk Management & Safety Infrastructure

Designed with strict prop-firm compliance in mind, Midas incorporates multiple layers of automated risk protection:

•
Circuit Breaker (risk/circuit_breaker.py): Automatically halts all trading activity upon reaching 4 consecutive losses or a 5.0% daily drawdown, resetting safely at midnight UTC .

•
Fail-Closed News Filter (utils/news_filter.py): Integrates with the Finnhub Economic Calendar API to poll high-impact USD events . If a red-folder event occurs within a 30-minute window, or if the API connection fails, the bot executes fail-closed behavior by blocking new entries.

•
Margin Safety Ceiling (risk/trade_manager.py): Dynamically computes maximum allowable lot size based on live account balance, leverage, and prevailing ask price, preventing margin exhaustion .

•
Execution Safeguards: Enforces strict spread filtering (MAX_SPREAD_POINTS = 20), hard 24-hour time-based exits, and automatic Friday market-close flattening .




Project Structure

Plain Text


MIDAS TRADING BOT/
├── main.py                     # Central execution loop and event dispatcher
├── config/
│   └── settings.py             # Global parameters, risk rules, and API keys
├── strategy/
│   └── lsc_m15.py              # Validated Liquidity-Sweep Continuation engine
├── risk/
│   ├── trade_manager.py        # Position sizing, order execution, and management
│   └── circuit_breaker.py      # Drawdown guard and daily loss tracker
├── utils/
│   ├── filters.py              # Spread and session validation wrapper
│   ├── news_filter.py          # Finnhub API integration with fail-closed logic
│   └── notifications.py        # Real-time WhatsApp (CallMeBot) dispatchers
├── backtest/
│   └── lsc_engine.py           # Historical bar-by-bar backtesting simulator
├── dashboard/
│   └── midas_dashboard_local.py# Local auto-refresh monitoring interface
└── tools/
    └── test_news_api.py        # Diagnostic tool for news feed verification






Roadmap

•
Stage 1 — Foundation (Active): Single-strategy LSC execution engine on MT5 demo, robust circuit breakers, Finnhub news protection, and WhatsApp telemetry.

•
Stage 2 — Performance (Q3 2026): Transition to funded prop-firm accounts ($10K–$25K challenges), real-time Firebase telemetry sync, and walk-forward backtesting optimization.

•
Stage 3 — Scale (Q4 2026+): Capital scaling protocol, multi-asset diversification (XAGUSD stat-arb), and institutional-grade reporting.




References

[1] MetaTrader 5 Platform Specifications. MetaQuotes Software, 2026.
[2] Midas Research Group. Stage 4 Liquidity-Sweep Continuation: Monte Carlo Stress Testing and Out-of-Sample Validation, August 2026.
[3] strategy/lsc_m15.py — Midas Quantitative Systems, 2026.
[4] config/settings.py — Midas Quantitative Systems, 2026.
[5] risk/trade_manager.py — Midas Quantitative Systems, 2026.
[6] risk/circuit_breaker.py — Midas Quantitative Systems, 2026.
[7] Finnhub Stock API Documentation. Economic Calendar Endpoint Reference, 2026.
