"""
utils/mt5_connection.py
Handles connecting and disconnecting from MT5 terminal.
"""

import MetaTrader5 as mt5
from utils.logger import setup_logger
from config.settings import MT5_LOGIN, MT5_PASSWORD, MT5_SERVER, SYMBOL

logger = setup_logger("mt5_connection")


def connect_mt5() -> bool:
    """
    Initialize connection to MT5 terminal.
    Returns True if successful, False otherwise.
    """
    # Build kwargs — only pass credentials if set in config
    kwargs = {}
    if MT5_LOGIN:    kwargs["login"]    = MT5_LOGIN
    if MT5_PASSWORD: kwargs["password"] = MT5_PASSWORD
    if MT5_SERVER:   kwargs["server"]   = MT5_SERVER

    if not mt5.initialize(**kwargs):
        logger.error(f"MT5 initialize() failed: {mt5.last_error()}")
        return False

    # Make sure the symbol is available
    if not mt5.symbol_select(SYMBOL, True):
        logger.error(f"Symbol {SYMBOL} not found in Market Watch — MT5 error: {mt5.last_error()}")
        logger.error(f"Check symbol name exactly (e.g. XAUUSD vs XAUUSD.a vs XAUUSDm)")
        mt5.shutdown()
        return False

    # Verify symbol actually returns data (catches name mismatches and permission issues)
    sym = mt5.symbol_info(SYMBOL)
    if sym is None:
        logger.error(f"symbol_info({SYMBOL}) returned None after select — MT5 error: {mt5.last_error()}")
        logger.error(f"Possible causes: symbol name mismatch, not in Market Watch, broker permissions")
        mt5.shutdown()
        return False

    info = mt5.terminal_info()
    account = mt5.account_info()
    logger.info(f"Connected to: {info.name}")
    logger.info(f"Account: {account.login} | Balance: {account.balance} {account.currency}")
    logger.info(f"Server: {account.server}")
    logger.info(f"Symbol: {SYMBOL} | Digits={sym.digits} | Point={sym.point} | Spread={sym.spread}pts")

    return True


def disconnect_mt5():
    """Cleanly disconnect from MT5."""
    mt5.shutdown()
    logger.info("MT5 connection closed.")
