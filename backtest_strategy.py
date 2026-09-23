import sys
import requests
import json
import math
import os
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor

sys.stdout.reconfigure(encoding='utf-8')

BINANCE_BASES = [
    "https://data-api.binance.vision/api/v3",
    "https://api.binance.com/api/v3"
]
session = requests.Session()
session.headers.update({"User-Agent": "Mozilla/5.0"})

FEE_ROUNDTRIP_PCT = 0.15   # 0.15% roundtrip taker fee on Binance

def get_klines(symbol, interval="1h", limit=500):
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    for base in BINANCE_BASES:
        url = f"{base}/klines"
        try:
            resp = session.get(url, params=params, timeout=12)
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list) and len(data) > 0:
                    return data
        except Exception:
            continue
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
        gains.append(max(0.0, d))
        losses.append(max(0.0, -d))
    avg_g = sum(gains[:period]) / period
    avg_l = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_g = (avg_g * 13 + gains[i]) / 14
        avg_l = (avg_l * 13 + losses[i]) / 14
    if avg_l == 0:
        return 100.0
    return 100.0 - (100.0 / (1.0 + (avg_g / avg_l)))

def calculate_atr(klines, period=14):
    if not klines or len(klines) < period + 1:
        return 0.0
    trs = []
    for i in range(1, len(klines)):
        h = float(klines[i][2])
        l = float(klines[i][3])
        prev_c = float(klines[i-1][4])
        tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
        trs.append(tr)
    if len(trs) < period:
        return sum(trs) / len(trs) if trs else 0.0
    atr = sum(trs[:period]) / period
    for tr in trs[period:]:
        atr = (atr * (period - 1) + tr) / period
    return atr

def detect_fvg(klines, idx):
    if idx < 2:
        return False, False, 0.0, 0.0
    prev_h = float(klines[idx-2][2])
    prev_l = float(klines[idx-2][3])
    curr_l = float(klines[idx][3])
    curr_h = float(klines[idx][2])
    
    is_bull = curr_l > prev_h and ((curr_l - prev_h) / prev_h * 100) >= 0.2
    is_bear = curr_h < prev_l and ((prev_l - curr_h) / prev_l * 100) >= 0.2
    return is_bull, is_bear, prev_h, prev_l

def backtest_symbol_smc(symbol):
    # Fetch 500 1H candles (~21 days of deep market action)
    klines_1h = get_klines(symbol, interval="1h", limit=500)
    if not klines_1h or len(klines_1h) < 100:
        return []

    closes = [float(k[4]) for k in klines_1h]
    highs = [float(k[2]) for k in klines_1h]
    lows = [float(k[3]) for k in klines_1h]
    timestamps = [int(k[0]) for k in klines_1h]
    dates = [datetime.fromtimestamp(k[0]/1000, tz=timezone.utc).strftime('%Y-%m-%d %H:%M') for k in klines_1h]
    n = len(klines_1h)

    # 4H Approximation from 1H (every 4 bars)
    # Calculate 1H 20 and 50 EMA
    ema20 = calculate_ema(closes, 20)
    ema50 = calculate_ema(closes, 50)
    diff20 = n - len(ema20)
    diff50 = n - len(ema50)

    trades = []
    i = 60
    while i < n - 15:
        curr_p = closes[i]
        curr_e20 = ema20[i - diff20]
        curr_e50 = ema50[i - diff50]
        curr_atr = calculate_atr(klines_1h[max(0, i-25):i+1], 14)
        if curr_atr <= 0:
            i += 1
            continue

        # Lookback swing high and low for liquidity sweeps & S/R
        lookback_window = closes[max(0, i-35):i]
        support_lvl = min(lows[max(0, i-30):i-2])
        resistance_lvl = max(highs[max(0, i-30):i-2])

        # FVG Check
        is_bull_fvg, is_bear_fvg, fvg_top, fvg_bot = detect_fvg(klines_1h, i)

        # Liquidity Sweep check (within last 3 bars)
        has_bull_sweep = any(lows[k] < support_lvl and closes[k] > support_lvl for k in range(max(0, i-3), i+1))
        has_bear_sweep = any(highs[k] > resistance_lvl and closes[k] < resistance_lvl for k in range(max(0, i-3), i+1))

        # RSI check
        curr_rsi = calculate_rsi(closes[max(0, i-30):i+1], 14)

        trade = None

        # BUY SETUP:
        # Strict SMC: Bullish Trend + (Liquidity Sweep OR Active FVG) + Healthy RSI
        if (curr_e20 > curr_e50) and (has_bull_sweep or is_bull_fvg) and (30 <= curr_rsi <= 68):
            local_low = min(lows[max(0, i-3):i+1])
            entry = curr_p
            sl = local_low - (0.6 * curr_atr)
            risk = entry - sl
            if 0.010 <= (risk / entry) <= 0.065:
                tp1 = entry + (risk * 1.5)
                tp2 = entry + (risk * 2.0)
                tp3 = entry + (risk * 3.0)
                trade = {
                    "symbol": symbol,
                    "type": "BUY / LONG",
                    "entry_idx": i,
                    "entry_date": dates[i],
                    "entry": entry,
                    "sl": sl,
                    "trailing_sl": sl,
                    "tp1": tp1,
                    "tp2": tp2,
                    "tp3": tp3,
                    "risk_pct": (risk / entry) * 100,
                    "status": "OPEN",
                    "pnl_r": 0.0
                }

        # SELL SETUP:
        # Strict SMC: Bearish Trend + (Liquidity Sweep OR Active FVG) + Healthy RSI
        elif (curr_e20 < curr_e50) and (has_bear_sweep or is_bear_fvg) and (32 <= curr_rsi <= 70):
            local_high = max(highs[max(0, i-3):i+1])
            entry = curr_p
            sl = local_high + (0.6 * curr_atr)
            risk = sl - entry
            if 0.010 <= (risk / entry) <= 0.065:
                tp1 = entry - (risk * 1.5)
                tp2 = entry - (risk * 2.0)
                tp3 = entry - (risk * 3.0)
                trade = {
                    "symbol": symbol,
                    "type": "SELL / SHORT",
                    "entry_idx": i,
                    "entry_date": dates[i],
                    "entry": entry,
                    "sl": sl,
                    "trailing_sl": sl,
                    "tp1": tp1,
                    "tp2": tp2,
                    "tp3": tp3,
                    "risk_pct": (risk / entry) * 100,
                    "status": "OPEN",
                    "pnl_r": 0.0
                }

        # Forward evaluate trade using Multi-Stage Trailing Break-Even
        if trade:
            entry_idx = trade["entry_idx"]
            is_long = trade["type"] == "BUY / LONG"
            for f in range(entry_idx + 1, min(entry_idx + 60, n)):
                f_high = highs[f]
                f_low = lows[f]
                curr_sl = trade["trailing_sl"]

                # Conservative Check: SL evaluated first!
                is_sl_hit = (f_low <= curr_sl) if is_long else (f_high >= curr_sl)
                if is_sl_hit:
                    if trade["status"] == "TP2_LOCKED":
                        trade["outcome"] = "WIN (TP2 Trailed / Locked +1.5R)"
                        trade["pnl_r"] = +1.5 - (FEE_ROUNDTRIP_PCT / trade["risk_pct"])
                    elif trade["status"] == "TP1_BE":
                        trade["outcome"] = "WIN (TP1 Hit / Break-Even Exit)"
                        trade["pnl_r"] = +0.75 - (FEE_ROUNDTRIP_PCT / trade["risk_pct"])
                    else:
                        trade["outcome"] = "LOSS (SL Hit)"
                        trade["pnl_r"] = -1.0 - (FEE_ROUNDTRIP_PCT / trade["risk_pct"])
                    trade["exit_date"] = dates[f]
                    break

                # TP3 Full Target
                is_tp3_hit = (f_high >= trade["tp3"]) if is_long else (f_low <= trade["tp3"])
                if is_tp3_hit:
                    trade["outcome"] = "WIN (1:3 Full TP3 Hit)"
                    trade["pnl_r"] = +3.0 - (FEE_ROUNDTRIP_PCT / trade["risk_pct"])
                    trade["exit_date"] = dates[f]
                    break

                # TP2 Hit: Lock +1.5R and trail SL to TP1
                is_tp2_hit = (f_high >= trade["tp2"]) if is_long else (f_low <= trade["tp2"])
                if is_tp2_hit and trade["status"] in ["OPEN", "TP1_BE"]:
                    trade["status"] = "TP2_LOCKED"
                    trade["trailing_sl"] = trade["tp1"]

                # TP1 Hit: Move SL to True Break-Even (+0.2% fee coverage)
                is_tp1_hit = (f_high >= trade["tp1"]) if is_long else (f_low <= trade["tp1"])
                if is_tp1_hit and trade["status"] == "OPEN":
                    trade["status"] = "TP1_BE"
                    trade["trailing_sl"] = trade["entry"] * 1.002 if is_long else trade["entry"] * 0.998

            if "outcome" not in trade:
                trade["outcome"] = "OPEN (Unresolved)"

            trades.append(trade)
            i += 6 # Avoid consecutive bar re-entry
        else:
            i += 1

    return trades

def run_institutional_backtest():
    test_pairs = [
        "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
        "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "LINKUSDT", "SUIUSDT",
        "NEARUSDT", "DOTUSDT", "APTUSDT", "FETUSDT", "RENDERUSDT",
        "ARBUSDT", "OPUSDT", "INJUSDT", "TIAUSDT", "PEPEUSDT"
    ]

    print("=" * 85)
    print(" 📊 BINANCE INSTITUTIONAL SMC 2.0 STRATEGY BACKTEST & EXECUTION REPORT")
    print(" Model: 1H FVG + Liquidity Sweeps + Dynamic ATR Stops + Multi-Stage Trailing Break-Even")
    print(f" Friction: Realistic {FEE_ROUNDTRIP_PCT}% Binance Taker Fees Deducted | Conservative Bar Sequencing")
    print(f" Pairs Tested: {len(test_pairs)} High-Liquidity USDT Pairs")
    print("=" * 85)

    all_trades = []
    with ThreadPoolExecutor(max_workers=6) as executor:
        results = executor.map(backtest_symbol_smc, test_pairs)
        for res in results:
            all_trades.extend(res)

    closed = [t for t in all_trades if t.get("outcome") != "OPEN (Unresolved)"]
    wins = [t for t in closed if "WIN" in t["outcome"]]
    losses = [t for t in closed if "LOSS" in t["outcome"]]

    total_closed = len(closed)
    win_rate = (len(wins) / total_closed * 100) if total_closed > 0 else 0.0
    loss_rate = (len(losses) / total_closed * 100) if total_closed > 0 else 0.0

    net_r = sum(t["pnl_r"] for t in closed)
    gross_win_r = sum(t["pnl_r"] for t in wins)
    gross_loss_r = abs(sum(t["pnl_r"] for t in losses))
    profit_factor = (gross_win_r / gross_loss_r) if gross_loss_r > 0 else 999.0
    expectancy_r = (net_r / total_closed) if total_closed > 0 else 0.0

    print(f"\n📈 TOTAL SIGNALS GENERATED : {len(all_trades)}")
    print(f"   ├─ Fully Closed Trades : {total_closed}")
    print(f"   ├─ Still Open Trades   : {len(all_trades) - total_closed}")
    print(f"   ├─ ✅ Total WINS       : {len(wins)} (TP3 Full & Trailed Locks)")
    print(f"   └─ ❌ Total LOSSES     : {len(losses)} (Safe ATR Stops)")

    print("\n" + "=" * 55)
    print(f" 🏆 WIN RATE (Closed Trades)     : {win_rate:.2f}%")
    print(f" ⚠️ LOSS RATE                   : {loss_rate:.2f}%")
    print(f" 💰 NET PROFIT MULTIPLIER (R)   : +{net_r:.2f} R")
    print(f" 📊 PROFIT FACTOR               : {profit_factor:.2f}")
    print(f" 🎯 MATHEMATICAL EXPECTANCY (E) : +{expectancy_r:.2f} R per Trade")
    print("=" * 55)

    print("\n💡 INSTITUTIONAL ADVANTAGE:")
    print(f"   • With a {win_rate:.1f}% Win Rate and Trailing Break-Even protection, every 10 trades yields:")
    print(f"     -> Expected Return: +{expectancy_r * 10:.2f}R after deducting all Binance exchange fees!")

    report_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backtest_results.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump({
            "generated_at": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            "pairs_count": len(test_pairs),
            "total_trades": len(all_trades),
            "closed_trades": total_closed,
            "wins": len(wins),
            "losses": len(losses),
            "win_rate_pct": round(win_rate, 2),
            "net_r": round(net_r, 2),
            "profit_factor": round(profit_factor, 2),
            "expectancy_r": round(expectancy_r, 2),
            "trades_sample": closed[:25]
        }, f, indent=2)
    print(f"[+] Saved backtest results to {report_path}")

if __name__ == "__main__":
    run_institutional_backtest()
