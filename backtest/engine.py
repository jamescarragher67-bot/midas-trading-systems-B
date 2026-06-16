"""
backtest/engine.py — MIDAS BACKTESTING ENGINE (CLEAN REBUILD)

Clean bar-by-bar simulation on H1 data.
Fixed 0.5% risk — no dynamic scaling to keep results clean and comparable.

Features simulated:
  - 5-strategy voting engine (4/5 threshold)
  - Smart SL placement (structural swing levels)
  - Time-based exit (8h soft, 24h hard)
  - Session filter
  - Cooldown between trades
  - Performance monitor (reduces risk during bad streaks)
"""

import MetaTrader5 as mt5
import pandas as pd
import numpy as np
import json
import logging
from strategy.indicators import add_indicators
from strategy.ema_stack            import get_signal as ema_signal
from strategy.rsi_divergence       import get_signal as rsi_signal
from strategy.bollinger_bands      import get_signal as bb_signal
from strategy.vwap_strategy        import get_signal as vwap_signal
from strategy.candlestick_patterns import get_signal as candle_signal

# ── Constants ─────────────────────────────────────────────────────────────────
CONTRACT_SIZE = 100
MIN_LOOKBACK  = 60

INDICATOR_CONFIG = {
    "EMA_FAST":   9,
    "EMA_SLOW":   21,
    "EMA_TREND":  50,
    "RSI_PERIOD": 14,
    "ATR_PERIOD": 14,
}

STRATEGIES = [
    ("EMA Stack",       ema_signal),
    ("RSI Divergence",  rsi_signal),
    ("Bollinger Bands", bb_signal),
    ("VWAP",            vwap_signal),
    ("Candlestick",     candle_signal),
]


# ── Data fetching ─────────────────────────────────────────────────────────────
def fetch_historical_data(symbol: str, days: int) -> pd.DataFrame:
    mt5.symbol_select(symbol, True)
    num_bars = min(days * 24, 100000)
    print(f"Requesting {num_bars} H1 bars from MT5...")
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_H1, 0, num_bars)
    if rates is None or len(rates) == 0:
        raise ValueError(f"No data returned. Error: {mt5.last_error()}")
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    df.set_index("time", inplace=True)
    print(f"Got {len(df):,} bars ({df.index[0].date()} → {df.index[-1].date()})")
    return df


# ── Voting ────────────────────────────────────────────────────────────────────
def _run_voting(df_slice: pd.DataFrame, threshold: int) -> dict:
    score   = 0
    details = []
    for name, fn in STRATEGIES:
        try:
            vote, reason = fn(df_slice)
            score += vote
            details.append({"strategy": name, "vote": vote, "reason": reason})
        except Exception:
            details.append({"strategy": name, "vote": 0, "reason": "error"})
    direction = "BUY" if score >= threshold else "SELL" if score <= -threshold else "NEUTRAL"
    return {"direction": direction, "score": score, "details": details}


# ── Smart SL ──────────────────────────────────────────────────────────────────
def _find_swing_lows(series: pd.Series, window: int = 2) -> list:
    return [i for i in range(window, len(series) - window)
            if series.iloc[i] == series.iloc[i - window: i + window + 1].min()]

def _find_swing_highs(series: pd.Series, window: int = 2) -> list:
    return [i for i in range(window, len(series) - window)
            if series.iloc[i] == series.iloc[i - window: i + window + 1].max()]

def _smart_sl(df_slice: pd.DataFrame, direction: str,
              entry: float, atr: float) -> tuple[float, str]:
    atr_sl = entry - atr * 1.5 if direction == "BUY" else entry + atr * 1.5
    try:
        recent = df_slice.iloc[-25:-1]
        buf    = atr * 0.3
        if direction == "BUY":
            lows   = _find_swing_lows(recent["low"])
            valid  = [recent["low"].iloc[i] for i in lows if recent["low"].iloc[i] < entry]
            if valid:
                sl = max(valid) - buf
                if atr * 0.5 <= (entry - sl) <= atr * 2.5:
                    return round(sl, 2), "structural"
        else:
            highs  = _find_swing_highs(recent["high"])
            valid  = [recent["high"].iloc[i] for i in highs if recent["high"].iloc[i] > entry]
            if valid:
                sl = min(valid) + buf
                if atr * 0.5 <= (sl - entry) <= atr * 2.5:
                    return round(sl, 2), "structural"
    except Exception:
        pass
    return round(atr_sl, 2), "atr"


# ── Lot size ──────────────────────────────────────────────────────────────────
def _lot_size(balance: float, sl_dist: float, risk_pct: float) -> float:
    """Simple fixed risk lot size — no dynamic scaling."""
    risk_amt  = balance * (risk_pct / 100)
    sl_points = sl_dist / 0.01
    if sl_points <= 0:
        return 0.01
    lot = risk_amt / (sl_points * CONTRACT_SIZE * 0.01)
    return max(0.01, min(round(round(lot / 0.01) * 0.01, 2), 10.0))


# ── Trade simulation ──────────────────────────────────────────────────────────
def _simulate_trade(df: pd.DataFrame, df_slice: pd.DataFrame,
                    entry_idx: int, direction: str,
                    atr: float, balance: float, config: dict) -> dict | None:
    if entry_idx + 1 >= len(df):
        return None

    entry_candle = df.iloc[entry_idx + 1]
    entry_price  = entry_candle["open"]

    sl, sl_method = _smart_sl(df_slice, direction, entry_price, atr)
    sl_dist = abs(entry_price - sl)
    if sl_dist <= 0:
        return None

    tp_dist  = sl_dist * config["reward_ratio"]
    tp       = entry_price + tp_dist if direction == "BUY" else entry_price - tp_dist
    lot_size = _lot_size(balance, sl_dist, config["risk_pct"])
    mult     = lot_size * CONTRACT_SIZE

    result     = None
    exit_price = None
    max_bars   = config.get("max_trade_hours_hard", 24)
    soft_bars  = config.get("max_trade_hours", 8)

    for j in range(entry_idx + 2, min(entry_idx + max_bars + 2, len(df))):
        c         = df.iloc[j]
        bars_open = j - entry_idx - 1

        # SL / TP check
        if direction == "BUY":
            if c["low"] <= sl:
                result, exit_price = "LOSS", sl; break
            if c["high"] >= tp:
                result, exit_price = "WIN", tp; break
        else:
            if c["high"] >= sl:
                result, exit_price = "LOSS", sl; break
            if c["low"] <= tp:
                result, exit_price = "WIN", tp; break

        # Time exit — soft (close if in profit after N hours)
        curr     = c["close"]
        in_profit = (curr > entry_price) if direction == "BUY" else (curr < entry_price)
        if bars_open >= soft_bars and in_profit:
            result, exit_price = "WIN", curr; break

    # Hard timeout
    if result is None:
        last       = df.iloc[min(entry_idx + max_bars + 1, len(df) - 1)]
        exit_price = last["close"]
        raw        = (exit_price - entry_price) if direction == "BUY" else (entry_price - exit_price)
        result     = "WIN" if raw > 0 else "LOSS"

    pnl = (tp_dist if result == "WIN" else -sl_dist) * mult
    entry_time = df.index[entry_idx + 1]

    return {
        "date":       entry_time.strftime("%Y-%m-%d"),
        "time":       entry_time.strftime("%H:%M"),
        "direction":  direction,
        "entry":      round(entry_price, 2),
        "exit":       round(exit_price, 2),
        "sl":         sl,
        "tp":         round(tp, 2),
        "lots":       lot_size,
        "pnl":        round(pnl, 2),
        "result":     result,
        "atr":        round(atr, 2),
        "sl_method":  sl_method,
    }


# ── Best hours export ─────────────────────────────────────────────────────────
def write_best_hours(trades: list, output_path: str = "best_hours.json"):
    from collections import defaultdict
    hourly = defaultdict(lambda: {"wins": 0, "total": 0})
    for t in trades:
        h = int(t["time"][:2])
        hourly[h]["total"] += 1
        if t["result"] == "WIN":
            hourly[h]["wins"] += 1
    result = {
        str(h): {
            "win_rate": round(hourly[h]["wins"] / hourly[h]["total"] * 100, 1) if hourly[h]["total"] > 0 else 0,
            "trades":   hourly[h]["total"],
            "wins":     hourly[h]["wins"],
        } for h in range(24)
    }
    with open(output_path, "w") as f:
        json.dump(result, f, indent=2)
    print(f"Best hours saved → {output_path}")


# ── Main simulation ───────────────────────────────────────────────────────────
def run_simulation(symbol: str, days: int, config: dict,
                   progress_callback=None) -> list:

    # Silence strategy loggers
    for name in ["ema_stack", "rsi_divergence", "bollinger_bands",
                 "vwap", "candlestick", "signal_engine", "regime"]:
        logging.getLogger(name).setLevel(logging.WARNING)

    df = fetch_historical_data(symbol, days)
    df = add_indicators(df, INDICATOR_CONFIG)

    trades         = []
    balance        = config["initial_balance"]
    last_trade_bar = -config["cooldown_bars"]
    threshold      = config["vote_threshold"]
    total_bars     = len(df) - MIN_LOOKBACK - 1

    print(f"Simulating {total_bars:,} bars | Risk: {config['risk_pct']}% | Threshold: {threshold}/5")

    for i in range(MIN_LOOKBACK, len(df) - 1):
        if progress_callback and i % 500 == 0:
            pct = (i - MIN_LOOKBACK) / total_bars * 100
            progress_callback(pct)

        # Cooldown
        if i - last_trade_bar < config["cooldown_bars"]:
            continue

        # Session filter
        if config.get("session_filter", True):
            hour = df.index[i].hour
            if not (0 <= hour < 15 or 20 <= hour <= 23):
                continue

        df_slice = df.iloc[:i + 1]

        # Voting
        vote = _run_voting(df_slice, threshold)
        if vote["direction"] == "NEUTRAL":
            continue

        # Performance monitor — reduce risk after bad streak
        effective_risk = config["risk_pct"]
        lookback = config.get("perf_lookback", 20)
        min_wr   = config.get("perf_min_wr", 30.0)
        reduced  = config.get("perf_reduced_risk", 0.25)
        if len(trades) >= config.get("perf_min_trades", 10):
            recent_trades = trades[-lookback:]
            recent_wr     = sum(1 for t in recent_trades if t["result"] == "WIN") / len(recent_trades) * 100
            if recent_wr < min_wr:
                effective_risk = reduced

        atr   = df.iloc[i]["atr"]
        trade_config = {**config, "risk_pct": effective_risk}
        trade = _simulate_trade(df, df_slice, i, vote["direction"], atr, balance, trade_config)

        if trade is None:
            continue

        trade["vote_score"]     = vote["score"]
        trade["strategy_votes"] = {d["strategy"]: d["vote"] for d in vote["details"]}
        balance                += trade["pnl"]
        trade["balance_after"]  = round(balance, 2)
        trades.append(trade)
        last_trade_bar = i

        if len(trades) % 50 == 0:
            wr = sum(1 for t in trades if t["result"] == "WIN") / len(trades) * 100
            print(f"  {len(trades)} trades | WR: {wr:.1f}% | Balance: ${balance:,.0f}")

    print(f"\nDone: {len(trades)} trades | Final balance: ${balance:,.0f}")
    return trades
