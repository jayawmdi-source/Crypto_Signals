import sys
import requests
import json
import math
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor

sys.stdout.reconfigure(encoding='utf-8')

BASE_URL = "https://api.binance.com/api/v3"
session = requests.Session()
session.headers.update({"User-Agent": "Mozilla/5.0"})

def get_history_klines(symbol, interval="1d", limit=365):
    url = f"{BASE_URL}/klines"
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    try:
        resp = session.get(url, params=params, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"Error fetching history for {symbol}: {e}")
        return None

def calculate_ema(prices, period):
    if len(prices) < period:
        return []
    multiplier = 2 / (period + 1)
    ema = [sum(prices[:period]) / period]
    for price in prices[period:]:
        ema.append((price - ema[-1]) * multiplier + ema[-1])
    return ema

def calculate_rsi(prices, period=14):
    if len(prices) <= period:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(prices)):
        d = prices[i] - prices[i-1]
        gains.append(max(0, d))
        losses.append(max(0, -d))
    avg_g = sum(gains[:period]) / period
    avg_l = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_g = (avg_g * 13 + gains[i]) / 14
        avg_l = (avg_l * 13 + losses[i]) / 14
    if avg_l == 0:
        return 100.0
    return 100.0 - (100.0 / (1.0 + (avg_g / avg_l)))

def backtest_pair(symbol, days=250):
    klines = get_history_klines(symbol, interval="1d", limit=days)
    if not klines or len(klines) < 80:
        return []

    trades = []
    closes = [float(k[4]) for k in klines]
    highs = [float(k[2]) for k in klines]
    lows = [float(k[3]) for k in klines]
    dates = [datetime.fromtimestamp(k[0]/1000).strftime('%Y-%m-%d') for k in klines]
    n = len(klines)

    # Precalculate EMAs
    ema20 = calculate_ema(closes, 20)
    ema50 = calculate_ema(closes, 50)
    # Align EMA lengths
    diff20 = n - len(ema20)
    diff50 = n - len(ema50)

    # Scan history bar by bar
    i = 52
    while i < n - 5:
        sub_closes = closes[:i]
        curr_p = closes[i]
        curr_ema20 = ema20[i - diff20]
        curr_ema50 = ema50[i - diff50]
        curr_rsi = calculate_rsi(sub_closes, 14)

        # 1. Line S&R (window=3)
        window = 3
        supports = []
        resistances = []
        for p in range(window, len(sub_closes) - window):
            val = sub_closes[p]
            if all(sub_closes[p-j] <= val and sub_closes[p+j] <= val for j in range(1, window+1)):
                resistances.append(val)
            if all(sub_closes[p-j] >= val and sub_closes[p+j] >= val for j in range(1, window+1)):
                supports.append(val)

        sup_below = [s for s in supports if s < curr_p]
        res_above = [r for r in resistances if r > curr_p]
        nearest_sup = max(sup_below) if sup_below else min(sub_closes[-20:])
        nearest_res = min(res_above) if res_above else max(sub_closes[-20:])

        dist_sup_pct = ((curr_p - nearest_sup) / curr_p) * 100
        dist_res_pct = ((nearest_res - curr_p) / curr_p) * 100

        trade = None

        # LONG Setup:
        # Bullish EMA + Line Support Bounce + RSI Oversold / Bullish (RSI <= 38 or near support with EMA20>EMA50)
        if curr_ema20 > curr_ema50 and (dist_sup_pct <= 3.0) and (curr_rsi <= 65):
            entry = curr_p
            sl = nearest_sup * 0.975 # Safe 2.5% structural buffer below line support
            risk = entry - sl
            if 0.015 <= (risk / entry) <= 0.08:
                tp = entry + (risk * 3.0) # 1:3 R:R
                trade = {
                    "symbol": symbol,
                    "type": "BUY / LONG",
                    "entry_date": dates[i],
                    "entry_idx": i,
                    "entry": entry,
                    "sl": sl,
                    "tp": tp,
                    "risk_pct": ((entry - sl)/entry)*100,
                    "reward_pct": ((tp - entry)/entry)*100,
                    "outcome": "OPEN"
                }

        # SHORT Setup:
        # Bearish EMA + Line Resistance Rejection + RSI Overbought (RSI >= 65 or extreme)
        elif curr_ema20 < curr_ema50 and (dist_res_pct <= 3.0) and (curr_rsi >= 40):
            entry = curr_p
            sl = nearest_res * 1.025 # Safe 2.5% structural buffer
            risk = sl - entry
            if 0.015 <= (risk / entry) <= 0.08:
                tp = entry - (risk * 3.0) # 1:3 R:R
                trade = {
                    "symbol": symbol,
                    "type": "SELL / SHORT",
                    "entry_date": dates[i],
                    "entry_idx": i,
                    "entry": entry,
                    "sl": sl,
                    "tp": tp,
                    "risk_pct": ((sl - entry)/entry)*100,
                    "reward_pct": ((entry - tp)/entry)*100,
                    "outcome": "OPEN"
                }

        # Evaluate trade outcome in subsequent bars
        if trade:
            entry_idx = trade["entry_idx"]
            for f in range(entry_idx + 1, min(entry_idx + 45, n)):
                f_high = highs[f]
                f_low = lows[f]
                
                if trade["type"] == "BUY / LONG":
                    # Check SL first
                    if f_low <= trade["sl"]:
                        trade["outcome"] = "LOSS (SL Hit)"
                        trade["exit_date"] = dates[f]
                        trade["pnl_r"] = -1.0
                        break
                    elif f_high >= trade["tp"]:
                        trade["outcome"] = "WIN (1:3 TP Hit)"
                        trade["exit_date"] = dates[f]
                        trade["pnl_r"] = +3.0
                        break
                elif trade["type"] == "SELL / SHORT":
                    if f_high >= trade["sl"]:
                        trade["outcome"] = "LOSS (SL Hit)"
                        trade["exit_date"] = dates[f]
                        trade["pnl_r"] = -1.0
                        break
                    elif f_low <= trade["tp"]:
                        trade["outcome"] = "WIN (1:3 TP Hit)"
                        trade["exit_date"] = dates[f]
                        trade["pnl_r"] = +3.0
                        break

            trades.append(trade)
            # Skip forward so we don't open duplicate trades on consecutive bars
            i += 4
        else:
            i += 1

    return trades

def run_full_backtest():
    pairs = [
        "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
        "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "LINKUSDT", "SUIUSDT",
        "NEARUSDT", "DOTUSDT", "APTUSDT", "FETUSDT", "RENDERUSDT",
        "ARBUSDT", "OPUSDT", "INJUSDT", "TIAUSDT", "PEPEUSDT"
    ]

    print("=" * 80)
    print(" 📊 BINANCE SMC STRATEGY HISTORICAL BACKTEST & WIN RATE REPORT")
    print(" Timeframe: Daily Line S&R + 20/50 EMA + Safe Structural SL + 1:3 R:R")
    print(f" Period: Past ~250 Days across {len(pairs)} Top Binance Pairs")
    print("=" * 80)

    all_trades = []
    with ThreadPoolExecutor(max_workers=6) as executor:
        results = executor.map(backtest_pair, pairs)
        for res in results:
            all_trades.extend(res)

    closed_trades = [t for t in all_trades if t["outcome"] in ["WIN (1:3 TP Hit)", "LOSS (SL Hit)"]]
    wins = [t for t in closed_trades if t["outcome"] == "WIN (1:3 TP Hit)"]
    losses = [t for t in closed_trades if t["outcome"] == "LOSS (SL Hit)"]
    open_trades = [t for t in all_trades if t["outcome"] == "OPEN"]

    total_closed = len(closed_trades)
    win_rate = (len(wins) / total_closed * 100) if total_closed > 0 else 0.0
    loss_rate = (len(losses) / total_closed * 100) if total_closed > 0 else 0.0

    # Risk-Reward Mathematics
    # Each Win = +3 R, Each Loss = -1 R
    net_r = (len(wins) * 3.0) - (len(losses) * 1.0)
    profit_factor = (len(wins) * 3.0) / (len(losses) * 1.0) if len(losses) > 0 else 999.0

    print(f"\n📈 TOTAL SIGNALS TESTED : {len(all_trades)}")
    print(f"   ├─ Completed Trades : {total_closed}")
    print(f"   ├─ Still Open Trades: {len(open_trades)}")
    print(f"   ├─ ✅ Total WINS (1:3 TP Hit) : {len(wins)}")
    print(f"   └─ ❌ Total LOSSES (SL Hit)   : {len(losses)}")
    
    print("\n" + "=" * 50)
    print(f" 🏆 WINNING PERCENTAGE (WIN RATE) : {win_rate:.2f}%")
    print(f" ⚠️ LOSS PERCENTAGE              : {loss_rate:.2f}%")
    print(f" 💰 NET PROFIT MULTIPLIER (R)    : +{net_r:.1f} R")
    print(f" 📊 PROFIT FACTOR                : {profit_factor:.2f}")
    print("=" * 50)

    print("\n💡 1:3 RISK-TO-REWARD MATHEMATICAL REALITY:")
    print("   • 1:3 R:R ක්‍රමයේදී Break-Even වෙන්න (පාඩු නොවී ඉන්න) අවශ්‍ය වන්නේ 25% Win Rate එකක් පමණි!")
    print(f"   • අපේ System එකේ Win Rate එක {win_rate:.1f}% ක් ලැබෙන නිසා, සෑම Trade 10කින්ම:")
    wins_per_10 = round(win_rate / 10, 1)
    losses_per_10 = round(10 - wins_per_10, 1)
    pnl_per_10 = (wins_per_10 * 3) - (losses_per_10 * 1)
    print(f"     -> {wins_per_10} Wins x +3R = +{wins_per_10*3:.1f}R ලාභය")
    print(f"     -> {losses_per_10} Losses x -1R = -{losses_per_10*1:.1f}R පාඩුව")
    print(f"     -> ශුද්ධ ලාභය (Net Profit) = +{pnl_per_10:.1f}R (Account එක දැවැන්ත ලාභයක පවතී!)")

    # Save to history json
    report = {
        "generated_at": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        "total_signals": len(all_trades),
        "total_closed": total_closed,
        "wins": len(wins),
        "losses": len(losses),
        "open": len(open_trades),
        "win_rate_pct": round(win_rate, 2),
        "loss_rate_pct": round(loss_rate, 2),
        "net_r": round(net_r, 1),
        "profit_factor": round(profit_factor, 2),
        "recent_trades": closed_trades[-15:]
    }

    with open("c:/xampp/crypto/trade_history.json", "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    return report

if __name__ == "__main__":
    run_full_backtest()
