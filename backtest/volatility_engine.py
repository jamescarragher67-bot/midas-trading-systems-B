"""
backtest/volatility_engine.py - MIDAS-B Volatility Backtest Engine

Bar-by-bar simulation using the three-layer architecture:
  1. Volatility fingerprint (precomputed for speed)
  2. Regime classifier → A / B / C / UNKNOWN
  3. Transition detector → fires signal on regime change

Trade simulation uses ATR-relative SL (not structural swing levels).
All indicator series precomputed once before the loop for ~30s runtime on 51k bars.
"""

import pandas as pd
import numpy as np
import logging
from datetime import timezone

import MetaTrader5 as mt5

from strategy.volatility_metrics   import precompute_volatility_series, fingerprint_at
from strategy.regime_classifier    import classify_regime
from strategy.transition_detector  import detect_transition
from strategy.volatility_signal_engine import get_signal, passes_filters
from utils.news_filter             import is_news_blackout

CONTRACT_SIZE = 100
POINT         = 0.01
MIN_LOOKBACK  = 100   # need enough bars for ATR_MA50 + stddev_MA50


# ── Data fetch (reused from existing engine) ───────────────────────────────────

def fetch_historical_data(symbol: str, days: int) -> pd.DataFrame:
    mt5.symbol_select(symbol, True)
    num_bars = min(days * 288, 75000)
    print(f"Requesting {num_bars:,} M5 bars from MT5...")
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, 0, num_bars)
    if rates is None or len(rates) == 0:
        raise ValueError(f"No data returned. Error: {mt5.last_error()}")
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    df.set_index("time", inplace=True)
    print(f"Got {len(df):,} bars ({df.index[0].date()} to {df.index[-1].date()})")
    return df


# ── ATR-relative trade simulation ──────────────────────────────────────────────

def _lot_size(balance: float, sl_dist: float, risk_pct: float, max_lot: float) -> float:
    risk_amt  = balance * (risk_pct / 100)
    sl_points = sl_dist / POINT
    if sl_points <= 0:
        return 0.01
    lot = risk_amt / (sl_points * CONTRACT_SIZE * POINT)
    return max(0.01, min(round(round(lot / 0.01) * 0.01, 2), max_lot))


def _simulate_vol_trade(df: pd.DataFrame, entry_idx: int, direction: str,
                        signal: dict, balance: float, config: dict) -> dict | None:
    if entry_idx + 1 >= len(df):
        return None

    entry_bar   = df.iloc[entry_idx + 1]
    entry_price = float(entry_bar["open"])
    atr         = signal["atr"]
    sl_dist     = atr * signal["sl_atr_mult"]
    trail_dist  = atr * signal["trail_atr_mult"]
    rr          = signal["reward_ratio"]
    tp_dist     = sl_dist * rr

    if sl_dist <= 0:
        return None

    sl = entry_price - sl_dist if direction == "BUY" else entry_price + sl_dist
    tp = entry_price + tp_dist if direction == "BUY" else entry_price - tp_dist

    max_lot  = config.get("max_lot_size", 0.5)
    lot_size = _lot_size(balance, sl_dist, config["risk_pct"], max_lot)
    mult     = lot_size * CONTRACT_SIZE

    current_sl = sl
    max_bars   = config.get("max_trade_hours_hard", 24) * 12
    soft_bars  = config.get("max_trade_hours",      8)  * 12

    result     = None
    exit_price = None

    for j in range(entry_idx + 2, min(entry_idx + max_bars + 2, len(df))):
        c    = df.iloc[j]
        high = float(c["high"])
        low  = float(c["low"])
        bars_open = j - entry_idx - 1

        # Trail SL
        if direction == "BUY":
            cand = round(high - trail_dist, 2)
            if cand > current_sl:
                current_sl = cand
        else:
            cand = round(low + trail_dist, 2)
            if cand < current_sl:
                current_sl = cand

        if direction == "BUY":
            if low <= current_sl:
                result     = "WIN" if current_sl > entry_price else "LOSS"
                exit_price = current_sl
                break
            if high >= tp:
                result, exit_price = "WIN", tp
                break
        else:
            if high >= current_sl:
                result     = "WIN" if current_sl < entry_price else "LOSS"
                exit_price = current_sl
                break
            if low <= tp:
                result, exit_price = "WIN", tp
                break

        in_profit = (float(c["close"]) > entry_price) if direction == "BUY" else (float(c["close"]) < entry_price)
        if bars_open >= soft_bars and in_profit:
            result, exit_price = "WIN", float(c["close"])
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
        "date":            entry_time.strftime("%Y-%m-%d"),
        "time":            entry_time.strftime("%H:%M"),
        "direction":       direction,
        "entry":           round(entry_price, 2),
        "exit":            round(exit_price, 2),
        "sl":              round(sl, 2),
        "sl_initial":      round(sl, 2),
        "sl_final":        round(current_sl, 2),
        "tp":              round(tp, 2),
        "lots":            lot_size,
        "pnl":             round(pnl, 2),
        "result":          result,
        "atr":             round(atr, 2),
        "sl_method":       f"atr_x{signal['sl_atr_mult']}",
        "trailing_active": True,
        "strategy_votes":  {"Vol Engine": 1 if direction == "BUY" else -1},
    }


# ── Main simulation ───────────────────────────────────────────────────────────

def run_volatility_simulation(symbol: str, days: int, config: dict,
                              progress_callback=None) -> tuple[list, dict]:
    """
    Run full bar-by-bar volatility backtest.
    Returns (trades, regime_counts).
    """
    for name in ["volatility_metrics", "regime_classifier",
                 "transition_detector", "volatility_signal_engine"]:
        logging.getLogger(name).setLevel(logging.WARNING)

    df = fetch_historical_data(symbol, days)

    print("Precomputing volatility series...")
    series = precompute_volatility_series(df)
    print("Done. Starting simulation...")

    trades         = []
    balance        = config["initial_balance"]
    regime_history = []
    regime_counts  = {"A": 0, "B": 0, "C": 0, "UNKNOWN": 0}
    last_trade_bar = -config.get("cooldown_bars", 3)
    trades_today   = 0
    current_date   = None
    total_bars     = len(df) - MIN_LOOKBACK - 1

    print(f"Simulating {total_bars:,} bars | Volatility Engine | Risk: {config['risk_pct']}%")

    for i in range(MIN_LOOKBACK, len(df) - 1):
        if progress_callback and i % 2000 == 0:
            pct = (i - MIN_LOOKBACK) / total_bars * 100
            progress_callback(pct)

        bar_time = df.index[i]
        bar_date = bar_time.date()

        # Daily trade counter reset
        if bar_date != current_date:
            current_date = bar_date
            trades_today = 0

        # Get fingerprint at this bar (O(1) from precomputed series)
        fp = fingerprint_at(series, i)

        # Skip bars with invalid data (NaN during warmup)
        if fp["atr_ma50"] == 0.0 or fp["stddev_ma50"] == 0.0:
            regime_history.append("UNKNOWN")
            regime_counts["UNKNOWN"] += 1
            continue

        # Classify regime
        regime = classify_regime(fp)
        regime_counts[regime] = regime_counts.get(regime, 0) + 1

        # Only detect transitions after enough history
        if len(regime_history) < 4:
            regime_history.append(regime)
            continue

        prev_regime = regime_history[-1]
        regime_history.append(regime)
        # Keep history bounded
        if len(regime_history) > 30:
            regime_history.pop(0)

        # Only check transition on regime change
        if regime == prev_regime:
            continue

        current_bar_series = df.iloc[i]
        transition = detect_transition(regime_history, fp, current_bar_series)

        if transition["transition"] in ("NONE", "C_to_A"):
            continue

        # Filters
        ok, reason = passes_filters(
            bar_time, config, trades_today, last_trade_bar, i
        )
        if not ok:
            continue

        # News filter
        if is_news_blackout(bar_time.to_pydatetime().replace(tzinfo=timezone.utc)):
            continue

        # Generate signal
        signal = get_signal(transition, fp, config)
        if signal is None:
            continue

        # Performance monitor
        effective_risk = config["risk_pct"]
        if len(trades) >= config.get("perf_min_trades", 10):
            recent    = trades[-config.get("perf_lookback", 20):]
            recent_wr = sum(1 for t in recent if t["result"] == "WIN") / len(recent) * 100
            if recent_wr < config.get("perf_min_wr", 30.0):
                effective_risk = config.get("perf_reduced_risk", 0.5)

        trade_signal = {**signal, "reward_ratio": config["reward_ratio"]}
        trade_config = {**config, "risk_pct": effective_risk}

        trade = _simulate_vol_trade(df, i, signal["direction"], trade_signal, balance, trade_config)
        if trade is None:
            continue

        trade["regime"]     = regime
        trade["transition"] = transition["transition"]
        trade["confidence"] = transition["confidence"]
        balance            += trade["pnl"]
        trade["balance_after"] = round(balance, 2)
        trades.append(trade)
        last_trade_bar = i
        trades_today  += 1

        if len(trades) % 50 == 0:
            wr = sum(1 for t in trades if t["result"] == "WIN") / len(trades) * 100
            print(f"  {len(trades)} trades | WR: {wr:.1f}% | Balance: ${balance:,.0f}")

    print(f"\nDone: {len(trades)} trades | Final balance: ${balance:,.0f}")
    print(f"Regime distribution: A={regime_counts['A']:,} | B={regime_counts['B']:,} | "
          f"C={regime_counts['C']:,} | UNKNOWN={regime_counts['UNKNOWN']:,}")
    return trades, regime_counts
