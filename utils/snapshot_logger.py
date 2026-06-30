"""
utils/snapshot_logger.py — Background signal snapshot logger (Task 1)

Runs as a daemon thread inside main_combined.py.
Every 15 minutes, writes a compact 2-line snapshot to logs/signal_snapshots.log
showing exactly what both bots see — without entering the trading loop.

Thread-safe: only performs MT5 reads (no order sends).
Any exception is caught and logged; the thread never crashes the main bot.
"""

import logging
import os
import threading
import time
from datetime import datetime, timezone

import MetaTrader5 as mt5
import numpy as np
import pandas as pd

from config import settings
from strategy.indicators import add_indicators
from strategy.ema_stack import get_signal as ema_signal
from strategy.atr_expansion import get_signal as atr_signal
from strategy.prev_day_structure import get_signal as pds_signal
from strategy.m5_execution_engine import get_daily_bias
from strategy.volatility_metrics import get_volatility_fingerprint
from strategy.regime_classifier import classify_regime
from strategy.transition_detector import detect_transition

# ── Constants (mirror main_combined.py) ──────────────────────────────────────
_INTERVAL_SEC   = 900        # 15 minutes
_INITIAL_DELAY  = 60         # first snapshot after 60 s (one loop has run)
_SNAPSHOT_FILE  = os.path.join("logs", "signal_snapshots.log")
_INDICATOR_CFG  = {"EMA_FAST": 9, "EMA_SLOW": 21, "EMA_TREND": 50,
                   "RSI_PERIOD": 14, "ATR_PERIOD": 14}
_SESSION_HOURS  = set(range(0, 15)) | {20, 21, 22, 23}
_BOT1_SPREAD    = 15
_BOT2_SPREAD    = 20
_HIST_BARS      = 20   # regime history window

_stop = threading.Event()

# ── Dedicated snapshot log file (separate from daily trading logs) ────────────
def _get_logger() -> logging.Logger:
    name = "snapshot"
    log  = logging.getLogger(name)
    if log.handlers:
        return log
    log.setLevel(logging.INFO)
    os.makedirs("logs", exist_ok=True)
    fh = logging.FileHandler(_SNAPSHOT_FILE, encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(message)s"))
    log.addHandler(fh)
    return log


def _v(vote: int) -> str:
    return {1: "BUY ", -1: "SELL", 0: "NONE"}.get(vote, "NONE")


def _take_snapshot() -> None:
    now = datetime.now(timezone.utc)

    # ── Fetch bars ────────────────────────────────────────────────────────────
    mt5.symbol_select(settings.SYMBOL, True)
    rates = mt5.copy_rates_from_pos(settings.SYMBOL, mt5.TIMEFRAME_M5, 0, 200)
    if rates is None or len(rates) < 60:
        _get_logger().info(
            f"[{now.strftime('%Y-%m-%d %H:%M UTC')}] SNAPSHOT SKIPPED — insufficient M5 bars"
        )
        return

    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    df.set_index("time", inplace=True)
    df_ind = add_indicators(df.copy(), _INDICATOR_CFG)

    # ── Bot 1 voters ──────────────────────────────────────────────────────────
    ev, _  = ema_signal(df_ind)
    av, _  = atr_signal(df_ind)
    pv, _  = pds_signal(df_ind)
    bias   = get_daily_bias(df_ind)

    # Raw values for EMA voter
    closed = df_ind.iloc[-2]
    ema9   = float(closed["ema_fast"])
    ema21  = float(closed["ema_slow"])
    ema50  = float(closed["ema_trend"])

    # Raw values for ATR voter
    atr_series = df_ind["atr"].iloc[:-1]
    atr_val    = float(atr_series.iloc[-1])
    atr_ma20   = float(atr_series.rolling(20).mean().iloc[-1])
    atr_thresh = atr_ma20 * 1.2
    atr_pct    = round(atr_val / atr_thresh * 100, 1) if atr_thresh > 0 else 0.0

    # Raw values for PDS voter
    current_close = float(df_ind.iloc[-1]["close"])
    current_date  = df_ind.index[-1].date()
    prev_bars     = df_ind[df_ind.index.date < current_date]
    pdh = pdl = float("nan")
    if len(prev_bars) >= 12:
        prev_date = prev_bars.index.date[-1]
        day_bars  = prev_bars[prev_bars.index.date == prev_date]
        if len(day_bars) > 0:
            pdh = float(day_bars["high"].max())
            pdl = float(day_bars["low"].min())

    pdh_dist = round(current_close - pdh, 2) if not np.isnan(pdh) else float("nan")
    pdl_dist = round(current_close - pdl, 2) if not np.isnan(pdl) else float("nan")

    # ── Bot 2 regime ──────────────────────────────────────────────────────────
    fp     = get_volatility_fingerprint(df)
    regime = classify_regime(fp)

    # Build recent regime history for transition detection
    regime_history: list[str] = []
    n = len(df)
    for bar_i in range(max(0, n - _HIST_BARS), n):
        sl = df.iloc[max(0, bar_i - 100): bar_i + 1]
        if len(sl) >= 50:
            regime_history.append(classify_regime(get_volatility_fingerprint(sl)))

    last_transition = "none"
    last_bars_ago   = -1
    if len(regime_history) >= 2:
        t = detect_transition(regime_history, fp, df.iloc[-1])
        if t["transition"] != "NONE":
            last_transition = t["transition"]
            last_bars_ago   = 0
        else:
            # Scan back to find the most recent transition
            for offset in range(1, min(_HIST_BARS, len(regime_history))):
                sub = regime_history[:-offset]
                if len(sub) < 2:
                    break
                fp_past = get_volatility_fingerprint(df.iloc[max(0, n - _HIST_BARS + len(sub) - 100): n - _HIST_BARS + len(sub)])
                t2 = detect_transition(sub, fp_past, df.iloc[n - _HIST_BARS + len(sub) - 1])
                if t2["transition"] != "NONE":
                    last_transition = t2["transition"]
                    last_bars_ago   = offset
                    break

    # ── Spread + session ─────────────────────────────────────────────────────
    sym_info   = mt5.symbol_info(settings.SYMBOL)
    spread_pts = sym_info.spread if sym_info else 9999
    b1_spread  = "PASS" if spread_pts <= _BOT1_SPREAD else "FAIL"
    b2_spread  = "PASS" if spread_pts <= _BOT2_SPREAD else "FAIL"
    in_session = now.hour in _SESSION_HOURS
    session    = "ACTIVE" if in_session else "BLOCKED"

    # ── ATR proximity toward B threshold ─────────────────────────────────────
    atr_r  = fp["atr_ratio"]
    std_r  = fp["stddev_ratio"]
    hl_c   = fp["hl_compression"]
    vov_r  = fp["vov_ratio"]

    # ── Write compact 2-line snapshot ─────────────────────────────────────────
    ts = now.strftime("%Y-%m-%d %H:%M UTC")
    log = _get_logger()

    # Line 1 — Bot 1
    log.info(
        f"[{ts}] "
        f"BOT1: EMA={_v(ev)}({ema9:.1f}/{ema21:.1f}/{ema50:.1f}) "
        f"ATR={_v(av)}({atr_pct:.0f}%→thresh) "
        f"PDS={_v(pv)}(pdh_dist={pdh_dist:+.1f} pdl_dist={pdl_dist:+.1f}) "
        f"→ bias={bias}"
    )

    # Line 2 — Bot 2 + shared
    trans_str = (f"transition={last_transition}@{last_bars_ago}b"
                 if last_bars_ago >= 0 else "no-transition")
    log.info(
        f"          "
        f"BOT2: regime={regime} atr_r={atr_r:.3f} std_r={std_r:.3f} "
        f"hl={hl_c:.3f} vov_r={vov_r:.3f} → {trans_str} | "
        f"spread={spread_pts}pt B1={b1_spread} B2={b2_spread} | "
        f"session={session}"
    )


def _loop() -> None:
    # First snapshot after the initial delay (lets main loop run at least once)
    _stop.wait(_INITIAL_DELAY)
    while not _stop.is_set():
        try:
            _take_snapshot()
        except Exception as exc:
            # Never let the snapshot thread kill the main bot
            try:
                _get_logger().warning(f"Snapshot error (non-fatal): {exc}")
            except Exception:
                pass
        _stop.wait(_INTERVAL_SEC)


def start() -> threading.Thread:
    """Start the background snapshot thread. Call once from main_combined.py main()."""
    t = threading.Thread(target=_loop, name="snapshot-logger", daemon=True)
    t.start()
    return t


def stop() -> None:
    """Signal the snapshot thread to exit cleanly (optional — daemon thread auto-dies)."""
    _stop.set()
