"""
tools/preflight_check.py  —  MIDAS Pre-launch Go/No-Go Checker

Run this BEFORE launching main.py on launch day:
    python tools/preflight_check.py

Every check prints PASS, WARN, or FAIL.
  PASS  = green, no action needed
  WARN  = review carefully (acceptable on demo, must be resolved on launch day)
  FAIL  = stop and fix before touching live capital

Launch day credential swap — 3-line edit in config/.env:
    MT5_LOGIN=<live account number>
    MT5_PASSWORD=<live password>
    MT5_SERVER=Pepperstone-Live  (or Pepperstone-Edge-Live — confirm with broker)
"""

import sys
import os
import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv
load_dotenv(ROOT / "config" / ".env")

import requests
import MetaTrader5 as mt5

# ── Constants ────────────────────────────────────────────────────────────────
DEMO_LOGIN     = 108470975
DEMO_SERVER    = "MetaQuotes-Demo"
SYMBOL         = "XAUUSD.a"
SPREAD_MAX_PTS = 20

JASONS_DIR  = ROOT / "jasons"
JSON_FILES  = [
    "trades.json",
    "seen_tickets.json",
    "heartbeat.json",
    "open_positions.json",
]

FIREBASE_URL = os.getenv("FIREBASE_DB_URL", "")
WA_PHONE     = os.getenv("WHATSAPP_PHONE", "")
WA_KEY       = os.getenv("CALLMEBOT_API_KEY", "")

# ── Result tracking ───────────────────────────────────────────────────────────
_results = []


def result(status: str, label: str, detail: str = ""):
    _results.append((status, label, detail))
    suffix = f" -- {detail}" if detail else ""
    print(f"  [{status:<4}] {label}{suffix}")


def passed(label, detail=""):
    result("PASS", label, detail)
    return True


def failed(label, detail=""):
    result("FAIL", label, detail)
    return False


def warned(label, detail=""):
    result("WARN", label, detail)
    return True


# ─────────────────────────────────────────────────────────────────────────────
# CHECK 1 — MT5 connection
# ─────────────────────────────────────────────────────────────────────────────
def check_mt5_connection():
    try:
        login    = int(os.getenv("MT5_LOGIN", "0"))
        password = os.getenv("MT5_PASSWORD", "")
        server   = os.getenv("MT5_SERVER", "")
        kwargs   = {}
        if login:    kwargs["login"]    = login
        if password: kwargs["password"] = password
        if server:   kwargs["server"]   = server
        if mt5.initialize(**kwargs):
            passed("MT5 connection")
            return True
        failed("MT5 connection", f"{mt5.last_error()}")
        return False
    except Exception as e:
        failed("MT5 connection", str(e))
        return False


# ─────────────────────────────────────────────────────────────────────────────
# CHECK 2 — Account number / mode
# ─────────────────────────────────────────────────────────────────────────────
def check_account():
    acct = mt5.account_info()
    if not acct:
        failed("Account info", "mt5.account_info() returned None")
        return False

    login  = acct.login
    server = acct.server
    mode   = "DEMO" if acct.trade_mode == 0 else "LIVE"
    bal    = acct.balance

    detail = f"#{login} | {server} | {mode} | Balance ${bal:,.2f}"

    if login == DEMO_LOGIN or server == DEMO_SERVER:
        warned("Account: DEMO credentials active", detail)
        warned("  Launch day action", "Update MT5_LOGIN / MT5_PASSWORD / MT5_SERVER in config/.env")
    else:
        passed(f"Account: LIVE credentials", detail)

    return True


# ─────────────────────────────────────────────────────────────────────────────
# CHECK 3 — XAUUSD symbol info
# ─────────────────────────────────────────────────────────────────────────────
def check_symbol():
    mt5.symbol_select(SYMBOL, True)
    info = mt5.symbol_info(SYMBOL)
    if info is None:
        failed(f"{SYMBOL} symbol info", "symbol_info() returned None")
        return False
    if not info.visible:
        warned(f"{SYMBOL} not in Market Watch", "Will be added automatically by the bot")
    detail = (f"digits={info.digits} | point={info.point} | "
              f"vol_min={info.volume_min} | contract={info.trade_contract_size}")
    passed(f"{SYMBOL} symbol info", detail)
    return True


# ─────────────────────────────────────────────────────────────────────────────
# CHECK 4 — Current spread
# ─────────────────────────────────────────────────────────────────────────────
def check_spread():
    tick = mt5.symbol_info_tick(SYMBOL)
    info = mt5.symbol_info(SYMBOL)
    if not tick or not info:
        failed("Spread check", "Could not get tick data")
        return False
    spread_pts = round((tick.ask - tick.bid) / info.point, 1)
    detail = f"{spread_pts:.0f}pts (limit {SPREAD_MAX_PTS}pts) | bid={tick.bid} ask={tick.ask}"
    if spread_pts <= SPREAD_MAX_PTS:
        passed("Spread", detail)
        return True
    warned("Spread elevated", detail + " -- avoid trading until spread narrows")
    return True


# ─────────────────────────────────────────────────────────────────────────────
# CHECK 5 — WhatsApp ping
# ─────────────────────────────────────────────────────────────────────────────
def check_whatsapp():
    if not WA_PHONE or not WA_KEY:
        warned("WhatsApp", "WHATSAPP_PHONE or CALLMEBOT_API_KEY missing in config/.env")
        return False
    try:
        from urllib.parse import quote
        msg  = quote("MIDAS PREFLIGHT CHECK -- WhatsApp OK")
        url  = (f"https://api.callmebot.com/whatsapp.php"
                f"?phone={WA_PHONE}&text={msg}&apikey={WA_KEY}")
        resp = requests.get(url, timeout=10)
        if resp.ok:
            passed("WhatsApp ping", f"Sent to {WA_PHONE}")
            return True
        failed("WhatsApp ping", f"HTTP {resp.status_code}: {resp.text[:80]}")
        return False
    except Exception as e:
        failed("WhatsApp ping", str(e))
        return False


# ─────────────────────────────────────────────────────────────────────────────
# CHECK 6 — Firebase write
# ─────────────────────────────────────────────────────────────────────────────
def check_firebase():
    if not FIREBASE_URL:
        failed("Firebase", "FIREBASE_DB_URL missing in config/.env")
        return False
    try:
        test_path = f"{FIREBASE_URL}/preflight_test.json"
        payload   = {"ts": time.time(), "status": "preflight_ok"}
        put_resp  = requests.put(test_path, json=payload, timeout=10)
        if not put_resp.ok:
            failed("Firebase write", f"PUT HTTP {put_resp.status_code}")
            return False
        # Clean up the test node
        requests.delete(test_path, timeout=5)
        passed("Firebase write", f"PUT + DELETE to {FIREBASE_URL.split('//')[1].split('.')[0]}")
        return True
    except Exception as e:
        failed("Firebase write", str(e))
        return False


# ─────────────────────────────────────────────────────────────────────────────
# CHECK 7 — JSON files (jasons/ folder)
# ─────────────────────────────────────────────────────────────────────────────
def check_json_files():
    all_ok = True
    for fname in JSON_FILES:
        fpath = JASONS_DIR / fname
        if not fpath.exists():
            passed(f"  {fname}", "not found (clean start -- OK)")
            continue
        try:
            with open(fpath, encoding="utf-8") as f:
                data = json.load(f)
            size = fpath.stat().st_size
            if isinstance(data, list):
                passed(f"  {fname}", f"valid JSON list ({len(data)} entries, {size}B)")
            elif isinstance(data, dict):
                passed(f"  {fname}", f"valid JSON object ({len(data)} keys, {size}B)")
            else:
                passed(f"  {fname}", f"valid JSON ({size}B)")
        except json.JSONDecodeError as e:
            failed(f"  {fname}", f"CORRUPTED: {e}")
            all_ok = False
        except Exception as e:
            failed(f"  {fname}", str(e))
            all_ok = False
    return all_ok


# ─────────────────────────────────────────────────────────────────────────────
# CHECK 8 — Circuit breaker state
# ─────────────────────────────────────────────────────────────────────────────
def check_circuit_breaker():
    try:
        from risk.circuit_breaker import circuit_breaker
        if circuit_breaker.is_tripped():
            failed("Circuit breaker",
                   f"TRIPPED: {circuit_breaker._trip_reason} -- "
                   "will auto-reset at midnight UTC")
            return False
        losses = circuit_breaker._consecutive_losses
        daily  = circuit_breaker._daily_loss
        passed("Circuit breaker",
               f"clean | {losses} consec losses | ${daily:.2f} daily loss")
        return True
    except Exception as e:
        failed("Circuit breaker", str(e))
        return False


# ─────────────────────────────────────────────────────────────────────────────
# CHECK 9 — Dashboard HTML hardcoded account (informational)
# ─────────────────────────────────────────────────────────────────────────────
def check_dashboard_html():
    html_path = ROOT / "dashboard" / "midas_dashboard_local.html"
    if not html_path.exists():
        warned("Dashboard HTML", "File not found")
        return
    content = html_path.read_text(encoding="utf-8", errors="replace")
    if str(DEMO_LOGIN) in content:
        warned("Dashboard HTML has hardcoded demo account",
               f"Update line containing '{DEMO_LOGIN}' in dashboard/midas_dashboard_local.html "
               "after credential swap")
    else:
        passed("Dashboard HTML", "No hardcoded demo account found")


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────
def main():
    print()
    print("=" * 72)
    print("   MIDAS PREFLIGHT CHECK")
    print("=" * 72)
    print()

    mt5_ok = check_mt5_connection()

    if mt5_ok:
        check_account()
        check_symbol()
        check_spread()

    print()
    check_whatsapp()
    check_firebase()

    print()
    print("  JSON files (jasons/):")
    check_json_files()

    print()
    check_circuit_breaker()

    print()
    check_dashboard_html()

    if mt5_ok:
        mt5.shutdown()

    # ── Summary ───────────────────────────────────────────────────────────────
    fails  = [r for r in _results if r[0] == "FAIL"]
    warns  = [r for r in _results if r[0] == "WARN"]
    passes = [r for r in _results if r[0] == "PASS"]

    print()
    print("=" * 72)
    print(f"  Results: {len(passes)} PASS  |  {len(warns)} WARN  |  {len(fails)} FAIL")
    print()

    if fails:
        print("  GO / NO-GO: NO-GO")
        print()
        print("  FAILs to fix:")
        for _, label, detail in fails:
            print(f"    - {label}" + (f": {detail}" if detail else ""))
    elif warns:
        demo_warn = any("DEMO" in label or "demo" in detail.lower()
                        for _, label, detail in warns)
        if demo_warn:
            print("  GO / NO-GO: DEMO ONLY")
            print("  All systems functional. Switch credentials before live launch.")
        else:
            print("  GO / NO-GO: GO (with warnings noted above)")
    else:
        print("  GO / NO-GO: GO -- all systems green")

    print("=" * 72)
    print()

    # Exit code: 0 = pass/warn, 1 = any fail
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
