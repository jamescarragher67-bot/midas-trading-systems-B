import MetaTrader5 as mt5
from datetime import datetime, timezone

mt5.initialize()

# Get ALL history with no date filter
deals = mt5.history_deals_get(
    datetime(2026, 6, 1, tzinfo=timezone.utc),
    datetime(2026, 6, 30, tzinfo=timezone.utc)
)

if not deals:
    print("No deals found. Error:", mt5.last_error())
else:
    print(f"Found {len(deals)} deals:")
    for d in deals:
        print(f"Ticket={d.ticket} | Magic={d.magic} | Entry={d.entry} | Profit={d.profit} | Time={datetime.fromtimestamp(d.time)}")

mt5.shutdown()