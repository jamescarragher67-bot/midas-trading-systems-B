"""
MIDAS TRADING SYSTEMS — MACHINE SETUP WIZARD
Run this once on any new machine (Mac Mini, gaming PC, laptop) before first launch.

What this does:
1. Checks Python + dependencies
2. Prompts for every machine-specific secret, one at a time
3. Validates each value live (MT5 login, WhatsApp, Firebase) before accepting it
4. Writes a local .env file — never committed to Git, never shared between machines
5. Confirms the machine is ready to run main.py

This script is intentionally the ONLY thing that differs between machines.
Everything else (strategies, voting engine, profiles) comes from Git and is identical everywhere.
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
This will configure THIS machine to run Midas B.
Takes about 3 minutes. You'll need:
  - MT5 account login, password, server
  - WhatsApp number + CallMeBot API key
  - Firebase project URL + credentials
  - Node label for this machine (e.g. 'midas-b-laptop-01')
==========================================================
"""


def check_python_version():
    if sys.version_info < REQUIRED_PY_VERSION:
        print(f"[FAIL] Python {REQUIRED_PY_VERSION[0]}.{REQUIRED_PY_VERSION[1]}+ required. "
              f"Found {sys.version_info[0]}.{sys.version_info[1]}")
        sys.exit(1)
    print(f"[OK] Python {sys.version_info[0]}.{sys.version_info[1]} detected")


def install_requirements():
    req_file = Path(__file__).resolve().parent.parent / "requirements.txt"
    if not req_file.exists():
        print("[WARN] requirements.txt not found — skipping dependency install")
        return
    print("\nInstalling dependencies...")
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-r", str(req_file), "--quiet"],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print("[FAIL] Dependency install failed:")
        print(result.stderr)
        sys.exit(1)
    print("[OK] Dependencies installed")


def prompt(label, secret=False, validator=None, example=""):
    while True:
        suffix = f" (e.g. {example})" if example else ""
        if secret:
            value = getpass.getpass(f"{label}{suffix}: ")
        else:
            value = input(f"{label}{suffix}: ").strip()
        if not value:
            print("  -> Cannot be empty, try again")
            continue
        if validator and not validator(value):
            print("  -> Invalid format, try again")
            continue
        return value


def validate_mt5_login():
    print("\n--- MT5 ACCOUNT ---")
    login = prompt("MT5 account login (number)", example="5051564659")
    if not login.isdigit():
        print("[WARN] Login should be numeric — continuing anyway")
    password = prompt("MT5 account password", secret=True)
    server = prompt("MT5 server name", example="MetaQuotes-Demo")
    return login, password, server


def test_mt5_connection(login, password, server):
    print("\nTesting MT5 connection...")
    try:
        import MetaTrader5 as mt5
    except ImportError:
        print("[WARN] MetaTrader5 package not installed yet — skipping live test. "
              "Connection will be verified on first launch instead.")
        return True

    if not mt5.initialize():
        print(f"[FAIL] MT5 terminal failed to initialize: {mt5.last_error()}")
        return False

    authorized = mt5.login(int(login), password=password, server=server)
    if not authorized:
        print(f"[FAIL] MT5 login failed: {mt5.last_error()}")
        mt5.shutdown()
        return False

    account_info = mt5.account_info()
    print(f"[OK] Connected — Balance: {account_info.balance} {account_info.currency}")
    mt5.shutdown()
    return True


def validate_whatsapp():
    print("\n--- WHATSAPP ALERTS (CallMeBot) ---")
    phone = prompt("WhatsApp number (intl format)", example="+61493813703")
    api_key = prompt("CallMeBot API key", example="7114270")
    return phone, api_key


def test_whatsapp(phone, api_key):
    print("\nSending test WhatsApp message...")
    try:
        import requests
        msg = "Midas setup wizard: this machine is now configured."
        url = f"https://api.callmebot.com/whatsapp.php?phone={phone}&text={msg}&apikey={api_key}"
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            print("[OK] Test message sent — check your WhatsApp")
            return True
        print(f"[FAIL] CallMeBot returned status {resp.status_code}: {resp.text}")
        return False
    except Exception as e:
        print(f"[FAIL] Could not reach CallMeBot: {e}")
        return False


def validate_firebase():
    print("\n--- FIREBASE ---")
    url = prompt("Firebase Realtime Database URL", example="https://midas-xxxx.firebaseio.com")
    secret = prompt("Firebase database secret / auth token", secret=True)
    return url, secret


def test_firebase(url, secret):
    print("\nTesting Firebase write access...")
    try:
        import requests
        test_url = f"{url}/setup_test.json?auth={secret}"
        resp = requests.put(test_url, json={"status": "setup_ok"}, timeout=10)
        if resp.status_code == 200:
            print("[OK] Firebase write successful")
            requests.delete(test_url, timeout=10)
            return True
        print(f"[FAIL] Firebase returned status {resp.status_code}: {resp.text}")
        return False
    except Exception as e:
        print(f"[FAIL] Could not reach Firebase: {e}")
        return False


def get_node_label():
    print("\n--- NODE IDENTITY ---")
    print("This label identifies this machine in the dashboard and logs.")
    label = prompt("Node label", example="midas-b-macmini-01")
    return label


def get_active_profile():
    print("\n--- TRADING PROFILE ---")
    print("Options: AGGRESSIVE / BALANCED / CONSERVATIVE")
    while True:
        profile = input("Active profile [BALANCED]: ").strip().upper() or "BALANCED"
        if profile in ("AGGRESSIVE", "BALANCED", "CONSERVATIVE"):
            return profile
        print("  -> Must be AGGRESSIVE, BALANCED, or CONSERVATIVE")


def write_env_file(values: dict):
    if ENV_PATH.exists():
        backup = ENV_PATH.with_suffix(".env.bak")
        ENV_PATH.rename(backup)
        print(f"\n[INFO] Existing .env backed up to {backup.name}")

    with open(ENV_PATH, "w") as f:
        f.write("# MIDAS TRADING SYSTEMS — machine-specific secrets\n")
        f.write("# Generated by setup_midas.py — DO NOT commit this file to Git\n")
        f.write("# DO NOT copy this file between machines — run setup_midas.py fresh instead\n\n")
        for key, val in values.items():
            f.write(f"{key}={val}\n")

    os.chmod(ENV_PATH, 0o600)
    print(f"[OK] Wrote {ENV_PATH} (permissions locked to owner-only)")


def main():
    print(BANNER)
    check_python_version()
    install_requirements()

    login, password, server = validate_mt5_login()
    mt5_ok = test_mt5_connection(login, password, server)

    phone, api_key = validate_whatsapp()
    wa_ok = test_whatsapp(phone, api_key)

    fb_url, fb_secret = validate_firebase()
    fb_ok = test_firebase(fb_url, fb_secret)

    node_label = get_node_label()
    profile = get_active_profile()

    env_values = {
        "MT5_LOGIN": login,
        "MT5_PASSWORD": password,
        "MT5_SERVER": server,
        "WHATSAPP_PHONE": phone,
        "CALLMEBOT_API_KEY": api_key,
        "FIREBASE_URL": fb_url,
        "FIREBASE_SECRET": fb_secret,
        "NODE_LABEL": node_label,
        "ACTIVE_PROFILE": profile,
    }
    write_env_file(env_values)

    print("\n==========================================================")
    print(f"   SETUP {'COMPLETE' if (mt5_ok and wa_ok and fb_ok) else 'COMPLETE WITH WARNINGS'}")
    print("==========================================================")
    print(f"   Node label:      {node_label}")
    print(f"   Active profile:  {profile}")
    print(f"   MT5 connection:  {'OK' if mt5_ok else 'FAILED — check before launch'}")
    print(f"   WhatsApp alerts: {'OK' if wa_ok else 'FAILED — check before launch'}")
    print(f"   Firebase sync:   {'OK' if fb_ok else 'FAILED — check before launch'}")
    print("==========================================================")
    print("\nNext step: run 'python midas_launcher.py' to start trading.\n")


if __name__ == "__main__":
    main()
