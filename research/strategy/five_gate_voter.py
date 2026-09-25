"""
research/strategy/five_gate_voter.py - faithful reconstruction of the original
5-gate voting engine (Bot 1 lineage), re-tested 2026-09-16 under the current
validation standard.

SOURCE: commit a53504f (identical at 57cd845) - backtest/engine.py,
strategy/{ema_stack,rsi_extreme,atr_expansion,prev_day_structure,session_bias,
indicators}.py and tools/run_backtest.py CONFIG. The originals are extracted
verbatim to D:/MIDAS TRADING BOT/frozen_bars_2026-09-16/orig_5gate/ and this
port is proven trade-for-trade identical to them by
research/tools/five_gate_equivalence.py (original sizing mode, original data).

WHAT THE VALIDATED CONFIGURATION ACTUALLY WAS (tools/run_backtest.py, 57cd845):
  M5 bars, 100 days, $500, 1.5% risk, vote_threshold 4 ("4/5 when Session
  votes, 3/4 when it abstains"), cooldown 5 bars, session filter 00-14 &
  20-23 (bar hour), reward 2:1, structural "smart" SL (swing low/high in the
  prior 24 bars, 0.3 ATR buffer, accepted if 0.5-2.5 ATR from entry, else
  1.5 ATR), trailing stop at 0.5 ATR behind the extreme, soft exit after 8h
  if in profit, hard exit at 24h, performance monitor (risk to a flat 0.5%
  when trailing-20 win rate < 30%), max lot 0.5, NO spread cost, NO daily
  trade cap, NO margin cap.

FINDINGS THAT AFFECT THE RECONSTRUCTION (stated, not hidden):
  1. Session Bias reads best_hours.json, a file the backtest itself writes
     from its own trades (circular), and abstains unless an hour has >= 50
     trades. The committed file (102 trades in total) has NO such hour, so
     Session Bias abstained on every bar and the operative rule was 3-of-4
     among EMA Stack, RSI Extreme, ATR Expansion and Prev Day Structure.
     That is what is reproduced here; regenerating the file from this run's
     own trades would be in-sample leakage and is not done. Because RSI
     Extreme is counter-trend, 3-of-4 in practice means the three structural
     voters unanimous with RSI not opposing - which is why the later Bot 1
     "3/3 unanimous" simplification behaved the same way.
  2. "PF 1.75 / 81 trades" appears nowhere in the repository. The committed
     report (backtest_report.html at a53504f) shows 96 trades, PF 1.55,
     WR 53.1%, max DD 15.6%, on 100 days of M5 at $500. Earlier committed
     reports (PF 2.04 / 3.62 / 4.94 with 128,000% returns) belong to earlier,
     visibly broken engine versions.
  3. The original rounds SL and trailing stop to 2 decimals (gold-specific).
     For other instruments that rounding is applied at the symbol's own digit
     count - the only per-instrument adaptation, and necessary for the code
     to mean anything on 5-digit FX.
  4. Under the current standard (this file's "standard" sizing) the account is
     $50,000 at 1:10 with the 25% margin budget, the broker lot floor/step,
     spread cost, and floor/margin flags, i.e. research/tools/risk_calibrator's
     sizing. The performance monitor is a sizing overlay, not a signal, and a
     flat "0.5%" target makes no sense at the calibrated sizes, so it is OFF
     in standard mode and its trigger frequency is reported instead.
"""

import numpy as np
import pandas as pd

# ---- original constants -------------------------------------------------------
EMA_FAST, EMA_SLOW, EMA_TREND = 9, 21, 50
RSI_PERIOD, ATR_PERIOD        = 14, 14
RSI_BUY_THRESHOLD, RSI_SELL_THRESHOLD = 25.0, 75.0
ATR_EXPANSION_MULT, ATR_MA_PERIOD     = 1.2, 20
MIN_LOOKBACK      = 60
VOTE_THRESHOLD    = 4          # effective 3 when Session Bias abstains (always, see docstring)
COOLDOWN_BARS     = 5
REWARD_RATIO      = 2.0
TRAIL_ATR_MULT    = 0.5
SOFT_EXIT_HOURS   = 8
HARD_EXIT_HOURS   = 24
SESSION_HOURS     = set(range(0, 15)) | {20, 21, 22, 23}
PERF_LOOKBACK, PERF_MIN_TRADES, PERF_MIN_WR, PERF_REDUCED_RISK = 20, 10, 30.0, 0.5

# current-standard sizing framework (config/settings.py + risk_calibrator.py)
MARGIN_BUDGET_PCT = 0.25


# ---- indicators: verbatim from strategy/indicators.py -------------------------
def _ema(s, period):  return s.ewm(span=period, adjust=False).mean()

def _rsi(s, period=14):
    delta = s.diff(); gain = delta.clip(lower=0); loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=period - 1, adjust=False).mean()
    avg_loss = loss.ewm(com=period - 1, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))

def _atr(df, period=14):
    pc = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(com=period - 1, adjust=False).mean()

def add_indicators(df):
    df = df.copy()
    df["ema_fast"]  = _ema(df["close"], EMA_FAST)
    df["ema_slow"]  = _ema(df["close"], EMA_SLOW)
    df["ema_trend"] = _ema(df["close"], EMA_TREND)
    df["rsi"]       = _rsi(df["close"], RSI_PERIOD)
    df["atr"]       = _atr(df, ATR_PERIOD)
    return df


# ---- voters, vectorised per bar i (== each voter run on df.iloc[:i+1]) -------
def compute_votes(df):
    """Returns a DataFrame of per-bar votes and the summed score, exactly as
    backtest/engine.py's _run_voting would produce on the slice ending at i."""
    n = len(df)
    close = df["close"].to_numpy(float)
    e9, e21, e50 = (df[c].to_numpy(float) for c in ("ema_fast", "ema_slow", "ema_trend"))
    rsi, atr = df["rsi"].to_numpy(float), df["atr"].to_numpy(float)
    prev = lambda a: np.concatenate([[np.nan], a[:-1]])          # value at i-1

    # Voter 1 EMA Stack (row i-1)
    p9, p21, p50, pc = prev(e9), prev(e21), prev(e50), prev(close)
    with np.errstate(invalid="ignore"):
        v_ema = np.where((p9 > p21) & (p21 > p50) & (pc > p50), 1,
                 np.where((p9 < p21) & (p21 < p50) & (pc < p50), -1, 0))
        # Voter 2 RSI Extreme (row i-1)
        pr = prev(rsi)
        v_rsi = np.where(pr < RSI_BUY_THRESHOLD, 1, np.where(pr > RSI_SELL_THRESHOLD, -1, 0))
        # Voter 3 ATR Expansion: atr[i-1] vs mean(atr[i-20..i-1]); net = close[i-1]-close[i-3]
        atr_ma_prev = prev(pd.Series(atr).rolling(ATR_MA_PERIOD).mean().to_numpy())
        pa = prev(atr)
        net = pc - np.concatenate([[np.nan] * 3, close[:-3]])
        ok = (np.arange(n) + 1 >= ATR_MA_PERIOD + 5) & ~np.isnan(atr_ma_prev) & (atr_ma_prev > 0) & (pa >= atr_ma_prev * ATR_EXPANSION_MULT)
        v_atr = np.where(ok & (net > 0), 1, np.where(ok & (net < 0), -1, 0))
        # Voter 4 Prev Day Structure (row i close vs previous calendar day's H/L)
        day = df.index.normalize()
        agg = pd.DataFrame({"h": df["high"], "l": df["low"], "d": day}).groupby("d").agg(h=("h", "max"), l=("l", "min")).shift(1)
        ds = day.to_series(index=df.index)
        pdh = ds.map(agg["h"]).to_numpy(float); pdl = ds.map(agg["l"]).to_numpy(float)
        day_codes = pd.factorize(day)[0]
        first_pos_of_day = np.zeros(n, dtype=int)
        starts = np.r_[0, np.flatnonzero(np.diff(day_codes)) + 1]
        first_pos_of_day = np.repeat(starts, np.diff(np.r_[starts, n]))
        enough = first_pos_of_day >= 12
        v_pd = np.where(enough & (close > pdh), 1, np.where(enough & (close < pdl), -1, 0))
    v_sess = np.zeros(n, dtype=int)     # committed best_hours.json: no hour has >= 50 trades -> abstains
    score = v_ema + v_rsi + v_atr + v_pd + v_sess
    eff = np.where(v_sess != 0, VOTE_THRESHOLD, VOTE_THRESHOLD - 1)
    direction = np.where(score >= eff, 1, np.where(score <= -eff, -1, 0))
    return pd.DataFrame({"v_ema": v_ema, "v_rsi": v_rsi, "v_atr": v_atr, "v_pd": v_pd, "v_sess": v_sess,
                         "score": score, "direction": direction}, index=df.index)


# ---- smart SL: verbatim logic from backtest/engine.py -------------------------
def _swings(series, window, kind):
    vals = series.to_numpy(float); out = []
    for k in range(window, len(vals) - window):
        seg = vals[k - window:k + window + 1]
        if (kind == "low" and vals[k] == seg.min()) or (kind == "high" and vals[k] == seg.max()):
            out.append(k)
    return out

def smart_sl(df, i, direction, entry, atr, digits):
    atr_sl = entry - atr * 1.5 if direction == "BUY" else entry + atr * 1.5
    recent = df.iloc[max(0, i - 24):i]           # == df_slice.iloc[-25:-1]
    buf = atr * 0.3
    if len(recent) > 4:
        if direction == "BUY":
            lows = recent["low"]; valid = [lows.iloc[k] for k in _swings(lows, 2, "low") if lows.iloc[k] < entry]
            if valid:
                sl = max(valid) - buf
                if atr * 0.5 <= (entry - sl) <= atr * 2.5:
                    return round(sl, digits), "structural"
        else:
            highs = recent["high"]; valid = [highs.iloc[k] for k in _swings(highs, 2, "high") if highs.iloc[k] > entry]
            if valid:
                sl = min(valid) + buf
                if atr * 0.5 <= (sl - entry) <= atr * 2.5:
                    return round(sl, digits), "structural"
    return round(atr_sl, digits), "atr"


# ---- sizing -------------------------------------------------------------------
def _lot_original(balance, sl_dist, risk_pct, point, contract, max_lot):
    risk_amt = balance * (risk_pct / 100); sl_points = sl_dist / point
    if sl_points <= 0: return 0.01, False, False
    lot = risk_amt / (sl_points * contract * point)
    return max(0.01, min(round(round(lot / 0.01) * 0.01, 2), max_lot)), False, False

def _lot_standard(balance, sl_dist, risk_pct, point, contract, price, leverage, vmin, vmax, vstep):
    risk_amt = balance * (risk_pct / 100); sl_points = sl_dist / point
    raw = risk_amt / (sl_points * contract * point)
    cap = (balance * MARGIN_BUDGET_PCT * leverage) / (contract * price)
    lot = min(raw, cap, vmax); lot = max(lot, vmin)
    lot = round(round(lot / vstep) * vstep, 2)
    return lot, raw < vmin, raw > cap


# ---- trade simulation: exits verbatim; sizing per mode ------------------------
def simulate_trade(df, i, direction, atr, balance, risk_pct, mode, specs, digits, bars_per_hour,
                   spread_points=0.0, leverage=10, max_lot=0.5, conservative_trailing=False):
    """conservative_trailing (diagnostic, NOT the original): test the stop against
    the bar BEFORE raising it with that bar's high/low. The original raises first,
    which assumes the favourable extreme always printed before the adverse one
    inside every bar - an optimistic intrabar assumption that matters when
    nearly all exits are trailing exits."""
    n = len(df)
    if i + 1 >= n: return None
    point, contract, vmin, vmax, vstep = specs
    entry = float(df["open"].iloc[i + 1])
    sl, sl_method = smart_sl(df, i, direction, entry, atr, digits)
    sl_dist = abs(entry - sl)
    if sl_dist <= 0: return None
    if mode == "original":
        lot, floor_c, margin_c = _lot_original(balance, sl_dist, risk_pct, point, contract, max_lot)
    else:
        lot, floor_c, margin_c = _lot_standard(balance, sl_dist, risk_pct, point, contract, entry, leverage, vmin, vmax, vstep)
    mult = lot * contract
    tp_dist = sl_dist * REWARD_RATIO
    tp = entry + tp_dist if direction == "BUY" else entry - tp_dist
    trail = atr * TRAIL_ATR_MULT
    max_bars, soft_bars = HARD_EXIT_HOURS * bars_per_hour, SOFT_EXIT_HOURS * bars_per_hour
    highs, lows, closes = df["high"].to_numpy(float), df["low"].to_numpy(float), df["close"].to_numpy(float)
    cur_sl, exit_price, reason = sl, None, None
    for j in range(i + 2, min(i + max_bars + 2, n)):
        h, l, c = highs[j], lows[j], closes[j]
        if direction == "BUY":
            cand = round(h - trail, digits)
            if not conservative_trailing and cand > cur_sl: cur_sl = cand
            if l <= cur_sl: exit_price, reason = cur_sl, "TRAIL" if cur_sl > sl else "SL"; break
            if h >= tp: exit_price, reason = tp, "TP"; break
            if conservative_trailing and cand > cur_sl: cur_sl = cand
            in_profit = c > entry
        else:
            cand = round(l + trail, digits)
            if not conservative_trailing and cand < cur_sl: cur_sl = cand
            if h >= cur_sl: exit_price, reason = cur_sl, "TRAIL" if cur_sl < sl else "SL"; break
            if l <= tp: exit_price, reason = tp, "TP"; break
            if conservative_trailing and cand < cur_sl: cur_sl = cand
            in_profit = c < entry
        if (j - i - 1) >= soft_bars and in_profit:
            exit_price, reason = c, "SOFT"; break
    if exit_price is None:
        exit_price, reason = closes[min(i + max_bars + 1, n - 1)], "TIME"
    raw_move = (exit_price - entry) if direction == "BUY" else (entry - exit_price)
    spread_cost = spread_points * point * mult if mode == "standard" else 0.0
    pnl = raw_move * mult - spread_cost
    t = df.index[i + 1]
    return {"date": t.strftime("%Y-%m-%d"), "time": t.strftime("%H:%M"), "direction": direction,
            "entry": round(entry, digits), "exit": round(float(exit_price), digits), "sl": sl, "sl_final": round(cur_sl, digits),
            "tp": round(tp, digits), "lots": lot, "pnl": round(pnl, 2), "result": "WIN" if pnl > 0 else "LOSS",
            "atr": round(atr, digits), "sl_method": sl_method, "exit_reason": reason, "spread_cost": round(spread_cost, 2),
            "floor_clamped": floor_c, "margin_capped": margin_c,
            "actual_risk_pct": round(lot * contract * sl_dist / balance * 100, 4) if balance > 0 else 0.0}


# ---- main loop: verbatim control flow from run_simulation ----------------------
def run(df, specs, digits, mode="standard", risk_pct=0.1, initial_balance=50000.0, spread_points=0.0,
        leverage=10, bars_per_hour=12, max_lot=0.5, perf_monitor=None, cooldown_bars=COOLDOWN_BARS,
        conservative_trailing=False):
    """df: raw OHLC frame (indicators added here). mode 'original' reproduces
    the a53504f engine's sizing ($500 / 1.5% / max_lot / no spread / perf
    monitor ON); 'standard' applies the current framework. Returns trades and
    a dict of loop diagnostics (perf-monitor triggers, signal counts)."""
    if perf_monitor is None: perf_monitor = (mode == "original")
    df = add_indicators(df)
    votes = compute_votes(df)
    direction = votes["direction"].to_numpy()
    hours = df.index.hour
    atr = df["atr"].to_numpy(float)
    trades, balance, last_trade_bar = [], initial_balance, -cooldown_bars
    n_signals = perf_triggers = 0
    for i in range(MIN_LOOKBACK, len(df) - 1):
        if i - last_trade_bar < cooldown_bars: continue
        if hours[i] not in SESSION_HOURS: continue
        if direction[i] == 0: continue
        n_signals += 1
        eff_risk = risk_pct
        if perf_monitor and len(trades) >= PERF_MIN_TRADES:
            recent = trades[-PERF_LOOKBACK:]
            if sum(t["result"] == "WIN" for t in recent) / len(recent) * 100 < PERF_MIN_WR:
                eff_risk = PERF_REDUCED_RISK; perf_triggers += 1
        tr = simulate_trade(df, i, "BUY" if direction[i] > 0 else "SELL", float(atr[i]), balance, eff_risk, mode,
                            specs, digits, bars_per_hour, spread_points, leverage, max_lot, conservative_trailing)
        if tr is None: continue
        tr["vote_score"] = int(votes["score"].iloc[i])
        tr["votes"] = {k: int(votes[k].iloc[i]) for k in ("v_ema", "v_rsi", "v_atr", "v_pd", "v_sess")}
        balance += tr["pnl"]; tr["balance_after"] = round(balance, 2)
        trades.append(tr); last_trade_bar = i
    return trades, {"n_signals": n_signals, "perf_monitor_triggers": perf_triggers, "bars": len(df)}
