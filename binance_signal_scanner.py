import sys
import requests
import json
import math
import os
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor

sys.stdout.reconfigure(encoding='utf-8')

BASE_URL = "https://api.binance.com/api/v3"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}

session = requests.Session()
session.headers.update(HEADERS)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
HISTORY_FILE = os.path.join(SCRIPT_DIR, "trade_history.json")

def send_telegram_message(message_html):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    
    config_file = os.path.join(SCRIPT_DIR, "telegram_config.json")
    if (not token or not chat_id) and os.path.exists(config_file):
        try:
            with open(config_file, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                token = token or cfg.get("bot_token")
                chat_id = chat_id or cfg.get("chat_id")
        except Exception:
            pass

    if not token or not chat_id:
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message_html,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    try:
        resp = session.post(url, json=payload, timeout=8)
        if resp.status_code == 200:
            print("[+] Telegram alert sent successfully!")
            return True
        else:
            print(f"[!] Telegram failed ({resp.status_code}): {resp.text}")
            return False
    except Exception as e:
        print(f"[!] Telegram request error: {e}")
        return False

def send_telegram_new_signal(sig):
    is_long = "BUY" in sig.get("signal", "") or "LONG" in sig.get("signal", "")
    icon = "🟢" if is_long else "🔴"
    action = "BUY / LONG 📈" if is_long else "SELL / SHORT 📉"
    
    limit_text = ""
    if sig.get("limit_setup"):
        ls = sig["limit_setup"]
        limit_text = (
            f"\n🟡 <b>RECOMMENDED SMC LIMIT ORDER:</b>\n"
            f"👉 <b>Limit Entry:</b> <code>${ls.get('limit_entry')}</code> (Wait for Retest)\n"
            f"🛑 <b>Limit SL:</b> <code>${ls.get('limit_sl')}</code>\n"
            f"🏆 <b>Limit TP1:</b> <code>${ls.get('limit_tp1', '-')}</code>\n"
            f"🏆 <b>Limit TP3:</b> <code>${ls.get('limit_tp')}</code>\n"
        )

    reasons_bullets = "\n".join([f"• {r}" for r in sig.get("reasons", [])[:3]])

    msg = (
        f"🚀 <b>NEW SMC TRADE SIGNAL</b> {icon}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🪙 <b>PAIR:</b> #{sig.get('symbol')}\n"
        f"⚡ <b>ACTION:</b> <b>{action}</b>\n"
        f"⭐ <b>TIER:</b> {sig.get('tier_badge', 'TIER 1')}\n"
        f"📐 <b>SETUP:</b> {sig.get('setup', '-')}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🎯 <b>MARKET ENTRY:</b> <code>${sig.get('entry')}</code>\n"
        f"🛑 <b>SAFE SL:</b> <code>${sig.get('stop_loss')}</code> (-{sig.get('risk_pct', '-')})\n"
        f"🏆 <b>TP 1 (1:1.5):</b> <code>${sig.get('tp_1', '-')}</code> (50% + SL to BE)\n"
        f"🏆 <b>TP 2 (1:2.0):</b> <code>${sig.get('tp_2', '-')}</code>\n"
        f"🏆 <b>TP 3 (1:3.0):</b> <code>${sig.get('take_profit_1_3')}</code> (+{sig.get('reward_pct', '-')})\n"
        f"{limit_text}"
        f"📦 <b>1H Order Block:</b> {sig.get('order_block_1h', 'None')}\n"
        f"📊 <b>RSI:</b> Daily {sig.get('rsi_daily', '-')} | 1H {sig.get('rsi_1h', '-')}\n\n"
        f"💡 <b>Key Confirmations:</b>\n{reasons_bullets}\n\n"
        f"🔗 <a href=\"https://www.binance.com/en/trade/{sig.get('symbol')}\">Trade #{sig.get('symbol')} on Binance</a>"
    )
    send_telegram_message(msg.strip())

def send_telegram_resolution(sig, event_type):
    sym = sig.get('symbol')
    if event_type == "WIN_TP3":
        msg = (
            f"🎉 <b>1:3 FULL TARGET HIT! (WIN +3.0R)</b> 🏆\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🪙 #{sym} has reached its <b>1:3 Take Profit</b> Target (<code>${sig.get('tp_str', sig.get('tp'))}</code>)!\n"
            f"💰 <b>Net Gain: +3.0 R Profit!</b>\n"
            f"✅ Trade completed successfully."
        )
    elif event_type == "WIN_TP1":
        msg = (
            f"🛡️ <b>TP1 (1:1.5) HIT & PROFIT SECURED!</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🪙 #{sym} hit TP1 at <code>${sig.get('tp1_str', sig.get('tp1'))}</code>!\n"
            f"✅ <b>Action:</b> Book 50% Profit now!\n"
            f"🔒 <b>Action:</b> Move Stop Loss to Break-Even (<code>${sig.get('entry_str', sig.get('entry'))}</code>) for a 100% Risk-Free Runner!"
        )
    elif event_type == "LOSS_SL":
        msg = (
            f"🛑 <b>STOP LOSS HIT (EXPIRED)</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🪙 #{sym} touched Safe SL at <code>${sig.get('sl_str', sig.get('sl'))}</code>.\n"
            f"📉 Net Loss: -1.0 R\n"
            f"Trade closed according to risk management rules."
        )
    else:
        return
    send_telegram_message(msg.strip())

def get_klines(symbol, interval="1d", limit=100):
    url = f"{BASE_URL}/klines"
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    try:
        resp = session.get(url, params=params, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception:
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
        delta = prices[i] - prices[i-1]
        if delta >= 0:
            gains.append(delta)
            losses.append(0.0)
        else:
            gains.append(0.0)
            losses.append(abs(delta))
            
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * 13 + gains[i]) / 14
        avg_loss = (avg_loss * 13 + losses[i]) / 14
        
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))

def find_line_chart_sr(close_prices, window=3):
    supports = []
    resistances = []
    n = len(close_prices)
    for i in range(window, n - window):
        current = close_prices[i]
        is_high = all(close_prices[i - j] <= current and close_prices[i + j] <= current for j in range(1, window + 1))
        if is_high:
            resistances.append(current)
            
        is_low = all(close_prices[i - j] >= current and close_prices[i + j] >= current for j in range(1, window + 1))
        if is_low:
            supports.append(current)
            
    def cluster_levels(levels):
        if not levels:
            return []
        levels = sorted(levels)
        clustered = [levels[0]]
        for lvl in levels[1:]:
            if (lvl - clustered[-1]) / clustered[-1] > 0.015:
                clustered.append(lvl)
        return clustered

    return cluster_levels(supports), cluster_levels(resistances)

def detect_1h_order_blocks(klines_1h):
    if not klines_1h or len(klines_1h) < 20:
        return None, None

    bodies = [abs(float(k[4]) - float(k[1])) for k in klines_1h]
    avg_body = sum(bodies) / len(bodies)

    bullish_obs = []
    bearish_obs = []
    current_price = float(klines_1h[-1][4])
    n = len(klines_1h)

    for i in range(1, n - 2):
        o, h, l, c = float(klines_1h[i][1]), float(klines_1h[i][2]), float(klines_1h[i][3]), float(klines_1h[i][4])
        next_o, next_h, next_l, next_c = float(klines_1h[i+1][1]), float(klines_1h[i+1][2]), float(klines_1h[i+1][3]), float(klines_1h[i+1][4])
        
        if c < o and (next_c - next_o) > (avg_body * 1.3) and next_c > h:
            ob_top = h
            ob_bottom = l
            is_mitigated = any(float(klines_1h[j][4]) < ob_bottom for j in range(i + 2, n))
            if not is_mitigated:
                bullish_obs.append({
                    "type": "Bullish 1H OB (Demand)",
                    "top": ob_top,
                    "bottom": ob_bottom,
                    "age_hours": n - 1 - i,
                    "is_testing": (ob_bottom * 0.995 <= current_price <= ob_top * 1.015)
                })

        if c > o and (next_o - next_c) > (avg_body * 1.3) and next_c < l:
            ob_top = h
            ob_bottom = l
            is_mitigated = any(float(klines_1h[j][4]) > ob_top for j in range(i + 2, n))
            if not is_mitigated:
                bearish_obs.append({
                    "type": "Bearish 1H OB (Supply)",
                    "top": ob_top,
                    "bottom": ob_bottom,
                    "age_hours": n - 1 - i,
                    "is_testing": (ob_bottom * 0.985 <= current_price <= ob_top * 1.005)
                })

    recent_bull = bullish_obs[-1] if bullish_obs else None
    recent_bear = bearish_obs[-1] if bearish_obs else None
    return recent_bull, recent_bear

def detect_1h_choch(klines_1h):
    if not klines_1h or len(klines_1h) < 25:
        return {
            "has_bearish_choch": False,
            "has_bullish_choch": False,
            "key_hl": None,
            "key_lh": None,
            "recent_high": None,
            "recent_low": None,
            "status": "Insufficient Data"
        }

    highs = [float(k[2]) for k in klines_1h]
    lows = [float(k[3]) for k in klines_1h]
    closes = [float(k[4]) for k in klines_1h]
    current_price = closes[-1]
    n = len(klines_1h)

    swing_highs = []
    swing_lows = []
    for i in range(2, n - 2):
        if highs[i] > highs[i-1] and highs[i] > highs[i-2] and highs[i] >= highs[i+1] and highs[i] >= highs[i+2]:
            swing_highs.append((i, highs[i]))
        if lows[i] < lows[i-1] and lows[i] < lows[i-2] and lows[i] <= lows[i+1] and lows[i] <= lows[i+2]:
            swing_lows.append((i, lows[i]))

    if not swing_highs:
        max_idx = highs[-12:].index(max(highs[-12:])) + (n - 12)
        swing_highs.append((max_idx, highs[max_idx]))
    if not swing_lows:
        min_idx = lows[-12:].index(min(lows[-12:])) + (n - 12)
        swing_lows.append((min_idx, lows[min_idx]))

    recent_high = max(highs[-15:])
    recent_low = min(lows[-15:])

    highest_high_tuple = max(swing_highs[-4:], key=lambda x: x[1])
    prior_lows = [l for l in swing_lows if l[0] < highest_high_tuple[0]]
    key_hl = prior_lows[-1][1] if prior_lows else min(lows[-10:-1])

    lowest_low_tuple = min(swing_lows[-4:], key=lambda x: x[1])
    prior_highs = [h for h in swing_highs if h[0] < lowest_low_tuple[0]]
    key_lh = prior_highs[-1][1] if prior_highs else max(highs[-10:-1])

    has_bearish_choch = (current_price < key_hl) or (closes[-2] < key_hl)
    has_bullish_choch = (current_price > key_lh) or (closes[-2] > key_lh)

    return {
        "has_bearish_choch": has_bearish_choch,
        "has_bullish_choch": has_bullish_choch,
        "key_hl": round(key_hl, 4 if key_hl < 10 else 2),
        "key_lh": round(key_lh, 4 if key_lh < 10 else 2),
        "recent_high": round(recent_high, 4 if recent_high < 10 else 2),
        "recent_low": round(recent_low, 4 if recent_low < 10 else 2),
        "status": "Bearish CHoCH Confirmed" if has_bearish_choch else ("Bullish CHoCH Confirmed" if has_bullish_choch else "Waiting for CHoCH")
    }

def analyze_symbol(symbol, anchored_signal=None):
    daily_klines = get_klines(symbol, interval="1d", limit=100)
    klines_1h = get_klines(symbol, interval="1h", limit=80)
    
    if not daily_klines or len(daily_klines) < 50 or not klines_1h:
        return None
    
    close_prices = [float(k[4]) for k in daily_klines]
    volumes = [float(k[5]) for k in daily_klines]
    current_price = close_prices[-1]
    
    historical_closes = close_prices[:-1]
    supports, resistances = find_line_chart_sr(historical_closes, window=3)
    
    supports_below = [s for s in supports if s <= current_price]
    resistances_above = [r for r in resistances if r >= current_price]
    
    nearest_support = max(supports_below) if supports_below else min(historical_closes[-30:])
    nearest_resistance = min(resistances_above) if resistances_above else max(historical_closes[-30:])
    
    resistances_below = [r for r in resistances if r < current_price]
    recent_broken_resistance = max(resistances_below) if resistances_below else None

    bull_ob_1h, bear_ob_1h = detect_1h_order_blocks(klines_1h)
    choch_data = detect_1h_choch(klines_1h)
    
    ema_20_series = calculate_ema(close_prices, 20)
    ema_50_series = calculate_ema(close_prices, 50)
    ema_20 = ema_20_series[-1]
    ema_50 = ema_50_series[-1]
    ema_bullish = ema_20 > ema_50
    
    rsi_daily = calculate_rsi(close_prices, 14)
    closes_1h = [float(k[4]) for k in klines_1h]
    rsi_1h = calculate_rsi(closes_1h, 14)
    
    vol_sma_20 = sum(volumes[-21:-1]) / 20
    current_vol = volumes[-1]
    now_utc = datetime.now(timezone.utc)
    hours_passed = now_utc.hour + (now_utc.minute / 60.0)
    projected_day_vol = current_vol * (24.0 / max(hours_passed, 1.0))
    vol_proj_ratio = projected_day_vol / vol_sma_20 if vol_sma_20 > 0 else 1.0

    dist_to_support_pct = ((current_price - nearest_support) / current_price) * 100
    dist_to_resistance_pct = ((nearest_resistance - current_price) / current_price) * 100

    is_sr_flip = False
    flip_lvl = None
    if recent_broken_resistance:
        dist_above_flip = ((current_price - recent_broken_resistance) / current_price) * 100
        if 0.1 <= dist_above_flip <= 3.5:
            is_sr_flip = True
            flip_lvl = recent_broken_resistance

    signal_type = "WATCHLIST"
    tier_badge = "WATCHLIST"
    trade_setup = ""
    reasons = []
    entry = current_price
    sl = 0
    tp = 0
    tp1 = 0
    tp2 = 0
    ob_info_str = "None"
    choch_badge = "No CHoCH"

    is_extreme_overbought = (rsi_daily >= 78.0) or (rsi_1h >= 80.0)
    is_extreme_oversold = (rsi_daily <= 22.0) or (rsi_1h <= 20.0)

    # TIER 2: SNIPER EXTREME REVERSALS
    if is_extreme_overbought and choch_data["has_bearish_choch"]:
        signal_type = "SELL / SHORT"
        tier_badge = "🔥 TIER 2: SNIPER EXTREME"
        trade_setup = "Extreme Overbought (RSI > 80) + 1H Bearish CHoCH"
        choch_badge = "1H CHoCH Confirmed 🔴"
        sl = choch_data["recent_high"] * 1.015
        risk = sl - entry
        if risk / entry < 0.025:
            sl = entry * 1.028
            risk = sl - entry
        tp1 = entry - (risk * 1.5)
        tp2 = entry - (risk * 2.0)
        tp = entry - (risk * 3.0)
        reasons.append(f"Extreme RSI Overbought ({rsi_daily:.1f})")
        reasons.append(f"Confirmed 1H Bearish CHoCH below ${choch_data['key_hl']}")
        if bear_ob_1h:
            ob_info_str = f"Bearish 1H OB [${bear_ob_1h['bottom']:.4f} - ${bear_ob_1h['top']:.4f}]"

    elif is_extreme_oversold and choch_data["has_bullish_choch"]:
        signal_type = "BUY / LONG"
        tier_badge = "🔥 TIER 2: SNIPER EXTREME"
        trade_setup = "Extreme Oversold (RSI < 20) + 1H Bullish CHoCH"
        choch_badge = "1H CHoCH Confirmed 🟢"
        sl = choch_data["recent_low"] * 0.985
        risk = entry - sl
        if risk / entry < 0.025:
            sl = entry * 0.972
            risk = entry - sl
        tp1 = entry + (risk * 1.5)
        tp2 = entry + (risk * 2.0)
        tp = entry + (risk * 3.0)
        reasons.append(f"Extreme RSI Oversold ({rsi_daily:.1f})")
        reasons.append(f"Confirmed 1H Bullish CHoCH above ${choch_data['key_lh']}")
        if bull_ob_1h:
            ob_info_str = f"Bullish 1H OB [${bull_ob_1h['bottom']:.4f} - ${bull_ob_1h['top']:.4f}]"

    # TIER 1: DAILY BREAD & BUTTER
    elif ema_bullish and (dist_to_support_pct <= 3.5 or is_sr_flip) and (38 <= rsi_daily <= 68):
        signal_type = "BUY / LONG"
        tier_badge = "⭐ TIER 1: DAILY SETUP"
        trade_setup = "Daily Line Support Bounce + 1H Demand OB" if not is_sr_flip else "S/R Flip Breakout & Retest"
        
        base_support = flip_lvl if is_sr_flip else nearest_support
        if bull_ob_1h and bull_ob_1h['bottom'] < entry:
            base_support = min(base_support, bull_ob_1h['bottom'])
            
        # Structural Stop Loss: strictly anchored below OB bottom or Daily Support with safe 1.5% buffer
        sl = base_support * 0.985
        risk = entry - sl
        if risk / entry < 0.02:
            sl = entry * 0.978
            risk = entry - sl
            
        tp1 = entry + (risk * 1.5)
        tp2 = entry + (risk * 2.0)
        tp = entry + (risk * 3.0)
        reasons.append(f"Holding Daily Line Support ${base_support:.4f} (+{dist_to_support_pct:.1f}%)")
        reasons.append("20 EMA > 50 EMA Bullish Trend Aligned")
        reasons.append(f"RSI {rsi_daily:.1f} in healthy bullish momentum")
        if bull_ob_1h:
            ob_info_str = f"Bullish 1H OB [${bull_ob_1h['bottom']:.4f} - ${bull_ob_1h['top']:.4f}]"
            reasons.append(f"1H Demand Order Block Active: {ob_info_str}")

    elif (not ema_bullish) and (dist_to_resistance_pct <= 3.5) and (32 <= rsi_daily <= 62):
        signal_type = "SELL / SHORT"
        tier_badge = "⭐ TIER 1: DAILY SETUP"
        trade_setup = "Daily Line Resistance Rejection + 1H Supply OB"
        
        base_res = nearest_resistance
        if bear_ob_1h and bear_ob_1h['top'] > entry:
            base_res = max(base_res, bear_ob_1h['top'])
            
        # Structural Stop Loss: strictly anchored above OB top or Daily Resistance with safe 1.5% buffer
        sl = base_res * 1.015
        risk = sl - entry
        if risk / entry < 0.02:
            sl = entry * 1.022
            risk = sl - entry
            
        tp1 = entry - (risk * 1.5)
        tp2 = entry - (risk * 2.0)
        tp = entry - (risk * 3.0)
        reasons.append(f"Testing Daily Line Resistance ${base_res:.4f}")
        reasons.append("20 EMA < 50 EMA Bearish Trend Aligned")
        reasons.append(f"RSI {rsi_daily:.1f} in bearish momentum")
        if bear_ob_1h:
            ob_info_str = f"Bearish 1H OB [${bear_ob_1h['bottom']:.4f} - ${bear_ob_1h['top']:.4f}]"
            reasons.append(f"1H Supply Order Block Active: {ob_info_str}")

    else:
        signal_type = "WATCHLIST"
        if is_extreme_overbought:
            tier_badge = "🛡️ PUMP PROTECTED"
            trade_setup = f"RSI Overbought ({rsi_daily:.1f}), WAITING for 1H CHoCH (< ${choch_data['key_hl']})"
            choch_badge = f"Waiting CHoCH (< ${choch_data['key_hl']})"
            reasons.append(f"Protected against Parabolic Pump: Waiting for 1H CHoCH below ${choch_data['key_hl']}")
        else:
            tier_badge = "WATCHLIST"
            reasons.append(f"Mid-range RSI ({rsi_daily:.1f}). Daily Line S&R: Sup ${nearest_support:.4f} | Res ${nearest_resistance:.4f}")
            if choch_data["has_bearish_choch"]:
                choch_badge = "1H CHoCH Bearish"
            elif choch_data["has_bullish_choch"]:
                choch_badge = "1H CHoCH Bullish"

    # Anchor persistence: if this symbol already has an active OPEN trade of the same type, freeze its entry & targets!
    if anchored_signal and signal_type in ["BUY / LONG", "SELL / SHORT"] and anchored_signal.get("type") == signal_type:
        entry = anchored_signal.get("entry", entry)
        sl = anchored_signal.get("sl", sl)
        tp = anchored_signal.get("tp", tp)
        tp1 = anchored_signal.get("tp1", tp1)
        tp2 = anchored_signal.get("tp2", tp2)

    risk_pct = 0.0
    reward_pct = 0.0
    if signal_type == "BUY / LONG":
        risk_pct = ((entry - sl) / entry) * 100
        reward_pct = ((tp - entry) / entry) * 100
    elif signal_type == "SELL / SHORT":
        risk_pct = ((sl - entry) / entry) * 100
        reward_pct = ((entry - tp) / entry) * 100

    def fmt(val):
        if val >= 1000:
            return f"{val:,.2f}"
        elif val >= 1:
            return f"{val:.4f}"
        elif val >= 0.0001:
            return f"{val:.6f}"
        else:
            return f"{val:.8f}"

    limit_setup = None
    if signal_type == "BUY / LONG" and bull_ob_1h and bull_ob_1h['top'] < entry:
        dist_to_ob_pct = ((entry - bull_ob_1h['top']) / entry) * 100
        if dist_to_ob_pct >= 2.0:
            l_entry = bull_ob_1h['top']
            l_sl = sl
            l_risk = l_entry - l_sl
            l_tp1 = l_entry + (l_risk * 1.5)
            l_tp2 = l_entry + (l_risk * 2.0)
            l_tp = l_entry + (l_risk * 3.0)
            limit_setup = {
                "limit_entry": fmt(l_entry),
                "raw_limit_entry": l_entry,
                "limit_sl": fmt(l_sl),
                "limit_tp1": fmt(l_tp1),
                "limit_tp2": fmt(l_tp2),
                "limit_tp": fmt(l_tp),
                "risk_pct": f"{((l_risk / l_entry) * 100):.2f}%",
                "reward_pct": f"{(((l_tp - l_entry) / l_entry) * 100):.2f}%",
                "dist_pct": f"{dist_to_ob_pct:.1f}%"
            }
    elif signal_type == "SELL / SHORT" and bear_ob_1h and bear_ob_1h['bottom'] > entry:
        dist_to_ob_pct = ((bear_ob_1h['bottom'] - entry) / entry) * 100
        if dist_to_ob_pct >= 2.0:
            l_entry = bear_ob_1h['bottom']
            l_sl = sl
            l_risk = l_sl - l_entry
            l_tp1 = l_entry - (l_risk * 1.5)
            l_tp2 = l_entry - (l_risk * 2.0)
            l_tp = l_entry - (l_risk * 3.0)
            limit_setup = {
                "limit_entry": fmt(l_entry),
                "raw_limit_entry": l_entry,
                "limit_sl": fmt(l_sl),
                "limit_tp1": fmt(l_tp1),
                "limit_tp2": fmt(l_tp2),
                "limit_tp": fmt(l_tp),
                "risk_pct": f"{((l_risk / l_entry) * 100):.2f}%",
                "reward_pct": f"{(((l_entry - l_tp) / l_entry) * 100):.2f}%",
                "dist_pct": f"{dist_to_ob_pct:.1f}%"
            }

    if anchored_signal and anchored_signal.get("limit_setup"):
        limit_setup = anchored_signal["limit_setup"]

    action_status = "MONITORING"
    action_label = "Monitoring Structure"
    if signal_type in ["BUY / LONG", "SELL / SHORT"]:
        if limit_setup:
            action_status = "LIMIT"
            action_label = f"SET LIMIT @ ${limit_setup['limit_entry']}"
        else:
            action_status = "READY"
            action_label = "READY TO ENTER NOW"

    return {
        "symbol": symbol,
        "current_price": fmt(current_price),
        "raw_price": current_price,
        "signal": signal_type,
        "tier_badge": tier_badge,
        "setup": trade_setup if trade_setup else "Monitoring Structure",
        "choch_badge": choch_badge,
        "choch_data": choch_data,
        "order_block_1h": ob_info_str,
        "rsi_daily": round(rsi_daily, 1),
        "rsi_1h": round(rsi_1h, 1),
        "ema_20": fmt(ema_20),
        "ema_50": fmt(ema_50),
        "ema_status": "BULLISH (20>50)" if ema_bullish else "BEARISH (20<50)",
        "daily_support": fmt(nearest_support),
        "daily_resistance": fmt(nearest_resistance),
        "vol_ratio": round(vol_proj_ratio, 2),
        "entry": fmt(entry),
        "raw_entry": entry,
        "stop_loss": fmt(sl) if sl > 0 else "-",
        "raw_sl": sl,
        "tp_1": fmt(tp1) if tp1 > 0 else "-",
        "raw_tp1": tp1,
        "tp_2": fmt(tp2) if tp2 > 0 else "-",
        "raw_tp2": tp2,
        "take_profit_1_3": fmt(tp) if tp > 0 else "-",
        "raw_tp": tp,
        "risk_pct": f"{risk_pct:.2f}%" if risk_pct > 0 else "-",
        "reward_pct": f"{reward_pct:.2f}%" if reward_pct > 0 else "-",
        "rr_ratio": "1:3" if signal_type != "WATCHLIST" else "-",
        "limit_setup": limit_setup,
        "action_status": action_status,
        "action_label": action_label,
        "reasons": reasons
    }

def update_trade_history(actionable_signals):
    history_data = {
        "total_signals": 0,
        "wins": 0,
        "losses": 0,
        "pending": 0,
        "win_rate_pct": 0.0,
        "signals": []
    }
    
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                history_data = json.load(f)
        except Exception:
            pass

    existing_signals = history_data.get("signals", [])
    existing_keys = {f"{s['symbol']}_{s['date']}_{s['type']}" for s in existing_signals}
    current_ts = int(datetime.now(timezone.utc).timestamp() * 1000)
    today_str = datetime.now().strftime('%Y-%m-%d')

    for act in actionable_signals:
        sig_type = act["signal"]
        key = f"{act['symbol']}_{today_str}_{sig_type}"
        if key not in existing_keys:
            existing_signals.append({
                "id": len(existing_signals) + 1,
                "symbol": act["symbol"],
                "type": sig_type,
                "setup": act["setup"],
                "date": today_str,
                "timestamp": current_ts,
                "entry": act["raw_entry"],
                "entry_str": act["entry"],
                "sl": act["raw_sl"],
                "sl_str": act["stop_loss"],
                "tp1": act.get("raw_tp1", 0),
                "tp1_str": act.get("tp_1", "-"),
                "tp2": act.get("raw_tp2", 0),
                "tp2_str": act.get("tp_2", "-"),
                "tp": act["raw_tp"],
                "tp_str": act["take_profit_1_3"],
                "limit_setup": act.get("limit_setup"),
                "status": "OPEN",
                "outcome_pnl": 0.0
            })
            send_telegram_new_signal(act)
        else:
            for s in existing_signals:
                if s["symbol"] == act["symbol"] and s.get("status") == "OPEN":
                    if not s.get("limit_setup") and act.get("limit_setup"):
                        s["limit_setup"] = act.get("limit_setup")

    for s in existing_signals:
        if s["status"] == "OPEN":
            sig_ts = s.get("timestamp", 0)
            kl = get_klines(s["symbol"], interval="1h", limit=50)
            if kl:
                future_bars = [bar for bar in kl if bar[0] > sig_ts]
                for bar in future_bars:
                    high = float(bar[2])
                    low = float(bar[3])
                    if "BUY" in s["type"] or "LONG" in s["type"]:
                        if high >= s["tp"]:
                            s["status"] = "WIN (1:3 TP3 Hit)"
                            s["outcome_pnl"] = +3.0
                            s["resolved_at"] = datetime.now().strftime('%Y-%m-%d %H:%M')
                            if not s.get("notified_win"):
                                s["notified_win"] = True
                                send_telegram_resolution(s, "WIN_TP3")
                            break
                        elif s.get("tp1", 0) > 0 and high >= s["tp1"]:
                            s["tp1_hit"] = True
                            if not s.get("notified_tp1"):
                                s["notified_tp1"] = True
                                send_telegram_resolution(s, "WIN_TP1")

                        if low <= s["sl"]:
                            if s.get("tp1_hit"):
                                s["status"] = "WIN (TP1 Hit / SL at BE)"
                                s["outcome_pnl"] = +0.75
                            else:
                                s["status"] = "LOSS (SL Hit)"
                                s["outcome_pnl"] = -1.0
                            s["resolved_at"] = datetime.now().strftime('%Y-%m-%d %H:%M')
                            if not s.get("notified_loss"):
                                s["notified_loss"] = True
                                send_telegram_resolution(s, "LOSS_SL")
                            break
                    elif "SELL" in s["type"] or "SHORT" in s["type"]:
                        if low <= s["tp"]:
                            s["status"] = "WIN (1:3 TP3 Hit)"
                            s["outcome_pnl"] = +3.0
                            s["resolved_at"] = datetime.now().strftime('%Y-%m-%d %H:%M')
                            if not s.get("notified_win"):
                                s["notified_win"] = True
                                send_telegram_resolution(s, "WIN_TP3")
                            break
                        elif s.get("tp1", 0) > 0 and low <= s["tp1"]:
                            s["tp1_hit"] = True
                            if not s.get("notified_tp1"):
                                s["notified_tp1"] = True
                                send_telegram_resolution(s, "WIN_TP1")

                        if high >= s["sl"]:
                            if s.get("tp1_hit"):
                                s["status"] = "WIN (TP1 Hit / SL at BE)"
                                s["outcome_pnl"] = +0.75
                            else:
                                s["status"] = "LOSS (SL Hit)"
                                s["outcome_pnl"] = -1.0
                            s["resolved_at"] = datetime.now().strftime('%Y-%m-%d %H:%M')
                            if not s.get("notified_loss"):
                                s["notified_loss"] = True
                                send_telegram_resolution(s, "LOSS_SL")
                            break

    closed = [s for s in existing_signals if s["status"] in ["WIN (1:3 TP Hit)", "LOSS (SL Hit)"]]
    wins = len([s for s in existing_signals if "WIN" in s["status"]])
    losses = len([s for s in existing_signals if "LOSS" in s["status"]])
    pending = len([s for s in existing_signals if s["status"] == "OPEN"])
    
    total_closed = len(closed)
    win_rate = (wins / total_closed * 100) if total_closed > 0 else 0.0

    history_data = {
        "updated_at": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        "total_signals": len(existing_signals),
        "total_closed": total_closed,
        "wins": wins,
        "losses": losses,
        "pending": pending,
        "win_rate_pct": round(win_rate, 1),
        "net_pnl_r": round((wins * 3.0) - (losses * 1.0), 1),
        "signals": existing_signals
    }

    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history_data, f, indent=2)

    return history_data

def get_top_pairs():
    return [
        "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
        "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "LINKUSDT", "SUIUSDT",
        "NEARUSDT", "DOTUSDT", "APTUSDT", "FETUSDT", "RENDERUSDT",
        "ARBUSDT", "OPUSDT", "INJUSDT", "TIAUSDT", "PEPEUSDT",
        "SHIBUSDT", "LTCUSDT", "UNIUSDT", "AAVEUSDT", "FTMUSDT",
        "ONEUSDT", "GALAUSDT", "SEIUSDT", "WLDUSDT", "TAOUSDT",
        "KASUSDT", "STXUSDT", "FILUSDT", "ICPUSDT", "SANDUSDT"
    ]

def scan_all_pairs():
    pairs = get_top_pairs()

    # Load active open signals to anchor entries and prevent shifting targets
    open_signals_map = {}
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                h_data = json.load(f)
                for s in h_data.get("signals", []):
                    if s.get("status") == "OPEN":
                        open_signals_map[s["symbol"]] = s
        except Exception:
            pass

    print("=" * 95)
    print(" 🎯 BINANCE HYBRID SMC ENGINE (TIER 1: DAILY SETUPS + TIER 2: SNIPER EXTREMES)")
    print(f" Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} (Local)")
    print("=" * 95)

    results = []
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(analyze_symbol, p, open_signals_map.get(p)): p for p in pairs}
        for future in futures:
            res = future.result()
            if res:
                results.append(res)

    actionable = [r for r in results if r["signal"] in ["BUY / LONG", "SELL / SHORT"]]
    watchlist = [r for r in results if r["signal"] == "WATCHLIST"]

    history = update_trade_history(actionable)

    print("-" * 95)
    print(f" 🚀 ACTIVE TRADE SIGNALS (1:3 R:R): {len(actionable)} Found")
    print("-" * 95)

    for idx, a in enumerate(actionable, 1):
        print(f"\n[{idx}] 🪙 {a['symbol']}  |  {a['signal']}  |  {a['tier_badge']}")
        print(f"    ├─ Setup            : {a['setup']}")
        print(f"    ├─ Live Current Price: ${a['current_price']}")
        print(f"    ├─ Entry Price      : ${a['entry']}")
        print(f"    ├─ Safe SL (Buffer) : ${a['stop_loss']} (-{a['risk_pct']})")
        print(f"    ├─ Take Profit (TP) : ${a['take_profit_1_3']} (+{a['reward_pct']}) [1:3 R:R TARGET]")
        print(f"    ├─ Daily Line S&R   : Sup: ${a['daily_support']} | Res: ${a['daily_resistance']}")
        print(f"    ├─ 1H Order Block   : {a['order_block_1h']}")
        print(f"    ├─ Trend (20/50)    : {a['ema_status']} | Daily RSI: {a['rsi_daily']}")
        print(f"    └─ Key Confirmations:")
        for r in a['reasons']:
            print(f"       • {r}")

    # Write output to json and html (dashboard.html for XAMPP, index.html for GitHub Pages)
    json_path = os.path.join(SCRIPT_DIR, "latest_signals.json")
    html_path = os.path.join(SCRIPT_DIR, "dashboard.html")
    index_path = os.path.join(SCRIPT_DIR, "index.html")
    
    payload = {
        "updated_at": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        "filter": "Hybrid SMC: Tier 1 Daily Setups + Tier 2 Sniper Extremes (1:3 R:R)",
        "active_count": len(actionable),
        "active_signals": actionable,
        "all_monitored": results,
        "history": history
    }
    
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

    generate_html_dashboard(payload, html_path)
    generate_html_dashboard(payload, index_path)
    print(f"\n[+] Updated {json_path}")
    print(f"[+] Updated {html_path} (Live at http://localhost/crypto/dashboard.html)")
    print(f"[+] Updated {index_path} (For GitHub Pages Cloud)")

    return actionable, results

def generate_html_dashboard(data, output_path):
    data_json_str = json.dumps(data, indent=2)
    history = data.get("history", {})
    win_rate = history.get("win_rate_pct", 0.0)
    total_signals = history.get("total_signals", 0)
    wins = history.get("wins", 0)
    losses = history.get("losses", 0)
    net_r = history.get("net_pnl_r", 0.0)

    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Binance SMC Pro - Live Price & Signal Scanner</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <style>
        :root {{
            --bg-dark: #0b0e14;
            --card-dark: #151a23;
            --border-color: #242c38;
            --accent-green: #0ecb81;
            --accent-red: #f6465d;
            --accent-yellow: #f0b90b;
            --accent-purple: #9b51e0;
            --accent-cyan: #00f2fe;
        }}
        body {{
            background-color: var(--bg-dark);
            color: #eaecef;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
            padding-bottom: 50px;
        }}
        .navbar {{
            background-color: #12161f;
            border-bottom: 1px solid var(--border-color);
        }}
        .stat-card {{
            background-color: var(--card-dark);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            padding: 16px 20px;
        }}
        .stat-title {{
            font-size: 0.75rem;
            color: #848e9c;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            margin-bottom: 4px;
        }}
        .stat-value {{
            font-size: 1.6rem;
            font-weight: 800;
        }}
        .signal-card {{
            background-color: var(--card-dark);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            padding: 20px;
            transition: all 0.3s ease;
            position: relative;
            overflow: hidden;
        }}
        .signal-card:hover {{
            transform: translateY(-4px);
            border-color: #3b4759;
            box-shadow: 0 10px 25px rgba(0,0,0,0.5);
        }}
        .signal-card.long {{
            border-top: 4px solid var(--accent-green);
        }}
        .signal-card.short {{
            border-top: 4px solid var(--accent-red);
        }}
        .badge-tier1 {{
            background-color: rgba(240, 185, 11, 0.15);
            color: #f6ad55;
            font-weight: 700;
            padding: 4px 10px;
            border-radius: 6px;
            border: 1px solid rgba(240, 185, 11, 0.4);
            font-size: 0.75rem;
        }}
        .badge-tier2 {{
            background-color: rgba(246, 70, 93, 0.2);
            color: #ff6b81;
            font-weight: 800;
            padding: 4px 10px;
            border-radius: 6px;
            border: 1px solid #f6465d;
            font-size: 0.75rem;
        }}
        .badge-long {{
            background-color: rgba(14, 203, 129, 0.15);
            color: var(--accent-green);
            font-weight: 700;
            padding: 6px 12px;
            border-radius: 6px;
            border: 1px solid rgba(14, 203, 129, 0.3);
        }}
        .badge-short {{
            background-color: rgba(246, 70, 93, 0.15);
            color: var(--accent-red);
            font-weight: 700;
            padding: 6px 12px;
            border-radius: 6px;
            border: 1px solid rgba(246, 70, 93, 0.3);
        }}
        .badge-rr {{
            background-color: rgba(240, 185, 11, 0.15);
            color: var(--accent-yellow);
            font-weight: 700;
            padding: 4px 10px;
            border-radius: 6px;
            border: 1px solid rgba(240, 185, 11, 0.3);
        }}
        .price-hero {{
            background: linear-gradient(145deg, #0d121a, #131a24);
            border: 1px solid #232c3a;
            border-radius: 10px;
            padding: 12px 16px;
            margin-bottom: 12px;
        }}
        .price-box {{
            background-color: #0d1117;
            border: 1px solid #1f2733;
            border-radius: 8px;
            padding: 12px;
            margin-bottom: 12px;
        }}
        .metric-title {{
            font-size: 0.75rem;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            color: #848e9c;
            margin-bottom: 4px;
        }}
        .metric-val {{
            font-size: 1.1rem;
            font-weight: 700;
        }}
        .val-green {{ color: var(--accent-green); }}
        .val-red {{ color: var(--accent-red); }}
        .val-yellow {{ color: var(--accent-yellow); }}
        .val-cyan {{ color: var(--accent-cyan); }}
        .ob-box {{
            background: rgba(155, 81, 224, 0.08);
            border: 1px dashed rgba(155, 81, 224, 0.4);
            border-radius: 8px;
            padding: 8px 12px;
            margin-bottom: 12px;
            font-size: 0.8rem;
        }}
        .live-pulse {{
            display: inline-block;
            width: 8px;
            height: 8px;
            background-color: #0ecb81;
            border-radius: 50%;
            margin-right: 6px;
            box-shadow: 0 0 8px #0ecb81;
            animation: pulse 1.5s infinite;
        }}
        @keyframes pulse {{
            0% {{ transform: scale(0.95); box-shadow: 0 0 0 0 rgba(14, 203, 129, 0.7); }}
            70% {{ transform: scale(1); box-shadow: 0 0 0 8px rgba(14, 203, 129, 0); }}
            100% {{ transform: scale(0.95); box-shadow: 0 0 0 0 rgba(14, 203, 129, 0); }}
        }}
        .table-dark-custom {{
            background-color: var(--card-dark);
            border-radius: 12px;
            overflow: hidden;
            border: 1px solid var(--border-color);
        }}
        .table-dark-custom th {{
            background-color: #12161f;
            color: #848e9c;
            font-size: 0.8rem;
            text-transform: uppercase;
            border-bottom: 1px solid var(--border-color);
            padding: 14px 16px;
        }}
        .table-dark-custom td {{
            background-color: var(--card-dark);
            color: #eaecef;
            border-bottom: 1px solid #1c2430;
            padding: 14px 16px;
            vertical-align: middle;
        }}
        .filter-tab-btn {{
            font-size: 0.82rem;
            font-weight: 600;
            border-radius: 8px;
            padding: 5px 12px;
            transition: all 0.2s ease;
        }}
        .filter-tab-btn.active {{
            box-shadow: 0 0 10px rgba(255, 255, 255, 0.25);
            font-weight: 700;
        }}
        .action-status-bar {{
            border-radius: 8px;
            background: rgba(255, 255, 255, 0.04);
            border-left: 4px solid #0ecb81;
            transition: border-color 0.3s ease;
        }}
    </style>
</head>
<body>

    <nav class="navbar navbar-dark px-4 py-3 mb-4">
        <div class="container-fluid">
            <span class="navbar-brand mb-0 h1 d-flex align-items-center">
                <i class="fa-solid fa-bolt text-warning me-2 fs-4"></i>
                <div>
                    <span class="fw-bold">Binance Live SMC Trading Scanner</span>
                    <span class="badge bg-warning text-dark ms-2" style="font-size: 0.7rem;">Live Price & Distance Tracker</span>
                </div>
            </span>
            <div class="d-flex align-items-center gap-3">
                <span class="badge bg-dark border border-secondary text-light">
                    <span class="live-pulse"></span>Live Price Stream: Active
                </span>
                <span id="update-time" class="text-muted small">Updated: <span class="text-warning">{data.get('updated_at')}</span></span>
            </div>
        </div>
    </nav>

    <div class="container-fluid px-4">
        
        <!-- Live Win Rate Bar -->
        <div class="row g-3 mb-4">
            <div class="col-12 col-md-3">
                <div class="stat-card">
                    <div class="stat-title"><i class="fa-solid fa-trophy text-warning me-1"></i> Winning Percentage</div>
                    <div class="stat-value val-yellow">{win_rate}%</div>
                    <small class="text-muted">Target 1:3 R:R (Break-even is 25%)</small>
                </div>
            </div>
            <div class="col-12 col-md-3">
                <div class="stat-card">
                    <div class="stat-title"><i class="fa-solid fa-check-double text-success me-1"></i> Wins (1:3 TP Hit)</div>
                    <div class="stat-value val-green">{wins}</div>
                    <small class="text-muted">Each Win = +3x Risk Profit</small>
                </div>
            </div>
            <div class="col-12 col-md-3">
                <div class="stat-card">
                    <div class="stat-title"><i class="fa-solid fa-xmark text-danger me-1"></i> Losses (SL Hit)</div>
                    <div class="stat-value val-red">{losses}</div>
                    <small class="text-muted">Each Loss = -1x Risk</small>
                </div>
            </div>
            <div class="col-12 col-md-3">
                <div class="stat-card">
                    <div class="stat-title"><i class="fa-solid fa-scale-balanced text-info me-1"></i> Net Return (R)</div>
                    <div class="stat-value text-info">+{net_r} R</div>
                    <small class="text-muted">Total Multiplier Return</small>
                </div>
            </div>
        </div>

        <!-- Section 1: Active Signals -->
        <div class="mb-4">
            <div class="d-flex align-items-center justify-content-between mb-2">
                <h4 class="fw-bold mb-0 text-white">
                    <i class="fa-solid fa-bolt text-warning me-2"></i>Live Trading Signals (1:3 R:R Multi-TP)
                    <span class="badge bg-warning text-dark ms-2" id="active-total-badge">{data.get('active_count', 0)}</span>
                </h4>
                <small class="text-muted">Live prices auto-updating from Binance API every 4s</small>
            </div>

            <!-- Trade Category Filter Tabs -->
            <div class="d-flex flex-wrap gap-2 mb-3 mt-3">
                <button class="btn btn-sm btn-outline-light active filter-tab-btn" id="btn-filter-all" onclick="filterSignals('ALL')">
                    <i class="fa-solid fa-layer-group me-1"></i> All Signals (<span id="cnt-all">{data.get('active_count', 0)}</span>)
                </button>
                <button class="btn btn-sm btn-outline-success filter-tab-btn" id="btn-filter-ready" onclick="filterSignals('READY')">
                    <i class="fa-solid fa-circle-check me-1"></i> 🟢 Ganna Puluwan (Ready Now) (<span id="cnt-ready">0</span>)
                </button>
                <button class="btn btn-sm btn-outline-warning filter-tab-btn" id="btn-filter-limit" onclick="filterSignals('LIMIT')">
                    <i class="fa-solid fa-clock me-1"></i> 🟡 Pending Limit Orders (<span id="cnt-limit">0</span>)
                </button>
                <button class="btn btn-sm btn-outline-info filter-tab-btn" id="btn-filter-running" onclick="filterSignals('RUNNING')">
                    <i class="fa-solid fa-rocket me-1"></i> 🚀 Running In Profit (<span id="cnt-running">0</span>)
                </button>
                <a href="#history-section" class="btn btn-sm btn-outline-danger filter-tab-btn">
                    <i class="fa-solid fa-ban me-1"></i> 🛑 Expired / Closed Trades (<span id="cnt-expired">{losses + wins}</span>)
                </a>
            </div>

            <div id="signals-container" class="row g-3">
            </div>
        </div>

        <!-- Section 2: Watchlist -->
        <div class="mt-5">
            <h4 class="fw-bold mb-3 text-white">
                <i class="fa-solid fa-binoculars text-info me-2"></i>SMC Structure Tracker & Watchlist
            </h4>
            <div class="table-responsive table-dark-custom">
                <table class="table mb-0">
                    <thead>
                        <tr>
                            <th>Pair</th>
                            <th>Current Live Price</th>
                            <th>Daily RSI</th>
                            <th>Tier / Protection</th>
                            <th>1H Order Block</th>
                            <th>Daily S&R</th>
                            <th>Signal</th>
                            <th>Action</th>
                        </tr>
                    </thead>
                    <tbody id="watchlist-body">
                    </tbody>
                </table>
            </div>
        </div>

        <!-- Section 3: Signal Performance & Expired Trades -->
        <div class="mt-5 mb-5" id="history-section">
            <div class="d-flex align-items-center justify-content-between mb-3">
                <h4 class="fw-bold mb-0 text-white">
                    <i class="fa-solid fa-clock-rotate-left text-warning me-2"></i>Signal History & Trade Outcomes (Active & Expired)
                </h4>
                <span class="badge bg-dark border border-secondary text-muted">Auto-Tracks 1:3 TP & SL Outcomes</span>
            </div>
            <div class="table-responsive table-dark-custom">
                <table class="table mb-0">
                    <thead>
                        <tr>
                            <th>Signal Date</th>
                            <th>Pair</th>
                            <th>Type</th>
                            <th>Setup</th>
                            <th>Entry</th>
                            <th>Safe SL</th>
                            <th>TP1 (1:1.5)</th>
                            <th>TP3 (1:3.0)</th>
                            <th>Status / Outcome</th>
                            <th>PnL (R)</th>
                        </tr>
                    </thead>
                    <tbody id="history-body">
                    </tbody>
                </table>
            </div>
        </div>

    </div>

    <script>
        const EMBEDDED_DATA = {data_json_str};

        function renderUI(data) {{
            const active = data.active_signals || [];
            const container = document.getElementById('signals-container');
            container.innerHTML = "";

            if (active.length === 0) {{
                container.innerHTML = `
                    <div class="col-12">
                        <div class="signal-card text-center py-5">
                            <i class="fa-solid fa-clock-rotate-left fs-1 text-muted mb-3"></i>
                            <h5 class="text-white">No active pairs at exact entry trigger right now</h5>
                            <p class="text-muted small">All monitored coins are currently mid-range. See the Watchlist below for setups forming near Line S&R levels.</p>
                        </div>
                    </div>
                `;
            }} else {{
                active.forEach(sig => {{
                    const isLong = sig.signal.includes("BUY") || sig.signal.includes("LONG");
                    const cardClass = isLong ? "long" : "short";
                    const badgeClass = isLong ? "badge-long" : "badge-short";
                    const isTier2 = sig.tier_badge.includes("TIER 2");

                    const limitOrderType = isLong ? "Buy Limit" : "Sell Limit";
                    const limitBlock = sig.limit_setup ? `
                        <div class="p-3 mb-3 rounded-3" style="background: rgba(240, 185, 11, 0.08); border: 2px solid #f0b90b !important;">
                            <div class="d-flex justify-content-between align-items-center mb-2 pb-1 border-bottom border-warning border-opacity-25">
                                <span class="badge bg-warning text-dark fw-bold px-2 py-1" style="font-size: 0.78rem;">
                                    <i class="fa-solid fa-clock me-1"></i>RECOMMENDED SMC RETEST (LIMIT)
                                </span>
                                <span class="badge bg-dark border border-warning text-warning fw-bold" style="font-size: 0.75rem;">
                                    <i class="fa-solid fa-arrow-turn-down me-1"></i>Retest Pullback: +${{sig.limit_setup.dist_pct}}
                                </span>
                            </div>

                            <!-- Big Bold Limit Entry Price Box -->
                            <div class="text-center py-2 px-3 mb-2 rounded" style="background: #0d1117; border: 1px solid rgba(240, 185, 11, 0.4);">
                                <div class="text-warning text-uppercase fw-bold" style="font-size: 0.75rem; letter-spacing: 0.8px;">
                                    <i class="fa-solid fa-bullseye text-warning me-1"></i>BINANCE ${{limitOrderType.toUpperCase()}} ENTRY:
                                </div>
                                <div class="text-warning display-6 my-1" style="font-size: 1.65rem; font-weight: 900; letter-spacing: 0.5px;">
                                    $${{sig.limit_setup.limit_entry}}
                                </div>
                                <div class="text-white-50 small" style="font-size: 0.72rem;">
                                    👉 Binance එකේ <strong>${{limitOrderType}} Order</strong> එකක් දාන්න මේ Price එකට
                                </div>
                            </div>

                            <!-- Limit SL, TP1, TP3 Box -->
                            <div class="row g-2 text-center">
                                <div class="col-4">
                                    <div class="p-1 rounded" style="background: #161b22; border: 1px solid rgba(246, 70, 93, 0.3);">
                                        <div class="text-white-50 fw-bold" style="font-size: 0.68rem;">LIMIT SL</div>
                                        <div class="val-red fw-bold" style="font-size: 0.88rem;">$${{sig.limit_setup.limit_sl}}</div>
                                        <div class="text-danger" style="font-size: 0.65rem;">-${{sig.limit_setup.risk_pct}}</div>
                                    </div>
                                </div>
                                <div class="col-4">
                                    <div class="p-1 rounded" style="background: #161b22; border: 1px solid rgba(14, 203, 129, 0.3);">
                                        <div class="text-white-50 fw-bold" style="font-size: 0.68rem;">TP1 (1:1.5)</div>
                                        <div class="val-green fw-bold" style="font-size: 0.88rem;">$${{sig.limit_setup.limit_tp1 || '-'}}</div>
                                        <div class="text-white-50" style="font-size: 0.65rem;">50% + BE</div>
                                    </div>
                                </div>
                                <div class="col-4">
                                    <div class="p-1 rounded" style="background: #161b22; border: 1px solid rgba(14, 203, 129, 0.3);">
                                        <div class="text-white-50 fw-bold" style="font-size: 0.68rem;">TP3 (1:3.0)</div>
                                        <div class="val-green fw-bold" style="font-size: 0.88rem;">$${{sig.limit_setup.limit_tp}}</div>
                                        <div class="text-success" style="font-size: 0.65rem;">+${{sig.limit_setup.reward_pct}}</div>
                                    </div>
                                </div>
                            </div>
                        </div>
                    ` : '';

                    const col = document.createElement('div');
                    col.className = "col-12 col-md-6 col-lg-4 signal-col";
                    col.id = `card-col-${{sig.symbol}}`;
                    const initialCat = sig.limit_setup ? 'LIMIT' : 'READY';
                    col.setAttribute('data-category', initialCat);

                    let initialBorder = sig.limit_setup ? '#f0b90b' : '#0ecb81';
                    let initialBadge = sig.limit_setup ? 
                        '<span class="badge bg-warning text-dark py-1 px-2"><i class="fa-solid fa-clock me-1"></i>🟡 SET LIMIT ORDER</span>' :
                        '<span class="badge bg-success text-white py-1 px-2"><i class="fa-solid fa-circle-check me-1"></i>🟢 GANNA PULUWAN</span>';
                    let initialDesc = sig.limit_setup ?
                        `<span class="text-warning fw-bold">Pending Limit @ $${{sig.limit_setup.limit_entry}}</span>` :
                        '<span class="text-success fw-bold">Price in Entry Zone</span>';

                    const marketPlanHeader = sig.limit_setup ? `
                        <div class="d-flex align-items-center gap-2 mb-2 mt-1">
                            <small class="text-muted fw-bold text-uppercase" style="font-size: 0.68rem; letter-spacing: 0.5px;">
                                — OR — Instant Market Entry Plan
                            </small>
                            <div class="flex-grow-1 border-top border-secondary opacity-25"></div>
                        </div>
                    ` : '';

                    col.innerHTML = `
                        <div class="signal-card ${{cardClass}}">
                            <!-- Action Status Banner -->
                            <div class="d-flex justify-content-between align-items-center p-2 mb-2 action-status-bar" id="action-bar-${{sig.symbol}}" style="border-left-color: ${{initialBorder}};">
                                <div id="action-badge-${{sig.symbol}}">${{initialBadge}}</div>
                                <div id="action-desc-${{sig.symbol}}"><small>${{initialDesc}}</small></div>
                            </div>

                            <div class="d-flex justify-content-between align-items-center mb-2">
                                <div>
                                    <h5 class="fw-bold mb-0 text-white">${{sig.symbol}}</h5>
                                    <small class="text-muted">${{sig.setup || 'Daily Setup'}}</small>
                                </div>
                                <div class="d-flex align-items-center gap-2">
                                    <span class="${{isTier2 ? 'badge-tier2' : 'badge-tier1'}}">${{sig.tier_badge}}</span>
                                    <span class="${{badgeClass}}">${{sig.signal}}</span>
                                </div>
                            </div>

                            <!-- Live Price vs Entry Hero Banner -->
                            <div class="price-hero d-flex justify-content-between align-items-center">
                                <div>
                                    <div class="metric-title"><span class="live-pulse"></span>Live Price</div>
                                    <div class="h4 mb-0 fw-bold text-white live-price-val" id="live-${{sig.symbol}}">$${{sig.current_price}}</div>
                                </div>
                                <div class="text-end border-start border-secondary ps-3">
                                    <div class="metric-title">${{sig.limit_setup ? 'Market (Now)' : 'Market Entry'}}</div>
                                    <div class="h5 mb-0 fw-bold val-yellow">$${{sig.entry}}</div>
                                    <small class="fw-bold live-diff-val" id="diff-${{sig.symbol}}">0.00%</small>
                                </div>
                            </div>

                            ${{limitBlock}}

                            ${{marketPlanHeader}}

                            <!-- SL, TP, R:R Box -->
                            <div class="row g-2 price-box">
                                <div class="col-6 text-center">
                                    <div class="metric-title">Safe Structural SL</div>
                                    <div class="metric-val val-red">$${{sig.stop_loss}}</div>
                                    <small class="val-red">${{sig.risk_pct}}</small>
                                </div>
                                <div class="col-6 text-center border-start border-secondary">
                                    <div class="metric-title">Take Profit (1:3)</div>
                                    <div class="metric-val val-green">$${{sig.take_profit_1_3}}</div>
                                    <small class="val-green">+${{sig.reward_pct}}</small>
                                </div>
                            </div>

                            <!-- Multi-TP Scaled Targets -->
                            <div class="p-2 mb-2 rounded border border-secondary" style="background-color: #12161f !important;">
                                <div class="d-flex justify-content-between align-items-center mb-1 pb-1 border-bottom border-secondary">
                                    <small class="text-white fw-bold"><i class="fa-solid fa-layer-group text-warning me-1"></i>Multi-TP Scaled Targets</small>
                                    <span class="badge bg-secondary" style="font-size: 0.65rem;">Scale Out & Secure</span>
                                </div>
                                <div class="row g-1 text-center small">
                                    <div class="col-4 border-end border-secondary">
                                        <div class="text-muted" style="font-size: 0.7rem;">TP 1 (1:1.5)</div>
                                        <div class="fw-bold val-green" style="font-size: 0.85rem;">$${{sig.tp_1 || '-'}}</div>
                                        <div class="text-white-50" style="font-size: 0.65rem;">50% + SL to BE</div>
                                    </div>
                                    <div class="col-4 border-end border-secondary">
                                        <div class="text-muted" style="font-size: 0.7rem;">TP 2 (1:2.0)</div>
                                        <div class="fw-bold val-green" style="font-size: 0.85rem;">$${{sig.tp_2 || '-'}}</div>
                                        <div class="text-white-50" style="font-size: 0.65rem;">Take 25%</div>
                                    </div>
                                    <div class="col-4">
                                        <div class="text-muted" style="font-size: 0.7rem;">TP 3 (1:3.0)</div>
                                        <div class="fw-bold val-green" style="font-size: 0.85rem;">$${{sig.take_profit_1_3}}</div>
                                        <div class="text-white-50" style="font-size: 0.65rem;">Final Runner</div>
                                    </div>
                                </div>
                            </div>

                            <div class="ob-box mb-2">
                                <small class="text-white-50"><i class="fa-solid fa-cube me-1"></i>1H Order Block:</small>
                                <div class="text-white fw-bold">${{sig.order_block_1h || 'None'}}</div>
                            </div>

                            <div class="d-flex flex-wrap gap-2 mb-2">
                                <span class="badge bg-dark border border-secondary text-light">Daily RSI: ${{sig.rsi_daily}}</span>
                                <span class="badge bg-dark border border-secondary text-light">1H RSI: ${{sig.rsi_1h}}</span>
                                <span class="badge-rr">R:R 1:3</span>
                            </div>

                            <div class="mt-3 pt-2 border-top border-secondary">
                                <small class="text-muted d-block mb-1">Key Factors:</small>
                                <ul class="small text-light ps-3 mb-3">
                                    ${{(sig.reasons || []).map(r => `<li>${{r}}</li>`).join('')}}
                                </ul>
                                <a href="https://www.binance.com/en/trade/${{sig.symbol}}" target="_blank" class="btn btn-outline-warning btn-sm w-100 fw-bold">
                                    <i class="fa-solid fa-arrow-up-right-from-square me-1"></i> Trade on Binance
                                </a>
                            </div>
                        </div>
                    `;
                    container.appendChild(col);
                }});
            }}

            // Watchlist
            const tbody = document.getElementById('watchlist-body');
            tbody.innerHTML = "";
            const all = data.all_monitored || [];
            all.forEach(item => {{
                const isBuy = item.signal.includes("BUY") || item.signal.includes("LONG");
                const isSell = item.signal.includes("SELL") || item.signal.includes("SHORT");
                const sigBadge = isBuy ? '<span class="badge badge-long">LONG</span>' : (isSell ? '<span class="badge badge-short">SHORT</span>' : '<span class="badge bg-secondary">WATCHLIST</span>');

                const tr = document.createElement('tr');
                tr.innerHTML = `
                    <td class="fw-bold text-white">${{item.symbol}}</td>
                    <td class="text-white fw-bold" id="watch-${{item.symbol}}">$${{item.entry}}</td>
                    <td>${{item.rsi_daily}}</td>
                    <td><span class="badge bg-dark border border-secondary">${{item.tier_badge}}</span></td>
                    <td><small class="text-muted">${{item.order_block_1h || '-'}}</small></td>
                    <td><small class="text-success">$${{item.daily_support}}</small> / <small class="text-danger">$${{item.daily_resistance}}</small></td>
                    <td>${{sigBadge}}</td>
                    <td>
                        <a href="https://www.binance.com/en/trade/${{item.symbol}}" target="_blank" class="btn btn-sm btn-outline-light py-0 px-2">
                            Trade
                        </a>
                    </td>
                `;
                tbody.appendChild(tr);
            }});

            // Render History & Expired Signals
            const histBody = document.getElementById('history-body');
            if (histBody) {{
                histBody.innerHTML = "";
                const histList = (data.history && data.history.signals) ? data.history.signals : [];
                if (histList.length === 0) {{
                    histBody.innerHTML = '<tr><td colspan="10" class="text-center text-muted py-3">No trade signals recorded yet.</td></tr>';
                }} else {{
                    histList.slice().reverse().forEach(s => {{
                        let stBadge = '<span class="badge bg-warning text-dark"><i class="fa-solid fa-hourglass-half me-1"></i>OPEN / ACTIVE</span>';
                        let rText = '<span class="text-muted">Pending</span>';
                        if (s.status.includes("WIN (1:3") || s.status.includes("WIN (1:3 TP Hit)")) {{
                            stBadge = '<span class="badge bg-success"><i class="fa-solid fa-check-double me-1"></i>WIN (1:3 TP3 Hit)</span>';
                            rText = '<span class="val-green fw-bold">+3.0 R</span>';
                        }} else if (s.status.includes("TP1 Hit") || s.status.includes("BE")) {{
                            stBadge = '<span class="badge bg-info text-dark"><i class="fa-solid fa-shield-halved me-1"></i>WIN (TP1 Hit / BE)</span>';
                            rText = '<span class="val-green fw-bold">+0.75 R</span>';
                        }} else if (s.status.includes("LOSS")) {{
                            stBadge = '<span class="badge bg-danger"><i class="fa-solid fa-xmark me-1"></i>EXPIRED (SL Hit)</span>';
                            rText = '<span class="val-red fw-bold">-1.0 R</span>';
                        }}

                        const isLong = s.type.includes("BUY") || s.type.includes("LONG");
                        const typeBadge = isLong ? '<span class="badge badge-long">LONG</span>' : '<span class="badge badge-short">SHORT</span>';

                        const tr = document.createElement('tr');
                        tr.innerHTML = `
                            <td class="text-muted small">${{s.date || '-'}}</td>
                            <td class="fw-bold text-white">${{s.symbol}}</td>
                            <td>${{typeBadge}}</td>
                            <td><small class="text-muted">${{s.setup || '-'}}</small></td>
                            <td class="text-white fw-bold">$${{s.entry_str || s.entry}}</td>
                            <td class="val-red">$${{s.sl_str || s.sl}}</td>
                            <td class="val-green">$${{s.tp1_str || (s.tp1 ? s.tp1.toFixed(4) : '-')}}</td>
                            <td class="val-green">$${{s.tp_str || s.tp}}</td>
                            <td>${{stBadge}}</td>
                            <td>${{rText}}</td>
                        `;
                        histBody.appendChild(tr);
                    }});
                }}
            }}
        }}

        let currentFilter = 'ALL';
        function filterSignals(cat) {{
            currentFilter = cat;
            document.querySelectorAll('.filter-tab-btn').forEach(b => b.classList.remove('active'));
            const btn = document.getElementById(`btn-filter-${{cat.toLowerCase()}}`);
            if (btn) btn.classList.add('active');

            const cols = document.querySelectorAll('.signal-col');
            cols.forEach(col => {{
                const cCat = col.getAttribute('data-category');
                if (cat === 'ALL' || cCat === cat) {{
                    col.style.display = 'block';
                }} else {{
                    col.style.display = 'none';
                }}
            }});
        }}

        // Real-Time Live Price Polling from Binance API every 4 seconds!
        async function fetchLivePrices() {{
            try {{
                const resp = await fetch('https://api.binance.com/api/v3/ticker/price');
                if (!resp.ok) return;
                const tickerList = await resp.json();
                const priceMap = {{}};
                tickerList.forEach(t => priceMap[t.symbol] = parseFloat(t.price));

                let countReady = 0;
                let countLimit = 0;
                let countRunning = 0;

                const active = EMBEDDED_DATA.active_signals || [];
                active.forEach(sig => {{
                    const liveP = priceMap[sig.symbol];
                    if (liveP !== undefined) {{
                        const liveEl = document.getElementById(`live-${{sig.symbol}}`);
                        const diffEl = document.getElementById(`diff-${{sig.symbol}}`);
                        const watchEl = document.getElementById(`watch-${{sig.symbol}}`);

                        // Format live price
                        let pStr = liveP >= 1000 ? liveP.toLocaleString('en-US', {{minimumFractionDigits: 2, maximumFractionDigits: 2}}) :
                                   (liveP >= 1 ? liveP.toFixed(4) : (liveP >= 0.0001 ? liveP.toFixed(6) : liveP.toFixed(8)));

                        if (liveEl) liveEl.innerText = `$${{pStr}}`;
                        if (watchEl) watchEl.innerText = `$${{pStr}}`;

                        // Calculate distance from entry
                        const entry = sig.raw_entry;
                        const diffPct = ((liveP - entry) / entry) * 100;
                        const isLong = sig.signal.includes("BUY") || sig.signal.includes("LONG");
                        
                        let diffText = "";
                        let diffColor = "text-muted";

                        if (isLong) {{
                            if (Math.abs(diffPct) <= 0.7) {{
                                diffText = `🎯 In Entry Zone (${{diffPct >= 0 ? '+' : ''}}${{diffPct.toFixed(2)}}%)`;
                                diffColor = "val-green";
                            }} else if (diffPct > 0.7) {{
                                diffText = `🚀 Floating Profit (+${{diffPct.toFixed(2)}}%)`;
                                diffColor = "val-green";
                            }} else {{
                                diffText = `📉 Discount Dip (${{diffPct.toFixed(2)}}%)`;
                                diffColor = "val-yellow";
                            }}
                        }} else {{
                            if (Math.abs(diffPct) <= 0.7) {{
                                diffText = `🎯 In Entry Zone (${{-diffPct >= 0 ? '+' : ''}}${{(-diffPct).toFixed(2)}}%)`;
                                diffColor = "val-red";
                            }} else if (diffPct < -0.7) {{
                                diffText = `🚀 Floating Profit (+${{(-diffPct).toFixed(2)}}%)`;
                                diffColor = "val-green";
                            }} else {{
                                diffText = `📈 Slight Pullback (+${{diffPct.toFixed(2)}}%)`;
                                diffColor = "val-yellow";
                            }}
                        }}

                        if (diffEl) {{
                            diffEl.innerText = diffText;
                            diffEl.className = `fw-bold live-diff-val ${{diffColor}}`;
                        }}

                        // Determine live status category
                        let cat = "READY";
                        let barBorder = "#0ecb81";
                        let badgeHtml = "";
                        let descHtml = "";

                        if (sig.limit_setup) {{
                            cat = "LIMIT";
                            countLimit++;
                            barBorder = "#f0b90b";
                            badgeHtml = '<span class="badge bg-warning text-dark py-1 px-2"><i class="fa-solid fa-clock me-1"></i>🟡 SET LIMIT ORDER</span>';
                            descHtml = `<span class="text-warning fw-bold">Pending Limit @ $${{sig.limit_setup.limit_entry}}</span>`;
                        }} else if (isLong ? diffPct > 0.75 : diffPct < -0.75) {{
                            cat = "RUNNING";
                            countRunning++;
                            barBorder = "#0dcaf0";
                            badgeHtml = '<span class="badge bg-info text-dark py-1 px-2"><i class="fa-solid fa-rocket me-1"></i>🚀 RUNNING IN PROFIT</span>';
                            descHtml = `<span class="text-info fw-bold">+${{Math.abs(diffPct).toFixed(2)}}% Away (Do Not Chase)</span>`;
                        }} else {{
                            cat = "READY";
                            countReady++;
                            barBorder = "#0ecb81";
                            badgeHtml = '<span class="badge bg-success text-white py-1 px-2"><i class="fa-solid fa-circle-check me-1"></i>🟢 GANNA PULUWAN</span>';
                            descHtml = `<span class="text-success fw-bold">In Entry Zone (${{diffPct >= 0 ? '+' : ''}}${{diffPct.toFixed(2)}}%)</span>`;
                        }}

                        const barEl = document.getElementById(`action-bar-${{sig.symbol}}`);
                        if (barEl) {{
                            barEl.style.borderLeftColor = barBorder;
                            barEl.innerHTML = `<div id="action-badge-${{sig.symbol}}">${{badgeHtml}}</div><div id="action-desc-${{sig.symbol}}"><small>${{descHtml}}</small></div>`;
                        }}

                        const colEl = document.getElementById(`card-col-${{sig.symbol}}`);
                        if (colEl) {{
                            colEl.setAttribute('data-category', cat);
                            if (currentFilter === 'ALL' || currentFilter === cat) {{
                                colEl.style.display = 'block';
                            }} else {{
                                colEl.style.display = 'none';
                            }}
                        }}
                    }}
                }});

                // Update counter numbers
                const elAll = document.getElementById('cnt-all');
                const elReady = document.getElementById('cnt-ready');
                const elLimit = document.getElementById('cnt-limit');
                const elRunning = document.getElementById('cnt-running');
                if (elAll) elAll.innerText = active.length;
                if (elReady) elReady.innerText = countReady;
                if (elLimit) elLimit.innerText = countLimit;
                if (elRunning) elRunning.innerText = countRunning;

            }} catch (e) {{
                console.log("Binance direct ticker fetch skipped:", e);
            }}
        }}

        document.addEventListener('DOMContentLoaded', () => {{
            renderUI(EMBEDDED_DATA);
            fetchLivePrices();
            setInterval(fetchLivePrices, 4000); // Live tick every 4 seconds!
        }});
    </script>
</body>
</html>
"""
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html_content)

if __name__ == "__main__":
    scan_all_pairs()
