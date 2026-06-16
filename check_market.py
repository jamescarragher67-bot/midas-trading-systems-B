import MetaTrader5 as mt5
import pandas as pd

mt5.initialize()
rates = mt5.copy_rates_from_pos("XAUUSD", mt5.TIMEFRAME_M5, 0, 3)
if rates is None:
    print("No data - market likely closed")
else:
    df = pd.DataFrame(rates)
    df['time'] = pd.to_datetime(df['time'], unit='s')
    print(df[['time','open','high','low','close']])
mt5.shutdown()