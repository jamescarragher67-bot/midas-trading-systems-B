"""
check_current_signal.py — MIDAS live diagnostic snapshot

Connects to MT5 and prints exactly what both bots see right now, live,
without waiting for the 30-second loop or an actual trade.

Run from the MIDAS TRADING BOT directory:
    python check_current_signal.py
"""

import sys
import os
import io
from pathlib import Path
from datetime import datetime, timezone

# Force UTF-8 output on Windows so box/arrow chars render correctly
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ── Root setup ─────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

# Load .env before settings.py tries to (it only calls load_dotenv() with no path)
from dotenv import load_dotenv
load_dotenv(ROOT / "config" / ".env")

# Suppress the settings.py startup banner
_stdout = sys.stdout
sys.stdout = io.StringIO()
from config.settings import MT5_LOGIN, MT5_PASSWORD, MT5_SERVER, SYMBOL
sys.stdout = _stdout

import MetaTrader5 as mt5
import pandas as pd
import numpy as np

from strategy.indicators          import add_indicators
from strategy.ema_stack           import get_signal as ema_signal
from strategy.atr_expansion       import get_signal as atr_signal
from strategy.prev_day_structure  import get_signal as pds_signal
from strategy.m5_execution_engine import get_daily_bias
from strategy.volatility_metrics  import get_volatility_fingerprint
from strategy.regime_classifier   import classify_regime
from strategy.transition_detector import detect_transition

# ── Constants — must match main_combined.py exactly ───────────────────────────
INDICATOR_CONFIG = {"EMA_FAST": 9, "EMA_SLOW": 21, "EMA_TREND": 50,
                    "RSI_PERIOD": 14, "ATR_PERIOD": 14}

SESSION_HOURS    = set(range(0, 15)) | {20, 21, 22, 23}   # hours 00-14 + 20-23 UTC
BOT1_SPREAD_MAX  = 15
BOT2_SPREAD_MAX  = 20
REGIME_HISTORY_BARS = 25   # how many bars back to build regime history

# ── Formatting helpers ─────────────────────────────────────────────────────────

W = 32   # label column width

def banner(title):
    print(f"\n{'═' * 64}")
    print(f"  {title}")
    print(f"{'═' * 64}")

def row(label, value):
    print(f"  {label:<{W}} {value}")

def spacer():
    print()

def vote_label(v):
    if v == 1:  return "BUY  [+1]"
    if v == -1: return "SELL [-1]"
    return               "NONE [ 0]"

def yn(condition):
    return "YES ✓" if condition else "NO  ✗"

def pf(condition):
    return "PASS ✓" if condition else "FAIL ✗"


# ══════════════════════════════════════════════════════════════════════════════
def main():
    now_utc = datetime.now(timezone.utc)

    print()
    print("█" * 64)
    print(f"  MIDAS LIVE DIAGNOSTIC  —  {now_utc.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print("█" * 64)

    # ══════════════════════════════════════════════════════════════════════════
    # 1. MT5 CONNECTION
    # ══════════════════════════════════════════════════════════════════════════
    banner("1 ▸ MT5 CONNECTION")

    kwargs = {}
    if MT5_LOGIN:    kwargs["login"]    = MT5_LOGIN
    if MT5_PASSWORD: kwargs["password"] = MT5_PASSWORD
    if MT5_SERVER:   kwargs["server"]   = MT5_SERVER

    if not mt5.initialize(**kwargs):
        row("Status:", f"FAILED — {mt5.last_error()}")
        print("\n  Cannot proceed without MT5 connection. Is MT5 running?\n")
        return

    mt5.symbol_select(SYMBOL, True)
    account  = mt5.account_info()
    sym_info = mt5.symbol_info(SYMBOL)
    tick     = mt5.symbol_info_tick(SYMBOL)

    row("Status:",         "CONNECTED ✓")
    row("Account:",        f"{account.login}  ({account.server})")
    row("Balance:",        f"${account.balance:>10,.2f}  {account.currency}")
    row("Equity:",         f"${account.equity:>10,.2f}  {account.currency}")
    row("Open positions:", str(len(mt5.positions_get() or [])))
    spacer()

    if tick and sym_info:
        spread_pts = sym_info.spread
        row(f"{SYMBOL} Bid:",    f"{tick.bid:.2f}")
        row(f"{SYMBOL} Ask:",    f"{tick.ask:.2f}")
        row(f"{SYMBOL} Spread:", f"{spread_pts} points")
    else:
        spread_pts = 9999
        row(f"{SYMBOL}:", f"Could not get tick — {mt5.last_error()}")

    # ══════════════════════════════════════════════════════════════════════════
    # 2. FETCH CANDLES
    # ══════════════════════════════════════════════════════════════════════════
    banner("2 ▸ CANDLE DATA")

    rates_m5 = mt5.copy_rates_from_pos(SYMBOL, mt5.TIMEFRAME_M5, 0, 200)
    rates_h1 = mt5.copy_rates_from_pos(SYMBOL, mt5.TIMEFRAME_H1, 0, 50)

    if rates_m5 is None or len(rates_m5) < 60:
        row("M5 bars:", f"FAILED — {mt5.last_error()}")
        mt5.shutdown()
        return

    def to_df(rates):
        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s")
        df.set_index("time", inplace=True)
        return df

    df_m5 = to_df(rates_m5)
    df_h1 = to_df(rates_h1) if rates_h1 is not None else pd.DataFrame()

    row("M5 bars fetched:", f"{len(df_m5)}")
    row("M5 oldest bar:",   f"{df_m5.index[0].strftime('%Y-%m-%d %H:%M')} UTC")
    row("M5 newest bar:",   f"{df_m5.index[-1].strftime('%Y-%m-%d %H:%M')} UTC  ← current (forming)")
    row("M5 last closed:",  f"{df_m5.index[-2].strftime('%Y-%m-%d %H:%M')} UTC")
    spacer()
    row("H1 bars fetched:", f"{len(df_h1)}" if not df_h1.empty else "0 (fetch failed)")
    if not df_h1.empty:
        row("H1 newest bar:",   f"{df_h1.index[-1].strftime('%Y-%m-%d %H:%M')} UTC")

    # Add indicators (EMA9/21/50, ATR14, RSI14)
    df_ind = add_indicators(df_m5.copy(), INDICATOR_CONFIG)

    # Last closed bar (voters use iloc[-2], not the forming bar)
    closed = df_ind.iloc[-2]
    ema9   = float(closed["ema_fast"])
    ema21  = float(closed["ema_slow"])
    ema50  = float(closed["ema_trend"])
    close_c = float(closed["close"])
    atr_c   = float(closed["atr"])

    # ══════════════════════════════════════════════════════════════════════════
    # 3. BOT 1 — VOTER 1: EMA STACK
    # ══════════════════════════════════════════════════════════════════════════
    banner("3 ▸ BOT 1 — VOTER 1: EMA STACK")

    ema_vote, ema_reason = ema_signal(df_ind)

    if ema9 > ema21 > ema50:
        alignment = f"9 > 21 > 50  ←  BULLISH stack"
    elif ema9 < ema21 < ema50:
        alignment = f"9 < 21 < 50  ←  BEARISH stack"
    else:
        alignment = "MIXED  (no full alignment)"

    row("Vote:",      vote_label(ema_vote))
    row("Reason:",    ema_reason)
    spacer()
    row("EMA9  (ema_fast):",   f"{ema9:.2f}")
    row("EMA21 (ema_slow):",   f"{ema21:.2f}")
    row("EMA50 (ema_trend):",  f"{ema50:.2f}")
    row("Close (last closed):", f"{close_c:.2f}")
    row("Alignment:",           alignment)
    row("Close > EMA50:",       yn(close_c > ema50))
    spacer()

    # Proximity: how far each gap is from alignment
    gap9_21  = ema9  - ema21          # positive → already above for BUY
    gap21_50 = ema21 - ema50          # positive → already above for BUY

    def _gap_label(gap: float, label_above: str) -> str:
        if gap > 0:
            return f"+{gap:.2f} pts  (✓ {label_above})"
        return f"{gap:.2f} pts  (✗ need +{abs(gap):.2f} to reach {label_above})"

    print(f"  {'─' * 58}")
    print(f"  PROXIMITY TO FIRE")
    row("EMA9 vs EMA21:",    _gap_label(gap9_21,  "BUY-aligned"))
    row("EMA21 vs EMA50:",   _gap_label(gap21_50, "BUY-aligned"))
    row("Close vs EMA50:",   f"{close_c - ema50:+.2f} pts  ({'✓ above' if close_c > ema50 else '✗ below'})")
    if ema9 > ema21 > ema50:
        row("→ BUY stack:",  "FULLY ALIGNED — waiting for M5 entry trigger")
    elif ema9 < ema21 < ema50:
        row("→ SELL stack:", "FULLY ALIGNED — waiting for M5 entry trigger")
    else:
        blockers = []
        if ema9 <= ema21:
            blockers.append(f"EMA9 needs +{ema21 - ema9 + 0.01:.2f} to clear EMA21 (BUY)")
        if ema21 <= ema50:
            blockers.append(f"EMA21 needs +{ema50 - ema21 + 0.01:.2f} to clear EMA50 (BUY)")
        row("→ BUY blockers:", "  |  ".join(blockers) if blockers else "none")

    # ══════════════════════════════════════════════════════════════════════════
    # 4. BOT 1 — VOTER 2: ATR EXPANSION
    # ══════════════════════════════════════════════════════════════════════════
    banner("4 ▸ BOT 1 — VOTER 2: ATR EXPANSION")

    atr_vote, atr_reason = atr_signal(df_ind)

    # Mirror the voter's internal calculation
    atr_series = df_ind["atr"].iloc[:-1]   # exclude forming bar
    atr_val    = float(atr_series.iloc[-1])
    atr_ma20   = float(atr_series.rolling(20).mean().iloc[-1])
    atr_thresh = atr_ma20 * 1.2
    last3_close = df_ind["close"].iloc[-4:-1]
    last3_net   = float(last3_close.iloc[-1] - last3_close.iloc[0])

    row("Vote:",           vote_label(atr_vote))
    row("Reason:",         atr_reason)
    spacer()
    row("ATR14:",          f"{atr_val:.3f}")
    row("ATR_MA20:",       f"{atr_ma20:.3f}")
    row("Threshold (×1.2):", f"{atr_thresh:.3f}")
    row("ATR > threshold?",  yn(atr_val >= atr_thresh))
    row("Last 3 bars net:", f"{last3_net:+.3f}  ({'BUY direction' if last3_net > 0 else 'SELL direction' if last3_net < 0 else 'FLAT'})")
    spacer()

    # Proximity: % of the way to the expansion threshold
    atr_pct     = atr_val / atr_thresh * 100 if atr_thresh > 0 else 0.0
    atr_gap     = atr_thresh - atr_val
    bar_width   = 30
    filled      = min(bar_width, int(atr_pct / 100 * bar_width))
    prog_bar    = "█" * filled + "░" * (bar_width - filled)

    print(f"  {'─' * 58}")
    print(f"  PROXIMITY TO FIRE")
    row("Progress:",         f"[{prog_bar}] {atr_pct:.1f}%")
    if atr_val >= atr_thresh:
        row("→ THRESHOLD MET:", f"ATR is {atr_val - atr_thresh:.3f} pts ABOVE threshold — direction via net candles")
    else:
        row("→ Still needs:",   f"+{atr_gap:.3f} pts of ATR expansion to fire")
        row("  Last 3 net:",    f"{last3_net:+.3f} pts — {'would vote BUY' if last3_net > 0 else 'would vote SELL' if last3_net < 0 else 'FLAT = no signal'} if threshold crossed now")

    # ══════════════════════════════════════════════════════════════════════════
    # 5. BOT 1 — VOTER 3: PREV DAY STRUCTURE
    # ══════════════════════════════════════════════════════════════════════════
    banner("5 ▸ BOT 1 — VOTER 3: PREV DAY STRUCTURE")

    pds_vote, pds_reason = pds_signal(df_ind)

    current_date  = df_ind.index[-1].date()
    current_close = float(df_ind.iloc[-1]["close"])
    prev_mask     = df_ind.index.date < current_date
    prev_bars     = df_ind[prev_mask]

    if len(prev_bars) >= 12:
        prev_date = prev_bars.index.date[-1]
        day_bars  = prev_bars[prev_bars.index.date == prev_date]
        pdh = float(day_bars["high"].max())
        pdl = float(day_bars["low"].min())
        pdr = pdh - pdl
        dist_from_pdh = current_close - pdh
        dist_from_pdl = current_close - pdl
    else:
        pdh = pdl = pdr = float("nan")
        dist_from_pdh = dist_from_pdl = float("nan")

    row("Vote:",              vote_label(pds_vote))
    row("Reason:",            pds_reason)
    spacer()
    row("Prev day date:",     str(prev_date) if len(prev_bars) >= 12 else "N/A")
    row("Prev Day High (PDH):", f"{pdh:.2f}" if not np.isnan(pdh) else "N/A")
    row("Prev Day Low  (PDL):", f"{pdl:.2f}" if not np.isnan(pdl) else "N/A")
    row("Prev Day Range:",    f"{pdr:.2f}" if not np.isnan(pdr) else "N/A")
    row("Current price:",     f"{current_close:.2f}")
    if not np.isnan(dist_from_pdh):
        pdh_tag = "ABOVE ✓ — BUY signal" if dist_from_pdh > 0 else f"below  — need +{abs(dist_from_pdh):.2f} to break out"
        row("Dist from PDH:", f"{dist_from_pdh:+.2f}  ({pdh_tag})")
    else:
        row("Dist from PDH:", "N/A")
    if not np.isnan(dist_from_pdl):
        pdl_tag = f"above  — need -{dist_from_pdl:.2f} to break down" if dist_from_pdl > 0 else "BELOW ✓ — SELL signal"
        row("Dist from PDL:", f"{dist_from_pdl:+.2f}  ({pdl_tag})")
    else:
        row("Dist from PDL:", "N/A")
    spacer()

    print(f"  {'─' * 58}")
    print(f"  PROXIMITY TO FIRE")
    if not np.isnan(pdh) and not np.isnan(pdl):
        pd_range = pdh - pdl
        pct_from_pdl = (current_close - pdl) / pd_range * 100 if pd_range > 0 else 50.0
        bar_w  = 30
        filled = min(bar_w, max(0, int(pct_from_pdl / 100 * bar_w)))
        bar    = "░" * filled + "▓" * (bar_w - filled)   # left=PDL, right=PDH
        row("Price in PD range:", f"[PDL{bar}PDH]  {pct_from_pdl:.0f}% from PDL")
        if dist_from_pdh > 0:
            row("→ BUY ACTIVE:",  f"Price {dist_from_pdh:+.2f} pts above PDH")
        else:
            row("→ BUY trigger:", f"need price to rise {abs(dist_from_pdh):.2f} pts to PDH ({pdh:.2f})")
        if dist_from_pdl < 0:
            row("→ SELL ACTIVE:", f"Price {dist_from_pdl:+.2f} pts below PDL")
        else:
            row("→ SELL trigger:", f"need price to fall {dist_from_pdl:.2f} pts to PDL ({pdl:.2f})")
    else:
        row("Proximity:", "N/A — insufficient prior-day data")

    # ══════════════════════════════════════════════════════════════════════════
    # 6. BOT 1 — COMBINED DAILY BIAS
    # ══════════════════════════════════════════════════════════════════════════
    banner("6 ▸ BOT 1 — COMBINED DAILY BIAS")

    score      = ema_vote + atr_vote + pds_vote
    daily_bias = get_daily_bias(df_ind)
    unanimous  = abs(score) == 3

    def fmt_vote(v):
        return f"+1" if v == 1 else ("-1" if v == -1 else " 0")

    vote_breakdown = (f"EMA({fmt_vote(ema_vote)}) + ATR({fmt_vote(atr_vote)}) "
                      f"+ PDS({fmt_vote(pds_vote)}) = {score:+d}")

    row("Score:",             vote_breakdown)
    row("Unanimous (3/3)?",   yn(unanimous))
    row("Daily bias:",        daily_bias)
    spacer()

    if unanimous:
        print(f"  ✅  BIAS LOCKED: {daily_bias} — Bot 1 will look for M5 entry setups.")
    else:
        need = 3 - abs(score)
        agree_dir = "BUY" if score > 0 else ("SELL" if score < 0 else "split")
        print(f"  ⛔  NO BIAS — {need} more voter(s) needed in same direction (currently leaning {agree_dir}).")
        print(f"      No Bot 1 trades will fire until this resolves to 3/3.")

    # ══════════════════════════════════════════════════════════════════════════
    # 7. BOT 2 — VOLATILITY REGIME CLASSIFIER
    # ══════════════════════════════════════════════════════════════════════════
    banner("7 ▸ BOT 2 — VOLATILITY REGIME CLASSIFIER")

    fingerprint = get_volatility_fingerprint(df_m5)
    regime      = classify_regime(fingerprint)

    atr_r   = fingerprint["atr_ratio"]
    std_r   = fingerprint["stddev_ratio"]
    hl_comp = fingerprint["hl_compression"]
    vov_r   = fingerprint["vov_ratio"]
    wick_r  = fingerprint["wick_ratio"]
    body_r  = fingerprint["body_size_ratio"]

    regime_labels = {"A": "COMPRESSION (coiling)", "B": "EXPANSION (normal)",
                     "C": "EXHAUSTION (spike / mean reversion)",
                     "UNKNOWN": "UNKNOWN (no threshold met)"}

    row("Current regime:",    f"  ┌── REGIME {regime} — {regime_labels.get(regime, '')} ──┐")
    spacer()
    row("ATR14:",             f"{fingerprint['atr']:.4f}")
    row("ATR_MA50:",          f"{fingerprint['atr_ma50']:.4f}")
    row("ATR ratio (÷ MA50):", f"{atr_r:.3f}  [thresholds: <0.80=A, 0.80-1.50=B, >1.50=C]")
    spacer()
    row("StdDev (20-bar):",   f"{fingerprint['stddev']:.6f}")
    row("StdDev_MA50:",       f"{fingerprint['stddev_ma50']:.6f}")
    row("StdDev ratio:",      f"{std_r:.3f}  [thresholds: <0.80=A, 0.80-1.50=B]")
    spacer()
    row("H/L compression:",   f"{hl_comp:.3f}  [threshold: <0.75 for A]")
    row("VoV ratio:",         f"{vov_r:.3f}  [threshold: >1.30 for C]")
    row("Wick ratio:",        f"{wick_r:.3f}  [threshold: >0.60 for B→C entry]")
    row("Body/range ratio:",  f"{body_r:.3f}")
    spacer()

    # Explain the classification result
    if regime == "C":
        print("  Classification: C — Exhaustion")
        print(f"    atr_ratio {atr_r:.3f} > 1.50  {yn(atr_r > 1.50)}")
        print(f"    vov_ratio {vov_r:.3f} > 1.30  {yn(vov_r > 1.30)}")
        print(f"    Both must pass → regime C  {yn(atr_r > 1.50 and vov_r > 1.30)}")
    elif regime == "A":
        print("  Classification: A — Compression")
        print(f"    atr_ratio  {atr_r:.3f} < 0.80  {yn(atr_r < 0.80)}")
        print(f"    std_ratio  {std_r:.3f} < 0.80  {yn(std_r < 0.80)}")
        print(f"    hl_comp    {hl_comp:.3f} < 0.75  {yn(hl_comp < 0.75)}")
        print(f"    All three must pass → regime A  {yn(atr_r < 0.80 and std_r < 0.80 and hl_comp < 0.75)}")
    elif regime == "B":
        print("  Classification: B — Expansion (normal, default state)")
        print(f"    atr_ratio  {atr_r:.3f} in [0.80, 1.50]  {yn(0.80 <= atr_r <= 1.50)}")
        print(f"    std_ratio  {std_r:.3f} in [0.80, 1.50]  {yn(0.80 <= std_r <= 1.50)}")
        print(f"    Both in range → regime B  {yn(0.80 <= atr_r <= 1.50 and 0.80 <= std_r <= 1.50)}")
    else:
        print("  Classification: UNKNOWN — no threshold set is fully satisfied")
        print(f"    C check: atr_ratio({atr_r:.3f})>1.50={atr_r>1.50} AND vov_ratio({vov_r:.3f})>1.30={vov_r>1.30}")
        print(f"    A check: atr<0.80={atr_r<0.80} AND std<0.80={std_r<0.80} AND hl<0.75={hl_comp<0.75}")
        print(f"    B check: atr in [0.80,1.50]={0.80<=atr_r<=1.50} AND std in [0.80,1.50]={0.80<=std_r<=1.50}")

    # ── Regime proximity: how far each metric is from the next threshold ──────
    spacer()
    print(f"  {'─' * 58}")
    print(f"  PROXIMITY TO NEXT REGIME")
    spacer()

    def _pct_bar(val: float, threshold: float, direction: str = "up") -> str:
        """direction='up' means we need val to rise to threshold."""
        if direction == "up":
            pct = min(100.0, val / threshold * 100) if threshold > 0 else 0.0
        else:
            pct = min(100.0, (1 - val / threshold) * 100 + 100) if threshold > 0 else 0.0
        bw = 20
        f  = min(bw, int(pct / 100 * bw))
        return f"[{'█' * f}{'░' * (bw - f)}] {pct:.0f}%"

    if regime in ("A", "UNKNOWN"):
        print("  → To reach Regime B (need atr_r ≥ 0.80 AND std_r ≥ 0.80):")
        row("  ATR ratio  0.80 target:", f"{atr_r:.3f}  {_pct_bar(atr_r, 0.80)}  gap={0.80 - atr_r:+.3f}")
        row("  StdDev ratio 0.80 target:", f"{std_r:.3f}  {_pct_bar(std_r, 0.80)}  gap={0.80 - std_r:+.3f}")
        row("  H/L comp  ≥0.75 target:", f"{hl_comp:.3f}  {_pct_bar(hl_comp, 0.75)}  gap={0.75 - hl_comp:+.3f}")
        bottleneck = min([(atr_r/0.80, "ATR ratio"), (std_r/0.80, "StdDev ratio"), (hl_comp/0.75, "H/L comp")], key=lambda x: x[0])
        spacer()
        row("  Binding constraint:", f"{bottleneck[1]} — furthest from its B-threshold")
        spacer()
        print("  → To reach Regime C directly (need atr_r > 1.50 AND vov_r > 1.30):")
        row("  ATR ratio  1.50 target:", f"{atr_r:.3f}  {_pct_bar(atr_r, 1.50)}  gap={1.50 - atr_r:+.3f}")
        row("  VoV ratio  1.30 target:", f"{vov_r:.3f}  {_pct_bar(vov_r, 1.30)}  gap={1.30 - vov_r:+.3f}")

    elif regime == "B":
        print("  → Already in Regime B (expansion). Watching for C:")
        row("  ATR ratio → C (>1.50):", f"{atr_r:.3f}  {_pct_bar(atr_r, 1.50)}  gap={1.50 - atr_r:+.3f}")
        row("  VoV ratio → C (>1.30):", f"{vov_r:.3f}  {_pct_bar(vov_r, 1.30)}  gap={1.30 - vov_r:+.3f}")
        row("  Wick ratio (B→C entry, >0.60):", f"{wick_r:.3f}  {_pct_bar(wick_r, 0.60)}  gap={0.60 - wick_r:+.3f}")
        spacer()
        if atr_r > 1.30 and vov_r > 1.10:
            print("  ⚡  Approaching C territory — watch for B→C transition signal.")
        else:
            print("  ℹ️   Solidly in B; C transition not imminent.")

    elif regime == "C":
        print("  → In Regime C (exhaustion). Watching for C→A cooling:")
        row("  ATR ratio → below 1.20:", f"{atr_r:.3f}  gap={atr_r - 1.20:+.3f}")
        row("  VoV ratio → below 1.30:", f"{vov_r:.3f}  gap={vov_r - 1.30:+.3f}")
        if atr_r < 1.35:
            print("  ⚡  ATR dropping toward 1.20 cooling threshold — C→A may be near.")

    # ══════════════════════════════════════════════════════════════════════════
    # 8. BOT 2 — REGIME HISTORY & TRANSITION DETECTOR
    # ══════════════════════════════════════════════════════════════════════════
    banner("8 ▸ BOT 2 — REGIME HISTORY & TRANSITION DETECTOR")

    regime_history = []
    transitions    = []   # list of (bars_ago, transition_dict)

    n = len(df_m5)
    for step, bar_i in enumerate(range(n - REGIME_HISTORY_BARS, n)):
        slice_df = df_m5.iloc[max(0, bar_i - 100): bar_i + 1]
        if len(slice_df) < 50:
            regime_history.append("?")
            continue
        fp_i = get_volatility_fingerprint(slice_df)
        r_i  = classify_regime(fp_i)
        regime_history.append(r_i)

        if len(regime_history) >= 2:
            t = detect_transition(regime_history, fp_i, slice_df.iloc[-1])
            bars_ago = (REGIME_HISTORY_BARS - 1) - step   # 0 = current bar
            if t["transition"] != "NONE":
                transitions.append((bars_ago, t))

    # History display
    print(f"\n  Regime history — last {REGIME_HISTORY_BARS} M5 bars (oldest → newest):")
    print(f"  [{' '.join(regime_history)}]")
    spacer()

    # Current bar = last regime in history
    current_regime_hist = regime_history[-1] if regime_history else "?"
    row("Current regime (live):", current_regime_hist)
    spacer()

    if transitions:
        last_bars_ago, last_t = transitions[-1]
        row("Last transition:",   f"{last_t['transition']}")
        row("Direction:",         str(last_t.get("direction")) if last_t.get("direction") else "none (cooling)")
        row("Confidence:",        f"{last_t['confidence']:.0%}")
        row("Bars ago:",          f"{last_bars_ago}  ({'current bar' if last_bars_ago == 0 else f'~{last_bars_ago * 5} minutes ago'})")
        row("Reason:",            last_t["reason"])
        spacer()

        if last_bars_ago == 0:
            print(f"  🔥  ACTIVE TRANSITION on current bar: {last_t['transition']} → {last_t['direction']}")
        elif last_bars_ago <= 3:
            print(f"  ⚡  RECENT transition {last_bars_ago} bar(s) ago — may already have fired a trade.")
        else:
            print(f"  ℹ️   Transition detected {last_bars_ago} bars ago (~{last_bars_ago * 5} min); no current signal.")

        if len(transitions) > 1:
            print(f"\n  All transitions in last {REGIME_HISTORY_BARS} bars:")
            for bars_ago, t in transitions:
                bar_time = df_m5.index[n - 1 - bars_ago].strftime("%H:%M") if bars_ago <= n - 1 else "?"
                print(f"    {bar_time} UTC (+{bars_ago}b ago) — {t['transition']} → {t['direction'] or 'no-trade'}  conf={t['confidence']:.0%}")
    else:
        row("Transitions (last 25 bars):", "None detected")
        row("Reason:", "No regime change in this window, or thresholds not met")

    # ══════════════════════════════════════════════════════════════════════════
    # 9. SPREAD vs BOT THRESHOLDS
    # ══════════════════════════════════════════════════════════════════════════
    banner("9 ▸ SPREAD vs BOT THRESHOLDS")

    b1_spread_ok = spread_pts <= BOT1_SPREAD_MAX
    b2_spread_ok = spread_pts <= BOT2_SPREAD_MAX

    row("Current spread:",       f"{spread_pts} points")
    row("Bot 1 max (15 pts):",   pf(b1_spread_ok))
    row("Bot 2 max (20 pts):",   pf(b2_spread_ok))
    spacer()

    if not b1_spread_ok and not b2_spread_ok:
        print(f"  ⛔  Both bots BLOCKED by spread ({spread_pts} pts > 20 max).")
    elif not b1_spread_ok:
        print(f"  ⚠️   Bot 1 BLOCKED ({spread_pts} > 15).  Bot 2 CLEAR.")
    elif b1_spread_ok and b2_spread_ok:
        print(f"  ✅  Both bots clear spread filter.")

    # ══════════════════════════════════════════════════════════════════════════
    # 10. SESSION FILTER
    # ══════════════════════════════════════════════════════════════════════════
    banner("10 ▸ SESSION FILTER")

    hour       = now_utc.hour
    in_session = hour in SESSION_HOURS

    row("Current UTC time:",   f"{now_utc.strftime('%H:%M:%S')}  (hour {hour:02d})")
    row("Active windows:",     "00:00–14:59 UTC  (Asian + London + NY morning)")
    row("",                    "20:00–23:59 UTC  (NY PM + evening)")
    row("In active session?",  yn(in_session))
    spacer()

    if in_session:
        print(f"  ✅  Inside trading window — session filter PASS.")
    else:
        if 15 <= hour < 20:
            mins_to_next = (20 - hour) * 60 - now_utc.minute
            print(f"  ⛔  OUTSIDE session (NY afternoon dead zone, 15:00–19:59 UTC).")
            print(f"      Next window opens at 20:00 UTC (~{mins_to_next} min from now).")
        else:
            print(f"  ⛔  OUTSIDE session.")

    # ══════════════════════════════════════════════════════════════════════════
    # SUMMARY
    # ══════════════════════════════════════════════════════════════════════════
    banner("SUMMARY")

    b1_ready = (daily_bias != "NONE") and b1_spread_ok and in_session
    b2_ready = bool(transitions and transitions[-1][0] <= 1 and
                    transitions[-1][1]["transition"] not in ("NONE", "C_to_A") and
                    b2_spread_ok and in_session)

    def status(ok):
        return "🟢 READY" if ok else "🔴 BLOCKED"

    print()
    print(f"  ┌─────────────────────────────────────────────────────┐")
    print(f"  │  BOT 1   Bias={daily_bias:<4}  Spread={'OK' if b1_spread_ok else 'BLOCKED'}  Session={'OK' if in_session else 'BLOCKED'}      {status(b1_ready):<10}│")
    print(f"  │  BOT 2   Regime={current_regime_hist:<7}  Spread={'OK' if b2_spread_ok else 'BLOCKED'}  Session={'OK' if in_session else 'BLOCKED'}  {status(b2_ready):<10}│")
    print(f"  └─────────────────────────────────────────────────────┘")

    # Blockers
    blockers = []
    if daily_bias == "NONE":
        blockers.append(f"Bot 1: bias NONE ({abs(score)}/3 voters agree — need 3/3)")
    if not b1_spread_ok:
        blockers.append(f"Bot 1: spread {spread_pts} > {BOT1_SPREAD_MAX}")
    if not b2_spread_ok:
        blockers.append(f"Bot 2: spread {spread_pts} > {BOT2_SPREAD_MAX}")
    if not in_session:
        blockers.append(f"Both bots: outside session window (hour {hour:02d} UTC)")
    if not transitions or transitions[-1][0] > 1:
        blockers.append(f"Bot 2: no active regime transition on current bar")

    if blockers:
        print()
        for b in blockers:
            print(f"  ⛔  {b}")

    print()
    print("█" * 64)
    print()

    mt5.shutdown()


if __name__ == "__main__":
    main()
