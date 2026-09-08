"""
backtest/metrics.py

Calculates comprehensive performance metrics from backtest trades.
"""

import numpy as np
from collections import defaultdict


def calculate_metrics(trades: list, initial_balance: float = 500.0) -> dict:
    """Calculate full performance metrics from trade list."""
    if not trades:
        return {"error": "No trades to analyse"}

    wins   = [t for t in trades if t["result"] == "WIN"]
    losses = [t for t in trades if t["result"] == "LOSS"]
    total  = len(trades)

    win_pnls  = [t["pnl"] for t in wins]
    loss_pnls = [abs(t["pnl"]) for t in losses]

    gross_win  = sum(win_pnls)
    gross_loss = sum(loss_pnls)
    net_pnl    = gross_win - gross_loss

    win_rate      = len(wins) / total * 100
    profit_factor = gross_win / gross_loss if gross_loss > 0 else 999.99
    avg_win       = gross_win / len(wins) if wins else 0
    avg_loss      = gross_loss / len(losses) if losses else 0
    expectancy    = net_pnl / total
    avg_rr        = avg_win / avg_loss if avg_loss > 0 else 0

    # Equity curve
    balance    = initial_balance
    equity     = [balance]
    peak       = balance
    max_dd     = 0
    max_dd_pct = 0

    for t in trades:
        balance += t["pnl"]
        equity.append(round(balance, 2))
        if balance > peak:
            peak = balance
        dd     = peak - balance
        dd_pct = dd / peak * 100 if peak > 0 else 0
        if dd_pct > max_dd_pct:
            max_dd_pct = dd_pct
            max_dd     = dd

    final_balance = balance
    total_return  = (final_balance - initial_balance) / initial_balance * 100

    # Sharpe ratio (trade-level, annualized proxy)
    pnls = [t["pnl"] for t in trades]
    if len(pnls) > 1 and np.std(pnls) > 0:
        sharpe = (np.mean(pnls) / np.std(pnls)) * np.sqrt(260)
    else:
        sharpe = 0

    # Consecutive losses
    max_consec_loss = 0
    curr_consec     = 0
    for t in trades:
        if t["result"] == "LOSS":
            curr_consec += 1
            max_consec_loss = max(max_consec_loss, curr_consec)
        else:
            curr_consec = 0

    # Max consecutive wins
    max_consec_win = 0
    curr_consec    = 0
    for t in trades:
        if t["result"] == "WIN":
            curr_consec += 1
            max_consec_win = max(max_consec_win, curr_consec)
        else:
            curr_consec = 0

    # Best / worst trade
    best_trade  = max(t["pnl"] for t in trades)
    worst_trade = min(t["pnl"] for t in trades)

    return {
        "total_trades":      total,
        "wins":              len(wins),
        "losses":            len(losses),
        "win_rate":          round(win_rate, 1),
        "profit_factor":     round(profit_factor, 2),
        "net_pnl":           round(net_pnl, 2),
        "gross_win":         round(gross_win, 2),
        "gross_loss":        round(gross_loss, 2),
        "avg_win":           round(avg_win, 2),
        "avg_loss":          round(avg_loss, 2),
        "avg_rr":            round(avg_rr, 2),
        "expectancy":        round(expectancy, 2),
        "initial_balance":   round(initial_balance, 2),
        "final_balance":     round(final_balance, 2),
        "total_return":      round(total_return, 1),
        "max_drawdown":      round(max_dd, 2),
        "max_drawdown_pct":  round(max_dd_pct, 1),
        "sharpe_ratio":      round(sharpe, 2),
        "best_trade":        round(best_trade, 2),
        "worst_trade":       round(worst_trade, 2),
        "max_consec_losses": max_consec_loss,
        "max_consec_wins":   max_consec_win,
        "equity_curve":      equity,
    }


def monthly_breakdown(trades: list) -> list:
    """Group trades by month and calculate monthly stats."""
    monthly = defaultdict(lambda: {"trades": [], "pnl": 0, "wins": 0})

    for t in trades:
        key = t["date"][:7]   # "YYYY-MM"
        monthly[key]["trades"].append(t)
        monthly[key]["pnl"]  += t["pnl"]
        if t["result"] == "WIN":
            monthly[key]["wins"] += 1

    result = []
    for month in sorted(monthly.keys()):
        data   = monthly[month]
        total  = len(data["trades"])
        result.append({
            "month":    month,
            "trades":   total,
            "wins":     data["wins"],
            "losses":   total - data["wins"],
            "win_rate": round(data["wins"] / total * 100, 1) if total > 0 else 0,
            "pnl":      round(data["pnl"], 2),
        })
    return result


def strategy_contribution(trades: list) -> list:
    """Breakdown of reversal patterns that triggered trades."""
    from collections import defaultdict
    patterns = defaultdict(lambda: {"trades": 0, "wins": 0, "pnl": 0.0})

    for t in trades:
        p = t.get("pattern", "Unknown")
        patterns[p]["trades"] += 1
        patterns[p]["pnl"]    += t["pnl"]
        if t["result"] == "WIN":
            patterns[p]["wins"] += 1

    result = []
    for name, d in patterns.items():
        total = d["trades"]
        result.append({
            "strategy": name,
            "votes":    total,
            "win_rate": round(d["wins"] / total * 100, 1) if total > 0 else 0,
            "pnl":      round(d["pnl"], 2),
        })

    return sorted(result, key=lambda x: x["win_rate"], reverse=True)


def hourly_breakdown(trades: list) -> list:
    """Win rate and count by UTC hour."""
    hourly = defaultdict(lambda: {"wins": 0, "total": 0, "pnl": 0})
    for t in trades:
        h = int(t["time"][:2])
        hourly[h]["total"] += 1
        hourly[h]["pnl"]   += t["pnl"]
        if t["result"] == "WIN":
            hourly[h]["wins"] += 1

    result = []
    for h in range(24):
        d = hourly[h]
        result.append({
            "hour":     h,
            "trades":   d["total"],
            "wins":     d["wins"],
            "win_rate": round(d["wins"] / d["total"] * 100, 1) if d["total"] > 0 else 0,
            "pnl":      round(d["pnl"], 2),
        })
    return result