"""
midas_dashboard_local.py
MIDAS TRADING SYSTEMS — Local Dashboard

Reads live data directly from MT5 + your log file.
Generates a local HTML file and opens it in your browser.
Auto-refreshes every 30 seconds.

Run with: python midas_dashboard_local.py
"""

import MetaTrader5 as mt5
import os
import re
import json
import webbrowser
import time
import threading
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

LOG_FILE   = Path(__file__).parent / "logs" / f"{datetime.now().strftime('%Y-%m-%d')}.log"
HTML_FILE  = Path(__file__).parent / "midas_dashboard_local.html"
SYMBOL     = "XAUUSD"
REFRESH_S  = 30


def connect_mt5():
    if not mt5.initialize():
        return False
    login    = int(os.getenv("MT5_LOGIN", 0))
    password = os.getenv("MT5_PASSWORD", "")
    server   = os.getenv("MT5_SERVER", "")
    if login:
        mt5.login(login, password=password, server=server)
    return True


def get_account_data():
    info = mt5.account_info()
    if not info:
        return {}
    return {
        "balance":     round(info.balance, 2),
        "equity":      round(info.equity, 2),
        "profit":      round(info.profit, 2),
        "margin_free": round(info.margin_free, 2),
        "login":       info.login,
        "server":      info.server,
        "currency":    info.currency,
    }


def get_open_positions():
    positions = mt5.positions_get(symbol=SYMBOL) or []
    result = []
    for p in positions:
        if p.magic != 10001:
            continue
        tick = mt5.symbol_info_tick(SYMBOL)
        current = tick.bid if p.type == 0 else tick.ask
        result.append({
            "ticket":  p.ticket,
            "type":    "BUY" if p.type == 0 else "SELL",
            "volume":  p.volume,
            "open":    round(p.price_open, 2),
            "sl":      round(p.sl, 2),
            "tp":      round(p.tp, 2),
            "profit":  round(p.profit, 2),
            "current": round(current, 2),
        })
    return result


def get_trade_history(days=30):
    from datetime import timedelta
    start = datetime.now(timezone.utc) - timedelta(days=days)
    deals = mt5.history_deals_get(start, datetime.now(timezone.utc))
    if not deals:
        return [], []

    trades  = []
    equity  = []
    balance = get_account_data().get("balance", 500)
    running = balance

    closed = [d for d in deals if d.magic == 10001 and d.entry == mt5.DEAL_ENTRY_OUT]
    for d in sorted(closed, key=lambda x: x.time):
        running += d.profit
        trades.append({
            "time":   datetime.fromtimestamp(d.time).strftime("%d/%m %H:%M"),
            "type":   "BUY" if d.type == mt5.DEAL_TYPE_BUY else "SELL",
            "profit": round(d.profit, 2),
            "price":  round(d.price, 2),
            "result": "WIN" if d.profit > 0 else "LOSS",
        })
        equity.append(round(running, 2))

    return trades, equity


def get_current_vote():
    """Parse the most recent vote from today's log file."""
    if not LOG_FILE.exists():
        # try yesterday's log too
        yesterday = Path(__file__).parent / "logs" / f"{datetime.now().strftime('%Y-%m-%d')}.log"
        if not yesterday.exists():
            return {}

    try:
        lines = LOG_FILE.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return {}

    vote_data = {
        "ema":       "—", "rsi": "—", "bb": "—",
        "vwap":      "—", "candle": "—",
        "score":     "—", "direction": "NEUTRAL",
        "buy": 0, "sell": 0, "neutral": 0,
        "timestamp": "—"
    }

    # scan backwards for most recent complete vote block
    for i in range(len(lines) - 1, -1, -1):
        line = lines[i]
        if "Vote: " in line and "BUY=" in line:
            m = re.search(r"Vote: (\w+) \| Score=(-?\d+)/8 \| BUY=(\d+) SELL=(\d+) NEUTRAL=(\d+)", line)
            if m:
                vote_data["direction"] = m.group(1)
                vote_data["score"]     = m.group(2)
                vote_data["buy"]       = int(m.group(3))
                vote_data["sell"]      = int(m.group(4))
                vote_data["neutral"]   = int(m.group(5))
                # grab timestamp from same line
                ts = re.match(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", line)
                if ts:
                    vote_data["timestamp"] = ts.group(1)

            # now scan back a few lines for strategy details
            for j in range(i - 1, max(i - 8, -1), -1):
                l = lines[j]
                if "EMA Stack:" in l:
                    vote_data["ema"]    = l.split("EMA Stack:")[-1].strip()
                elif "RSI Divergence:" in l:
                    vote_data["rsi"]    = l.split("RSI Divergence:")[-1].strip()
                elif "Bollinger Bands:" in l:
                    vote_data["bb"]     = l.split("Bollinger Bands:")[-1].strip()
                elif "VWAP:" in l:
                    vote_data["vwap"]   = l.split("VWAP:")[-1].strip()
                elif "Candlestick:" in l:
                    vote_data["candle"] = l.split("Candlestick:")[-1].strip()
            break

    return vote_data


def signal_color(text):
    t = text.lower()
    if any(w in t for w in ["bullish", "buy", "above", "bounce from above", "bullish stack"]):
        return "bull"
    if any(w in t for w in ["bearish", "sell", "below", "rejection", "reversal", "bearish stack"]):
        return "bear"
    return "neut"


def build_html(account, positions, trades, equity_curve, vote):
    wins   = sum(1 for t in trades if t["result"] == "WIN")
    losses = sum(1 for t in trades if t["result"] == "LOSS")
    total  = len(trades)
    wr     = round(wins / total * 100, 1) if total else 0
    pnl    = round(sum(t["profit"] for t in trades), 2)

    direction       = vote.get("direction", "NEUTRAL")
    dir_class       = "bull" if direction == "BUY" else ("bear" if direction == "SELL" else "neut")
    score           = vote.get("score", "—")
    now             = datetime.now().strftime("%H:%M:%S")

    # equity chart points
    if equity_curve:
        min_e  = min(equity_curve)
        max_e  = max(equity_curve)
        rng    = max_e - min_e or 1
        pts    = []
        for i, v in enumerate(equity_curve):
            x = round(i / max(len(equity_curve) - 1, 1) * 560, 1)
            y = round(140 - ((v - min_e) / rng * 120), 1)
            pts.append(f"{x},{y}")
        sparkline = " ".join(pts)
        first_eq  = equity_curve[0]
        last_eq   = equity_curve[-1]
        eq_color  = "#f5c518" if last_eq >= first_eq else "#e05c5c"
    else:
        sparkline = "0,70 560,70"
        eq_color  = "#f5c518"

    # trades table rows
    trade_rows = ""
    for t in reversed(trades[-20:]):
        cls = "win" if t["result"] == "WIN" else "loss"
        pnl_str = f"+{t['profit']}" if t["profit"] > 0 else str(t["profit"])
        trade_rows += f"""
        <tr>
            <td>{t['time']}</td>
            <td class="{cls}">{t['type']}</td>
            <td>{t['price']}</td>
            <td class="{cls}">{pnl_str}</td>
            <td class="{cls}">{t['result']}</td>
        </tr>"""

    # open positions rows
    pos_rows = ""
    for p in positions:
        cls     = "win" if p["profit"] > 0 else "loss"
        pnl_str = f"+{p['profit']}" if p["profit"] > 0 else str(p["profit"])
        pos_rows += f"""
        <tr>
            <td>#{p['ticket']}</td>
            <td class="{'bull' if p['type'] == 'BUY' else 'bear'}">{p['type']}</td>
            <td>{p['volume']}</td>
            <td>{p['open']}</td>
            <td>{p['sl']}</td>
            <td>{p['tp']}</td>
            <td class="{cls}">{pnl_str}</td>
        </tr>"""
    if not pos_rows:
        pos_rows = '<tr><td colspan="7" class="empty">No open positions</td></tr>'

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta http-equiv="refresh" content="{REFRESH_S}">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Midas Trading Systems</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;600&family=IBM+Plex+Sans:wght@300;400;600&display=swap');

  *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}

  :root {{
    --bg:       #0a0a0a;
    --surface:  #111111;
    --border:   #1e1e1e;
    --gold:     #f5c518;
    --gold-dim: #a88a10;
    --bull:     #3ecf8e;
    --bear:     #e05c5c;
    --neut:     #888888;
    --text:     #e8e8e8;
    --dim:      #555555;
    --font-mono: 'IBM Plex Mono', monospace;
    --font-sans: 'IBM Plex Sans', sans-serif;
  }}

  body {{
    background: var(--bg);
    color: var(--text);
    font-family: var(--font-sans);
    font-size: 13px;
    line-height: 1.5;
    min-height: 100vh;
    padding: 24px;
  }}

  header {{
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    border-bottom: 1px solid var(--border);
    padding-bottom: 16px;
    margin-bottom: 24px;
  }}

  .brand {{
    font-family: var(--font-mono);
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.2em;
    text-transform: uppercase;
    color: var(--gold);
  }}

  .brand span {{
    color: var(--dim);
    font-weight: 400;
  }}

  .refresh-note {{
    font-family: var(--font-mono);
    font-size: 10px;
    color: var(--dim);
  }}

  .grid {{
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 12px;
    margin-bottom: 20px;
  }}

  .card {{
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 4px;
    padding: 16px;
  }}

  .card-label {{
    font-family: var(--font-mono);
    font-size: 9px;
    letter-spacing: 0.15em;
    text-transform: uppercase;
    color: var(--dim);
    margin-bottom: 6px;
  }}

  .card-value {{
    font-family: var(--font-mono);
    font-size: 22px;
    font-weight: 600;
    color: var(--text);
  }}

  .card-value.gold {{ color: var(--gold); }}
  .card-value.bull  {{ color: var(--bull); }}
  .card-value.bear  {{ color: var(--bear); }}
  .card-value.neut  {{ color: var(--neut); }}

  .card-sub {{
    font-size: 11px;
    color: var(--dim);
    margin-top: 2px;
  }}

  .two-col {{
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 12px;
    margin-bottom: 20px;
  }}

  .full-col {{
    margin-bottom: 20px;
  }}

  .section-label {{
    font-family: var(--font-mono);
    font-size: 9px;
    letter-spacing: 0.15em;
    text-transform: uppercase;
    color: var(--gold-dim);
    margin-bottom: 10px;
  }}

  /* Vote panel */
  .vote-panel {{
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 4px;
    padding: 16px;
  }}

  .vote-header {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 14px;
  }}

  .vote-direction {{
    font-family: var(--font-mono);
    font-size: 18px;
    font-weight: 600;
  }}

  .vote-score {{
    font-family: var(--font-mono);
    font-size: 12px;
    color: var(--dim);
  }}

  .vote-ts {{
    font-family: var(--font-mono);
    font-size: 10px;
    color: var(--dim);
  }}

  .strategy-row {{
    display: flex;
    align-items: flex-start;
    gap: 8px;
    padding: 6px 0;
    border-bottom: 1px solid var(--border);
    font-size: 11px;
  }}

  .strategy-row:last-child {{ border-bottom: none; }}

  .strategy-name {{
    font-family: var(--font-mono);
    color: var(--dim);
    min-width: 52px;
    font-size: 10px;
    padding-top: 1px;
  }}

  .strategy-read {{
    flex: 1;
    color: var(--text);
  }}

  .dot {{
    width: 7px;
    height: 7px;
    border-radius: 50%;
    margin-top: 4px;
    flex-shrink: 0;
  }}

  .dot.bull {{ background: var(--bull); }}
  .dot.bear {{ background: var(--bear); }}
  .dot.neut {{ background: var(--dim); }}

  /* Equity chart */
  .chart-card {{
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 4px;
    padding: 16px;
  }}

  svg.equity {{ width: 100%; height: 150px; display: block; }}

  /* Tables */
  table {{
    width: 100%;
    border-collapse: collapse;
    font-family: var(--font-mono);
    font-size: 11px;
  }}

  th {{
    text-align: left;
    font-size: 9px;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: var(--dim);
    padding: 6px 8px;
    border-bottom: 1px solid var(--border);
  }}

  td {{
    padding: 7px 8px;
    border-bottom: 1px solid #161616;
    color: var(--text);
  }}

  tr:last-child td {{ border-bottom: none; }}

  .win  {{ color: var(--bull); }}
  .loss {{ color: var(--bear); }}
  .bull {{ color: var(--bull); }}
  .bear {{ color: var(--bear); }}
  .neut {{ color: var(--neut); }}
  .empty {{ color: var(--dim); text-align: center; padding: 20px; }}

  .tbl-wrap {{
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 4px;
    overflow: hidden;
  }}

  .vote-bar {{
    display: flex;
    gap: 6px;
    margin-top: 12px;
  }}

  .vote-pip {{
    flex: 1;
    height: 3px;
    border-radius: 2px;
    background: var(--border);
  }}

  .vote-pip.bull {{ background: var(--bull); }}
  .vote-pip.bear {{ background: var(--bear); }}
</style>
</head>
<body>

<header>
  <div>
    <div class="brand">MIDAS TRADING SYSTEMS <span>/ LOCAL DASHBOARD</span></div>
    <div class="refresh-note">Account {account.get('login', '—')} · {account.get('server', '—')} · refreshes every {REFRESH_S}s</div>
  </div>
  <div class="refresh-note">Last updated {now}</div>
</header>

<!-- STAT CARDS -->
<div class="grid">
  <div class="card">
    <div class="card-label">Balance</div>
    <div class="card-value gold">${account.get('balance', '—')}</div>
    <div class="card-sub">{account.get('currency', 'USD')}</div>
  </div>
  <div class="card">
    <div class="card-label">Equity</div>
    <div class="card-value">${account.get('equity', '—')}</div>
    <div class="card-sub">Open P&L: {'+'if account.get('profit',0)>=0 else ''}{account.get('profit', 0)}</div>
  </div>
  <div class="card">
    <div class="card-label">Total P&L ({total} trades)</div>
    <div class="card-value {'bull' if pnl >= 0 else 'bear'}">{'+'if pnl>=0 else ''}{pnl}</div>
    <div class="card-sub">{wins}W / {losses}L · {wr}% win rate</div>
  </div>
  <div class="card">
    <div class="card-label">Current Vote</div>
    <div class="card-value {dir_class}">{direction}</div>
    <div class="card-sub">Score {score}/5 · BUY={vote.get('buy',0)} SELL={vote.get('sell',0)}</div>
  </div>
</div>

<!-- VOTE + EQUITY -->
<div class="two-col">

  <!-- Vote panel -->
  <div class="vote-panel">
    <div class="section-label">Voting Engine</div>
    <div class="vote-header">
      <div class="vote-direction {dir_class}">{direction} ({score}/5)</div>
      <div class="vote-ts">{vote.get('timestamp', '—')}</div>
    </div>

    <div class="strategy-row">
      <div class="dot {signal_color(vote.get('ema',''))}"></div>
      <div class="strategy-name">EMA</div>
      <div class="strategy-read">{vote.get('ema', '—')}</div>
    </div>
    <div class="strategy-row">
      <div class="dot {signal_color(vote.get('rsi',''))}"></div>
      <div class="strategy-name">RSI</div>
      <div class="strategy-read">{vote.get('rsi', '—')}</div>
    </div>
    <div class="strategy-row">
      <div class="dot {signal_color(vote.get('bb',''))}"></div>
      <div class="strategy-name">BB</div>
      <div class="strategy-read">{vote.get('bb', '—')}</div>
    </div>
    <div class="strategy-row">
      <div class="dot {signal_color(vote.get('vwap',''))}"></div>
      <div class="strategy-name">VWAP</div>
      <div class="strategy-read">{vote.get('vwap', '—')}</div>
    </div>
    <div class="strategy-row">
      <div class="dot {signal_color(vote.get('candle',''))}"></div>
      <div class="strategy-name">CANDLE</div>
      <div class="strategy-read">{vote.get('candle', '—')}</div>
    </div>

    <div class="vote-bar">
      {''.join(f'<div class="vote-pip bull"></div>' for _ in range(vote.get('buy',0)))}
      {''.join(f'<div class="vote-pip bear"></div>' for _ in range(vote.get('sell',0)))}
      {''.join(f'<div class="vote-pip"></div>' for _ in range(vote.get('neutral',0)))}
    </div>
  </div>

  <!-- Equity curve -->
  <div class="chart-card">
    <div class="section-label">Equity Curve</div>
    <svg class="equity" viewBox="0 0 560 150" preserveAspectRatio="none">
      <defs>
        <linearGradient id="grad" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="{eq_color}" stop-opacity="0.15"/>
          <stop offset="100%" stop-color="{eq_color}" stop-opacity="0"/>
        </linearGradient>
      </defs>
      <polygon points="0,150 {sparkline} 560,150" fill="url(#grad)"/>
      <polyline points="{sparkline}" fill="none" stroke="{eq_color}" stroke-width="1.5"/>
    </svg>
    <div style="display:flex;justify-content:space-between;font-family:var(--font-mono);font-size:10px;color:var(--dim);margin-top:6px;">
      <span>${equity_curve[0] if equity_curve else '—'}</span>
      <span>${equity_curve[-1] if equity_curve else '—'}</span>
    </div>
  </div>
</div>

<!-- OPEN POSITIONS -->
<div class="full-col">
  <div class="section-label">Open Positions</div>
  <div class="tbl-wrap">
    <table>
      <thead><tr>
        <th>Ticket</th><th>Type</th><th>Volume</th>
        <th>Open</th><th>SL</th><th>TP</th><th>P&L</th>
      </tr></thead>
      <tbody>{pos_rows}</tbody>
    </table>
  </div>
</div>

<!-- TRADE HISTORY -->
<div class="full-col">
  <div class="section-label">Trade History (last 20)</div>
  <div class="tbl-wrap">
    <table>
      <thead><tr>
        <th>Time</th><th>Type</th><th>Price</th><th>P&L</th><th>Result</th>
      </tr></thead>
      <tbody>{''.join([trade_rows]) if trades else '<tr><td colspan="5" class="empty">No closed trades yet</td></tr>'}</tbody>
    </table>
  </div>
</div>

</body>
</html>"""
    return html


def generate_and_open():
    if not connect_mt5():
        print("[ERROR] Could not connect to MT5")
        return

    account = get_account_data()
    positions = get_open_positions()
    trades, equity_curve = get_trade_history()
    vote = get_current_vote()

    html = build_html(account, positions, trades, equity_curve, vote)
    HTML_FILE.write_text(html, encoding="utf-8")
    print(f"[OK] Dashboard written to {HTML_FILE}")
    webbrowser.open(f"file:///{HTML_FILE.as_posix()}")

    # keep updating every 30s
    while True:
        time.sleep(REFRESH_S)
        try:
            account   = get_account_data()
            positions = get_open_positions()
            trades, equity_curve = get_trade_history()
            vote      = get_current_vote()
            html      = build_html(account, positions, trades, equity_curve, vote)
            HTML_FILE.write_text(html, encoding="utf-8")
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Dashboard refreshed")
        except Exception as e:
            print(f"[WARN] Refresh error: {e}")


if __name__ == "__main__":
    generate_and_open()
