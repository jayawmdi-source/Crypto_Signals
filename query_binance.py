import sys
import os
import json

sys.stdout.reconfigure(encoding='utf-8')

from binance_futures_trader import BinanceFuturesTrader

t = BinanceFuturesTrader()
print("=== BINANCE API LIVE DATA ===")
print("Is Configured:", t.is_configured())
print("Is Live Enabled:", t.is_live_enabled())
print("Balances:", t.get_account_balances())

open_pos = t.get_open_positions_detail()
print(f"Open Positions Count: {len(open_pos) if open_pos else 0}")
if open_pos:
    for p in open_pos:
        print("  Position:", p)

recent_inc = t.get_recent_income(limit=30)
print(f"Recent Income Entries Count: {len(recent_inc) if recent_inc else 0}")
if recent_inc:
    for inc in recent_inc[:15]:
        print("  Income:", inc)
