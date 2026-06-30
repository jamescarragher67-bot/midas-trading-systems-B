"""
spread_analysis.py — XAUUSD spread distribution analysis

Pulls 30 days of M5 bars from MT5, analyses the spread field by UTC hour,
and reports how many bars would be blocked at 15 / 20 / 25 point thresholds.

Usage:
    python spread_analysis.py

MT5 must be running and logged in before running this script.
"""

import sys
import os

# Allow imports from project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import MetaTrader5 as mt5
import pandas as pd
import numpy as np
from datetime import datetime, timezone

from config.settings import MT5_LOGIN, MT5_PASSWORD, MT5_SERVER, SYMBOL


THRESHOLDS = [15, 20, 25]
DAYS = 30
M5_BARS = DAYS * 24 * 12  # 8,640 bars for 30 days


def connect():
    kwargs = {}
    if MT5_LOGIN:    kwargs["login"]    = MT5_LOGIN
    if MT5_PASSWORD: kwargs["password"] = MT5_PASSWORD
    if MT5_SERVER:   kwargs["server"]   = MT5_SERVER
    if not mt5.initialize(**kwargs):
        print(f"MT5 initialize() failed: {mt5.last_error()}")
        return False

    mt5.symbol_select(SYMBOL, True)
    sym = mt5.symbol_info(SYMBOL)
    if sym is None:
        print(f"symbol_info({SYMBOL}) returned None — MT5 error: {mt5.last_error()}")
        mt5.shutdown()
        return False

    acct = mt5.account_info()
    print(f"Connected | Account: {acct.login} | Server: {acct.server} | Balance: ${acct.balance:.2f}")
    print(f"Symbol: {SYMBOL} | Point: {sym.point} | Digits: {sym.digits}")
    print()
    return True


def fetch_bars() -> pd.DataFrame:
    rates = mt5.copy_rates_from_pos(SYMBOL, mt5.TIMEFRAME_M5, 0, M5_BARS)
    if rates is None or len(rates) == 0:
        print(f"No M5 data returned — MT5 error: {mt5.last_error()}")
        return pd.DataFrame()

    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
    df["hour"] = df["time"].dt.hour
    df["spread_pts"] = df["spread"].astype(float)  # MT5 spread column is already in points

    actual_days = (df["time"].iloc[-1] - df["time"].iloc[0]).days
    print(f"Fetched {len(df):,} M5 bars | {df['time'].iloc[0]} to {df['time'].iloc[-1]} ({actual_days} days)")
    print()
    return df


def hourly_stats(df: pd.DataFrame) -> pd.DataFrame:
    stats = df.groupby("hour")["spread_pts"].agg(
        count="count",
        mean="mean",
        p50=lambda x: np.percentile(x, 50),
        p95=lambda x: np.percentile(x, 95),
        p99=lambda x: np.percentile(x, 99),
        max="max",
    ).round(1)
    return stats


def threshold_blocking(df: pd.DataFrame, stats: pd.DataFrame) -> dict:
    total = len(df)
    results = {}
    for t in THRESHOLDS:
        blocked_total = (df["spread_pts"] > t).sum()
        pct_total = blocked_total / total * 100
        blocked_by_hour = df.groupby("hour").apply(
            lambda g: (g["spread_pts"] > t).sum()
        )
        results[t] = {
            "blocked_total": blocked_total,
            "pct_total": pct_total,
            "blocked_by_hour": blocked_by_hour,
        }
    return results


def print_report(df: pd.DataFrame, stats: pd.DataFrame, blocking: dict):
    total_bars = len(df)

    print("=" * 72)
    print(f"  XAUUSD SPREAD ANALYSIS — last {DAYS} days of M5 bars ({total_bars:,} total)")
    print("=" * 72)
    print()

    # Overall summary
    overall_mean = df["spread_pts"].mean()
    overall_p95  = np.percentile(df["spread_pts"], 95)
    overall_p99  = np.percentile(df["spread_pts"], 99)
    overall_max  = df["spread_pts"].max()
    print(f"Overall spread (all hours):  mean={overall_mean:.1f}pts  p95={overall_p95:.1f}pts  p99={overall_p99:.1f}pts  max={overall_max:.0f}pts")
    print()

    # Threshold blocking summary
    print("Threshold blocking (% of M5 bars blocked):")
    print(f"  {'Threshold':<12} {'Bars blocked':<15} {'% blocked'}")
    print(f"  {'-'*42}")
    for t in THRESHOLDS:
        b = blocking[t]
        marker = " <-- current Bot 1" if t == 15 else (" <-- current Bot 2" if t == 20 else "")
        print(f"  {t:>3}pts        {b['blocked_total']:>8,}         {b['pct_total']:>5.1f}%{marker}")
    print()

    # Hourly breakdown
    print("Hourly breakdown (UTC):")
    header = f"  {'Hour':<6} {'Bars':<7} {'Mean':>6} {'p50':>6} {'p95':>6} {'p99':>6} {'Max':>6}"
    for t in THRESHOLDS:
        header += f"  {f'>{t}pt':>6}"
    header += "  Session"
    print(header)
    print("  " + "-" * (len(header) - 2))

    session_label = {
        **{h: "ACTIVE" for h in list(range(0, 15)) + [20, 21, 22, 23]},
        **{h: "BLOCKED" for h in range(15, 20)},
    }

    for hour in range(24):
        if hour not in stats.index:
            continue
        row = stats.loc[hour]
        line = (f"  {hour:02d}:00   {int(row['count']):<7} "
                f"{row['mean']:>6.1f} {row['p50']:>6.1f} {row['p95']:>6.1f} "
                f"{row['p99']:>6.1f} {int(row['max']):>6}")
        for t in THRESHOLDS:
            bh = blocking[t]["blocked_by_hour"]
            n = bh.get(hour, 0)
            pct = n / row["count"] * 100 if row["count"] > 0 else 0.0
            line += f"  {pct:>5.1f}%"
        line += f"  {session_label.get(hour, '')}"
        print(line)

    print()

    # Recommendation
    print("=" * 72)
    print("  RECOMMENDATION")
    print("=" * 72)
    b15 = blocking[15]["pct_total"]
    b20 = blocking[20]["pct_total"]
    b25 = blocking[25]["pct_total"]
    print(f"  At 15pts: {b15:.1f}% of M5 bars blocked (current Bot 1 limit)")
    print(f"  At 20pts: {b20:.1f}% of M5 bars blocked (current Bot 2 limit)")
    print(f"  At 25pts: {b25:.1f}% of M5 bars blocked")
    print()

    # Find the worst hours (high spread AND in active session)
    active_hours = list(range(0, 15)) + [20, 21, 22, 23]
    active_stats = stats[stats.index.isin(active_hours)]
    if not active_stats.empty:
        worst_hour = int(active_stats["p95"].idxmax())
        best_hour  = int(active_stats["p95"].idxmin())
        print(f"  Worst active-session hour (p95): {worst_hour:02d}:00 UTC "
              f"— p95 spread = {active_stats.loc[worst_hour, 'p95']:.1f}pts")
        print(f"  Best  active-session hour (p95): {best_hour:02d}:00 UTC "
              f"— p95 spread = {active_stats.loc[best_hour, 'p95']:.1f}pts")
        print()

    if b15 > 30:
        print("  WARNING: >30% of bars blocked at 15pts — consider raising Bot 1 limit to 20pts for live.")
    elif b15 > 15:
        print("  CAUTION: 15-25% of bars blocked at 15pts — monitor whether this coincides with signal windows.")
    else:
        print("  15pt limit looks appropriate — blocking rate is low.")
    print()


def main():
    print(f"MIDAS Spread Analysis — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print()

    if not connect():
        sys.exit(1)

    df = fetch_bars()
    if df.empty:
        mt5.shutdown()
        sys.exit(1)

    stats   = hourly_stats(df)
    blocking = threshold_blocking(df, stats)
    print_report(df, stats, blocking)

    mt5.shutdown()
    print("MT5 disconnected.")


if __name__ == "__main__":
    main()
