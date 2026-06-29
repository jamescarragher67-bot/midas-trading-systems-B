"""
utils/logger.py
Centralised logging — logs to both console and a daily log file.
"""

import io
import logging
import os
import sys
from datetime import datetime
from config.settings import LOG_LEVEL, LOG_DIR


def setup_logger(name: str) -> logging.Logger:
    """
    Returns a logger that writes to console + logs/YYYY-MM-DD.log
    """
    os.makedirs(LOG_DIR, exist_ok=True)

    log_filename = os.path.join(LOG_DIR, f"{datetime.now().strftime('%Y-%m-%d')}.log")

    level = getattr(logging, LOG_LEVEL.upper(), logging.INFO)

    logger = logging.getLogger(name)
    logger.setLevel(level)

    # Avoid adding duplicate handlers if logger already exists
    if logger.handlers:
        return logger

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    # Console handler — wrap stdout in UTF-8 so Unicode chars don't crash on Windows cp1252
    utf8_stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
    console = logging.StreamHandler(utf8_stdout)
    console.setLevel(level)
    console.setFormatter(formatter)

    # File handler
    file_handler = logging.FileHandler(log_filename, encoding="utf-8")
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)

    logger.addHandler(console)
    logger.addHandler(file_handler)

    return logger
