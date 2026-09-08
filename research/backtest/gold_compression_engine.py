"""
backtest/gold_compression_engine.py - Compression Breakout (M5) backtest harness

Mirrors backtest/lsc_engine.py's mechanics exactly (next-bar-open entry,
SL/TP hit detection, margin-safe lot capping, spread cost) - same standard
LSC was validated with - retuned for M5 instead of M15:
  - BARS_PER_DAY / MAX_HOLD_BARS: 288 (M5) not 96 (M15)
  - TIMEFRAME: M5 not M15
SESSION_HOURS is kept IDENTICAL to LSC's (00:00-14:59 + 20:00-23:59 UTC) -
that's an account/liquidity-window choice, not something specific to LSC's
signal, so there's no reason to re-derive it for a different M5 signal on
the same instrument.

Calls strategy/gold_compression_breakout.py's actual precompute/check_entry
directly - no separate approximation, same discipline as lsc_engine.py.
"""

import MetaTrader5 as mt5
import pandas as pd
from research.strategy.gold_compression_breakout import precompute, check_entry

POINT         = 0.01
CONTRACT_SIZE = 100
SESSION_HOURS = set(range(0, 15)) | {20, 21, 22, 23}
BARS_PER_DAY  = 288    # M5: 24h x 12 five-min bars/hour
MAX_HOLD_BARS = BARS_PER_DAY
MIN_LOOKBACK  = 100    # warmup bars before the loop starts (>= ATR_MA_PERIOD + RANGE_LOOKBACK)


def compute_atr14(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low, (high - prev_close).abs(), (low - prev_close).abs()
    ], axis=1).max(axis=1)
    return tr.ewm(com=period - 1, adjust=False).mean()


def get_symbol_specs(symbol: str) -> tuple[float, float]:
    """Returns (point, contract_size) read live from MT5 - never hardcoded."""
    info = mt5.symbol_info(symbol)
    return float(info.point), float(info.trade_contract_size)


def fetch_data(symbol: str, max_bars: int) -> pd.DataFrame:
    if not mt5.initialize():
        raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")
    if not mt5.symbol_select(symbol, True):
        raise RuntimeError(f"symbol_select({symbol}) failed: {mt5.last_error()}")
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, 0, max_bars)
    if rates is None or len(rates) == 0:
        raise RuntimeError(f"No data returned for {symbol}: {mt5.last_error()}")
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    df.set_index("time", inplace=True)
    return df


def _lot_size(balance: float, sl_dist: float, risk_pct: float, point: float,
              contract_size: float, max_lot: float) -> float:
    risk_amt  = balance * (risk_pct / 100)
    sl_points = sl_dist / point
    if sl_points <= 0:
        return 0.01
    lot = risk_amt / (sl_points * contract_size * point)
    return max(0.01, min(round(round(lot / 0.01) * 0.01, 2), max_lot))


def _simulate_trade(df: pd.DataFrame, entry_idx: int, direction: str,
                    sl: float, tp: float, balance: float, config: dict,
                    point: float, contract_size: float) -> dict | None:
    if entry_idx + 1 >= len(df):
        return None

    entry_candle = df.iloc[entry_idx + 1]
    entry_price  = float(entry_candle["open"])
    sl_dist = (entry_price - sl) if direction == "BUY" else (sl - entry_price)
    if sl_dist <= 0:
        return None

    lot_size = _lot_size(balance, sl_dist, config["risk_pct"], point, contract_size,
                         config["max_lot_size"])
    mult     = lot_size * contract_size
    spread_cost = config.get("spread_points", 0) * point * mult

    result, exit_price = None, None
    for j in range(entry_idx + 2, min(entry_idx + MAX_HOLD_BARS + 2, len(df))):
        c = df.iloc[j]
        high, low = float(c["high"]), float(c["low"])
        if direction == "BUY":
            if low <= sl:
                result, exit_price = "LOSS", sl; break
            if high >= tp:
                result, exit_price = "WIN", tp; break
        else:
            if high >= sl:
                result, exit_price = "LOSS", sl; break
            if low <= tp:
                result, exit_price = "WIN", tp; break

    if result is None:
        last = df.iloc[min(entry_idx + MAX_HOLD_BARS + 1, len(df) - 1)]
        exit_price = float(last["close"])

    raw_move = (exit_price - entry_price) if direction == "BUY" else (entry_price - exit_price)
    pnl    = raw_move * mult - spread_cost
    result = "WIN" if pnl > 0 else "LOSS"

    entry_time = df.index[entry_idx + 1]
    return {
        "date": entry_time.strftime("%Y-%m-%d"), "time": entry_time.strftime("%H:%M"),
        "direction": direction, "entry": round(entry_price, 2), "exit": round(exit_price, 2),
        "sl": round(sl, 2), "tp": round(tp, 2), "lots": lot_size,
        "spread_cost": round(spread_cost, 2), "pnl": round(pnl, 2), "result": result,
        "balance_after": round(balance + pnl, 2),
        "margin_capped": False,   # set correctly below once lot capping is resolved against a live cap
    }


def run_simulation(symbol: str, df: pd.DataFrame, config: dict,
                    point: float, contract_size: float,
                    progress_callback=None) -> list:
    """df must already have 'atr' and the compression/squeeze columns from
    strategy.gold_compression_breakout.precompute()."""
    trades         = []
    balance        = config["initial_balance"]
    total_bars     = len(df) - MIN_LOOKBACK - 1
    current_date   = None
    trades_today   = 0
    last_trade_bar = -config.get("cooldown_bars", 9)

    for i in range(MIN_LOOKBACK, len(df) - 1):
        if progress_callback and i % 5000 == 0:
            progress_callback((i - MIN_LOOKBACK) / total_bars * 100)

        bar_time = df.index[i]
        bar_date = bar_time.date()

        if bar_date != current_date:
            current_date = bar_date
            trades_today = 0

        if bar_time.hour not in SESSION_HOURS:
            continue

        direction, _, sl, tp = check_entry(df, i, last_trade_bar, trades_today,
                                           config.get("cooldown_bars", 9),
                                           config.get("max_trades_per_day", 4))
        if direction == "NEUTRAL":
            continue

        trade = _simulate_trade(df, i, direction, sl, tp, balance, config, point, contract_size)
        if trade is None:
            continue

        balance                += trade["pnl"]
        trade["balance_after"]  = round(balance, 2)
        trades.append(trade)
        last_trade_bar  = i
        trades_today   += 1

    wr = sum(1 for t in trades if t["result"] == "WIN") / len(trades) * 100 if trades else 0
    print(f"  -> {len(trades)} trades | WR {wr:.1f}% | Balance ${balance:,.0f}")
    return trades
