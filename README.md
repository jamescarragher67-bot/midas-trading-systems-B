# MIDAS — Liquidity-Sweep Continuation (XAUUSD, M15)

Single-strategy algorithmic gold trading bot for MetaTrader 5. One entry
point (`main.py`), one validated strategy (`strategy/lsc_m15.py`), built to run
unattended 24/7 on a dedicated Windows box.

The earlier dual-bot system (Bot 1 structural bias + Bot 2 volatility regime,
`main_combined.py`) was retired after proper backtesting found no edge. LSC is
the one strategy that survived in-sample / out-of-sample validation, split-window
checks and a 1000-shuffle Monte Carlo stress test.

---

## How it trades

When price sweeps beyond the prior UTC day's high or low and then **closes
convincingly beyond that level in the sweep direction**, LSC treats it as
continuation (not exhaustion) and enters on the next M15 bar with a static
SL/TP:

| Parameter | Value |
|-----------|-------|
| Timeframe | M15 |
| Close-beyond confirmation | 0.2 × ATR14 past the prior-day level |
| Stop loss | prior-day level ∓ 0.3 × ATR14 |
| Take profit | 2.0 × SL distance |
| Cooldown | 3 bars (45 min) |
| Max trades / day | 4 |
| Session (UTC) | 00:00–14:59 and 20:00–23:59 |
| Hard time exit | 24 h (matches the backtest's 96-bar hold cap) |
| Friday | no new trades after 20:00 UTC, force-flat after 21:00 UTC |

The live loop calls the **same** `precompute()` / `check_entry()` functions the
backtest harness (`backtest/lsc_engine.py`) validated — there is no separate
live approximation.

Position size comes from the risk-% formula **capped** by a margin-safe ceiling
computed live from the account's balance, leverage and price (see
`risk/trade_manager.py`). `RISK_PERCENT` (0.045%) was calibrated for a $50K
account at 1:10 leverage with a 6% trailing-drawdown wall — see the comment in
`config/settings.py` and `research/tools/risk_calibrator.py` before changing it.

---

## Repository layout

```
main.py                      Live trading loop (LSC only)
config/
  settings.py                All live parameters (production account, MAGIC 20001)
  settings_diagnostic.py     Isolated $5K plumbing-test account (MAGIC 20002) — not a deployment
  .env.example               Template for config/.env (never commit .env)
strategy/lsc_m15.py          The strategy: pure functions on an indicator-added M15 DataFrame
backtest/lsc_engine.py       LSC's exact backtest harness (main.py also imports compute_atr14 from here)
risk/
  trade_manager.py           Order send, margin-safe lot sizing, closed-trade detection
  circuit_breaker.py         4 consecutive losses OR 5% daily loss → paused until midnight UTC
utils/
  mt5_connection.py          connect / disconnect
  filters.py                 spread filter + news filter wrapper
  news_filter.py             Finnhub high-impact US event blackout (±30 min, FAILS CLOSED)
  notifications.py           WhatsApp (CallMeBot) alerts
  logger.py                  Console + per-process rotating log file (logs/<process>.log, 30 days kept)
sync/
  watchdog.py                Supervises main.py, trade_sync.py, firebase_push.py; uncapped back-off restarts + WhatsApp alert — run THIS on the 24/7 box
  trade_sync.py              Writes jasons/{trades,open_positions,heartbeat}.json, hourly WhatsApp summary
  firebase_push.py           Pushes jasons/ state to Firebase Realtime Database for remote monitoring
tools/preflight_check.py     Go / no-go checker: MT5, account, symbol, spread, WhatsApp, Firebase, news feed, state files
research/                    Dev / research tooling. NOT needed on the live box (see research/requirements.txt)
  tools/risk_calibrator.py   Risk-per-trade Monte Carlo calibration for LSC
  tools/backtest_*.py        Candidate-strategy full-rigor tests (FX basket, gold compression)
  tools/tournament_*.py      The 10-candidate knockout tournament + OOS / regime / final MC stages
  backtest/, strategy/       Metrics, Monte Carlo, generic tournament engine, candidate strategies
  tournament_*.json          Tournament result / trade dumps
```

---

## Setup

### Requirements

- Windows, Python 3.10+
- MetaTrader 5 terminal installed and logged in, `XAUUSD.a` visible in Market Watch
- `pip install -r requirements.txt` (live box) — add `-r research/requirements.txt` on a dev machine

### Credentials

Copy `config/.env.example` to `config/.env` and fill in:

```
MT5_LOGIN / MT5_PASSWORD / MT5_SERVER      production account
FINNHUB_API_KEY                            news filter — REQUIRED, the filter fails closed without it
WHATSAPP_PHONE / CALLMEBOT_API_KEY         alerts (optional; silently disabled if missing)
FIREBASE_DB_URL                            remote monitoring (required by sync/firebase_push.py)
```

`config/.env` is git-ignored. Nothing in the code prompts for input; a missing
`MT5_LOGIN` makes `config/settings.py` exit immediately with an error rather
than hang.

### Go / no-go

```bash
python tools/preflight_check.py                      # production config
python tools/preflight_check.py --config diagnostic  # diagnostic account
```

Exit code 0 = go (warnings allowed), 1 = any FAIL.

### Run on the 24/7 box

One process. The watchdog starts and supervises the other three (main.py,
trade_sync.py, firebase_push.py), pins their working directory to the repo
root, restarts any that exit with exponential back-off (10 s doubling to
10 min, reset after 10 min of healthy uptime, no restart cap) and sends a
WhatsApp alert on every restart. Ctrl-C on the watchdog terminates all three.

```bash
python sync/watchdog.py
```

Logs land in `logs/<process>.log`, rotated nightly (UTC), 30 days retained.
Runtime state lives in `jasons/` (git-ignored).

---

## Safety mechanisms

| Mechanism | Trigger | Action |
|-----------|---------|--------|
| Circuit breaker | 4 consecutive losses OR 5% daily loss | No new trades until midnight UTC, WhatsApp alert |
| Spread filter | spread > 20 points | Skip signal |
| News filter | ±30 min of a high-impact US event, **or feed unavailable** | Skip signal (fail-closed) |
| Session filter | outside 00:00–14:59 / 20:00–23:59 UTC | Skip signal |
| Margin-safe sizing | risk-% lot would use > 25% of equity as margin | Lot capped |
| Time exit | position open ≥ 24 h | Force close |
| Friday cutoff | after 20:00 UTC Friday / weekend | No entries; force-flat after 21:00 UTC |
| MT5 reconnect | terminal connection lost | retries forever, 10 s doubling to 5 min; WhatsApp on outage start and recovery |

---

## Disclaimer

This project is for **educational and personal research purposes only**. It is
not financial advice. Algorithmic trading involves significant risk of loss.
Past backtest performance does not guarantee future results.

*Built by James Carragher | MIDAS Capital*
