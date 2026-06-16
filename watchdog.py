"""
watchdog.py

Monitors main.py and restarts it automatically if it crashes.
Run this instead of main.py directly for 24/7 reliability.

Usage:
    python watchdog.py
"""

import subprocess
import sys
import time
from datetime import datetime

RESTART_DELAY  = 10    # seconds to wait before restarting
MAX_RESTARTS   = 20    # max restarts per session before giving up
LOG_FILE       = "logs/watchdog.log"

import os
os.makedirs("logs", exist_ok=True)


def log(message: str):
    ts  = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"{ts} | WATCHDOG | {message}"
    print(line)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


def start_bot():
    return subprocess.Popen(
        [sys.executable, "main.py"],
        stdout=None,
        stderr=None,
    )


def main():
    log("Watchdog started. Monitoring main.py...")
    restarts = 0

    process = start_bot()
    log(f"Bot started (PID {process.pid})")

    while True:
        time.sleep(15)

        if process.poll() is not None:
            exit_code = process.returncode
            restarts += 1

            if restarts > MAX_RESTARTS:
                log(f"Max restarts ({MAX_RESTARTS}) reached. Watchdog stopping.")
                break

            log(f"Bot exited with code {exit_code}. Restart #{restarts} in {RESTART_DELAY}s...")
            time.sleep(RESTART_DELAY)

            process = start_bot()
            log(f"Bot restarted (PID {process.pid})")
        else:
            pass  # Bot is still running


if __name__ == "__main__":
    main()