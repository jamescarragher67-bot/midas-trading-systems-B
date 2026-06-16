# XAUUSD Scalping Bot

Automated Gold scalping bot — Python + MT5 Python API.

## File Structure

```
xauusd_bot/
│
├── main.py                  ← Run this to start the bot
│
├── config/
│   └── settings.py          ← All tunable parameters (edit this)
│
├── strategy/
│   ├── indicators.py        ← EMA, RSI, ATR calculations
│   └── signal_engine.py     ← Entry logic (BUY/SELL signals)
│
├── risk/
│   └── trade_manager.py     ← Lot sizing, order execution, trade management
│
├── utils/
│   ├── mt5_connection.py    ← MT5 connect/disconnect
│   ├── data_fetcher.py      ← Pulls candle data from MT5
│   ├── session_filter.py    ← Blocks trades outside London/NY sessions
│   └── logger.py            ← Logs to console + daily log file
│
├── logs/                    ← Auto-created. Daily .log files saved here
│
└── requirements.txt
```

## Setup

1. Install dependencies:
```
pip install -r requirements.txt
```

2. Open MT5 Terminal and log into your demo account.

3. Enable Algo Trading in MT5:
   Tools → Options → Expert Advisors → tick "Allow Algo Trading"

4. (Optional) Set your credentials in config/settings.py if MT5 doesn't auto-login.

## Run

```
python main.py
```

## Strategy Logic

- **Timeframe:** M5
- **Entry:** EMA9 crosses EMA21, confirmed by price side of EMA50 + RSI filter
- **SL:** ATR × 1.5
- **TP:** SL × 2.0 (2:1 reward:risk)
- **Session:** London session only (08:00–17:00 UTC)
- **Max trades/day:** 5
- **Max open trades:** 2

## Tuning Parameters (config/settings.py)

| Parameter            | Default | Description                        |
|---------------------|---------|------------------------------------|
| RISK_PERCENT         | 1.0     | % of balance risked per trade      |
| REWARD_RATIO         | 2.0     | TP = SL × this value               |
| ATR_SL_MULTIPLIER    | 1.5     | SL = ATR × this value              |
| MAX_TRADES_PER_DAY   | 5       | Hard cap on daily trades           |
| MAX_OPEN_TRADES      | 2       | Max simultaneous open positions    |
| EMA_FAST / EMA_SLOW  | 9 / 21  | Crossover EMAs                     |
| EMA_TREND            | 50      | Trend direction filter             |
| RSI_OVERBOUGHT       | 65      | Don't buy above this               |
| RSI_OVERSOLD         | 35      | Don't sell below this              |

## Logs

Live logs appear in the console and are saved daily to:
```
logs/YYYY-MM-DD.log
```
