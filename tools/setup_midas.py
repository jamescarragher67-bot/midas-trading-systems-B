"""
MIDAS TRADING SYSTEMS — MACHINE SETUP WIZARD
Run this once on any new machine before first launch.

What this does:
1. Checks Python + dependencies
2. Reads existing config/.env — skips prompting for anything already set
3. Prompts only for missing credentials
4. Validates each value live (MT5 login, WhatsApp, Firebase)
5. Writes / updates config/.env
"""

import os
import sys
import subprocess
import getpass
from pathlib import Path

ENV_PATH = Path(__file__).resolve().parent.parent / "config" / ".env"
REQUIRED_PY_VERSION = (3, 10)

BANNER = """
==========================================================
   MIDAS TRADING SYSTEMS — MACHINE SETUP WIZARD
==========================================================
Reads existing config/.env and skips anything already set.
Only prompts for missing credentials.
==========================================================
"""


# ── Helpers ───────────────────────────────────────────────────────────────────

def load_existing_env() -> dict:
    """Parse config/.env and return key→value dict. Returns {} if file missing."""
    values = {}
    if not ENV_PATH.exists():
        return values
    with open(ENV_PATH) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            values[key.strip()] = val.strip()
    return values


def getpass_safe(prompt_text: str) -> str:
    """
    Try hidden input via getpass. Fall back to visible input() if the terminal
    doesn't support it (VS Code integrated terminal, certain CI environments).
    """
    try:
        value = getpass.getpass(prompt_text)
        if value is not None:
            return value.strip()
    except Exception:
        pass
    # Fallback
    print("  [WARN] This terminal doesn't support hidden input — password will be visible.")
    return input(prompt_text).strip()


def prompt_for(label: str, key: str, existing: dict, secret: bool = False, example: str = "") -> str:
    """
    Return existing[key] if present and non-empty, otherwise prompt interactively.
    Prints clearly what's being asked and why.
    """
    if existing.get(key):
        masked = "****" if secret else existing[key]
        print(f"  [SKIP] {label} already set ({masked}) — using existing value")
        return existing[key]

    suffix = f"  (e.g. {example})" if example else ""
    print(f"\n  {label}{suffix}")
    while True:
        try:
            if secret:
                value = getpass_safe(f"  Enter {label}: ")
            else:
                value = input(f"  Enter {label}: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\n[ABORT] Setup cancelled.")
            sys.exit(1)

        if value:
            return value
        print("  -> Cannot be empty, try again")


# ── Step functions ────────────────────────────────────────────────────────────

def check_python_version():
    if sys.version_info < REQUIRED_PY_VERSION:
        print(f"[FAIL] Python {REQUIRED_PY_VERSION[0]}.{REQUIRED_PY_VERSION[1]}+ required. "
              f"Found {sys.version_info[0]}.{sys.version_info[1]}")
        sys.exit(1)
    print(f"[OK] Python {sys.version_info[0]}.{sys.version_info[1]}")


def install_requirements():
    req_file = Path(__file__).resolve().parent.parent / "requirements.txt"
    if not req_file.exists():
        print("[WARN] requirements.txt not found — skipping")
        return
    print("\nInstalling / verifying dependencies...")
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-r", str(req_file), "--quiet"],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print("[FAIL] Dependency install failed:")
        print(result.stderr)
        sys.exit(1)
    print("[OK] Dependencies ready")


def collect_mt5(existing: dict) -> tuple[str, str, str]:
    print("\n--- MT5 CREDENTIALS ---")
    print("Used to log into your MetaTrader 5 demo/live account.")
    login    = prompt_for("MT5 account number",  "MT5_LOGIN",    existing, example="5051564659")
    password = prompt_for("MT5 account password","MT5_PASSWORD", existing, secret=True)
    server   = prompt_for("MT5 server name",     "MT5_SERVER",   existing, example="MetaQuotes-Demo")
    return login, password, server


def test_mt5(login: str, password: str, server: str) -> bool:
    print("\nTesting MT5 connection...")
    try:
        import MetaTrader5 as mt5
    except ImportError:
        print("[WARN] MetaTrader5 package not installed — skipping live test")
        return True

    if not mt5.initialize():
        print(f"[FAIL] MT5 terminal failed to start: {mt5.last_error()}")
        return False

    ok = mt5.login(int(login), password=password, server=server)
    if not ok:
        print(f"[FAIL] MT5 login failed: {mt5.last_error()}")
        mt5.shutdown()
        return False

    info = mt5.account_info()
    print(f"[OK] Connected — Balance: {info.balance} {info.currency} | "
          f"{'DEMO' if info.trade_mode == 0 else 'LIVE'}")
    mt5.shutdown()
    return True


def collect_whatsapp(existing: dict) -> tuple[str, str]:
    print("\n--- WHATSAPP ALERTS ---")
    print("Used to send trade open/close alerts to your WhatsApp via CallMeBot.")
    print("Get your API key free at: https://www.callmebot.com/blog/free-api-whatsapp-messages/")
    phone   = prompt_for("WhatsApp number (international format)", "WHATSAPP_PHONE",    existing, example="+447911123456")
    api_key = prompt_for("CallMeBot API key",                      "CALLMEBOT_API_KEY", existing, example="7114270")
    return phone, api_key


def test_whatsapp(phone: str, api_key: str) -> bool:
    print("\nSending test WhatsApp message...")
    try:
        import requests
        msg = "Midas setup wizard: connection confirmed. Bot is ready."
        url = (f"https://api.callmebot.com/whatsapp.php"
               f"?phone={phone}&text={msg}&apikey={api_key}")
        resp = requests.get(url, timeout=15)
        if resp.status_code == 200:
            print("[OK] Test message sent — check your WhatsApp now")
            return True
        print(f"[FAIL] CallMeBot returned {resp.status_code}: {resp.text[:200]}")
        return False
    except Exception as e:
        print(f"[FAIL] Could not reach CallMeBot: {e}")
        return False


def collect_firebase(existing: dict) -> str:
    print("\n--- FIREBASE (optional) ---")
    print("Used to push live trade data to Firebase Realtime Database.")
    print("Leave blank and press Enter to skip Firebase setup.")

    existing_url = existing.get("FIREBASE_DB_URL", "")
    if existing_url:
        print(f"  [SKIP] FIREBASE_DB_URL already set — using existing value")
        return existing_url

    print("\n  Firebase Realtime Database URL  (e.g. https://midas-xxxx.firebaseio.com)")
    try:
        url = input("  Enter URL (or press Enter to skip): ").strip()
    except (KeyboardInterrupt, EOFError):
        return ""
    return url


def test_firebase(url: str) -> bool:
    if not url:
        print("[SKIP] Firebase not configured")
        return True
    print("\nTesting Firebase write access...")
    try:
        import requests
        test_url = f"{url}/setup_test.json"
        resp = requests.put(test_url, json={"status": "setup_ok"}, timeout=10)
        if resp.status_code == 200:
            print("[OK] Firebase write successful")
            requests.delete(test_url, timeout=10)
            return True
        print(f"[FAIL] Firebase returned {resp.status_code}: {resp.text[:200]}")
        return False
    except Exception as e:
        print(f"[FAIL] Could not reach Firebase: {e}")
        return False


def write_env(values: dict):
    ENV_PATH.parent.mkdir(parents=True, exist_ok=True)

    if ENV_PATH.exists():
        backup = ENV_PATH.parent / ".env.bak"
        import shutil
        shutil.copy2(ENV_PATH, backup)
        print(f"\n[INFO] Existing .env backed up to {backup.name}")

    with open(ENV_PATH, "w") as f:
        f.write("# MIDAS TRADING SYSTEMS — machine-specific secrets\n")
        f.write("# Generated by setup_midas.py — DO NOT commit to Git\n\n")
        for key, val in values.items():
            if val:
                f.write(f"{key}={val}\n")

    try:
        os.chmod(ENV_PATH, 0o600)
    except Exception:
        pass  # chmod not supported on Windows — harmless
    print(f"[OK] Wrote {ENV_PATH}")


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    print(BANNER)
    check_python_version()
    install_requirements()

    existing = load_existing_env()
    if existing:
        print(f"\n[INFO] Found existing config/.env with {len(existing)} key(s) — "
              f"will skip anything already set.\n")

    try:
        login, password, server = collect_mt5(existing)
        mt5_ok = test_mt5(login, password, server)

        phone, api_key = collect_whatsapp(existing)
        wa_ok = test_whatsapp(phone, api_key)

        fb_url = collect_firebase(existing)
        fb_ok  = test_firebase(fb_url)

    except (KeyboardInterrupt, EOFError):
        print("\n[ABORT] Setup cancelled by user.")
        sys.exit(1)
    except Exception as e:
        print(f"\n[ERROR] Unexpected error during credential collection: {e}")
        sys.exit(1)

    # Merge with existing values so we don't lose keys the wizard didn't touch
    merged = dict(existing)
    merged.update({
        "MT5_LOGIN":          login,
        "MT5_PASSWORD":       password,
        "MT5_SERVER":         server,
        "WHATSAPP_PHONE":     phone,
        "CALLMEBOT_API_KEY":  api_key,
        "FIREBASE_DB_URL":    fb_url,
    })
    write_env(merged)

    status = "COMPLETE" if (mt5_ok and wa_ok and fb_ok) else "COMPLETE WITH WARNINGS"
    print(f"""
==========================================================
   SETUP {status}
==========================================================
   MT5 connection:  {'OK' if mt5_ok else 'FAILED — check before launch'}
   WhatsApp alerts: {'OK' if wa_ok else 'FAILED — check before launch'}
   Firebase sync:   {'OK' if fb_ok else 'FAILED or skipped'}
==========================================================

Next step: python dashboard/midas_launcher.py
""")


if __name__ == "__main__":
    main()
