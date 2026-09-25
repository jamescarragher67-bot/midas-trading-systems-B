"""
sync/watchdog.py

Supervises the live processes in PROCESSES and restarts any that exit.
Run THIS on the 24/7 box instead of launching the scripts by hand.

Restart policy — built for unattended operation:
  * No restart cap. A capped supervisor goes permanently silent after a
    few minutes of MT5/network outage, which is the worst failure mode.
  * Exponential back-off between restarts of the same process
    (RESTART_DELAY_MIN doubling up to RESTART_DELAY_MAX), reset once the
    process has stayed up for HEALTHY_AFTER seconds.
  * A WhatsApp alert on every restart, so a crash loop is visible from
    the phone rather than only in logs/watchdog.log.
  * Back-off waits are non-blocking: one process waiting to restart never
    delays supervision of the others.
  * Ctrl-C / termination of the watchdog terminates its children, so a
    watchdog restart can never leave a second main.py trading alongside
    an orphaned first one.

Deliberately does NOT import config.settings: settings.py fails fast on a
broken config/.env, and that is exactly when the watchdog must still be
alive to report main.py exiting.

Launch it with sync/start_watchdog.bat, not a bare "python sync/watchdog.py":
on Windows "python" can resolve to a launcher/shim (Microsoft Store alias,
Python install manager) that spawns the real interpreter as a separate PID -
killing the shim's PID then leaves the real watchdog running (observed
2026-09-15). The .bat asks the interpreter for its own path and runs this
script directly under it. Children are always started with sys.executable,
which is the real interpreter regardless of how the watchdog was launched.
"""

import os
import subprocess
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from utils.logger import setup_logger
from utils.notifications import send_watchdog_restart

log = setup_logger("watchdog")

PROCESSES = {
    "main":          _ROOT / "main.py",
    "trade_sync":    _ROOT / "sync" / "trade_sync.py",
    "firebase_push": _ROOT / "sync" / "firebase_push.py",
}

POLL_SECONDS      = 15     # how often to check the children
RESTART_DELAY_MIN = 10     # seconds before the first restart of a process
RESTART_DELAY_MAX = 600    # back-off ceiling
HEALTHY_AFTER     = 600    # uptime (s) after which the back-off resets


class Supervised:
    def __init__(self, name: str, script: Path):
        self.name        = name
        self.script      = script
        self.proc        = None
        self.started_at  = 0.0
        self.restarts    = 0
        self.delay       = RESTART_DELAY_MIN
        self.restart_due = None   # epoch seconds when the pending restart fires

    def start(self):
        self.proc = subprocess.Popen(
            [sys.executable, str(self.script)],
            cwd=str(_ROOT),
            stdout=None,
            stderr=None,
        )
        self.started_at = time.time()
        log.info(f"{self.name} started (PID {self.proc.pid})")

    def poll(self) -> bool:
        """Check the process once. Returns True if it was restarted on this call."""
        now = time.time()

        if self.proc.poll() is None:
            if self.restarts and now - self.started_at >= HEALTHY_AFTER:
                log.info(f"{self.name} healthy for {HEALTHY_AFTER}s — restart back-off reset")
                self.restarts = 0
                self.delay    = RESTART_DELAY_MIN
            return False

        if self.restart_due is None:
            code   = self.proc.returncode
            uptime = now - self.started_at
            self.restarts += 1
            log.warning(f"{self.name} exited with code {code} after {uptime:.0f}s — "
                        f"restart #{self.restarts} in {self.delay}s")
            send_watchdog_restart(self.name, code, self.restarts, self.delay)
            self.restart_due = now + self.delay
            self.delay       = min(self.delay * 2, RESTART_DELAY_MAX)
            return False

        if now >= self.restart_due:
            self.restart_due = None
            self.start()
            return True
        return False

    def terminate(self):
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
            log.info(f"{self.name} terminated")


def main():
    log.info(f"Watchdog started (PID {os.getpid()}, interpreter {sys.executable}) — "
             "supervising: " + ", ".join(PROCESSES))
    children = [Supervised(name, script) for name, script in PROCESSES.items()]
    for child in children:
        child.start()

    try:
        while True:
            time.sleep(POLL_SECONDS)
            for child in children:
                child.poll()
    except KeyboardInterrupt:
        log.info("Watchdog stopped by user — terminating children")
    finally:
        for child in children:
            child.terminate()


if __name__ == "__main__":
    main()
