"""
tools/preflight_check.py  —  MIDAS Pre-launch Go/No-Go Checker

Run this BEFORE launching main.py on launch day:
    python tools/preflight_check.py                    # checks config/settings.py (production, $50K)
    python tools/preflight_check.py --config diagnostic  # checks config/settings_diagnostic.py ($5K FundedNext test)

Every check prints PASS, WARN, or FAIL.
  PASS  = green, no action needed
  WARN  = review carefully (acceptable on demo, must be resolved on launch day)
  FAIL  = stop and fix before touching live capital

Reads MT5 login/password/server/symbol/spread-limit from whichever config
module is selected, instead of hardcoding one account's env var names or
one symbol - a diagnostic-account run and a production-account run need
different credentials (MT5_LOGIN vs MT5_DIAGNOSTIC_LOGIN, see
config/.env.example) and this must check whichever one is actually active,
not assume production.

Launch day credential swap — edit in config/.env:
    MT5_LOGIN=<live account number>
    MT5_PASSWORD=<live password>
    MT5_SERVER=Pepperstone-Live  (or Pepperstone-Edge-Live — confirm with broker)
"""

import argparse
import sys
import os
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import requests
import MetaTrader5 as mt5

_parser = argparse.ArgumentParser(description="MIDAS pre-launch checker")
_parser.add_argument("--config", choices=["production", "diagnostic"], default="production",
                      help="Which config to check: production (config/settings.py, $50K) "
                           "or diagnostic (config/settings_diagnostic.py, $5K FundedNext test).")
_args, _ = _parser.parse_known_args()

if _args.config == "diagnostic":
    try:
        from config import settings_diagnostic as cfg
    except SystemExit as e:
        print(f"\n  Cannot check diagnostic config: {e}\n")
        sys.exit(1)
else:
    try:
        from config import settings as cfg
    except SystemExit as e:
        print(f"\n  Cannot check production config: {e}\n")
        sys.exit(1)

# ── Constants ────────────────────────────────────────────────────────────────
DEMO_LOGIN     = 108470975
DEMO_SERVER    = "MetaQuotes-Demo"
SYMBOL         = cfg.SYMBOL
SPREAD_MAX_PTS = cfg.MAX_SPREAD_POINTS
CFG_LOGIN      = cfg.MT5_LOGIN
CFG_PASSWORD   = cfg.MT5_PASSWORD
CFG_SERVER     = cfg.MT5_SERVER

JASONS_DIR  = ROOT / ("jasons_diagnostic" if _args.config == "diagnostic" else "jasons")
JSON_FILES  = [
    "trades.json",
    "seen_tickets.json",
    "heartbeat.json",
    "open_positions.json",
]

# Diagnostic mode uses FIREBASE_DB_URL_DIAGNOSTIC if set, else falls back to
# FIREBASE_DB_URL (matching sync/firebase_push.py --config diagnostic, which
# additionally namespaces pushes under "diagnostic/" in the fallback case).
if _args.config == "diagnostic":
    FIREBASE_URL = os.getenv("FIREBASE_DB_URL_DIAGNOSTIC") or os.getenv("FIREBASE_DB_URL", "")
else:
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
        kwargs = {}
        if CFG_LOGIN:    kwargs["login"]    = CFG_LOGIN
        if CFG_PASSWORD: kwargs["password"] = CFG_PASSWORD
        if CFG_SERVER:   kwargs["server"]   = CFG_SERVER
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

    # trade_mode 0 = demo per MT5; the login/server match keeps the old MetaQuotes-demo check.
    if acct.trade_mode == 0 or login == DEMO_LOGIN or server == DEMO_SERVER:
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
    """
    Limits come from the selected config module. Trip state is in-memory and
    per-process, so it is always clean at launch — the only thing worth
    checking here is that the breaker is enabled and what its limits are.
    """
    if not cfg.CIRCUIT_BREAKER_ENABLED:
        failed("Circuit breaker", "CIRCUIT_BREAKER_ENABLED is False in the selected config")
        return False
    passed("Circuit breaker",
           f"enabled | {cfg.MAX_CONSECUTIVE_LOSSES} consecutive losses OR "
           f"{cfg.MAX_DAILY_LOSS_PCT}% daily loss | state starts clean per process")
    return True


# ─────────────────────────────────────────────────────────────────────────────
# CHECK 9 — News feed (Finnhub economic calendar)
# ─────────────────────────────────────────────────────────────────────────────
def check_news_feed():
    """
    utils/news_filter.py FAILS CLOSED: a missing key or a dead feed blocks
    every trade, silently, forever. So this is a FAIL, not a WARN.
    Same request shape as the live filter (2h back, 24h ahead, US high-impact).
    """
    key = getattr(cfg, "FINNHUB_API_KEY", None)
    if not key:
        failed("News feed", "FINNHUB_API_KEY missing in config/.env -- bot would fail closed and never trade")
        return False
    now = datetime.now(timezone.utc)
    params = {
        "from":  (now - timedelta(hours=2)).strftime("%Y-%m-%d"),
        "to":    (now + timedelta(hours=24)).strftime("%Y-%m-%d"),
        "token": key,
    }
    try:
        resp = requests.get("https://finnhub.io/api/v1/calendar/economic", params=params, timeout=10)
    except requests.exceptions.RequestException as e:
        failed("News feed", f"network error -- {e}")
        return False
    if resp.status_code != 200:
        failed("News feed", f"HTTP {resp.status_code} -- {resp.text[:80]}")
        return False
    try:
        data = resp.json()
    except ValueError:
        failed("News feed", "response was not JSON")
        return False
    events = data.get("economicCalendar") if isinstance(data, dict) else None
    if not isinstance(events, list):
        failed("News feed", "unexpected response shape (no economicCalendar list)")
        return False
    high_us = [e for e in events
               if str(e.get("impact", "")).lower() == "high" and e.get("country") == "US"]
    passed("News feed", f"{len(high_us)} high-impact US events in next 24h ({len(events)} total)")
    return True


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────
def main():
    print()
    print("=" * 72)
    print(f"   MIDAS PREFLIGHT CHECK — config: {_args.config.upper()}"
          f"{'  ($50K production)' if _args.config == 'production' else '  ($5K FundedNext diagnostic)'}")
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
    check_news_feed()

    print()
    print(f"  JSON files ({JASONS_DIR.name}/):")
    check_json_files()

    print()
    check_circuit_breaker()

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
