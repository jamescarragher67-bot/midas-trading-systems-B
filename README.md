# MIDAS CAPITAL — Algorithmic Gold Trading System

![Python](https://img.shields.io/badge/Python-3.10%2B-blue?style=flat-square&logo=python)
![Platform](https://img.shields.io/badge/Platform-MetaTrader%205-brightgreen?style=flat-square)
![Asset](https://img.shields.io/badge/Asset-XAUUSD%20Gold-gold?style=flat-square)
![Status](https://img.shields.io/badge/Status-Live%20Demo-orange?style=flat-square)
![License](https://img.shields.io/badge/License-Private-red?style=flat-square)

> Dual-bot algorithmic trading system for XAUUSD (Gold) on M5 candles, combining a structural bias engine with a volatility regime classifier.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                        MIDAS CAPITAL                                │
│                    main_combined.py                                 │
│                                                                     │
│  ┌──────────────────────────┐  ┌──────────────────────────────┐    │
│  │        BOT 1             │  │          BOT 2               │    │
│  │   Structural Bias        │  │   Volatility Regime          │    │
│  │                          │  │                              │    │
│  │  ┌─────────────────┐     │  │  ┌────────────────────┐     │    │
│  │  │ EMA Stack       │ +1  │  │  │ ATR / StdDev / VoV │     │    │
│  │  │ ATR Expansion   │ +1  │  │  │ Regime Classifier  │     │    │
│  │  │ Prev Day Struct │ +1  │  │  │ A / B / C          │     │    │
│  │  └────────┬────────┘     │  │  └─────────┬──────────┘     │    │
│  │           │ 3/3 unanimous│  │            │                 │    │
│  │  Daily Bias: BUY/SELL    │  │  Transition Detector         │    │
│  │           │              │  │  B→C only (mean reversion)   │    │
│  │  M5 EMA21 Pullback Entry │  │            │                 │    │
│  └──────────┬───────────────┘  └────────────┬────────────────┘    │
│             │                               │                      │
│  ┌──────────▼───────────────────────────────▼────────────────┐    │
│  │                  SHARED INFRASTRUCTURE                     │    │
│  │  Circuit Breaker │ Session Filter │ Spread Filter          │    │
│  │  6 trades/day cap │ WhatsApp alerts │ trades.json          │    │
│  │  15-min snapshot logger → logs/signal_snapshots.log        │    │
│  └────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────┘
```

### Bot 1 — Structural Bias + EMA21 Pullback

Runs a 3/3 unanimous vote at the start of each UTC day:

| Voter | Logic | Vote |
|-------|-------|------|
| EMA Stack | EMA9 > EMA21 > EMA50 AND close > EMA50 | ±1 |
| ATR Expansion | ATR14 > ATR\_MA20 × 1.2 (volatility expanding) | ±1 |
| Prev Day Structure | Price above PDH (BUY) or below PDL (SELL) | ±1 |

All three must agree for a bias to lock. Once locked, Bot 1 waits for a **M5 EMA21 pullback** entry: candle touches EMA21, closes in the bias direction, with a strong momentum body (≥ 0.6 × ATR).

**Spread limit:** 15 points

### Bot 2 — Volatility Regime + B→C Mean Reversion

Classifies every M5 bar into one of three volatility regimes using ATR ratios relative to a 50-bar mean (no absolute price references — works at any Gold level):

| Regime | Description | Condition |
|--------|-------------|-----------|
| **A** | Compression / coiling | atr\_ratio < 0.80, stddev < 0.80, hl\_comp < 0.75 |
| **B** | Expansion / normal | atr\_ratio 0.80–1.50, stddev 0.80–1.50 |
| **C** | Exhaustion / spike | atr\_ratio > 1.50 AND vov\_ratio > 1.30 |

Fires **only on B→C transitions** (mean reversion into an exhaustion spike), requiring wick\_ratio > 0.60 as confirmation. Direction is counter-trend: SELL if the spike was bullish, BUY if bearish.

**Spread limit:** 20 points

---

## Backtest Results

*100-day simulation on XAUUSD M5 | $500 starting balance | 1.5% risk per trade | 2:1 RR | Trailing ATR×0.5*

| Metric | Bot 1 | Bot 2 |
|--------|-------|-------|
| Total trades | — | — |
| Win rate | — | — |
| Net P&L | — | — |
| Profit factor | — | — |
| Max drawdown | — | — |
| Sharpe ratio | — | — |
| Expectancy / trade | — | — |

> Run `python run_backtest.py` with MT5 connected to populate this table from your own historical data.

---

## Setup

### Prerequisites

- Python 3.10+
- MetaTrader 5 terminal installed and logged in
- XAUUSD available in Market Watch

### Install

```bash
git clone https://github.com/jamescarragher67-bot/midas-trading-systems-B.git
cd "midas-trading-systems-B"
pip install -r requirements.txt
```

### First-time configuration

```bash
python setup_midas.py
```

The setup wizard prompts for MT5 credentials, WhatsApp (CallMeBot) API key, and Firebase URL, validates each live, and writes `config/.env`. Never committed to Git.

### Run

```bash
# Live dual-bot trading
python main_combined.py

# GUI launcher (process manager + live log viewer)
python dashboard/midas_launcher.py

# 100-day backtest
python tools/run_backtest.py

# Live dashboard (auto-refresh every 30s)
python dashboard/midas_dashboard_local.py

# One-shot diagnostic snapshot (what both bots see right now)
python tools/check_current_signal.py

# 30-day spread analysis by UTC hour
python tools/spread_analysis.py
```

---

## Configuration

All parameters live in `config/settings.py`. Switch risk profile by changing one line:

```python
ACTIVE_PROFILE = "BALANCED"   # "AGGRESSIVE" | "BALANCED" | "CONSERVATIVE"
```

| Profile | Risk/trade | Min RR |
|---------|-----------|--------|
| AGGRESSIVE | 2.0% | 2.0:1 |
| BALANCED | 1.5% | 2.0:1 |
| CONSERVATIVE | 1.0% | 2.5:1 |

MT5 credentials and API keys are loaded from `config/.env` — never hardcoded.

---

## Safety Mechanisms

| Mechanism | Trigger | Action |
|-----------|---------|--------|
| Circuit breaker | 4 consecutive losses OR 5% daily loss | Pauses all trading |
| Performance monitor | Win rate < 35% over last 20 trades | Halves risk to 0.75% |
| Session filter | Outside 00:00–14:59 UTC or 20:00–23:59 UTC | No new entries |
| Spread filter | Bot 1 > 15pt, Bot 2 > 20pt | Blocks entry |
| Daily trade cap | 6 combined trades across both bots | Hard stop |
| Friday cutoff | After 20:00 UTC Friday | No new entries; close at 21:00 |

---

## Project Structure

```
├── main_combined.py          — Bot 1 + Bot 2 combined live trading loop
├── config/settings.py        — All parameters (single source of truth)
├── strategy/
│   ├── ema_stack.py          — Voter 1: EMA9/21/50 alignment
│   ├── atr_expansion.py      — Voter 2: ATR volatility expansion
│   ├── prev_day_structure.py — Voter 3: Previous day high/low breakout
│   ├── m5_execution_engine.py — Bot 1 daily bias + M5 entry logic
│   ├── volatility_metrics.py  — ATR/StdDev/VoV fingerprint computation
│   ├── regime_classifier.py   — A/B/C regime classification
│   └── transition_detector.py — B→C transition detection
├── risk/
│   ├── trade_manager.py      — Position sizing, SL/TP, trailing
│   └── circuit_breaker.py    — Loss streak / daily drawdown guard
├── utils/
│   ├── snapshot_logger.py    — 15-min background signal snapshots
│   ├── session_filter.py     — Trading hours validation
│   ├── filters.py            — Spread and volatility filters
│   └── notifications.py      — WhatsApp (CallMeBot) alerts
├── backtest/
│   ├── engine.py             — Bar-by-bar backtest simulation
│   └── report.py             — HTML report generator
├── dashboard/
│   ├── midas_launcher.py     — GUI process manager + live log viewer
│   └── midas_dashboard_local.py — Auto-refresh HTML dashboard
├── sync/
│   ├── firebase_push.py      — Pushes trades/positions/heartbeat to Firebase
│   ├── trade_sync.py         — Syncs closed trades, sends WhatsApp alerts
│   └── watchdog.py           — Monitors bot, auto-restarts on crash
└── tools/
    ├── check_current_signal.py — Live diagnostic snapshot (run on demand)
    ├── run_backtest.py         — 100-day backtest runner
    ├── spread_analysis.py      — 30-day spread distribution by UTC hour
    ├── setup_midas.py          — Machine setup wizard (.env creation)
    └── generate_preview.py     — Generates social_preview.png
```

---

## Roadmap

### Stage 1 — Foundation (Current)
- [x] Dual-bot architecture live on MT5 demo
- [x] 3/3 unanimous structural bias (Bot 1)
- [x] B→C volatility regime mean reversion (Bot 2)
- [x] Circuit breaker, session filter, spread filter
- [x] WhatsApp trade alerts
- [x] Live dashboard + GUI launcher
- [x] 15-min background signal snapshot logger
- [x] Diagnostic script with proximity-to-signal metrics

### Stage 2 — Performance (Q3 2026)
- [ ] Live account funding and transition from demo
- [ ] Firebase real-time sync across multiple machines
- [ ] Enhanced backtesting with walk-forward validation
- [ ] Performance analytics dashboard
- [ ] Automated daily PnL reporting

### Stage 3 — Scale (Q4 2026+)
- [ ] Capital scaling protocol (compounding rules)
- [ ] Second instrument (XAGUSD stat-arb integration)
- [ ] Multi-timeframe regime confirmation
- [ ] Institutional-grade risk reporting

---

## Disclaimer

This project is for **educational and personal research purposes only**. It is not financial advice. Algorithmic trading involves significant risk of loss. Past backtest performance does not guarantee future results. Use at your own risk.

The author is not a licensed financial adviser. Never trade with money you cannot afford to lose.

---

*Built by James Carragher | MIDAS Capital*
