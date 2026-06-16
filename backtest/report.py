"""
backtest/report.py

Generates a standalone HTML backtest report with charts.
Open the output file in Chrome — no server required.
"""

import json
from datetime import datetime


def generate_html_report(metrics: dict, monthly: list, strategy_contrib: list,
                          hourly: list, trades: list, config: dict,
                          output_path: str = "backtest_report.html"):
    """Generate a full HTML backtest report."""

    eq        = metrics["equity_curve"]
    eq_labels = [f"#{i+1}" for i in range(len(eq))]

    monthly_labels = [m["month"] for m in monthly]
    monthly_pnl    = [m["pnl"] for m in monthly]
    monthly_colors = ["rgba(62,207,142,0.7)" if p >= 0 else "rgba(248,113,113,0.7)" for p in monthly_pnl]

    hourly_labels  = [f"{h:02d}:00" for h in range(24)]
    hourly_wr      = [hourly[h]["win_rate"] for h in range(24)]
    hourly_counts  = [hourly[h]["trades"] for h in range(24)]

    strat_labels = [s["strategy"] for s in strategy_contrib]
    strat_wr     = [s["win_rate"] for s in strategy_contrib]
    strat_votes  = [s["votes"] for s in strategy_contrib]

    recent_trades = trades[-50:][::-1]

    now = datetime.now().strftime("%d %b %Y %H:%M")

    pnl_color = "#3ecf8e" if metrics["net_pnl"] >= 0 else "#f87171"

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Midas Backtest Report</title>
<link href="https://fonts.googleapis.com/css2?family=DM+Mono:wght@400;500&family=Syne:wght@400;500;700;800&display=swap" rel="stylesheet">
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.js"></script>
<style>
:root{{--bg:#0a0a0a;--bg2:#111;--bg3:#1a1a1a;--border:#2a2a2a;--text:#f0f0f0;--muted:#666;--gold:#d4a843;--gold2:#f0c060;--green:#3ecf8e;--red:#f87171;--font:'Syne',sans-serif;--mono:'DM Mono',monospace;}}
*{{box-sizing:border-box;margin:0;padding:0;}}
body{{background:var(--bg);color:var(--text);font-family:var(--mono);font-size:13px;}}
.header{{padding:24px 40px;border-bottom:1px solid var(--border);display:flex;align-items:center;justify-content:space-between;}}
.logo{{display:flex;align-items:center;gap:14px;}}
.logo-box{{width:40px;height:40px;background:linear-gradient(135deg,#B8892A,#F0C060);border-radius:10px;display:flex;align-items:center;justify-content:center;font-family:var(--font);font-weight:800;font-size:16px;color:#000;}}
.logo-title{{font-family:var(--font);font-weight:700;font-size:20px;letter-spacing:-0.5px;}}
.logo-sub{{font-size:11px;color:var(--muted);margin-top:2px;}}
.generated{{font-size:11px;color:var(--muted);}}
.main{{padding:32px 40px;max-width:1400px;}}
.ticker{{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:1px;background:var(--border);border:1px solid var(--border);border-radius:10px;overflow:hidden;margin-bottom:20px;}}
.t-item{{background:var(--bg2);padding:14px 16px;}}
.t-label{{font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:0.08em;margin-bottom:4px;}}
.t-val{{font-family:var(--font);font-weight:700;font-size:20px;letter-spacing:-0.5px;}}
.t-sub{{font-size:10px;color:var(--muted);margin-top:3px;}}
.pos{{color:var(--green);}} .neg{{color:var(--red);}} .gold{{color:var(--gold2);}}
.grid2{{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:16px;}}
.grid3{{display:grid;grid-template-columns:1fr 1fr 1fr;gap:16px;margin-bottom:16px;}}
.panel{{background:var(--bg2);border:1px solid var(--border);border-radius:10px;overflow:hidden;}}
.ph{{padding:14px 18px;border-bottom:1px solid var(--border);display:flex;align-items:center;justify-content:space-between;}}
.pt{{font-family:var(--font);font-weight:500;font-size:12px;text-transform:uppercase;letter-spacing:0.1em;color:var(--muted);}}
.pb{{padding:18px;}}
.chart-w{{position:relative;height:220px;}}
.chart-sm{{position:relative;height:180px;}}
table{{width:100%;border-collapse:collapse;font-size:12px;}}
thead tr{{border-bottom:1px solid var(--border);}}
th{{padding:10px 12px;text-align:left;font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:0.06em;}}
td{{padding:9px 12px;border-bottom:1px solid var(--border);}}
tr:last-child td{{border-bottom:none;}}
tr:hover td{{background:var(--bg3);}}
.badge{{display:inline-block;padding:2px 8px;border-radius:4px;font-size:10px;font-weight:500;}}
.b-buy{{background:rgba(62,207,142,0.1);color:var(--green);border:1px solid rgba(62,207,142,0.2);}}
.b-sell{{background:rgba(248,113,113,0.1);color:var(--red);border:1px solid rgba(248,113,113,0.2);}}
.b-win{{background:rgba(62,207,142,0.08);color:var(--green);}}
.b-loss{{background:rgba(248,113,113,0.08);color:var(--red);}}
.config-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:8px;}}
.config-item{{background:var(--bg3);border-radius:6px;padding:10px 12px;}}
.config-label{{font-size:10px;color:var(--muted);margin-bottom:3px;}}
.config-val{{font-size:13px;font-weight:500;}}
.footer{{text-align:center;padding:24px;font-size:10px;color:var(--muted);border-top:1px solid var(--border);margin-top:16px;}}
.strat-bar{{display:flex;align-items:center;gap:10px;margin-bottom:8px;}}
.strat-name{{width:130px;font-size:12px;flex-shrink:0;}}
.strat-track{{flex:1;height:8px;background:var(--bg3);border-radius:4px;overflow:hidden;}}
.strat-fill{{height:100%;border-radius:4px;background:var(--gold);transition:width 0.5s;}}
.strat-pct{{width:40px;font-size:11px;color:var(--muted);text-align:right;}}
</style>
</head>
<body>

<div class="header">
  <div class="logo">
    <div class="logo-box">M</div>
    <div>
      <div class="logo-title">Midas — Backtest Report</div>
      <div class="logo-sub">XAUUSD · M5 Scalping · {config.get('days', '?')} days · {metrics['total_trades']} trades</div>
    </div>
  </div>
  <div class="generated">Generated {now}</div>
</div>

<div class="main">

  <div class="ticker">
    <div class="t-item"><div class="t-label">Net P&L</div><div class="t-val {'pos' if metrics['net_pnl']>=0 else 'neg'}">${metrics['net_pnl']:+.2f}</div><div class="t-sub">from ${metrics['initial_balance']}</div></div>
    <div class="t-item"><div class="t-label">Win rate</div><div class="t-val gold">{metrics['win_rate']}%</div><div class="t-sub">{metrics['wins']}W / {metrics['losses']}L</div></div>
    <div class="t-item"><div class="t-label">Profit factor</div><div class="t-val {'pos' if metrics['profit_factor']>=1 else 'neg'}">{metrics['profit_factor']}</div><div class="t-sub">gross win / gross loss</div></div>
    <div class="t-item"><div class="t-label">Total return</div><div class="t-val {'pos' if metrics['total_return']>=0 else 'neg'}">{metrics['total_return']}%</div><div class="t-sub">on initial balance</div></div>
    <div class="t-item"><div class="t-label">Max drawdown</div><div class="t-val neg">{metrics['max_drawdown_pct']}%</div><div class="t-sub">${metrics['max_drawdown']:.2f}</div></div>
    <div class="t-item"><div class="t-label">Sharpe ratio</div><div class="t-val {'pos' if metrics['sharpe_ratio']>=1 else 'gold'}">{metrics['sharpe_ratio']}</div><div class="t-sub">annualized</div></div>
    <div class="t-item"><div class="t-label">Expectancy</div><div class="t-val {'pos' if metrics['expectancy']>=0 else 'neg'}">${metrics['expectancy']:+.2f}</div><div class="t-sub">per trade avg</div></div>
    <div class="t-item"><div class="t-label">Avg RR</div><div class="t-val gold">{metrics['avg_rr']}:1</div><div class="t-sub">win / loss ratio</div></div>
  </div>

  <div class="grid2">
    <div class="panel">
      <div class="ph"><span class="pt">Equity curve</span><span style="font-size:10px;color:var(--muted)">{metrics['total_trades']} trades</span></div>
      <div class="pb"><div class="chart-w"><canvas id="equityChart"></canvas></div></div>
    </div>
    <div class="panel">
      <div class="ph"><span class="pt">Monthly P&L</span></div>
      <div class="pb"><div class="chart-w"><canvas id="monthlyChart"></canvas></div></div>
    </div>
  </div>

  <div class="grid2">
    <div class="panel">
      <div class="ph"><span class="pt">Win rate by hour (UTC)</span></div>
      <div class="pb"><div class="chart-sm"><canvas id="hourChart"></canvas></div></div>
    </div>
    <div class="panel">
      <div class="ph"><span class="pt">Strategy contribution</span></div>
      <div class="pb" style="padding-top:20px;">
        {''.join(f'<div class="strat-bar"><div class="strat-name">{s["strategy"]}</div><div class="strat-track"><div class="strat-fill" style="width:{s["win_rate"]}%"></div></div><div class="strat-pct">{s["win_rate"]}%</div></div>' for s in strategy_contrib)}
      </div>
    </div>
  </div>

  <div class="grid3" style="margin-bottom:16px;">
    <div class="panel">
      <div class="ph"><span class="pt">Best performance</span></div>
      <div class="pb">
        <div style="display:flex;flex-direction:column;gap:10px;">
          <div style="display:flex;justify-content:space-between;"><span style="color:var(--muted);font-size:11px;">Best trade</span><span class="pos">+${metrics['best_trade']}</span></div>
          <div style="display:flex;justify-content:space-between;"><span style="color:var(--muted);font-size:11px;">Max consec wins</span><span class="pos">{metrics['max_consec_wins']}</span></div>
          <div style="display:flex;justify-content:space-between;"><span style="color:var(--muted);font-size:11px;">Avg win</span><span class="pos">+${metrics['avg_win']}</span></div>
          <div style="display:flex;justify-content:space-between;"><span style="color:var(--muted);font-size:11px;">Gross profit</span><span class="pos">+${metrics['gross_win']}</span></div>
        </div>
      </div>
    </div>
    <div class="panel">
      <div class="ph"><span class="pt">Risk metrics</span></div>
      <div class="pb">
        <div style="display:flex;flex-direction:column;gap:10px;">
          <div style="display:flex;justify-content:space-between;"><span style="color:var(--muted);font-size:11px;">Worst trade</span><span class="neg">${metrics['worst_trade']}</span></div>
          <div style="display:flex;justify-content:space-between;"><span style="color:var(--muted);font-size:11px;">Max consec losses</span><span class="neg">{metrics['max_consec_losses']}</span></div>
          <div style="display:flex;justify-content:space-between;"><span style="color:var(--muted);font-size:11px;">Avg loss</span><span class="neg">-${metrics['avg_loss']}</span></div>
          <div style="display:flex;justify-content:space-between;"><span style="color:var(--muted);font-size:11px;">Max drawdown</span><span class="neg">{metrics['max_drawdown_pct']}%</span></div>
        </div>
      </div>
    </div>
    <div class="panel">
      <div class="ph"><span class="pt">Backtest config</span></div>
      <div class="pb">
        <div style="display:flex;flex-direction:column;gap:10px;">
          <div style="display:flex;justify-content:space-between;"><span style="color:var(--muted);font-size:11px;">Period</span><span>{config.get('days','?')} days</span></div>
          <div style="display:flex;justify-content:space-between;"><span style="color:var(--muted);font-size:11px;">Vote threshold</span><span>{config.get('vote_threshold','?')}/5</span></div>
          <div style="display:flex;justify-content:space-between;"><span style="color:var(--muted);font-size:11px;">Risk per trade</span><span>{config.get('risk_pct','?')}%</span></div>
          <div style="display:flex;justify-content:space-between;"><span style="color:var(--muted);font-size:11px;">Cooldown</span><span>{config.get('cooldown_bars','?')} bars</span></div>
        </div>
      </div>
    </div>
  </div>

  <div class="panel" style="margin-bottom:16px;">
    <div class="ph"><span class="pt">Monthly breakdown</span></div>
    <div style="overflow-x:auto;">
      <table>
        <thead><tr><th>Month</th><th>Trades</th><th>Wins</th><th>Losses</th><th>Win rate</th><th>P&L</th></tr></thead>
        <tbody>
          {''.join(f'<tr><td>{m["month"]}</td><td>{m["trades"]}</td><td style="color:var(--green)">{m["wins"]}</td><td style="color:var(--red)">{m["losses"]}</td><td style="color:var(--gold2)">{m["win_rate"]}%</td><td class="{"pos" if m["pnl"]>=0 else "neg"}">${m["pnl"]:+.2f}</td></tr>' for m in monthly)}
        </tbody>
      </table>
    </div>
  </div>

  <div class="panel">
    <div class="ph"><span class="pt">Last 50 trades</span></div>
    <div style="overflow-x:auto;">
      <table>
        <thead><tr><th>Date</th><th>Time</th><th>Dir</th><th>Entry</th><th>Exit</th><th>Lots</th><th>P&L</th><th>Result</th><th>Vote</th></tr></thead>
        <tbody>
          {''.join(f'<tr><td>{t["date"]}</td><td style="color:var(--muted)">{t["time"]}</td><td><span class="badge {"b-buy" if t["direction"]=="BUY" else "b-sell"}">{t["direction"]}</span></td><td>{t["entry"]}</td><td>{t["exit"]}</td><td>{t["lots"]}</td><td class="{"pos" if t["pnl"]>=0 else "neg"}" style="font-weight:500">${t["pnl"]:+.2f}</td><td><span class="badge {"b-win" if t["result"]=="WIN" else "b-loss"}">{t["result"]}</span></td><td style="color:var(--muted)">{t.get("vote_score","?")}/5</td></tr>' for t in recent_trades)}
        </tbody>
      </table>
    </div>
  </div>

</div>

<div class="footer">Midas Backtest Report · Generated {now} · For informational purposes only · Past performance does not guarantee future results</div>

<script>
const chartDefaults = {{
  responsive: true, maintainAspectRatio: false,
  plugins: {{ legend: {{ display: false }}, tooltip: {{ backgroundColor: '#1a1a1a', borderColor: '#333', borderWidth: 1, bodyColor: '#f0f0f0' }} }},
}};

const eq = {json.dumps(eq)};
const eqLabels = {json.dumps(eq_labels)};
const isPos = eq[eq.length-1] >= eq[0];

new Chart(document.getElementById('equityChart'), {{
  type: 'line',
  data: {{ labels: eqLabels, datasets: [{{ data: eq, borderColor: isPos ? '#3ecf8e' : '#f87171', backgroundColor: isPos ? 'rgba(62,207,142,0.05)' : 'rgba(248,113,113,0.05)', borderWidth: 1.5, fill: true, tension: 0.3, pointRadius: 0 }}] }},
  options: {{ ...chartDefaults, scales: {{ x: {{ display: false }}, y: {{ ticks: {{ color: '#444', callback: v => '$'+v.toFixed(0) }}, grid: {{ color: 'rgba(255,255,255,0.03)' }}, border: {{ color: '#2a2a2a' }} }} }} }}
}});

const mLabels = {json.dumps(monthly_labels)};
const mPnl    = {json.dumps(monthly_pnl)};
const mColors = {json.dumps(monthly_colors)};

new Chart(document.getElementById('monthlyChart'), {{
  type: 'bar',
  data: {{ labels: mLabels, datasets: [{{ data: mPnl, backgroundColor: mColors, borderRadius: 4 }}] }},
  options: {{ ...chartDefaults, scales: {{ x: {{ ticks: {{ color: '#444', font: {{ size: 10 }} }}, grid: {{ display: false }}, border: {{ color: '#2a2a2a' }} }}, y: {{ ticks: {{ color: '#444', callback: v => '$'+v.toFixed(0) }}, grid: {{ color: 'rgba(255,255,255,0.03)' }}, border: {{ color: '#2a2a2a' }} }} }} }}
}});

const hLabels = {json.dumps(hourly_labels)};
const hWr     = {json.dumps(hourly_wr)};
const hCounts = {json.dumps(hourly_counts)};
const hColors = hWr.map(w => w >= 60 ? 'rgba(62,207,142,0.7)' : w >= 40 ? 'rgba(212,168,67,0.7)' : w > 0 ? 'rgba(248,113,113,0.7)' : '#222');

new Chart(document.getElementById('hourChart'), {{
  type: 'bar',
  data: {{ labels: hLabels, datasets: [{{ data: hWr, backgroundColor: hColors, borderRadius: 3 }}] }},
  options: {{ ...chartDefaults, scales: {{ x: {{ ticks: {{ color: '#444', font: {{ size: 9 }}, maxRotation: 45 }}, grid: {{ display: false }}, border: {{ color: '#2a2a2a' }} }}, y: {{ min: 0, max: 100, ticks: {{ color: '#444', callback: v => v+'%' }}, grid: {{ color: 'rgba(255,255,255,0.03)' }}, border: {{ color: '#2a2a2a' }} }} }} }}
}});
</script>
</body>
</html>"""

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"Report saved to: {output_path}")
    return output_path