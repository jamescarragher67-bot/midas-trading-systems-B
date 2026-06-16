"""
utils/data_fetcher.py
Fetches OHLCV candle data from MT5 and returns a pandas DataFrame.
"""

import MetaTrader5 as mt5
import pandas as pd
from utils.logger import setup_logger
from config.settings import SYMBOL, SIGNAL_TIMEFRAME

logger = setup_logger("data_fetcher")

# Map minute integer to MT5 timeframe constant
TIMEFRAME_MAP = {
    1:  mt5.TIMEFRAME_M1,
    5:  mt5.TIMEFRAME_M5,
    15: mt5.TIMEFRAME_M15,
    30: mt5.TIMEFRAME_M30,
    60: mt5.TIMEFRAME_H1,
}


def get_candles(symbol: str = SYMBOL, timeframe: int = SIGNAL_TIMEFRAME, count: int = 200) -> pd.DataFrame:
    """
    Fetch the last `count` candles for `symbol` on `timeframe`.
    Returns a DataFrame with columns: time, open, high, low, close, tick_volume.
    Returns empty DataFrame on failure.
    """
    tf = TIMEFRAME_MAP.get(timeframe)
    if tf is None:
        logger.error(f"Unknown timeframe: {timeframe}")
        return pd.DataFrame()

    rates = mt5.copy_rates_from_pos(symbol, tf, 0, count)

    if rates is None or len(rates) == 0:
        logger.error(f"No data returned for {symbol} TF={timeframe}. Error: {mt5.last_error()}")
        return pd.DataFrame()

    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    df.set_index("time", inplace=True)

    return df
