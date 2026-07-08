"""
backtest/bot3_engine.py — Bot 3 High-Frequency Backtest Engine

Bar-by-bar M5 simulation.
Daily bias: 3/3 unanimous voters (EMA Stack + ATR Expansion + Prev Day Structure).
Lazy-bias: re-evaluated every bar until it locks non-NONE for the day.
M5 entry: check_entry() in the bias direction.
Session: 00:00-14:59 UTC and 20:00-23:59 UTC.
"""

import MetaTrader5 as mt5
import pandas as pd
from strategy.indicators import add_indicators
from strategy.m5_highfreq_engine import get_daily_bias, check_entry

POINT         = 0.01
CONTRACT_SIZE = 100
MIN_LOOKBACK  = 60

INDICATOR_CONFIG = {
    "EMA_FAST":   9,
    "EMA_SLOW":   21,
    "EMA_TREND":  50,
    "RSI_PERIOD": 14,
    "ATR_PERIOD": 14,
}

SESSION_HOURS = set(range(0, 15)) | {20, 21, 22, 23}


def fetch_data(symbol: str, days: int) -> pd.DataFrame:
    mt5.symbol_select(symbol, True)
    num_bars = min(days * 288, 75000)
    print(f"  Requesting {num_bars:,} M5 bars from MT5...")
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, 0, num_bars)
    if rates is None or len(rates) == 0:
        raise ValueError(f"No data returned: {mt5.last_error()}")
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    df.set_index("time", inplace=True)
    print(f"  Got {len(df):,} bars ({df.index[0].date()} -> {df.index[-1].date()})")
    return df


def _lot_size(balance: float, sl_dist: float, risk_pct: float,
              max_lot: float = 0.01) -> float:
    risk_amt  = balance * (risk_pct / 100)
    sl_points = sl_dist / POINT
    if sl_points <= 0:
        return 0.01
    lot = risk_amt / (sl_points * CONTRACT_SIZE * POINT)
    return max(0.01, min(round(round(lot / 0.01) * 0.01, 2), max_lot))


def _simulate_trade(df: pd.DataFrame, entry_idx: int, direction: str,
                    atr: float, balance: float, config: dict) -> dict | None:
    if entry_idx + 1 >= len(df):
        return None

    entry_candle = df.iloc[entry_idx + 1]
    entry_price  = float(entry_candle["open"])

    sl_dist = atr * config.get("sl_atr_mult", 1.5)
    tp_dist = sl_dist * config["reward_ratio"]

    if direction == "BUY":
        sl = round(entry_price - sl_dist, 2)
        tp = round(entry_price + tp_dist, 2)
    else:
        sl = round(entry_price + sl_dist, 2)
        tp = round(entry_price - tp_dist, 2)

    lot_size = _lot_size(balance, sl_dist, config["risk_pct"], config.get("max_lot_size", 0.01))
    mult     = lot_size * CONTRACT_SIZE

    max_bars  = 24 * 12
    soft_bars = 8 * 12

    result     = None
    exit_price = None

    for j in range(entry_idx + 2, min(entry_idx + max_bars + 2, len(df))):
        c         = df.iloc[j]
        high      = float(c["high"])
        low       = float(c["low"])
        close     = float(c["close"])
        bars_open = j - entry_idx - 1

        if direction == "BUY":
            if low <= sl:
                result, exit_price = "LOSS", sl
                break
            if high >= tp:
                result, exit_price = "WIN", tp
                break
        else:
            if high >= sl:
                result, exit_price = "LOSS", sl
                break
            if low <= tp:
                result, exit_price = "WIN", tp
                break

        in_profit = (close > entry_price) if direction == "BUY" else (close < entry_price)
        if bars_open >= soft_bars and in_profit:
            result, exit_price = "WIN", close
            break

    if result is None:
        last       = df.iloc[min(entry_idx + max_bars + 1, len(df) - 1)]
        exit_price = float(last["close"])
        raw        = (exit_price - entry_price) if direction == "BUY" else (entry_price - exit_price)
        result     = "WIN" if raw > 0 else "LOSS"

    raw_move = (exit_price - entry_price) if direction == "BUY" else (entry_price - exit_price)
    pnl      = raw_move * mult
    result   = "WIN" if pnl > 0 else "LOSS"

    entry_time = df.index[entry_idx + 1]
    return {
        "date":          entry_time.strftime("%Y-%m-%d"),
        "time":          entry_time.strftime("%H:%M"),
        "direction":     direction,
        "entry":         round(entry_price, 2),
        "exit":          round(exit_price, 2),
        "sl":            sl,
        "tp":            round(tp, 2),
        "lots":          lot_size,
        "pnl":           round(pnl, 2),
        "result":        result,
        "atr":           round(atr, 2),
        "balance_after": round(balance + pnl, 2),
    }


def run_bot3_simulation(symbol: str, days: int, config: dict,
                        progress_callback=None) -> list:
    df = fetch_data(symbol, days)
    df = add_indicators(df, INDICATOR_CONFIG)

    trades        = []
    balance       = config["initial_balance"]
    total_bars    = len(df) - MIN_LOOKBACK - 1
    cooldown_bars = config.get("cooldown_bars", 3)
    max_per_day   = config.get("max_trades_per_day", 4)

    current_date   = None
    daily_bias     = "NONE"
    bias_locked    = False
    trades_today   = 0
    last_trade_bar = -cooldown_bars

    close_pct = int((1 - config.get("close_range_thresh", 0.70)) * 100)
    print(f"  Simulating {total_bars:,} bars | RR {config['reward_ratio']}:1 | "
          f"Risk {config['risk_pct']}% | Prox {config.get('proximity_atr_mult', 1.5)}xATR | "
          f"Close top/bot {close_pct}% | "
          f"RSI={'ON' if config.get('rsi_filter') else 'OFF'} | "
          f"ATRFloor={'ON' if config.get('atr_floor_filter') else 'OFF'}")

    for i in range(MIN_LOOKBACK, len(df) - 1):
        if progress_callback and i % 1000 == 0:
            progress_callback((i - MIN_LOOKBACK) / total_bars * 100)

        bar_time = df.index[i]
        bar_date = bar_time.date()
        bar_hour = bar_time.hour

        if bar_hour not in SESSION_HOURS:
            continue

        # New day: reset state. Bias re-evaluates each bar until it locks.
        if bar_date != current_date:
            current_date = bar_date
            daily_bias   = "NONE"
            bias_locked  = False
            trades_today = 0

        # Lazy bias: re-evaluate every bar until a non-NONE bias locks in.
        # Use a sliding 200-bar window to avoid O(n²) growth on long backtests.
        if not bias_locked:
            window = df.iloc[max(0, i - 200):i + 1]
            daily_bias = get_daily_bias(window)
            if daily_bias != "NONE":
                bias_locked = True

        if daily_bias == "NONE":
            continue
        if trades_today >= max_per_day:
            continue
        if i - last_trade_bar < cooldown_bars:
            continue

        direction, _ = check_entry(
            df.iloc[max(0, i - 50):i + 1],
            daily_bias,
            proximity_atr_mult = config.get("proximity_atr_mult", 1.5),
            body_atr_mult      = config.get("body_atr_mult", 0.4),
            close_range_thresh = config.get("close_range_thresh", 0.70),
            rsi_filter         = config.get("rsi_filter", False),
            atr_floor_filter   = config.get("atr_floor_filter", False),
        )

        if direction == "NEUTRAL":
            continue

        atr   = float(df.iloc[i]["atr"])
        trade = _simulate_trade(df, i, direction, atr, balance, config)
        if trade is None:
            continue

        balance            += trade["pnl"]
        trade["balance_after"] = round(balance, 2)
        trades.append(trade)
        last_trade_bar  = i
        trades_today   += 1

    wr = sum(1 for t in trades if t["result"] == "WIN") / len(trades) * 100 if trades else 0
    print(f"  -> {len(trades)} trades | WR {wr:.1f}% | Balance ${balance:,.0f}")
    return trades
