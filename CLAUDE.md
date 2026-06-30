# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

MIDAS is an automated algorithmic trading bot for XAUUSD (Gold) scalping on MetaTrader 5. It uses a multi-strategy voting engine on M5 candles, executes trades with dynamic risk management, and includes a GUI launcher, live dashboard, and backtesting framework.

## Commands

```bash
# Run the trading bot
python main_combined.py

# GUI launcher (manages processes, views logs)
python dashboard/midas_launcher.py

# Run 100-day backtest (outputs backtest_report.html)
python tools/run_backtest.py

# Live HTML dashboard (auto-refreshes every 30s)
python dashboard/midas_dashboard_local.py

# One-shot diagnostic snapshot (what both bots see right now)
python tools/check_current_signal.py

# 30-day spread analysis by UTC hour
python tools/spread_analysis.py

# Initial setup wizard (validates MT5 credentials)
python tools/setup_midas.py

# Watchdog (auto-restarts bot on crash)
python sync/watchdog.py

# Install dependencies
pip install -r requirements.txt
```

There are no lint, test, or build steps.

## Architecture

### Signal Pipeline (`main.py` → `strategy/signal_engine.py`)

On each 30-second tick, the bot:
1. Applies pre-filters (session hours, spread ≤50 pts, ATR volatility, open trade count, daily trade limit, cooldown)
2. Fetches M5 candles via `utils/data_fetcher.py` and computes EMA/RSI/ATR indicators (`strategy/indicators.py`)
3. Runs 5 independent voting strategies — each returns (+1 BUY / -1 SELL / 0 NEUTRAL)
4. Requires ≥4/5 votes (`VOTE_THRESHOLD`) in the same direction
5. Confirms with multi-timeframe bias from H1 + M15 (`strategy/multi_timeframe.py`)
6. If confirmed, calls `risk/trade_manager.py` to size and place the order

### Voting Strategies (`strategy/`)

| File | Strategy |
|------|----------|
| `ema_stack.py` | EMA9 > EMA21 > EMA50 alignment |
| `rsi_divergence.py` | Price/RSI divergence |
| `bollinger_bands.py` | Price at band extremes |
| `vwap_strategy.py` | VWAP crossover |
| `candlestick_patterns.py` | Japanese candlestick formations |

`voting_engine.py` aggregates these into a score (−8 to +8) and applies the threshold check.

### Trade Management (`risk/trade_manager.py`)

- **Sizing:** 1% risk per trade; lot = (balance × risk%) / (SL_pips × pip_value)
- **SL:** ATR × `ATR_SL_MULTIPLIER` (default 1.5)
- **TP:** SL distance × `MIN_RR` (profile-dependent)
- **Post-entry management:** break-even at 1:1, partial close at 1:1, trailing stops (25–30 pip steps)
- **Time exits** (`utils/time_exit.py`): soft close after 8h if profitable, hard close after 24h

### Configuration (`config/settings.py`)

Single source of truth for all parameters. Switch between three risk profiles by changing one line:

```python
ACTIVE_PROFILE = "AGGRESSIVE"  # or "BALANCED" or "CONSERVATIVE"
```

Profile differences: `RISK_PERCENT` (2% / 1.5% / 1%), `MIN_RR` (1.5 / 2 / 2.5), trailing stop distances.

MT5 credentials and WhatsApp keys are loaded from `config/.env` (never hardcoded, gitignored).

### Safety Mechanisms

- **Circuit breaker** (`risk/circuit_breaker.py`): pauses bot after N consecutive losses or exceeding daily loss %
- **Performance monitor** (`utils/performance_monitor.py`): auto-reduces risk after a bad 20-trade streak
- **Session filter** (`utils/session_filter.py`): currently disabled (`SESSION_FILTER_ENABLED = False`); `best_hours.json` holds learned optimal hours from backtest

### Backtest (`backtest/`)

`engine.py` runs a bar-by-bar simulation applying the same live logic (voting, filters, sizing, trailing). `report.py` generates an HTML report with equity curve, monthly/hourly breakdowns, and metrics (win rate, profit factor, Sharpe, max drawdown).

After any logic change, re-run `python run_backtest.py` to validate.

## Adding a Voting Strategy

1. Create `strategy/my_strategy.py` with `def get_signal(df: pd.DataFrame) -> tuple[int, str]`
2. Register in `strategy/voting_engine.py` — add to the `STRATEGIES` list
3. Adjust `VOTE_THRESHOLD` in `config/settings.py` if needed

## Key Files

| File | Role |
|------|------|
| `main_combined.py` | Entry point — Bot 1 + Bot 2 combined live loop |
| `config/settings.py` | All tunable parameters and profiles |
| `risk/trade_manager.py` | Sizing, execution, BE/partial/trailing |
| `risk/circuit_breaker.py` | Loss streak / daily drawdown guard |
| `utils/snapshot_logger.py` | 15-min background signal snapshots → `logs/signal_snapshots.log` |
| `utils/logger.py` | `setup_logger("name")` used by all modules; writes to `logs/YYYY-MM-DD.log` |
| `backtest/engine.py` | Bar-by-bar simulation (mirrors live logic) |
| `dashboard/midas_launcher.py` | GUI process manager + live log viewer |
| `dashboard/midas_dashboard_local.py` | Auto-refresh HTML dashboard |
| `sync/firebase_push.py` | Pushes trades/positions/heartbeat to Firebase |
| `sync/trade_sync.py` | Syncs closed trades and sends WhatsApp alerts |
| `sync/watchdog.py` | Monitors main_combined.py, auto-restarts on crash |
| `tools/check_current_signal.py` | Live diagnostic snapshot (run on demand) |
| `tools/run_backtest.py` | 100-day backtest runner |
| `tools/spread_analysis.py` | 30-day spread distribution by UTC hour |
| `tools/setup_midas.py` | Machine setup wizard (.env creation) |
