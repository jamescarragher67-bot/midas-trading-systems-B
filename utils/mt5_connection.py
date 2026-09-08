"""
utils/mt5_connection.py
Handles connecting and disconnecting from MT5 terminal.
"""

import MetaTrader5 as mt5
from utils.logger import setup_logger

logger = setup_logger("mt5_connection")


def connect_mt5(login=None, password=None, server=None, symbol=None) -> bool:
    """
    Initialize connection to MT5 terminal.

    Defaults to config/settings.py's production credentials/symbol, so
    every existing caller (main.py's bare connect_mt5()) is unaffected.
    Pass login/password/server/symbol explicitly to connect to a different
    account instead - e.g. a diagnostic runner using
    config/settings_diagnostic.py's MT5_LOGIN/MT5_PASSWORD/MT5_SERVER/SYMBOL.

    config/settings.py is imported LAZILY, only if a default is actually
    needed (i.e. some argument was left None) - NOT at module load time.
    config/settings.py fails fast (SystemExit) if its own MT5_LOGIN isn't
    set, and this module must stay importable even when only the
    diagnostic config's credentials are configured; a top-level import
    here would force production's config to load as a side effect of
    merely importing this module, breaking diagnostic-only setups.

    Returns True if successful, False otherwise.
    """
    if login is None or password is None or server is None or symbol is None:
        from config.settings import MT5_LOGIN, MT5_PASSWORD, MT5_SERVER, SYMBOL
        login    = MT5_LOGIN    if login    is None else login
        password = MT5_PASSWORD if password is None else password
        server   = MT5_SERVER   if server   is None else server
        symbol   = SYMBOL       if symbol   is None else symbol

    # Build kwargs — only pass credentials if set
    kwargs = {}
    if login:    kwargs["login"]    = login
    if password: kwargs["password"] = password
    if server:   kwargs["server"]   = server

    if not mt5.initialize(**kwargs):
        logger.error(f"MT5 initialize() failed: {mt5.last_error()}")
        return False

    # Make sure the symbol is available
    if not mt5.symbol_select(symbol, True):
        logger.error(f"Symbol {symbol} not found in Market Watch — MT5 error: {mt5.last_error()}")
        logger.error(f"Check symbol name exactly (e.g. XAUUSD vs XAUUSD.a vs XAUUSDm)")
        mt5.shutdown()
        return False

    # Verify symbol actually returns data (catches name mismatches and permission issues)
    sym = mt5.symbol_info(symbol)
    if sym is None:
        logger.error(f"symbol_info({symbol}) returned None after select — MT5 error: {mt5.last_error()}")
        logger.error(f"Possible causes: symbol name mismatch, not in Market Watch, broker permissions")
        mt5.shutdown()
        return False

    info = mt5.terminal_info()
    account = mt5.account_info()
    logger.info(f"Connected to: {info.name}")
    logger.info(f"Account: {account.login} | Balance: {account.balance} {account.currency}")
    logger.info(f"Server: {account.server}")
    logger.info(f"Symbol: {symbol} | Digits={sym.digits} | Point={sym.point} | Spread={sym.spread}pts")

    return True


def disconnect_mt5():
    """Cleanly disconnect from MT5."""
    mt5.shutdown()
    logger.info("MT5 connection closed.")
