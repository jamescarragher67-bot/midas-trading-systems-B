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


def fetch_both_symbols(gold_symbol: str = "XAUUSD", silver_symbol: str = "XAGUSD",
                       timeframe: int = 5, count: int = 100):
    """
    Fetch aligned M5 bars for both symbols simultaneously.
    Returns (gold_df, silver_df) inner-joined on timestamp,
    or (None, None) on failure.
    """
    tf = TIMEFRAME_MAP.get(timeframe, mt5.TIMEFRAME_M5)

    for sym in (gold_symbol, silver_symbol):
        mt5.symbol_select(sym, True)

    g_rates = mt5.copy_rates_from_pos(gold_symbol,  tf, 0, count)
    s_rates = mt5.copy_rates_from_pos(silver_symbol, tf, 0, count)

    if g_rates is None or len(g_rates) == 0:
        logger.error(f"No data for {gold_symbol}")
        return None, None
    if s_rates is None or len(s_rates) == 0:
        logger.error(f"No data for {silver_symbol}")
        return None, None

    def _to_df(rates):
        d = pd.DataFrame(rates)
        d["time"] = pd.to_datetime(d["time"], unit="s")
        d.set_index("time", inplace=True)
        return d

    df_g = _to_df(g_rates)
    df_s = _to_df(s_rates)

    common = df_g.index.intersection(df_s.index)
    if len(common) < 10:
        logger.error(f"Insufficient aligned bars: {len(common)}")
        return None, None

    return df_g.loc[common], df_s.loc[common]
