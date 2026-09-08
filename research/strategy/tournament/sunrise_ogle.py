"""
strategy/tournament/sunrise_ogle.py - "Advanced Sunrise Strategy" (Sunrise Ogle),
ported from github.com/ilahuerta-IA/backtrader-pullback-window-xauusd,
src/strategy/sunrise_ogle_xauusd.py (184KB / 3450 lines, fetched directly and
read in full by a research pass - see task notes).

CRITICAL PROVENANCE NOTE: that file contains TWO parallel, independently-coded
entry state machines. Only ONE - the 4-phase "entry_state" machine
(SCANNING -> ARMED_LONG -> WINDOW_OPEN -> entry) hardcoded directly in
next() - is ever actually called. A second "pullback_state" 3-phase system
(_full_entry_signal and everything it calls) is fully implemented but its
entry point is never invoked anywhere in the file - dead code. This module
ports the LIVE path only. It also corrects for two stale-docstring
mismatches confirmed against the actual `params` dict: SL/TP ATR multipliers
are 4.5/6.5 (LONG), not the 2.5/12.0 the module's own top-of-file comment
claims; and the time-range filter defaults OFF (24/7), not "7:00-17:00 UTC".

CONFIGURATION PORTED: the repo's own default params ship LONG ONLY
(enable_short_trades=False) - confirmed both in the README and in the
params dict - so this port is long-only, matching what the repo actually
runs out of the box. All filters below are ported at their default values;
the (disabled-by-default) angle/candle-direction/EMA-order filters for LONG
are correctly omitted since they're off in the source.

DELIBERATE DEVIATION - position sizing: the source's live sizing path computes
1%-of-equity risk in 100oz whole contracts with `max(int(...), 1)` - i.e. it
is FLOORED AT ONE FULL 100oz CONTRACT NO MATTER HOW SMALL THE RISK BUDGET IS,
and has NO margin-safe ceiling in the active code path (a margin-aware sizer
exists in the file but is never called - also dead code). Reusing that
formula unmodified on a $50,000 account would routinely demand more margin
than the account has - precisely the failure mode this codebase's own
LSC validation already burned a night proving out (see backtest/lsc_engine.py
docstring: "never let the risk% formula decide lot size unchecked"). So this
port keeps the source's entry/exit signal logic exactly, but sizes positions
with the tournament-standard margin-safe formula (backtest/tournament/engine.
_lot_size: same 1% risk, same $50k/1:10/25%-budget assumptions as every other
candidate) instead of the source's uncapped integer-contract formula. This is
the one place signal-vs-risk are deliberately decoupled - flagged rather than
silently substituted.

DATA CAVEAT: the source repo backtested on 5 years of 5-minute XAUUSD data
(its own bundled CSV, 2019-2024ish). Our MT5/Pepperstone-Demo feed only has
M5 history back to 2025-04-03 (~17 months total) - nowhere near 5 years.
Round-1/2/3 in-sample and the Round-4 OOS split for this candidate are both
drawn from that much shorter window; any result here is far less statistically
grounded than the source repo's own claimed 44.75%/5yr, 0.892 Sharpe figures,
which we have NOT independently verified (WebFetch on that repo's README
returned a fetch-tool summary, not verified raw text - treat those headline
numbers as unverified marketing claims from the repo's own README, not as
something this tournament has reproduced).
"""

import math
import pandas as pd
import numpy as np
import MetaTrader5 as mt5

from research.backtest.tournament.engine import compute_atr14, _lot_size, POINT, CONTRACT_SIZE

TIMEFRAME_MT5   = mt5.TIMEFRAME_M5
TIMEFRAME_LABEL = "M5"

EMA_FAST, EMA_MEDIUM, EMA_SLOW = 14, 14, 24
EMA_FILTER_PRICE = 100
ATR_PERIOD = 10

ATR_MIN, ATR_MAX = 0.0, 2.00          # long_atr_min/max_threshold
PULLBACK_MAX_CANDLES = 3               # long_pullback_max_candles
WINDOW_PERIODS = 1                     # long_entry_window_periods
WINDOW_PRICE_OFFSET_MULT = 0.001       # window_price_offset_multiplier

SL_ATR_MULT = 4.5     # long_atr_sl_multiplier
TP_ATR_MULT = 6.5     # long_atr_tp_multiplier

RISK_PERCENT = 1.0     # matches source's risk_percent=0.01 AND tournament default


def _precompute(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["ema_fast"]   = df["close"].ewm(span=EMA_FAST, adjust=False).mean()
    df["ema_medium"] = df["close"].ewm(span=EMA_MEDIUM, adjust=False).mean()
    df["ema_slow"]   = df["close"].ewm(span=EMA_SLOW, adjust=False).mean()
    df["ema_filter_price"] = df["close"].ewm(span=EMA_FILTER_PRICE, adjust=False).mean()
    df["atr10"] = compute_atr14(df, period=ATR_PERIOD)
    # ema_confirm has period=1 -> alpha=1 -> identically equal to close, so
    # "close crosses above ema_fast/slow" IS the cross_confirm condition.
    return df


def _cross_above(cur_a, prev_a, cur_b, prev_b):
    return cur_a > cur_b and prev_a <= prev_b


def _cross_below(cur_a, prev_a, cur_b, prev_b):
    return cur_a < cur_b and prev_a >= prev_b


def run(df_raw: pd.DataFrame, initial_balance: float = 50_000.0,
        risk_pct: float = RISK_PERCENT, spread_points: int = 18,
        max_lot: float = 50.0, min_lookback: int = 120,
        skip_atr_filter: bool = False) -> list:
    """
    skip_atr_filter: the source's ATR range filter (0.0-2.00) is an ABSOLUTE
    DOLLAR threshold, presumably calibrated against whatever gold price level
    its own 5-year CSV covered. Our current data (XAUUSD.a, $2,959-$5,587)
    only satisfies atr10<=2.00 on ~13% of M5 bars, so the verbatim filter is
    almost certainly a stale price-scale artifact, not a deliberate signal
    filter, at today's gold price. Set True to test the rest of the state
    machine's logic without that one filter - a clearly-labeled deviation
    from the literal port, not a silent "fix".
    """
    df = _precompute(df_raw)
    n = len(df)
    if n < min_lookback + 10:
        return []

    trades = []
    balance = initial_balance

    state = "SCANNING"          # SCANNING | ARMED_LONG | WINDOW_OPEN
    pullback_count = 0
    window_top = window_bottom = None
    window_start_bar = window_expiry_bar = None
    in_position_until = -1      # bar index; skip signal scanning while a trade is open

    close = df["close"].values
    open_ = df["open"].values
    high  = df["high"].values
    low   = df["low"].values
    ema_f = df["ema_fast"].values
    ema_m = df["ema_medium"].values
    ema_s = df["ema_slow"].values
    ema_p = df["ema_filter_price"].values
    atr   = df["atr10"].values

    i = min_lookback
    while i < n - 1:
        if i <= in_position_until:
            i += 1
            continue

        if any(np.isnan(x) for x in (ema_f[i], ema_m[i], ema_s[i], ema_p[i], atr[i])):
            i += 1
            continue

        if state == "SCANNING":
            cross_any = (
                _cross_above(close[i], close[i-1], ema_f[i], ema_f[i-1]) or
                _cross_above(close[i], close[i-1], ema_m[i], ema_m[i-1]) or
                _cross_above(close[i], close[i-1], ema_s[i], ema_s[i-1])
            )
            atr_ok = skip_atr_filter or (ATR_MIN <= atr[i] <= ATR_MAX)
            if cross_any and close[i] > ema_p[i] and atr_ok:
                state = "ARMED_LONG"
                pullback_count = 0

        elif state == "ARMED_LONG":
            # Global invalidation: prior candle bearish + confirm crosses below any EMA
            invalidated = (
                close[i-1] < open_[i-1] and (
                    _cross_below(close[i], close[i-1], ema_f[i], ema_f[i-1]) or
                    _cross_below(close[i], close[i-1], ema_m[i], ema_m[i-1]) or
                    _cross_below(close[i], close[i-1], ema_s[i], ema_s[i-1])
                )
            )
            if invalidated:
                state = "SCANNING"
                pullback_count = 0
            else:
                is_pullback_candle = close[i] < open_[i]   # red candle
                if is_pullback_candle:
                    pullback_count += 1
                    if pullback_count >= PULLBACK_MAX_CANDLES:
                        rng = high[i] - low[i]
                        offset = rng * WINDOW_PRICE_OFFSET_MULT
                        window_top    = high[i] + offset
                        window_bottom = low[i] - offset
                        window_start_bar  = i
                        window_expiry_bar = i + WINDOW_PERIODS
                        state = "WINDOW_OPEN"
                else:
                    state = "SCANNING"
                    pullback_count = 0

        elif state == "WINDOW_OPEN":
            if i > window_expiry_bar:
                # timeout -> back to ARMED_LONG, pullback count resets, direction retained
                state = "ARMED_LONG"
                pullback_count = 0
                window_top = window_bottom = None
            elif high[i] >= window_top:
                # breakout SUCCESS - re-validate price filter at breakout time (only
                # active LONG re-check per source; candle-direction/angle/order are
                # off by default for LONG so nothing else to re-check here)
                if close[i] > ema_p[i]:
                    entry_price = float(close[i])
                    bar_low, bar_high = float(low[i]), float(high[i])
                    atr_now = float(atr[i])
                    sl = bar_low  - SL_ATR_MULT * atr_now
                    tp = bar_high + TP_ATR_MULT * atr_now
                    sl_dist = entry_price - sl
                    if sl_dist > 0:
                        lot = _lot_size(balance, sl_dist, risk_pct, entry_price, max_lot)
                        mult = lot * CONTRACT_SIZE
                        spread_cost = spread_points * POINT * mult

                        result, exit_price, j = None, None, i
                        for j in range(i + 1, n):
                            c_hi, c_lo = float(high[j]), float(low[j])
                            if c_lo <= sl:
                                result, exit_price = "LOSS", sl; break
                            if c_hi >= tp:
                                result, exit_price = "WIN", tp; break
                        if result is None:
                            j = n - 1
                            exit_price = float(close[j])

                        raw_move = exit_price - entry_price
                        pnl = raw_move * mult - spread_cost
                        result = "WIN" if pnl > 0 else "LOSS"
                        balance += pnl
                        entry_time = df.index[i]
                        trades.append({
                            "date": entry_time.strftime("%Y-%m-%d"), "time": entry_time.strftime("%H:%M"),
                            "direction": "BUY", "entry": round(entry_price, 2), "exit": round(exit_price, 2),
                            "sl": round(sl, 2), "tp": round(tp, 2), "lots": lot,
                            "spread_cost": round(spread_cost, 2), "pnl": round(pnl, 2), "result": result,
                            "balance_after": round(balance, 2),
                        })
                        in_position_until = j
                    state = "SCANNING"
                    pullback_count = 0
                    window_top = window_bottom = None
                else:
                    state = "SCANNING"
                    pullback_count = 0
                    window_top = window_bottom = None
            elif low[i] <= window_bottom:
                # wrong-side break -> FAILURE, revert to ARMED_LONG (retain direction)
                state = "ARMED_LONG"
                pullback_count = 0
                window_top = window_bottom = None

        i += 1

    return trades
