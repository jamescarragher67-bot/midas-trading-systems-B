"""
utils/logger.py
Centralised logging — console + a per-process rotating log file.

File: logs/<process>.log, where <process> is the entry script's stem
(main, trade_sync, firebase_push, preflight_check, ...). Each process owns
its own file so the midnight rotation never has to rename a file another
process still holds open — Windows refuses that rename, and the handler
would then retry (and print a traceback) on every subsequent log line.
Rotates at midnight UTC and keeps ROTATION_BACKUP_DAYS days, so disk use is
bounded on a box that runs unattended for months.

The console + file handlers are built ONCE per process and shared by every
named logger, so exactly one file handle does the rotating (one handle per
module would hit the same Windows rename problem within a single process).

Hardcoded rather than imported from config.settings: both config/settings.py
and config/settings_diagnostic.py set these to the same values anyway, and
EVERY module calls setup_logger() - importing config.settings here would
force it to load (and fail fast on a missing production MT5_LOGIN) as a
side effect of just logging something, breaking diagnostic-only setups.
"""

import io
import logging
import logging.handlers
import os
import sys
from pathlib import Path

LOG_LEVEL            = "INFO"
LOG_DIR              = "logs"
ROTATION_BACKUP_DAYS = 30

_handlers: list[logging.Handler] | None = None


def _process_name() -> str:
    """Stem of the running script (main.py -> 'main'); 'midas' for stdin / -c / unknown."""
    try:
        stem = Path(sys.argv[0]).stem
    except Exception:
        stem = ""
    return stem if stem and not stem.startswith("-") else "midas"


def _build_handlers(level: int) -> list[logging.Handler]:
    os.makedirs(LOG_DIR, exist_ok=True)

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    handlers: list[logging.Handler] = []

    # Console handler — wrap stdout in UTF-8 so Unicode chars don't crash on
    # Windows cp1252. A headless run (pythonw.exe, a scheduled task with no
    # console) has NO stdout at all: skip the console handler rather than
    # crash at import time; the file handler below still captures everything.
    if sys.stdout is not None and hasattr(sys.stdout, "buffer"):
        utf8_stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                       errors="replace", line_buffering=True)
        console = logging.StreamHandler(utf8_stdout)
        console.setLevel(level)
        console.setFormatter(formatter)
        handlers.append(console)

    # File handler — logs/<process>.log, rotated at midnight UTC to
    # logs/<process>.log.YYYY-MM-DD, oldest deleted past ROTATION_BACKUP_DAYS.
    file_handler = logging.handlers.TimedRotatingFileHandler(
        os.path.join(LOG_DIR, f"{_process_name()}.log"),
        when="midnight", utc=True, backupCount=ROTATION_BACKUP_DAYS, encoding="utf-8",
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)
    handlers.append(file_handler)

    return handlers


def setup_logger(name: str) -> logging.Logger:
    """
    Returns a logger that writes to console + logs/<process>.log (rotating).
    """
    global _handlers

    level  = getattr(logging, LOG_LEVEL.upper(), logging.INFO)
    logger = logging.getLogger(name)
    logger.setLevel(level)

    # Avoid adding duplicate handlers if logger already exists
    if logger.handlers:
        return logger

    if _handlers is None:
        _handlers = _build_handlers(level)
    for handler in _handlers:
        logger.addHandler(handler)

    return logger
