import sys
import os
import json

sys.stdout.reconfigure(encoding='utf-8')

from binance_futures_trader import BinanceFuturesTrader

t = BinanceFuturesTrader()
print("=== FETCHING AVAXUSDT TRADES & INCOME FROM BINANCE ===")

# 1. Fetch user trades for AVAXUSDT
trades = t.get_user_trades("AVAXUSDT", limit=50)
print(f"AVAXUSDT User Trades Count: {len(trades) if trades else 0}")
if trades:
    for tr in trades:
        print("  Trade:", tr)

# 2. Fetch recent income entries for AVAXUSDT
income = t.get_recent_income(limit=100)
avax_inc = [inc for inc in (income or []) if inc.get("symbol") == "AVAXUSDT"]
print(f"\nAVAXUSDT Income Entries Count: {len(avax_inc)}")
for inc in avax_inc:
    print("  Income:", inc)
