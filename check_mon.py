import sys
import os
import json
from datetime import datetime, timezone

sys.stdout.reconfigure(encoding='utf-8')

from binance_futures_trader import BinanceFuturesTrader

t = BinanceFuturesTrader()
print("=== FETCHING MONUSDT TRADES FROM BINANCE API ===")

trades = t.get_user_trades("MONUSDT", limit=50)
print(f"MONUSDT User Trades Count: {len(trades) if trades else 0}")
if trades:
    for tr in trades:
        ts = int(tr.get("time", 0))
        dt_str = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
        print(f"  Date: {dt_str} | Side: {tr.get('side')} | Qty: {tr.get('qty')} | Price: ${tr.get('price')} | Realized PnL: ${tr.get('realizedPnl')}")
