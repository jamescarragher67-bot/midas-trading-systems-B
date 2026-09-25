"""
research/tools/replay_live_vs_backtest.py - prove main.py trades what the backtest validated.

Drives main.py's REAL _get_signal() over frozen bars exactly as MT5 would: at
each step k the patched copy_rates_from_pos returns the last BARS_TO_FETCH
bars ending at bar k (the forming bar), so df.index[-2] is the latest closed
bar. Spread and news filters are forced open (they have no backtest
counterpart). When the live code emits a signal, the state update run()
performs on a filled order is applied (trades_today += 1,
last_trade_bar = bar_index). The resulting entry list - next bar's open,
direction, SL, TP - is compared one-for-one with the calibrator/backtest
harness (research/tools/risk_calibrator.run_backtest) on the same bars.

What this caught on 2026-09-15, before the fixes it now guards:
  * the session filter was applied to wall-clock UTC while the backtest
    applies it to the bar's own server-time hour (Pepperstone = UTC+3);
  * the cooldown compared a within-frame index against a global bar
    counter: no cooldown at all for the first ~frame-length bars of uptime,
    then a PERMANENT cooldown (the bot silently stopped trading after
    ~3 days up);
  * 150 fetched bars left the prior server-time day incomplete after ~13:30.

Run after ANY change to main.py's signal path or to strategy/lsc_m15.py:
    set MT5_LOGIN=<any>
    python research/tools/replay_live_vs_backtest.py --bars xauusd_m15_frozen.pkl [--bars-back 20000]
Expected: "sequence identical: True" with equal entry counts.
"""
import argparse
import contextlib
import io
import os
import pickle
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("MT5_LOGIN", "1")   # settings.py insists; no terminal call is made here

with contextlib.redirect_stdout(io.StringIO()):     # silence the config banner
    import MetaTrader5 as mt5
    import main
    import research.tools.risk_calibrator as rc
    from research.tools.rolling_month_backtest import load_bars

DT = np.dtype([("time", "<i8"), ("open", "<f8"), ("high", "<f8"), ("low", "<f8"), ("close", "<f8"),
               ("tick_volume", "<i8"), ("spread", "<i4"), ("real_volume", "<i8")])


def run(bars_path: str, bars_back: int) -> bool:
    raw = pickle.load(open(bars_path, "rb"))["df"]
    times = np.array([int(t.timestamp()) for t in raw.index], dtype="int64")
    assert len(set(times)) == len(times) and times[1] - times[0] == 900, "bars are not 15-minute epoch seconds"
    O, H, L, C = (raw[c].to_numpy(float) for c in ("open", "high", "low", "close"))
    state = {"k": 0}

    def fake_rates(symbol, tf, start_pos, count):
        k = state["k"]; lo = max(0, k - count + 1)
        arr = np.zeros(k + 1 - lo, dtype=DT)
        arr["time"] = times[lo:k + 1]; arr["open"] = O[lo:k + 1]; arr["high"] = H[lo:k + 1]
        arr["low"] = L[lo:k + 1]; arr["close"] = C[lo:k + 1]
        return arr

    mt5.copy_rates_from_pos = fake_rates
    mt5.symbol_select = lambda *a, **k: True
    main.spread_ok = lambda: True
    main.news_ok = lambda: True
    main.log.info = lambda *a, **k: None
    main.log.warning = lambda *a, **k: None

    # fresh live state, exactly as a freshly started main.py
    main._bias_date = None; main._trades_today = 0
    main._last_trade_bar = -main.settings.COOLDOWN_BARS; main._last_bar_time = None; main._bar_index = 0

    start = max(main.BARS_TO_FETCH, len(raw) - bars_back)
    t0 = time.time()
    live = []
    for k in range(start, len(raw)):
        state["k"] = k
        sig = main._get_signal()
        if sig:
            live.append((raw.index[k].strftime("%Y-%m-%d %H:%M"), sig["direction"],
                         round(sig["sl"], 2), round(sig["tp"], 2), raw.index[k - 1].hour))
            main._trades_today += 1
            main._last_trade_bar = main._bar_index
    assert main._bar_index == len(raw) - start, "live code did not see every closed bar - harness fault"
    print(f"live replay: {len(raw) - start} closed bars, {len(live)} signals, {time.time() - t0:.0f}s")

    df, specs = load_bars(bars_path)
    rc.INITIAL_BALANCE, rc.LEVERAGE_ASSUMED = 50000.0, 10
    bt = rc.run_backtest(df, main.settings.RISK_PERCENT, *specs)
    bt_all = [(f"{t['date']} {t['time']}", t["direction"], t["sl"], t["tp"]) for t in bt]

    # compare from the first server-time day boundary after the replay start,
    # so the cooldown / daily-cap state of the two runs has converged
    cmp_from = (raw.index[start].normalize() + pd.Timedelta(days=1)).strftime("%Y-%m-%d %H:%M")
    live_c = [x[:4] for x in live if x[0] >= cmp_from]
    bt_c = [x for x in bt_all if x[0] >= cmp_from]
    identical = live_c == bt_c
    print(f"comparison window (server time): {cmp_from} -> {raw.index[-1].strftime('%Y-%m-%d %H:%M')}")
    print(f"  backtest entries: {len(bt_c)} | live entries: {len(live_c)} | sequence identical: {identical}")
    for x in sorted(set(bt_c) - set(live_c))[:10]: print("    backtest-only:", x)
    for x in sorted(set(live_c) - set(bt_c))[:10]: print("    live-only:    ", x)

    srv_hours = sorted({x[4] for x in live if x[0] >= cmp_from})
    print(f"  server-time hours of live signal bars: {srv_hours}")
    print(f"  all inside SESSION_HOURS {sorted(main.settings.SESSION_HOURS)}: "
          f"{set(srv_hours) <= set(main.settings.SESSION_HOURS)}")
    print("  (MT5 bar timestamps are broker server time - the same clock the backtest's session "
          "filter runs on; wall-clock UTC is never consulted on this path)")
    return identical


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--bars", required=True, help="frozen bars pickle ({'df','specs'})")
    ap.add_argument("--bars-back", type=int, default=20000, help="how many closed bars to replay (full history ~5 min)")
    a = ap.parse_args()
    sys.exit(0 if run(a.bars, a.bars_back) else 1)
