"""
backtest/lsc_engine.py - Liquidity-Sweep Continuation (M15) backtest harness

Calls strategy/lsc_m15.py's actual precompute/check_entry directly - no
separate approximation. Mechanics (next-bar-open entry, SL/TP hit
detection, margin-safe lot capping, spread cost) carried forward from
backtest/bot1_tournament_engine.py, the harness LSC was actually
validated with tonight, simplified down to a single strategy instead of
a generic multi-strategy dispatcher.

Entry price = next bar's open (avoids lookahead). Position sizing: 1.5%
(now 1.0%, see config/settings.py)-of-balance risk formula, HARD-CAPPED
at a margin-safe lot ceiling - naive risk-formula sizing alone was proven
tonight (at multiple timeframes) to occasionally demand more margin than
the account has. Never let the risk% formula decide lot size unchecked.
"""

import MetaTrader5 as mt5
import pandas as pd
from strategy.lsc_m15 import precompute, check_entry

POINT         = 0.01
CONTRACT_SIZE = 100
SESSION_HOURS = set(range(0, 15)) | {20, 21, 22, 23}
BARS_PER_DAY  = 96     # M15: used for both "days" lookups and the 1-day hold cap
MAX_HOLD_BARS = BARS_PER_DAY
MIN_LOOKBACK  = 100    # warmup bars before the loop starts


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


def fetch_data(symbol: str, days: int, date_from=None, date_to=None) -> pd.DataFrame:
    mt5.symbol_select(symbol, True)
    if date_from is not None and date_to is not None:
        rates = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M15, date_from, date_to)
        if rates is None or len(rates) == 0:
            # copy_rates_range silently fails ("Invalid params") on very large
            # multi-year spans - fall back to fetching all available history
            # by count, then filter to the requested window in pandas.
            rates_all = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M15, 0, 75000)
            if rates_all is not None and len(rates_all) > 0:
                df_all = pd.DataFrame(rates_all)
                df_all["time"] = pd.to_datetime(df_all["time"], unit="s")
                mask = (df_all["time"] >= date_from) & (df_all["time"] <= date_to)
                df_all = df_all[mask]
                if len(df_all) > 0:
                    df_all.set_index("time", inplace=True)
                    return df_all
            rates = None
    else:
        num_bars = min(days * BARS_PER_DAY, 75000)
        rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M15, 0, num_bars)
    if rates is None or len(rates) == 0:
        raise ValueError(f"No data returned: {mt5.last_error()}")
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
    }


def run_lsc_simulation(symbol: str, days: int, config: dict,
                       date_from=None, date_to=None,
                       progress_callback=None) -> list:
    df = fetch_data(symbol, days, date_from, date_to)
    df["atr"] = compute_atr14(df)
    df = precompute(df)
    point, contract_size = get_symbol_specs(symbol)

    trades         = []
    balance        = config["initial_balance"]
    total_bars     = len(df) - MIN_LOOKBACK - 1
    current_date   = None
    trades_today   = 0
    last_trade_bar = -config.get("cooldown_bars", 3)

    for i in range(MIN_LOOKBACK, len(df) - 1):
        if progress_callback and i % 2000 == 0:
            progress_callback((i - MIN_LOOKBACK) / total_bars * 100)

        bar_time = df.index[i]
        bar_date = bar_time.date()

        if bar_date != current_date:
            current_date = bar_date
            trades_today = 0

        if bar_time.hour not in SESSION_HOURS:
            continue

        direction, _, sl, tp = check_entry(df, i, last_trade_bar, trades_today,
                                           config.get("cooldown_bars", 3),
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
