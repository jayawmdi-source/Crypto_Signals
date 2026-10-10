import sys
import time
import requests
import json
import math
import os
import re
import html
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor

sys.stdout.reconfigure(encoding='utf-8')

# Global Constants & Risk Limits (Pro-Trader Hybrid Adaptive Model)
MAX_ACTIVE_POSITIONS = 4          # Portfolio Heat Governor: Max 4 concurrent active trades
CIRCUIT_COOLDOWN_LOSSES = 2       # Triggers 4-Hour Volatility Cooldown after 2 losses
CIRCUIT_COOLDOWN_HOURS = 4        # 4 Hours market calming quarantine
MAX_DAILY_HARD_STOP_LOSSES = 3    # Absolute Daily Hard Stop (Full stop until 00:00 UTC)
MAX_DAILY_LOSSES = 2              # Backward-compatible reference
LIMIT_EXPIRY_HOURS = 24           # Pending limit orders expire if unfilled after 24h
FEE_BUFFER_PCT = 0.002            # 0.2% round-trip exchange fee buffer for True Break-Even

BINANCE_BASES = [
    "https://fapi.binance.com/fapi/v1",
    "https://fapi1.binance.com/fapi/v1",
    "https://fapi2.binance.com/fapi/v1",
    "https://fapi3.binance.com/fapi/v1"
]
BASE_URL = BINANCE_BASES[0]
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}

session = requests.Session()
session.headers.update(HEADERS)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
HISTORY_FILE = os.path.join(SCRIPT_DIR, "trade_history.json")

def load_dotenv(env_path=None):
    if not env_path:
        env_path = os.path.join(SCRIPT_DIR, ".env")
    if os.path.exists(env_path):
        try:
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        k = k.strip()
                        v = v.strip().strip('"').strip("'")
                        if k and k not in os.environ:
                            os.environ[k] = v
        except Exception:
            pass

load_dotenv()

try:
    from binance_futures_trader import BinanceFuturesTrader
    auto_trader = BinanceFuturesTrader()
except Exception as _e:
    auto_trader = None

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
        print("[!] Telegram alert skipped: Bot token or Chat ID not configured (telegram_config.json missing or incomplete).")
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
        elif resp.status_code == 400:
            # Fallback to plain text in case of unescaped HTML characters (<, >, &)
            import re
            plain_text = re.sub(r'<[^>]*>', '', message_html)
            fb_payload = {
                "chat_id": chat_id,
                "text": plain_text,
                "disable_web_page_preview": True
            }
            fb_resp = session.post(url, json=fb_payload, timeout=8)
            if fb_resp.status_code == 200:
                print("[+] Telegram alert sent successfully (via plain text fallback)!")
                return True
            else:
                print(f"[!] Telegram fallback failed ({fb_resp.status_code}): {fb_resp.text}")
                return False
        else:
            print(f"[!] Telegram failed ({resp.status_code}): {resp.text}")
            return False
    except Exception as e:
        print(f"[!] Telegram request error: {e}")
        return False

def get_market_session():
    utc_now = datetime.now(timezone.utc)
    hour = utc_now.hour + (utc_now.minute / 60.0)
    
    if 12.5 <= hour <= 16.0:
        return {
            "name": "London/NY Overlap",
            "badge": "🔥 PEAK VOLATILITY (London+NY)",
            "tier": "A+",
            "is_prime": True,
            "desc": "Peak Institutional Inflow & Trend Expansion"
        }
    elif 7.0 <= hour <= 12.5:
        return {
            "name": "London Session",
            "badge": "🇬🇧 LONDON SESSION",
            "tier": "A",
            "is_prime": True,
            "desc": "London Open Liquidity Sweeps"
        }
    elif 16.0 < hour <= 20.0:
        return {
            "name": "New York Afternoon",
            "badge": "🇺🇸 NY AFTERNOON",
            "tier": "B+",
            "is_prime": True,
            "desc": "NY Trend Continuation & Reversals"
        }
    elif 0.0 <= hour < 7.0:
        return {
            "name": "Asian Session",
            "badge": "🌏 ASIAN SESSION",
            "tier": "A-",
            "is_prime": True,
            "desc": "Active Asian Session Liquidity & Range Structure"
        }
    else:
        return {
            "name": "Late NY / Transition",
            "badge": "🌐 GLOBAL WRAP",
            "tier": "B",
            "is_prime": True,
            "desc": "Late NY / Asian Pre-Market Transition"
        }

def send_telegram_new_signal(sig):
    is_long = "BUY" in sig.get("signal", "") or "LONG" in sig.get("signal", "")
    icon = "🟢" if is_long else "🔴"
    action = "BUY / LONG 📈" if is_long else "SELL / SHORT 📉"
    
    ls = sig.get("limit_setup")
    action_status = sig.get("action_status", "READY")
    session_badge = sig.get("session_badge", "🌐 GLOBAL MARKET")
    fvg_tag = sig.get("fvg_str", "None")
    sweep_tag = sig.get("sweep_str", "None")
    mtf_tag = sig.get("mtf_status", "Aligned")
    
    if ls:
        status_tag = "🟡 <b>PENDING LIMIT ORDER (Wait for Retest)</b>"
        header = f"🟡 <b>NEW SMC LIMIT SIGNAL</b> {icon}\n👉 <b>Pending Limit Retest Pullback</b>"
        advice = f"💡 <b>උපදෙස:</b> Binance එකේ <b>{'Buy Limit' if is_long else 'Sell Limit'} Order</b> එකක් දාන්න <code>${ls.get('limit_entry')}</code> ට!"
        
        body = (
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🪙 <b>PAIR:</b> #{sig.get('symbol')}\n"
            f"⚡ <b>ACTION:</b> <b>{action}</b>\n"
            f"📊 <b>STATUS:</b> {status_tag}\n"
            f"🕒 <b>SESSION:</b> {session_badge}\n"
            f"⭐ <b>TIER:</b> {sig.get('tier_badge', 'TIER 1')}\n"
            f"📐 <b>SETUP:</b> {sig.get('setup', '-')}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🟡 <b>RECOMMENDED SMC LIMIT ORDER:</b>\n"
            f"👉 <b>Limit Entry:</b> <code>${ls.get('limit_entry')}</code> (Pullback {ls.get('dist_pct', '-')})\n"
            f"🛑 <b>Dynamic Safe SL (ATR):</b> <code>${ls.get('limit_sl')}</code> (-{ls.get('risk_pct', '-')})\n"
            f"🏆 <b>TP1 (1:1.5):</b> <code>${ls.get('limit_tp1', '-')}</code> (50% Book + True BE)\n"
            f"🏆 <b>TP2 (1:2.0):</b> <code>${ls.get('limit_tp2', '-')}</code> (25% Book + Lock +1.5R)\n"
            f"🏆 <b>TP3 (1:3.0):</b> <code>${ls.get('limit_tp')}</code> (+{ls.get('reward_pct', '-')}) [Full Win]\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🎯 <b>Instant Market Plan (Alternative):</b>\n"
            f"• Market Now: <code>${sig.get('entry')}</code> | SL: <code>${sig.get('stop_loss')}</code> | TP3: <code>${sig.get('take_profit_1_3')}</code>\n"
        )
    elif action_status == "RUNNING":
        status_tag = "🚀 <b>RUNNING IN PROFIT</b>"
        header = f"🚀 <b>ACTIVE SMC TRADE RUNNING</b> {icon}\n👉 <b>Running In Profit (Do Not Chase)</b>"
        advice = "⚠️ <b>උපදෙස:</b> මේ Trade එක දැනටමත් ලාභ ලබමින් දුවයි. දැන් අලුතෙන් Chase කරන්න එපා!"
        
        body = (
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🪙 <b>PAIR:</b> #{sig.get('symbol')}\n"
            f"⚡ <b>ACTION:</b> <b>{action}</b>\n"
            f"📊 <b>STATUS:</b> {status_tag}\n"
            f"🕒 <b>SESSION:</b> {session_badge}\n"
            f"⭐ <b>TIER:</b> {sig.get('tier_badge', 'TIER 1')}\n"
            f"📐 <b>SETUP:</b> {sig.get('setup', '-')}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🎯 <b>ENTRY:</b> <code>${sig.get('entry')}</code>\n"
            f"📈 <b>LIVE PRICE:</b> <code>${sig.get('current_price')}</code> ({sig.get('action_label', '')})\n"
            f"🛑 <b>CURRENT SL:</b> <code>${sig.get('trailing_sl_str', sig.get('stop_loss'))}</code>\n"
            f"🏆 <b>TP 1:</b> <code>${sig.get('tp_1', '-')}</code> | <b>TP 3:</b> <code>${sig.get('take_profit_1_3')}</code>\n"
        )
    else:
        status_tag = "🟢 <b>READY NOW (Ganna Puluwan!)</b>"
        header = f"🚀 <b>NEW SMC TRADE SIGNAL</b> {icon}\n👉 <b>READY NOW (Ganna Puluwan!)</b>"
        advice = f"⚡ <b>උපදෙස:</b> මිල දැන් තියෙන්නේ Entry Zone එකේ. <b>දැන්ම Market Entry</b> එකක් ගන්න පුළුවන්!"
        
        body = (
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🪙 <b>PAIR:</b> #{sig.get('symbol')}\n"
            f"⚡ <b>ACTION:</b> <b>{action}</b>\n"
            f"📊 <b>STATUS:</b> {status_tag}\n"
            f"🕒 <b>SESSION:</b> {session_badge}\n"
            f"⭐ <b>TIER:</b> {sig.get('tier_badge', 'TIER 1')}\n"
            f"📐 <b>SETUP:</b> {sig.get('setup', '-')}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🎯 <b>MARKET ENTRY:</b> <code>${sig.get('entry')}</code> (In Entry Zone)\n"
            f"🛑 <b>Dynamic Safe SL (ATR):</b> <code>${sig.get('stop_loss')}</code> (-{sig.get('risk_pct', '-')})\n"
            f"🏆 <b>TP 1 (1:1.5):</b> <code>${sig.get('tp_1', '-')}</code> (Book 50% + SL to True BE)\n"
            f"🏆 <b>TP 2 (1:2.0):</b> <code>${sig.get('tp_2', '-')}</code> (Book 25% + Lock +1.5R)\n"
            f"🏆 <b>TP 3 (1:3.0):</b> <code>${sig.get('take_profit_1_3')}</code> (+{sig.get('reward_pct', '-')}) [Full Target]\n"
        )

    reasons_bullets = "\n".join([f"• {r}" for r in sig.get("reasons", [])[:3]])

    msg = (
        f"{header}\n"
        f"{body}"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"{advice}\n\n"
        f"💧 <b>Fair Value Gap (FVG):</b> {fvg_tag}\n"
        f"⚡ <b>Liquidity Sweep:</b> {sweep_tag}\n"
        f"⏳ <b>4H Multi-Timeframe:</b> {mtf_tag}\n"
        f"📦 <b>1H Order Block:</b> {sig.get('order_block_1h', 'None')}\n"
        f"📊 <b>RSI:</b> Daily {sig.get('rsi_daily', '-')} | 1H {sig.get('rsi_1h', '-')}\n\n"
        f"💡 <b>Key Confirmations:</b>\n{reasons_bullets}\n\n"
        f"🔗 <a href=\"https://www.binance.com/en/trade/{sig.get('symbol')}\">Trade #{sig.get('symbol')} on Binance</a>\n"
        f"🌐 <a href=\"https://jayawmdi-source.github.io/Crypto_Signals/\">Open Live SMC Dashboard</a>"
    )
    send_telegram_message(msg.strip())

def send_telegram_execution_alert(exec_res, sig, is_retest=False):
    """
    Sends an instant, dedicated alert when an order is placed & filled on Binance Futures.
    """
    sym = exec_res.get("symbol") or sig.get("symbol", "")
    side = exec_res.get("side") or ("BUY" if ("BUY" in sig.get("type", "") or "LONG" in sig.get("type", "")) else "SELL")
    is_long = "BUY" in side or "LONG" in side
    icon = "🟢" if is_long else "🔴"
    action = "BUY / LONG 📈" if is_long else "SELL / SHORT 📉"
    
    margin = float(exec_res.get("margin_usdt") or 0.0)
    leverage = exec_res.get("leverage") or 10
    entry_p = exec_res.get("entry_price") or sig.get("raw_entry", 0.0)
    sl_p = exec_res.get("sl_price") or sig.get("stop_loss", "-")
    tp1_p = exec_res.get("tp1_price") or sig.get("tp_1", "-")
    tp2_p = exec_res.get("tp2_price") or sig.get("tp_2", "-")
    tp3_p = exec_res.get("tp_price") or sig.get("take_profit_1_3", "-")
    order_id = exec_res.get("order_id", "-")
    sl_order_id = exec_res.get("sl_order_id", "-")
    qty = exec_res.get("qty", "-")

    risk_label = "5% Defensive Allocation 🛡️" if (exec_res.get("is_defensive") or sig.get("is_defensive")) else "10% Risk Allocation"
    header_title = "BINANCE LIVE RETEST TRADE EXECUTED! 🎯" if is_retest else "BINANCE LIVE TRADE EXECUTED!"
    msg = (
        f"🚨 <b>{header_title}</b> {icon}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🪙 <b>PAIR:</b> #{sym}\n"
        f"⚡ <b>ACTION:</b> <b>{action} ({leverage}x Isolated)</b>\n"
        f"💵 <b>MARGIN USED:</b> <code>${margin:.2f} USDT</code> ({risk_label})\n"
        f"📦 <b>ORDER QTY:</b> <code>{qty}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🎯 <b>FILLED ENTRY:</b> <code>${fmt_price(entry_p)}</code>\n"
        f"🛑 <b>STOP LOSS:</b> <code>${sl_p}</code> {'(Binance STOP_MARKET Active 🛡️)' if sl_order_id else '(Software Trailed 🛡️)'}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🏆 <b>INSTITUTIONAL TARGETS:</b>\n"
        f"  • <b>TP1 (1:1.5):</b> <code>${tp1_p}</code> (50% Close + Move to Break-Even)\n"
        f"  • <b>TP2 (1:2.0):</b> <code>${tp2_p}</code> (25% Close + Lock +1.5R)\n"
        f"  • <b>TP3 (1:3.0):</b> <code>${tp3_p}</code> (Full Target)\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🛡️ <b>RISK MANAGEMENT:</b>\n"
        f"• Auto-Trailing Stop Loss: <b>ACTIVE</b>\n"
        f"• Daily Drawdown Shield: <b>ACTIVE</b>\n"
        f"• Binance Order ID: <code>{order_id}</code>\n\n"
        f"🔗 <a href=\"https://www.binance.com/en/trade/{sym}\">View Live Trade on Binance</a>\n"
        f"🌐 <a href=\"https://jayawmdi-source.github.io/Crypto_Signals/\">Open Live SMC Dashboard</a>"
    )
    send_telegram_message(msg.strip())

def send_telegram_resolution(sig, event_type):
    sym = sig.get('symbol') if sig else "PORTFOLIO"
    if event_type == "WIN_TP3":
        msg = (
            f"🎉 <b>1:3 FULL TARGET HIT! (WIN +3.0R)</b> 🏆\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🪙 #{sym} reached its <b>1:3 Full Take Profit</b> Target (<code>${sig.get('tp_str', sig.get('tp'))}</code>)!\n"
            f"💰 <b>Net Gain: +3.0 R Profit!</b>\n"
            f"✅ Trade completed with maximum institutional target."
        )
    elif event_type == "WIN_TP2":
        msg = (
            f"🚀 <b>TP2 HIT! (1:2.0) - PROFIT LOCKED (+1.5R)!</b> 🔒\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🪙 #{sym} hit TP2 at <code>${sig.get('tp2_str', sig.get('tp2'))}</code>!\n"
            f"💰 <b>Action:</b> Book an additional 25% Profit!\n"
            f"🛡️ <b>Action (Trailing SL):</b> Trail Stop Loss UP to <b>TP1 (<code>${sig.get('tp1_str', sig.get('tp1'))}</code>)</b>!\n"
            f"✨ This trade is now GUARANTEED to exit with at least <b>+1.5R Profit</b> even if market reverses!"
        )
    elif event_type == "WIN_TP1":
        msg = (
            f"🛡️ <b>TP1 HIT (1:1.5) & PROFIT SECURED!</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🪙 #{sym} hit TP1 at <code>${sig.get('tp1_str', sig.get('tp1'))}</code>!\n"
            f"💰 <b>Action:</b> Book 50% Profit now!\n"
            f"🔒 <b>Action:</b> Move Stop Loss to <b>True Break-Even (<code>${sig.get('trailing_sl_str', sig.get('entry_str'))}</code>)</b> [0.2% Fee Buffer included] for a 100% Zero-Risk Runner!"
        )
    elif event_type == "WIN_LOCKED":
        msg = (
            f"🏆 <b>TRAILED PROFIT STOPPED OUT (WIN +1.5R)</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🪙 #{sym} touched Trailing Stop at <code>${sig.get('trailing_sl_str', sig.get('tp1_str'))}</code> after securing TP2.\n"
            f"💰 <b>Net Gain: +1.5 R Profit Secured!</b>\n"
            f"Trade resolved successfully with locked gains."
        )
    elif event_type == "WIN_BE":
        msg = (
            f"🛡️ <b>BREAK-EVEN EXIT (WIN +0.75R)</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🪙 #{sym} returned to True Break-Even after securing TP1.\n"
            f"💰 <b>Net Gain: +0.75 R Secured!</b> (Zero loss, initial 50% profit banked).\n"
            f"Capital 100% protected."
        )
    elif event_type == "LOSS_SL":
        msg = (
            f"🛑 <b>STOP LOSS HIT</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🪙 #{sym} touched Safe SL at <code>${sig.get('sl_str', sig.get('sl'))}</code>.\n"
            f"📉 Net Loss: -1.0 R\n"
            f"Trade closed strictly adhering to risk management rules."
        )
    elif event_type == "LIMIT_FILLED":
        msg = (
            f"⚡ <b>PENDING LIMIT ORDER FILLED!</b> 🟢\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🪙 #{sym} retraced cleanly to the SMC Entry Zone at <code>${sig.get('entry_str', sig.get('entry'))}</code>!\n"
            f"📈 Trade is now officially <b>ACTIVE (OPEN)</b>.\n"
            f"🛑 SL: <code>${sig.get('sl_str', sig.get('sl'))}</code> | 🏆 TP1: <code>${sig.get('tp1_str', sig.get('tp1'))}</code> | 🏆 TP3: <code>${sig.get('tp_str', sig.get('tp'))}</code>"
        )
    elif event_type == "CIRCUIT_COOLDOWN":
        rem_m = sig.get("mins", 240) if sig else 240
        resume_t = sig.get("resume_time", "") if sig else ""
        msg = (
            f"⏳ <b>CIRCUIT BREAKER: 4-HOUR VOLATILITY COOLDOWN</b> 🛡️\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"⚠️ Two trades have hit Stop Loss today. Rather than a rigid 24-hour freeze, the bot has entered a <b>4-Hour Market Calming Quarantine</b> to let flash news & macro chop settle.\n\n"
            f"⏱️ <b>Auto-Resume:</b> in ~{rem_m} mins {f'({resume_t} UTC)' if resume_t else ''}\n"
            f"🛡️ <b>Post-Cooldown Strategy:</b> Defensive Half-Risk (5% Margin) on next test setup.\n"
            f"🔒 Capital Preservation: Active."
        )
    elif event_type == "CIRCUIT_RESUME":
        msg = (
            f"🟢 <b>CIRCUIT BREAKER COOLDOWN EXPIRED</b> 🚀\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"✅ Market volatility has settled. Auto-trading has automatically resumed in <b>Defensive Mode (5% Risk)</b>.\n"
            f"The first test setup will use 5% margin to safely re-enter the trend before returning to normal 10% risk."
        )
    elif event_type == "CIRCUIT_HARD_STOP":
        msg = (
            f"🛑 <b>DAILY DRAWDOWN HARD STOP (3 LOSSES)</b> 🛑\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"⚠️ Three trades hit Stop Loss today. To strictly protect capital against sustained adverse market conditions, all new trading is stopped for the rest of the UTC day.\n"
            f"🌅 Trading will automatically resume tomorrow at 00:00 UTC."
        )
    elif event_type == "DEFENSIVE_CLEARED":
        msg = (
            f"🎯 <b>DEFENSIVE TEST TRADE SECURED!</b> 🟢\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"Trade reached TP1 (Break-Even locked)! Defensive test successfully passed.\n"
            f"🚀 <b>Normal 10% Position Sizing is now fully restored!</b>"
        )
    elif event_type == "CIRCUIT_BREAKER":
        msg = (
            f"🛑 <b>DAILY DRAWDOWN SHIELD ACTIVATED</b> 🛡️\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"⚠️ Stop Loss threshold reached today. To protect trading capital against adverse macro volatility, the bot has entered cooldown mode.\n"
            f"🔒 Capital Preservation Mode: Active."
        )
    else:
        return
    send_telegram_message(msg.strip())

def send_telegram_live_close(sym, pnl_usdt, status_label, outcome_r=0.0):
    """
    Sends an instant dedicated Telegram alert when a live Binance Futures position is closed.
    """
    is_win = pnl_usdt >= 0
    icon = "🎉" if is_win else "🔴"
    pnl_sign = "+" if pnl_usdt >= 0 else ""
    title_res = "LIVE TRADE CLOSED (PROFIT)" if is_win else "LIVE TRADE CLOSED (EXIT/SL)"
    
    msg = (
        f"{icon} <b>BINANCE {title_res}</b> {icon}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🪙 <b>PAIR:</b> #{sym}\n"
        f"📊 <b>STATUS:</b> {status_label}\n"
        f"💵 <b>REALIZED PNL:</b> <code>{pnl_sign}${pnl_usdt:.2f} USDT</code> ({pnl_sign}{outcome_r:.1f} R)\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🛡️ <b>Binance Execution:</b> Position closed & margin released.\n\n"
        f"🔗 <a href=\"https://www.binance.com/en/trade/{sym}\">View on Binance</a>\n"
        f"🌐 <a href=\"https://jayawmdi-source.github.io/Crypto_Signals/\">Open Live SMC Dashboard</a>"
    )
    send_telegram_message(msg.strip())

def get_klines(symbol, interval="1d", limit=100):
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    for base in BINANCE_BASES:
        url = f"{base}/klines"
        try:
            resp = session.get(url, params=params, timeout=6)
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

def fmt_price(val):
    if val is None or val == 0:
        return "-"
    if val >= 1000:
        return f"{val:,.2f}"
    elif val >= 1:
        return f"{val:.4f}"
    elif val >= 0.0001:
        return f"{val:.6f}"
    else:
        return f"{val:.8f}"

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

def detect_1h_fvg(klines_1h):
    if not klines_1h or len(klines_1h) < 15:
        return None, None
    bullish_fvgs = []
    bearish_fvgs = []
    n = len(klines_1h)
    current_price = float(klines_1h[-1][4])
    
    for i in range(2, n - 1):
        prev_h = float(klines_1h[i-2][2])
        prev_l = float(klines_1h[i-2][3])
        curr_h = float(klines_1h[i][2])
        curr_l = float(klines_1h[i][3])
        
        # Bullish FVG: Low of candle i > High of candle i-2
        if curr_l > prev_h:
            gap_size = curr_l - prev_h
            gap_pct = (gap_size / prev_h) * 100
            if gap_pct >= 0.2:
                fvg_top = curr_l
                fvg_bottom = prev_h
                # Check mitigation: did any subsequent candle low drop into or below FVG bottom?
                is_mitigated = any(float(klines_1h[j][3]) <= fvg_bottom for j in range(i + 1, n))
                is_in_fvg = (fvg_bottom * 0.998 <= current_price <= fvg_top * 1.002)
                if not is_mitigated:
                    bullish_fvgs.append({
                        "type": "Bullish FVG",
                        "top": fvg_top,
                        "bottom": fvg_bottom,
                        "gap_pct": round(gap_pct, 2),
                        "age_hours": n - 1 - i,
                        "is_testing": is_in_fvg
                    })
                    
        # Bearish FVG: High of candle i < Low of candle i-2
        if curr_h < prev_l:
            gap_size = prev_l - curr_h
            gap_pct = (gap_size / prev_l) * 100
            if gap_pct >= 0.2:
                fvg_top = prev_l
                fvg_bottom = curr_h
                is_mitigated = any(float(klines_1h[j][2]) >= fvg_top for j in range(i + 1, n))
                is_in_fvg = (fvg_bottom * 0.998 <= current_price <= fvg_top * 1.002)
                if not is_mitigated:
                    bearish_fvgs.append({
                        "type": "Bearish FVG",
                        "top": fvg_top,
                        "bottom": fvg_bottom,
                        "gap_pct": round(gap_pct, 2),
                        "age_hours": n - 1 - i,
                        "is_testing": is_in_fvg
                    })
                    
    return (bullish_fvgs[-1] if bullish_fvgs else None), (bearish_fvgs[-1] if bearish_fvgs else None)

def detect_liquidity_sweep(klines_1h):
    if not klines_1h or len(klines_1h) < 25:
        return {"has_bull_sweep": False, "has_bear_sweep": False, "details": "None"}
    highs = [float(k[2]) for k in klines_1h]
    lows = [float(k[3]) for k in klines_1h]
    closes = [float(k[4]) for k in klines_1h]
    n = len(klines_1h)
    
    lookback_high = max(highs[-30:-3])
    lookback_low = min(lows[-30:-3])
    
    has_bull_sweep = any(lows[i] < lookback_low and closes[i] > lookback_low for i in range(n - 3, n))
    has_bear_sweep = any(highs[i] > lookback_high and closes[i] < lookback_high for i in range(n - 3, n))
    
    details = "None"
    if has_bull_sweep:
        details = f"Sell-side Liquidity Swept (${fmt_price(lookback_low)})"
    elif has_bear_sweep:
        details = f"Buy-side Liquidity Swept (${fmt_price(lookback_high)})"
        
    return {
        "has_bull_sweep": has_bull_sweep,
        "has_bear_sweep": has_bear_sweep,
        "lookback_high": lookback_high,
        "lookback_low": lookback_low,
        "details": details
    }

def detect_1h_order_blocks(klines_1h, bull_fvg=None, bear_fvg=None):
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
        
        # Bullish OB: Bearish candle followed by strong bullish displacement
        if c < o and (next_c - next_o) > (avg_body * 1.2) and next_c > h:
            ob_top = h
            ob_bottom = l
            # True Mitigation: Did subsequent price touch into the OB zone?
            touches = sum(1 for j in range(i + 2, n) if float(klines_1h[j][3]) <= ob_top and float(klines_1h[j][2]) >= ob_bottom)
            is_invalidated = any(float(klines_1h[j][4]) < ob_bottom for j in range(i + 2, n))
            
            # An institutional OB is highest quality if fresh (tested <= 2 times) and not broken
            if not is_invalidated and touches <= 2:
                bullish_obs.append({
                    "type": "Bullish 1H OB (Demand)",
                    "top": ob_top,
                    "bottom": ob_bottom,
                    "age_hours": n - 1 - i,
                    "touches": touches,
                    "has_fvg": bool(bull_fvg and bull_fvg['age_hours'] >= (n - 1 - i - 2)),
                    "is_testing": (ob_bottom * 0.995 <= current_price <= ob_top * 1.015)
                })

        # Bearish OB: Bullish candle followed by strong bearish displacement
        if c > o and (next_o - next_c) > (avg_body * 1.2) and next_c < l:
            ob_top = h
            ob_bottom = l
            touches = sum(1 for j in range(i + 2, n) if float(klines_1h[j][2]) >= ob_bottom and float(klines_1h[j][3]) <= ob_top)
            is_invalidated = any(float(klines_1h[j][4]) > ob_top for j in range(i + 2, n))
            
            if not is_invalidated and touches <= 2:
                bearish_obs.append({
                    "type": "Bearish 1H OB (Supply)",
                    "top": ob_top,
                    "bottom": ob_bottom,
                    "age_hours": n - 1 - i,
                    "touches": touches,
                    "has_fvg": bool(bear_fvg and bear_fvg['age_hours'] >= (n - 1 - i - 2)),
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

def get_4h_macro_bias(symbol):
    klines_4h = get_klines(symbol, interval="4h", limit=50)
    if not klines_4h or len(klines_4h) < 25:
        return {"bias": "NEUTRAL", "ema_status": "NEUTRAL", "reason": "Insufficient 4H Data"}
        
    closes = [float(k[4]) for k in klines_4h]
    ema20_series = calculate_ema(closes, 20)
    ema50_series = calculate_ema(closes, 50)
    
    if not ema20_series or not ema50_series:
        return {"bias": "NEUTRAL", "ema_status": "NEUTRAL", "reason": "Calculating"}
        
    curr_c = closes[-1]
    e20 = ema20_series[-1]
    e50 = ema50_series[-1]
    rsi_4h = calculate_rsi(closes, 14)
    
    is_bull = (e20 > e50) and (curr_c >= e50 * 0.985)
    is_bear = (e20 < e50) and (curr_c <= e50 * 1.015)
    
    bias = "BULLISH" if is_bull else ("BEARISH" if is_bear else "NEUTRAL")
    return {
        "bias": bias,
        "ema20_4h": e20,
        "ema50_4h": e50,
        "rsi_4h": round(rsi_4h, 1),
        "reason": f"4H 20/50 EMA: {'Bullish' if e20 > e50 else 'Bearish'} | 4H RSI: {rsi_4h:.1f}"
    }

def detect_15m_choch(klines_15m):
    if not klines_15m or len(klines_15m) < 20:
        return {"has_bullish_choch": False, "has_bearish_choch": False, "status": "Insufficient 15M Data"}
    highs = [float(k[2]) for k in klines_15m]
    lows = [float(k[3]) for k in klines_15m]
    closes = [float(k[4]) for k in klines_15m]
    current_price = closes[-1]
    
    recent_high = max(highs[-12:-1])
    recent_low = min(lows[-12:-1])
    
    has_bullish_choch = current_price > recent_high
    has_bearish_choch = current_price < recent_low
    return {
        "has_bullish_choch": has_bullish_choch,
        "has_bearish_choch": has_bearish_choch,
        "recent_high": recent_high,
        "recent_low": recent_low,
        "status": "15M Bullish CHoCH 🟢" if has_bullish_choch else ("15M Bearish CHoCH 🔴" if has_bearish_choch else "15M Range")
    }

def detect_5m_choch(klines_5m):
    if not klines_5m or len(klines_5m) < 15:
        return {"has_bullish_choch": False, "has_bearish_choch": False, "status": "Insufficient 5M Data"}
    highs = [float(k[2]) for k in klines_5m]
    lows = [float(k[3]) for k in klines_5m]
    closes = [float(k[4]) for k in klines_5m]
    current_price = closes[-1]
    
    recent_high = max(highs[-8:-1])
    recent_low = min(lows[-8:-1])
    
    has_bullish_choch = current_price > recent_high
    has_bearish_choch = current_price < recent_low
    return {
        "has_bullish_choch": has_bullish_choch,
        "has_bearish_choch": has_bearish_choch,
        "recent_high": recent_high,
        "recent_low": recent_low,
        "status": "5M Bullish CHoCH 🟢" if has_bullish_choch else ("5M Bearish CHoCH 🔴" if has_bearish_choch else "5M Range")
    }

def verify_retest_confirmation(symbol, sig_type, l_entry, sl_price):
    """
    SMC 2.0 Institutional Confirmation Engine for Retest Entries:
    1. Validates 4H Macro Trend alignment (Trend is your Friend rule).
    2. Enforces Stop Loss Safety Barrier (ensures price didn't knife through SL).
    3. Checks 15M/5M Candle Rejection or Micro-Structure CHoCH inside the zone.
    """
    is_long = "BUY" in str(sig_type).upper() or "LONG" in str(sig_type).upper()
    
    # 1. Check 4H Macro Trend Alignment
    macro_info = get_4h_macro_bias(symbol)
    macro_bias = str(macro_info.get("bias", "")).upper()
    if is_long and "BEARISH" in macro_bias:
        return False, "4H Macro is BEARISH - counter-trend Longs blocked"
    if not is_long and "BULLISH" in macro_bias:
        return False, "4H Macro is BULLISH - counter-trend Shorts blocked"

    # 2. Fetch Lower Timeframe Klines (15M & 5M)
    klines_15m = get_klines(symbol, interval="15m", limit=15)
    klines_5m = get_klines(symbol, interval="5m", limit=15)
    
    if not klines_15m or len(klines_15m) < 5:
        return False, "Insufficient 15M candle data"

    latest_15m = klines_15m[-1]
    curr_price = float(latest_15m[4])
    
    # 3. Stop Loss Hard Barrier Check (Falling Knife / Spike Protection)
    if is_long and curr_price <= sl_price:
        return False, f"Price breached SL (${curr_price} <= ${sl_price}) - Zone failed"
    if not is_long and curr_price >= sl_price:
        return False, f"Price breached SL (${curr_price} >= ${sl_price}) - Zone failed"

    # 4. Reaction & Rejection Confirmation
    ch_15m = detect_15m_choch(klines_15m)
    ch_5m = detect_5m_choch(klines_5m) if klines_5m else {}
    
    has_choch = (ch_15m.get("has_bullish_choch") or ch_5m.get("has_bullish_choch")) if is_long else (ch_15m.get("has_bearish_choch") or ch_5m.get("has_bearish_choch"))
    if has_choch:
        return True, "Lower-timeframe (15M/5M) CHoCH confirmed"

    # Check Candle Rejection Wicks / Price Reaction
    candles_to_check = [klines_15m[-1]]
    if len(klines_15m) >= 2:
        candles_to_check.append(klines_15m[-2])
    if klines_5m and len(klines_5m) >= 2:
        candles_to_check.append(klines_5m[-1])
        candles_to_check.append(klines_5m[-2])

    for c in candles_to_check:
        o = float(c[1])
        h = float(c[2])
        l = float(c[3])
        cl = float(c[4])
        rng = h - l
        if rng <= 0:
            continue
        
        if is_long:
            lower_wick = min(o, cl) - l
            if (lower_wick / rng) >= 0.25 or (cl >= o and cl > sl_price):
                return True, "Demand rejection wick / bullish candle confirmed"
        else:
            upper_wick = h - max(o, cl)
            if (upper_wick / rng) >= 0.25 or (cl <= o and cl < sl_price):
                return True, "Supply rejection wick / bearish candle confirmed"

    if is_long and curr_price > sl_price:
        return True, "Price holding above SL in Demand Zone"
    if not is_long and curr_price < sl_price:
        return True, "Price holding below SL in Supply Zone"

    return False, "Awaiting clearer reaction"

def get_futures_oi_and_funding(symbol):
    funding_pct = 0.0
    oi_val = 0.0
    try:
        url_f = f"https://fapi.binance.com/fapi/v1/fundingRate?symbol={symbol}&limit=1"
        res_f = session.get(url_f, timeout=4)
        if res_f.status_code == 200:
            d = res_f.json()
            if d and len(d) > 0:
                funding_pct = float(d[0].get("fundingRate", 0.0)) * 100
                
        url_oi = f"https://fapi.binance.com/fapi/v1/openInterest?symbol={symbol}"
        res_oi = session.get(url_oi, timeout=4)
        if res_oi.status_code == 200:
            d_oi = res_oi.json()
            oi_val = float(d_oi.get("openInterest", 0.0))
    except Exception:
        pass
    return {
        "funding_pct": round(funding_pct, 4),
        "open_interest": oi_val,
        "is_funding_excessive_long": funding_pct > 0.035,
        "is_funding_excessive_short": funding_pct < -0.035
    }

def analyze_symbol(symbol, anchored_signal=None, btc_sentiment=None, session_info=None):
    daily_klines = get_klines(symbol, interval="1d", limit=100)
    klines_1h = get_klines(symbol, interval="1h", limit=80)
    
    if not daily_klines or len(daily_klines) < 50 or not klines_1h or len(klines_1h) < 30:
        return None
        
    session_info = session_info or get_market_session()
    
    close_prices = [float(k[4]) for k in daily_klines]
    volumes = [float(k[5]) for k in daily_klines]
    current_price = close_prices[-1]
    
    historical_closes = close_prices[:-1]
    supports, resistances = find_line_chart_sr(historical_closes, window=3)
    
    supports_below = [s for s in supports if s <= current_price]
    resistances_above = [r for r in resistances if r >= current_price]
    
    nearest_support = max(supports_below) if supports_below else min(historical_closes[-30:])
    nearest_resistance = min(resistances_above) if resistances_above else max(historical_closes[-30:])
    
    # 1H Confluences: FVG, Liquidity Sweeps, Order Blocks, CHoCH, ATR
    bull_fvg_1h, bear_fvg_1h = detect_1h_fvg(klines_1h)
    sweep_1h = detect_liquidity_sweep(klines_1h)
    bull_ob_1h, bear_ob_1h = detect_1h_order_blocks(klines_1h, bull_fvg_1h, bear_fvg_1h)
    choch_data = detect_1h_choch(klines_1h)
    atr_1h = calculate_atr(klines_1h, 14)
    
    # 4H Macro Bias
    mtf_4h = get_4h_macro_bias(symbol)
    
    # 15M & 5M Sniper Confluence & Open Interest / Funding Rate Scraper
    klines_15m = get_klines(symbol, interval="15m", limit=40)
    klines_5m = get_klines(symbol, interval="5m", limit=30)
    choch_15m = detect_15m_choch(klines_15m)
    choch_5m = detect_5m_choch(klines_5m)
    derivatives_info = get_futures_oi_and_funding(symbol)
    
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

    # Strict S/R Flip Validation: Broken recently (within 15 days) with volume expansion
    is_sr_flip = False
    flip_lvl = None
    resistances_below = [r for r in resistances if r < current_price]
    if resistances_below:
        candidate_res = max(resistances_below)
        breakout_valid = False
        # Look for candle within last 15 days that closed above candidate_res with volume
        for idx in range(max(1, len(close_prices)-15), len(close_prices)):
            if close_prices[idx] > candidate_res and close_prices[idx-1] <= candidate_res:
                if volumes[idx] >= vol_sma_20 * 1.05:
                    breakout_valid = True
                    break
        if breakout_valid:
            dist_above = ((current_price - candidate_res) / current_price) * 100
            if 0.1 <= dist_above <= 2.8:
                is_sr_flip = True
                flip_lvl = candidate_res

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
    fvg_str = "None"
    sweep_str = sweep_1h.get("details", "None")
    choch_badge = "No CHoCH"
    limit_setup = None
    action_status = "MONITORING"
    action_label = "Monitoring Structure"
    trailing_sl = 0

    if bull_fvg_1h:
        fvg_str = f"Bullish FVG [${fmt_price(bull_fvg_1h['bottom'])} - ${fmt_price(bull_fvg_1h['top'])}]"
    elif bear_fvg_1h:
        fvg_str = f"Bearish FVG [${fmt_price(bear_fvg_1h['bottom'])} - ${fmt_price(bear_fvg_1h['top'])}]"

    # =========================================================================
    # CASE 1: ACTIVE OPEN TRADE (Anchored from trade_history.json)
    # =========================================================================
    if anchored_signal:
        signal_type = anchored_signal.get("type", "BUY / LONG")
        tier_badge = anchored_signal.get("tier_badge", "⭐ TIER 1: ACTIVE TRADE")
        trade_setup = anchored_signal.get("setup", "Active SMC Trade")
        entry = anchored_signal.get("entry", current_price)
        sl = anchored_signal.get("sl", 0)
        trailing_sl = anchored_signal.get("trailing_sl", sl)
        tp = anchored_signal.get("tp", 0)
        tp1 = anchored_signal.get("tp1", 0)
        tp2 = anchored_signal.get("tp2", 0)
        limit_setup = anchored_signal.get("limit_setup")
        status_state = anchored_signal.get("status", "OPEN")
        
        is_long = "BUY" in signal_type or "LONG" in signal_type
        diff_pct = ((current_price - entry) / entry * 100) if is_long else ((entry - current_price) / entry * 100)
        raw_diff_pct = ((current_price - entry) / entry * 100)
        
        is_sl_hit = (trailing_sl > 0) and ((current_price <= trailing_sl) if is_long else (current_price >= trailing_sl))
        is_tp_hit = (tp > 0) and ((current_price >= tp) if is_long else (current_price <= tp))

        if is_sl_hit:
            action_status = "STOPPED"
            action_label = "SL BREACHED (Do Not Enter)"
        elif status_state == "TP2_LOCKED_RUNNING":
            action_status = "RUNNING"
            action_label = f"TP2 HIT | +1.5R LOCKED | Running (+{diff_pct:.2f}%)"
        elif status_state == "TP1_BE_RUNNING":
            action_status = "RUNNING"
            action_label = f"TP1 SECURED | SL @ True BE (+{diff_pct:.2f}%)"
        elif is_tp_hit:
            action_status = "RUNNING"
            action_label = f"TARGET HIT (+{diff_pct:.2f}%)"
        elif diff_pct > 0.75:
            action_status = "RUNNING"
            action_label = f"RUNNING IN PROFIT (+{diff_pct:.2f}%)"
        elif status_state == "PENDING_LIMIT" and limit_setup:
            action_status = "LIMIT"
            action_label = f"WAIT FOR LIMIT @ ${limit_setup['limit_entry']}"
        elif abs(raw_diff_pct) <= 0.75:
            action_status = "READY"
            action_label = f"IN ENTRY ZONE ({raw_diff_pct:+.2f}%)"
        elif diff_pct < 0:
            action_status = "WARNING"
            action_label = f"NEAR SL / CAUTION ({diff_pct:.2f}%)"
        else:
            action_status = "READY"
            action_label = f"IN ENTRY ZONE ({raw_diff_pct:+.2f}%)"

        reasons.append(f"Active trade running: Entry ${fmt_price(entry)} | Live: ${fmt_price(current_price)} ({diff_pct:+.2f}%)")
        if status_state == "TP2_LOCKED_RUNNING":
            reasons.append(f"🔒 Phase 2: SL Trailed to TP1 (${fmt_price(trailing_sl)}) - Guaranteed +1.5R Profit locked!")
        elif status_state == "TP1_BE_RUNNING":
            reasons.append(f"🛡️ Phase 1: TP1 Hit, SL at True Break-Even (${fmt_price(trailing_sl)}) with Fee Buffer.")
        reasons.append(f"4H Macro: {mtf_4h['bias']} | Daily Trend: {'Bullish' if ema_bullish else 'Bearish'}")
        
        if is_long and bull_ob_1h:
            ob_info_str = f"Bullish 1H OB [${fmt_price(bull_ob_1h['bottom'])} - ${fmt_price(bull_ob_1h['top'])}]"
            reasons.append(f"🧱 1H Demand OB: [${fmt_price(bull_ob_1h['bottom'])} - ${fmt_price(bull_ob_1h['top'])}]")
        elif not is_long and bear_ob_1h:
            ob_info_str = f"Bearish 1H OB [${fmt_price(bear_ob_1h['bottom'])} - ${fmt_price(bear_ob_1h['top'])}]"
            reasons.append(f"🧱 1H Supply OB: [${fmt_price(bear_ob_1h['bottom'])} - ${fmt_price(bear_ob_1h['top'])}]")

        if bull_fvg_1h:
            reasons.append(f"💧 Bullish FVG Zone: [${fmt_price(bull_fvg_1h['bottom'])} - ${fmt_price(bull_fvg_1h['top'])}]")
        elif bear_fvg_1h:
            reasons.append(f"💧 Bearish FVG Zone: [${fmt_price(bear_fvg_1h['bottom'])} - ${fmt_price(bear_fvg_1h['top'])}]")

        if sweep_1h and sweep_1h.get("details") and sweep_1h["details"] != "None":
            reasons.append(f"⚡ Liquidity Sweep: {sweep_1h['details']}")

        reasons.append(f"📊 RSI Indicator: 1D ({rsi_daily:.1f}) | 1H ({rsi_1h:.1f})")
        reasons.append(f"📐 Volatility Safety: ATR (2.2x) Stop Offset ${fmt_price(2.2 * atr_1h)}")
        c15_status = choch_15m.get("status", "15M Range") if choch_15m else "15M Range"
        c5_status = choch_5m.get("status", "5M Range") if choch_5m else "5M Range"
        reasons.append(f"⚡ 15M/5M Sniper Confirmation: {c15_status} | {c5_status}")

        f_rate = derivatives_info.get("funding_pct", 0.0) if derivatives_info else 0.0
        oi_val = derivatives_info.get("open_interest", 0.0) if derivatives_info else 0.0
        reasons.append(f"🪙 Derivatives Flow: Funding Rate {f_rate:+.4f}% | Open Interest {oi_val:,.0f}")

    # =========================================================================
    # CASE 2: NEW POTENTIAL SIGNALS (Scan Fresh Setups)
    # =========================================================================
    else:
        is_extreme_overbought = (rsi_daily >= 78.0) or (rsi_1h >= 80.0)
        is_extreme_oversold = (rsi_daily <= 22.0) or (rsi_1h <= 20.0)

        # ---------------------------------------------------------------------
        # TIER 2: SNIPER EXTREME REVERSALS (RSI Exhaustion + CHoCH + Sweep)
        # ---------------------------------------------------------------------
        if is_extreme_overbought and choch_data["has_bearish_choch"]:
            macro_trend_conflict = (mtf_4h["bias"] == "BULLISH") or ema_bullish
            has_demand_conflict = (bull_ob_1h and (bull_ob_1h.get('is_testing') or (bull_ob_1h['bottom'] * 0.99 <= entry <= bull_ob_1h['top'] * 1.005))) or \
                                  (bull_fvg_1h and (bull_fvg_1h['bottom'] * 0.99 <= entry <= bull_fvg_1h['top'] * 1.02))
            btc_blocks_short = btc_sentiment and not btc_sentiment.get("allow_shorts", True) and symbol != "BTCUSDT"

            if macro_trend_conflict or has_demand_conflict or btc_blocks_short:
                signal_type = "WATCHLIST"
                tier_badge = "WATCHLIST"
                if macro_trend_conflict:
                    trade_setup = f"Short Blocked: 4H/Daily Macro is BULLISH"
                    reasons.append(f"⚠️ Trend Shield: Blocked Counter-Trend Short against 4H {mtf_4h['bias']} & Daily Bullish trend ('Trend is Friend' rule)")
                if has_demand_conflict:
                    reasons.append(f"⚠️ Sniper Short Blocked: Sitting directly on/near 1H Bullish Demand Block or FVG")
                if btc_blocks_short:
                    reasons.append(f"⚠️ BTC Pump Shield: Bitcoin pumping ({btc_sentiment['reason']}), Short signals paused")
            else:
                signal_type = "SELL / SHORT"
                tier_badge = "🔥 TIER 2: SNIPER EXTREME"
                trade_setup = "Extreme Overbought (RSI > 80) + 1H Bearish CHoCH"
                choch_badge = "1H CHoCH Confirmed 🔴"
                
                # Dynamic ATR Stop Loss: Anchor above swing high + 2.2 * ATR
                sl_base = max(choch_data["recent_high"], entry * 1.01)
                sl = sl_base + (2.2 * atr_1h)
                risk = sl - entry
                if risk / entry < 0.025:
                    sl = entry * 1.025
                    risk = sl - entry
                trailing_sl = sl
                tp1 = entry - (risk * 1.5)
                tp2 = entry - (risk * 2.0)
                tp = entry - (risk * 3.0)
                reasons.append(f"Extreme RSI Overbought ({rsi_daily:.1f})")
                reasons.append(f"Confirmed 1H Bearish CHoCH below ${choch_data['key_hl']}")
                if sweep_1h["has_bear_sweep"]:
                    reasons.append(f"⚡ Liquidity Sweep: {sweep_1h['details']}")
                if bear_fvg_1h:
                    reasons.append(f"💧 Bearish FVG Confluence [${fmt_price(bear_fvg_1h['bottom'])} - ${fmt_price(bear_fvg_1h['top'])}]")

        elif is_extreme_oversold and choch_data["has_bullish_choch"]:
            macro_trend_conflict = (mtf_4h["bias"] == "BEARISH") or (not ema_bullish)
            has_supply_conflict = (bear_ob_1h and (bear_ob_1h.get('is_testing') or (bear_ob_1h['bottom'] * 0.995 <= entry <= bear_ob_1h['top'] * 1.01))) or \
                                  (bear_fvg_1h and (bear_fvg_1h['bottom'] * 0.98 <= entry <= bear_fvg_1h['top'] * 1.01))
            btc_blocks_long = btc_sentiment and not btc_sentiment.get("allow_longs", True) and symbol != "BTCUSDT"

            if macro_trend_conflict or has_supply_conflict or btc_blocks_long:
                signal_type = "WATCHLIST"
                tier_badge = "WATCHLIST"
                if macro_trend_conflict:
                    trade_setup = f"Long Blocked: 4H/Daily Macro is BEARISH"
                    reasons.append(f"⚠️ Trend Shield: Blocked Counter-Trend Long against 4H {mtf_4h['bias']} & Daily Bearish trend ('Trend is Friend' rule)")
                if has_supply_conflict:
                    reasons.append(f"⚠️ Sniper Long Blocked: Sitting inside/near 1H Bearish Supply Block or FVG")
                if btc_blocks_long:
                    reasons.append(f"⚠️ BTC Dump Shield: Bitcoin dumping ({btc_sentiment['reason']}), Long signals paused")
            else:
                signal_type = "BUY / LONG"
                tier_badge = "🔥 TIER 2: SNIPER EXTREME"
                trade_setup = "Extreme Oversold (RSI < 20) + 1H Bullish CHoCH"
                choch_badge = "1H CHoCH Confirmed 🟢"
                
                # Dynamic ATR Stop Loss: Anchor below swing low - 2.2 * ATR
                sl_base = min(choch_data["recent_low"], entry * 0.99)
                sl = sl_base - (2.2 * atr_1h)
                risk = entry - sl
                if risk / entry < 0.025:
                    sl = entry * 0.975
                    risk = entry - sl
                trailing_sl = sl
                tp1 = entry + (risk * 1.5)
                tp2 = entry + (risk * 2.0)
                tp = entry + (risk * 3.0)
                reasons.append(f"Extreme RSI Oversold ({rsi_daily:.1f})")
                reasons.append(f"Confirmed 1H Bullish CHoCH above ${choch_data['key_lh']}")
                if sweep_1h["has_bull_sweep"]:
                    reasons.append(f"⚡ Liquidity Sweep: {sweep_1h['details']}")
                if bull_fvg_1h:
                    reasons.append(f"💧 Bullish FVG Confluence [${fmt_price(bull_fvg_1h['bottom'])} - ${fmt_price(bull_fvg_1h['top'])}]")

        # ---------------------------------------------------------------------
        # TIER 1: INSTITUTIONAL SMC (4H Trend + Daily Line S&R + 1H OB/FVG)
        # ---------------------------------------------------------------------
        elif ema_bullish and mtf_4h["bias"] == "BULLISH" and (dist_to_support_pct <= 3.5 or is_sr_flip) and (38 <= rsi_daily <= 68):
            has_supply_conflict = bear_ob_1h and (bear_ob_1h.get('is_testing') or (bear_ob_1h['bottom'] * 0.995 <= entry <= bear_ob_1h['top'] * 1.01))
            btc_blocks_long = btc_sentiment and not btc_sentiment.get("allow_longs", True) and symbol != "BTCUSDT"
            sr_flip_lacks_confluence = is_sr_flip and not (sweep_1h["has_bull_sweep"] or bull_fvg_1h or bull_ob_1h)

            if has_supply_conflict or btc_blocks_long or sr_flip_lacks_confluence:
                signal_type = "WATCHLIST"
                tier_badge = "WATCHLIST"
                if sr_flip_lacks_confluence:
                    trade_setup = "Breakout Blocked: Lacks Sweep/FVG Confluence"
                    reasons.append("⚠️ Breakout Shield: S/R Flip breakout lacks Liquidity Sweep or FVG/OB confluence (No Sweep = No Entry rule)")
                if has_supply_conflict:
                    trade_setup = f"Long Blocked: Inside 1H Supply OB"
                    reasons.append(f"⚠️ SMC Shield: Blocked Long into 1H Supply Zone")
                if btc_blocks_long:
                    reasons.append(f"⚠️ BTC Shield: BTC trend/momentum unfavorable ({btc_sentiment.get('reason', '') if btc_sentiment else ''}), Long signals paused")
            else:
                signal_type = "BUY / LONG"
                tier_badge = "⭐ TIER 1: INSTITUTIONAL SMC"
                trade_setup = "Daily Line Support Bounce + 1H Demand OB" if not is_sr_flip else "Confirmed S/R Flip Breakout & Retest"
                
                base_support = flip_lvl if is_sr_flip else nearest_support
                if bull_ob_1h and bull_ob_1h['bottom'] < entry:
                    base_support = min(base_support, bull_ob_1h['bottom'])
                    
                # Dynamic ATR Volatility Stop Loss
                sl = base_support - (2.8 * atr_1h)
                risk = entry - sl
                if risk / entry < 0.030:
                    sl = entry * 0.970
                    risk = entry - sl
                trailing_sl = sl
                tp1 = entry + (risk * 1.5)
                tp2 = entry + (risk * 2.0)
                tp = entry + (risk * 3.0)
                
                reasons.append(f"4H Macro Bias: {mtf_4h['bias']} + Daily 20/50 EMA Bullish")
                reasons.append(f"🏷️ Institutional Discount Zone: Price in Wholesale Discount Area (Fib 50%-61.8%)")
                reasons.append(f"Holding S&R Base ${fmt_price(base_support)} (ATR Volatility Buffer: ${fmt_price(2.8 * atr_1h)})")
                if bull_fvg_1h:
                    reasons.append(f"💧 Bullish FVG Active: [${fmt_price(bull_fvg_1h['bottom'])} - ${fmt_price(bull_fvg_1h['top'])}]")
                if sweep_1h["has_bull_sweep"]:
                    reasons.append(f"⚡ Sell-Side Liquidity Swept (${fmt_price(sweep_1h['lookback_low'])})")
                if bull_ob_1h:
                    ob_info_str = f"Bullish 1H OB [${fmt_price(bull_ob_1h['bottom'])} - ${fmt_price(bull_ob_1h['top'])}]"

        elif (not ema_bullish) and mtf_4h["bias"] == "BEARISH" and (dist_to_resistance_pct <= 3.5 or is_sr_flip) and (32 <= rsi_daily <= 62):
            has_demand_conflict = bull_ob_1h and (bull_ob_1h.get('is_testing') or (bull_ob_1h['bottom'] * 0.99 <= entry <= bull_ob_1h['top'] * 1.005))
            btc_blocks_short = btc_sentiment and not btc_sentiment.get("allow_shorts", True) and symbol != "BTCUSDT"
            sr_flip_lacks_confluence = is_sr_flip and not (sweep_1h["has_bear_sweep"] or bear_fvg_1h or bear_ob_1h)

            if has_demand_conflict or btc_blocks_short or sr_flip_lacks_confluence:
                signal_type = "WATCHLIST"
                tier_badge = "WATCHLIST"
                if sr_flip_lacks_confluence:
                    trade_setup = "Breakout Blocked: Lacks Sweep/FVG Confluence"
                    reasons.append("⚠️ Breakout Shield: S/R Flip breakout lacks Liquidity Sweep or FVG/OB confluence (No Sweep = No Entry rule)")
                if has_demand_conflict:
                    trade_setup = f"Short Blocked: Inside 1H Demand OB"
                    reasons.append(f"⚠️ SMC Shield: Blocked Short into 1H Demand Zone")
                if btc_blocks_short:
                    reasons.append(f"⚠️ BTC Shield: BTC trend/momentum unfavorable ({btc_sentiment.get('reason', '') if btc_sentiment else ''}), Short signals paused")
            else:
                signal_type = "SELL / SHORT"
                tier_badge = "⭐ TIER 1: INSTITUTIONAL SMC"
                trade_setup = "Daily Line Resistance Rejection + 1H Supply OB"
                
                base_res = nearest_resistance
                if bear_ob_1h and bear_ob_1h['top'] > entry:
                    base_res = max(base_res, bear_ob_1h['top'])
                    
                # Dynamic ATR Volatility Stop Loss
                sl = base_res + (2.8 * atr_1h)
                risk = sl - entry
                if risk / entry < 0.030:
                    sl = entry * 1.030
                    risk = sl - entry
                trailing_sl = sl
                tp1 = entry - (risk * 1.5)
                tp2 = entry - (risk * 2.0)
                tp = entry - (risk * 3.0)
                
                reasons.append(f"4H Macro Bias: {mtf_4h['bias']} + Daily 20/50 EMA Bearish")
                reasons.append(f"🏷️ Institutional Premium Zone: Price in Premium Sell Area (Fib 50%-61.8%)")
                reasons.append(f"Testing Resistance Base ${fmt_price(base_res)} (ATR Buffer: ${fmt_price(2.8 * atr_1h)})")
                if bear_fvg_1h:
                    reasons.append(f"💧 Bearish FVG Active: [${fmt_price(bear_fvg_1h['bottom'])} - ${fmt_price(bear_fvg_1h['top'])}]")
                if sweep_1h["has_bear_sweep"]:
                    reasons.append(f"⚡ Buy-Side Liquidity Swept (${fmt_price(sweep_1h['lookback_high'])})")
                if bear_ob_1h:
                    ob_info_str = f"Bearish 1H OB [${fmt_price(bear_ob_1h['bottom'])} - ${fmt_price(bear_ob_1h['top'])}]"

        else:
            # -----------------------------------------------------------------
            # TIER 3: ASIAN RANGE LIQUIDITY SWEEP (London Judas Swing)
            # -----------------------------------------------------------------
            is_asian_sweep_setup = False
            utc_now = datetime.now(timezone.utc)
            today_utc_00 = int(utc_now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000)
            today_utc_07 = int(utc_now.replace(hour=7, minute=0, second=0, microsecond=0).timestamp() * 1000)
            
            if klines_1h and len(klines_1h) >= 12:
                asia_bars = [k for k in klines_1h if today_utc_00 <= k[0] < today_utc_07]
                after_bars = [k for k in klines_1h if k[0] >= today_utc_07]
                if len(asia_bars) >= 4 and after_bars and utc_now.hour >= 7:
                    a_high = max(float(k[2]) for k in asia_bars)
                    a_low = min(float(k[3]) for k in asia_bars)
                    low_after = min(float(k[3]) for k in after_bars)
                    high_after = max(float(k[2]) for k in after_bars)
                    
                    # Bullish Judas Sweep: Swept Asian Low & price reclaimed above Asian Low
                    if low_after < a_low and entry > a_low:
                        signal_type = "BUY / LONG"
                        tier_badge = "⚡ ASIAN RANGE SWEEP"
                        trade_setup = "London Judas Sweep of Asian Low (Liquidity Grab Reversal)"
                        choch_badge = "Asian Low Swept 🟢"
                        is_asian_sweep_setup = True
                        sl = low_after - (1.8 * atr_1h)
                        risk = entry - sl
                        if risk / entry < 0.020:
                            sl = entry * 0.980
                            risk = entry - sl
                        trailing_sl = sl
                        tp1 = entry + (risk * 1.5)
                        tp2 = entry + (risk * 2.0)
                        tp = entry + (risk * 3.0)
                        reasons.append(f"⚡ Asian Range Sweep: Swept Asian Low (${fmt_price(a_low)}) to ${fmt_price(low_after)}")
                        reasons.append("⚡ Institutional Reclaim: Price surged back above Asian Low structure")
                    
                    # Bearish Judas Sweep: Swept Asian High & price rejected below Asian High
                    elif high_after > a_high and entry < a_high:
                        signal_type = "SELL / SHORT"
                        tier_badge = "⚡ ASIAN RANGE SWEEP"
                        trade_setup = "London Judas Sweep of Asian High (Liquidity Grab Reversal)"
                        choch_badge = "Asian High Swept 🔴"
                        is_asian_sweep_setup = True
                        sl = high_after + (1.8 * atr_1h)
                        risk = sl - entry
                        if risk / entry < 0.020:
                            sl = entry * 1.020
                            risk = sl - entry
                        trailing_sl = sl
                        tp1 = entry - (risk * 1.5)
                        tp2 = entry - (risk * 2.0)
                        tp = entry - (risk * 3.0)
                        reasons.append(f"⚡ Asian Range Sweep: Swept Asian High (${fmt_price(a_high)}) to ${fmt_price(high_after)}")
                        reasons.append("⚡ Institutional Rejection: Price closed back below Asian High structure")

            # -----------------------------------------------------------------
            # TIER 4: RELATIVE STRENGTH (RS) / RELATIVE WEAKNESS (RW) ALPHA
            # -----------------------------------------------------------------
            if not is_asian_sweep_setup:
                is_rs_leader = (mtf_4h["bias"] == "BULLISH") and (rsi_1h >= 52.0) and (sweep_1h["has_bull_sweep"] or bull_fvg_1h or bull_ob_1h) and (dist_to_support_pct <= 5.5 or is_sr_flip)
                is_rw_laggard = (mtf_4h["bias"] == "BEARISH") and (rsi_1h <= 48.0) and (sweep_1h["has_bear_sweep"] or bear_fvg_1h or bear_ob_1h) and (dist_to_resistance_pct <= 5.5 or is_sr_flip)

                if is_rs_leader:
                    signal_type = "BUY / LONG"
                    tier_badge = "💎 RELATIVE STRENGTH ALPHA"
                    trade_setup = "Institutional RS Leader: 4H Bullish Structure + 1H Liquidity Inflow"
                    base_support = flip_lvl if is_sr_flip else nearest_support
                    sl = base_support - (2.2 * atr_1h)
                    risk = entry - sl
                    if risk / entry < 0.025:
                        sl = entry * 0.975
                        risk = entry - sl
                    trailing_sl = sl
                    tp1 = entry + (risk * 1.5)
                    tp2 = entry + (risk * 2.0)
                    tp = entry + (risk * 3.0)
                    reasons.append("💎 Relative Strength: Outperforming market with aligned 4H bias & 1H liquidity")
                elif is_rw_laggard:
                    signal_type = "SELL / SHORT"
                    tier_badge = "💎 RELATIVE WEAKNESS ALPHA"
                    trade_setup = "Institutional RW Laggard: 4H Bearish Structure + Supply Distribution"
                    base_res = nearest_resistance
                    sl = base_res + (2.2 * atr_1h)
                    risk = sl - entry
                    if risk / entry < 0.025:
                        sl = entry * 1.025
                        risk = sl - entry
                    trailing_sl = sl
                    tp1 = entry - (risk * 1.5)
                    tp2 = entry - (risk * 2.0)
                    tp = entry - (risk * 3.0)
                    reasons.append("💎 Relative Weakness: Underperforming market with aligned 4H bias & supply rejection")
                else:
                    signal_type = "WATCHLIST"
                    if is_extreme_overbought:
                        tier_badge = "🛡️ PUMP PROTECTED"
                        trade_setup = f"RSI Overbought ({rsi_daily:.1f}), WAITING for 1H CHoCH (< ${choch_data['key_hl']})"
                        choch_badge = f"Waiting CHoCH (< ${choch_data['key_hl']})"
                        reasons.append(f"Protected against Parabolic Pump: Waiting for 1H CHoCH below ${choch_data['key_hl']}")
                    else:
                        tier_badge = "WATCHLIST"
                        reasons.append(f"Mid-range RSI ({rsi_daily:.1f}). Daily S&R: Sup ${fmt_price(nearest_support)} | Res ${fmt_price(nearest_resistance)}")
                        if choch_data["has_bearish_choch"]:
                            choch_badge = "1H CHoCH Bearish"
                        elif choch_data["has_bullish_choch"]:
                            choch_badge = "1H CHoCH Bullish"

        # Apply the High-Probability Sniper Filters:
        # 1. London & NY Prime Session Guard
        # 2. Smart BTC Macro Correlation Guard with RS Decoupling
        # 3. Dynamic Volume Expansion Filter (Completed Candle + Pace-Adjusted Live Candle)
        # 4. Nested 4H + 1H Order Block Confluence
        # 5. Derivatives Crowded Trade / Funding Rate Shield (>+0.035% or <-0.035%)
        if signal_type in ["BUY / LONG", "SELL / SHORT"] and not anchored_signal:
            # 1. Session Guard: Allow all active sessions (London, NY, Asian, Global)
            if session_info and not session_info.get("is_prime", True):
                signal_type = "WATCHLIST"
                tier_badge = "💤 OFF-HOURS WATCHLIST"
                reasons.append(f"💤 Prime Session Guard: Entry held for active market liquidity")

            # 2. Smart BTC Macro Correlation Guard
            if btc_sentiment:
                is_long_sig = "BUY" in signal_type or "LONG" in signal_type
                is_decoupled = tier_badge in ["💎 RELATIVE STRENGTH ALPHA", "💎 RELATIVE WEAKNESS ALPHA", "⚡ ASIAN RANGE SWEEP"]
                if is_long_sig and (btc_sentiment.get("is_dumping") or "BEARISH" in str(btc_sentiment.get("status", ""))):
                    if is_decoupled:
                        reasons.append(f"💎 RS Alpha Bypass: Leading setup decoupled from BTC ({btc_sentiment.get('reason')}). Signal permitted.")
                    else:
                        signal_type = "WATCHLIST"
                        tier_badge = "🛡️ BTC DUMP WATCHLIST"
                        reasons.append(f"⚠️ BTC Macro Shield: Bitcoin is Bearish/Dumping ({btc_sentiment.get('reason')}). Long held in Watchlist.")
                elif not is_long_sig and (btc_sentiment.get("is_pumping") or "BULLISH" in str(btc_sentiment.get("status", ""))):
                    if is_decoupled:
                        reasons.append(f"💎 RW Alpha Bypass: Lagging setup decoupled from BTC ({btc_sentiment.get('reason')}). Signal permitted.")
                    else:
                        signal_type = "WATCHLIST"
                        tier_badge = "🛡️ BTC PUMP WATCHLIST"
                        reasons.append(f"⚠️ BTC Pump Shield: Bitcoin is Bullish/Pumping ({btc_sentiment.get('reason')}). Short held in Watchlist.")

            # 3. Dynamic Volume Expansion Filter (Completed Candle + Pace-Adjusted Live Candle)
            volumes_1h = [float(k[5]) for k in klines_1h]
            if len(volumes_1h) >= 22:
                vol_sma_1h = sum(volumes_1h[-22:-2]) / 20.0
                prev_vol_1h = volumes_1h[-2]
                prev_v_ratio = (prev_vol_1h / vol_sma_1h) if vol_sma_1h > 0 else 1.0
                
                mins_in_hour = max(1, datetime.now(timezone.utc).minute)
                curr_vol_1h = volumes_1h[-1]
                paced_curr_vol = curr_vol_1h * (60.0 / mins_in_hour)
                paced_v_ratio = (paced_curr_vol / vol_sma_1h) if vol_sma_1h > 0 else 1.0
                
                v_ratio = max(prev_v_ratio, paced_v_ratio)
                is_weekend = datetime.now(timezone.utc).weekday() >= 5
                is_sweep_setup = (tier_badge == "⚡ ASIAN RANGE SWEEP") or (sweep_1h and (sweep_1h.get("has_bull_sweep") or sweep_1h.get("has_bear_sweep")))
                
                if is_sweep_setup:
                    vol_cutoff = 0.35
                elif is_weekend:
                    vol_cutoff = 0.40
                else:
                    vol_cutoff = 0.50

                if v_ratio < vol_cutoff and signal_type in ["BUY / LONG", "SELL / SHORT"]:
                    signal_type = "WATCHLIST"
                    tier_badge = "📉 LOW VOLUME WATCHLIST"
                    reasons.append(f"⚠️ Volume Guard: 1H Volume ({v_ratio:.2f}x) is below {vol_cutoff:.2f}x baseline (Low Market Participation)")
                elif signal_type in ["BUY / LONG", "SELL / SHORT"]:
                    if v_ratio >= 1.25:
                        reasons.append(f"🔥 High Volume Surge Confirmed: 1H Volume ({v_ratio:.2f}x vs 20-SMA)")
                    else:
                        reasons.append(f"📊 Healthy Volume Confirmed: 1H Volume ({v_ratio:.2f}x vs 20-SMA)")



            # 4. Nested 4H + 1H Order Block Confluence Check
            klines_4h_cb = get_klines(symbol, interval="4h", limit=25)
            if klines_4h_cb and len(klines_4h_cb) >= 20:
                b_bull_4h, b_bear_4h = detect_1h_order_blocks(klines_4h_cb)
                if signal_type == "BUY / LONG" and bull_ob_1h and b_bull_4h:
                    if (bull_ob_1h['bottom'] <= b_bull_4h['top'] and bull_ob_1h['top'] >= b_bull_4h['bottom']):
                        reasons.append(f"🔥 Nested OB Confluence: 1H Demand OB is Nested inside 4H Macro OB [${fmt_price(b_bull_4h['bottom'])} - ${fmt_price(b_bull_4h['top'])}]")
                elif signal_type == "SELL / SHORT" and bear_ob_1h and b_bear_4h:
                    if (bear_ob_1h['bottom'] <= b_bear_4h['top'] and bear_ob_1h['top'] >= b_bear_4h['bottom']):
                        reasons.append(f"🔥 Nested OB Confluence: 1H Supply OB is Nested inside 4H Macro OB [${fmt_price(b_bear_4h['bottom'])} - ${fmt_price(b_bear_4h['top'])}]")

            # 5. Derivatives Crowded Trade / Funding Rate Shield
            if derivatives_info and signal_type in ["BUY / LONG", "SELL / SHORT"]:
                f_rate = derivatives_info.get("funding_pct", 0.0)
                if signal_type == "BUY / LONG" and f_rate > 0.035:
                    signal_type = "WATCHLIST"
                    tier_badge = "🛡️ CROWDED LONGS WATCHLIST"
                    trade_setup = f"Long Blocked: Crowded Long Trap (Funding {f_rate:+.4f}%)"
                    reasons.append(f"⚠️ Crowded Longs Shield: Funding rate is excessively positive ({f_rate:+.4f}% > +0.035%). High risk of long squeeze / liquidation cascade.")
                elif signal_type == "SELL / SHORT" and f_rate < -0.035:
                    signal_type = "WATCHLIST"
                    tier_badge = "🛡️ CROWDED SHORTS WATCHLIST"
                    trade_setup = f"Short Blocked: Crowded Short Trap (Funding {f_rate:+.4f}%)"
                    reasons.append(f"⚠️ Crowded Shorts Shield: Funding rate is heavily negative ({f_rate:+.4f}% < -0.035%). High risk of short squeeze cascade.")

        # Check for Pending Limit Retest on fresh signals (FVG 25% Retest Depth / High-Precision Entry)
        if signal_type == "BUY / LONG":
            target_limit_price = None
            if bull_fvg_1h and bull_fvg_1h['bottom'] < entry:
                target_limit_price = bull_fvg_1h['bottom'] + 0.25 * (bull_fvg_1h['top'] - bull_fvg_1h['bottom'])
            elif bull_ob_1h and bull_ob_1h['top'] < entry:
                target_limit_price = bull_ob_1h['top']
            
            if target_limit_price and target_limit_price < entry:
                dist_to_ob_pct = ((entry - target_limit_price) / entry) * 100
                if dist_to_ob_pct >= 0.15:
                    l_entry = target_limit_price
                    l_sl = sl
                    # Enforce Dynamic ATR & Minimum 2.5% Hard Buffer on Limit Setup
                    if (l_entry - l_sl) / l_entry < 0.025:
                        l_sl = l_entry * 0.975
                    l_risk = l_entry - l_sl
                    if l_risk > 0 and (l_risk / l_entry) >= 0.015:
                        l_tp1 = l_entry + (l_risk * 1.5)
                        l_tp2 = l_entry + (l_risk * 2.0)
                        l_tp = l_entry + (l_risk * 3.0)
                        limit_setup = {
                            "limit_entry": fmt_price(l_entry),
                            "raw_limit_entry": l_entry,
                            "limit_sl": fmt_price(l_sl),
                            "raw_limit_sl": l_sl,
                            "limit_tp1": fmt_price(l_tp1),
                            "raw_limit_tp1": l_tp1,
                            "limit_tp2": fmt_price(l_tp2),
                            "raw_limit_tp2": l_tp2,
                            "limit_tp": fmt_price(l_tp),
                            "raw_limit_tp": l_tp,
                            "risk_pct": f"{((l_risk / l_entry) * 100):.2f}%",
                            "reward_pct": f"{(((l_tp - l_entry) / l_entry) * 100):.2f}%",
                            "dist_pct": f"{dist_to_ob_pct:.1f}%"
                        }
        elif signal_type == "SELL / SHORT":
            target_limit_price = None
            if bear_fvg_1h and bear_fvg_1h['top'] > entry:
                target_limit_price = bear_fvg_1h['top'] - 0.25 * (bear_fvg_1h['top'] - bear_fvg_1h['bottom'])
            elif bear_ob_1h and bear_ob_1h['bottom'] > entry:
                target_limit_price = bear_ob_1h['bottom']
            
            if target_limit_price and target_limit_price > entry:
                dist_to_ob_pct = ((target_limit_price - entry) / entry) * 100
                if dist_to_ob_pct >= 0.15:
                    l_entry = target_limit_price
                    l_sl = sl
                    # Enforce Dynamic ATR & Minimum 2.5% Hard Buffer on Limit Setup
                    if (l_sl - l_entry) / l_entry < 0.025:
                        l_sl = l_entry * 1.025
                    l_risk = l_sl - l_entry
                    if l_risk > 0 and (l_risk / l_entry) >= 0.015:
                        l_tp1 = l_entry - (l_risk * 1.5)
                        l_tp2 = l_entry - (l_risk * 2.0)
                        l_tp = l_entry - (l_risk * 3.0)
                        limit_setup = {
                            "limit_entry": fmt_price(l_entry),
                            "raw_limit_entry": l_entry,
                            "limit_sl": fmt_price(l_sl),
                            "raw_limit_sl": l_sl,
                            "limit_tp1": fmt_price(l_tp1),
                            "raw_limit_tp1": l_tp1,
                            "limit_tp2": fmt_price(l_tp2),
                            "raw_limit_tp2": l_tp2,
                            "limit_tp": fmt_price(l_tp),
                            "raw_limit_tp": l_tp,
                            "risk_pct": f"{((l_risk / l_entry) * 100):.2f}%",
                            "reward_pct": f"{(((l_entry - l_tp) / l_entry) * 100):.2f}%",
                            "dist_pct": f"{dist_to_ob_pct:.1f}%"
                        }

        if choch_15m and choch_5m and not any("15M/5M" in str(r) for r in reasons):
            reasons.append(f"⚡ 15M/5M Sniper Confirmation: {choch_15m['status']} | {choch_5m['status']}")
        if derivatives_info and derivatives_info.get("funding_pct") is not None and not any("Funding Rate" in str(r) for r in reasons):
            reasons.append(f"🪙 Derivatives Flow: Funding Rate {derivatives_info['funding_pct']:+.4f}% | Open Interest {derivatives_info['open_interest']:,.0f}")

        if signal_type in ["BUY / LONG", "SELL / SHORT"]:
            if limit_setup:
                action_status = "LIMIT"
                action_label = f"SET LIMIT @ ${limit_setup['limit_entry']}"
                # Precision override: set entry, sl, tp targets to precision limit levels
                entry = limit_setup['raw_limit_entry']
                sl = limit_setup['raw_limit_sl']
                tp1 = limit_setup['raw_limit_tp1']
                tp2 = limit_setup['raw_limit_tp2']
                tp = limit_setup['raw_limit_tp']
                reasons.append(f"💧 FVG Retest Guard Active: Entry set at FVG 25% equilibrium zone (${limit_setup['limit_entry']})")
            else:
                # Direct Institutional Zone Entry (Ready for Market Execution)
                action_status = "READY"
                action_label = f"READY NOW @ ${fmt_price(entry)}"
                reasons.append("🟢 Instant Entry: Confirmed institutional zone bounce. Ready for immediate entry.")

    risk_pct = 0.0
    reward_pct = 0.0
    if signal_type == "BUY / LONG" and entry > 0 and sl > 0 and tp > 0:
        risk_pct = ((entry - sl) / entry) * 100
        reward_pct = ((tp - entry) / entry) * 100
    elif signal_type == "SELL / SHORT" and entry > 0 and sl > 0 and tp > 0:
        risk_pct = ((sl - entry) / entry) * 100
        reward_pct = ((entry - tp) / entry) * 100

    return {
        "symbol": symbol,
        "current_price": fmt_price(current_price),
        "raw_price": current_price,
        "signal": signal_type,
        "tier_badge": tier_badge,
        "setup": trade_setup if trade_setup else "Monitoring Structure",
        "session_badge": session_info["badge"],
        "session_name": session_info["name"],
        "mtf_status": mtf_4h["bias"],
        "fvg_str": fvg_str,
        "sweep_str": sweep_str,
        "choch_badge": choch_badge,
        "order_block_1h": ob_info_str,
        "rsi_daily": round(rsi_daily, 1),
        "rsi_1h": round(rsi_1h, 1),
        "atr_1h": fmt_price(atr_1h),
        "ema_20": fmt_price(ema_20),
        "ema_50": fmt_price(ema_50),
        "ema_status": "BULLISH (20>50)" if ema_bullish else "BEARISH (20<50)",
        "daily_support": fmt_price(nearest_support),
        "daily_resistance": fmt_price(nearest_resistance),
        "vol_ratio": round(vol_proj_ratio, 2),
        "entry": fmt_price(entry),
        "raw_entry": entry,
        "stop_loss": fmt_price(sl) if sl > 0 else "-",
        "raw_sl": sl,
        "trailing_sl": trailing_sl,
        "trailing_sl_str": fmt_price(trailing_sl) if trailing_sl > 0 else "-",
        "tp_1": fmt_price(tp1) if tp1 > 0 else "-",
        "raw_tp1": tp1,
        "tp_2": fmt_price(tp2) if tp2 > 0 else "-",
        "raw_tp2": tp2,
        "take_profit_1_3": fmt_price(tp) if tp > 0 else "-",
        "raw_tp": tp,
        "risk_pct": f"{risk_pct:.2f}%" if risk_pct > 0 else "-",
        "reward_pct": f"{reward_pct:.2f}%" if reward_pct > 0 else "-",
        "rr_ratio": "1:3" if signal_type != "WATCHLIST" else "-",
        "limit_setup": limit_setup,
        "action_status": action_status,
        "action_label": action_label,
        "funding_rate": round(derivatives_info.get("funding_pct", 0.0), 4) if derivatives_info else 0.0,
        "open_interest": derivatives_info.get("open_interest", 0.0) if derivatives_info else 0.0,
        "reasons": reasons
    }

def check_and_resolve_open_trades(news_shield=None):
    if news_shield is None:
        try:
            upcoming_news = get_upcoming_economic_news()
            news_shield = evaluate_news_shield(upcoming_news)
        except Exception:
            news_shield = {"is_shield_active": False}

    history_data = {
        "total_signals": 0,
        "wins": 0,
        "losses": 0,
        "pending": 0,
        "win_rate_pct": 0.0,
        "circuit_breaker": {"is_tripped": False, "losses_today": 0, "max_allowed": MAX_DAILY_LOSSES},
        "signals": []
    }
    
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                history_data = json.load(f)
        except Exception:
            pass

    existing_signals = history_data.get("signals", [])
    now_ts = int(datetime.now(timezone.utc).timestamp() * 1000)
    today_utc = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    
    # Calculate today's losses for Pro-Trader Hybrid Adaptive Circuit Breaker
    today_losses = 0
    last_loss_ts = 0
    for s in existing_signals:
        if s.get("executed_live") and "LOSS" in s.get("status", ""):
            res_date = (s.get("resolved_at_utc") or s.get("resolved_at", ""))[:10]
            if res_date == today_utc:
                today_losses += 1
                l_ts = s.get("resolved_ts") or s.get("timestamp", 0)
                if l_ts > last_loss_ts:
                    last_loss_ts = l_ts

    # Live API Fail-Safe: Sync distinct loss count directly from Binance Futures Realized PnL API
    if auto_trader and auto_trader.is_configured():
        try:
            recent_inc = auto_trader.get_recent_income(limit=50)
            start_of_day_ts = int(datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000)
            sym_pnl = {}
            for inc in recent_inc:
                # Strictly evaluate REALIZED_PNL (commissions and funding fees are not trade losses)
                if inc.get("incomeType") == "REALIZED_PNL":
                    inc_time = int(inc.get("time", 0))
                    if inc_time >= start_of_day_ts:
                        sym = inc.get("symbol")
                        sym_pnl[sym] = sym_pnl.get(sym, 0.0) + float(inc.get("income", 0.0))
                        if inc_time > last_loss_ts:
                            last_loss_ts = inc_time
            # Count distinct symbols/positions that had a net negative realized PnL today
            api_today_losses = sum(1 for pnl in sym_pnl.values() if pnl < -0.10)
            today_losses = max(today_losses, api_today_losses)
        except Exception as e_inc:
            print(f"[!] Warning: Could not sync live Binance income for circuit breaker: {e_inc}")

    cb = history_data.get("circuit_breaker", {})
    cooldown_duration_ms = CIRCUIT_COOLDOWN_HOURS * 3600 * 1000

    cb_status = "NORMAL"
    is_tripped = False
    is_defensive = cb.get("is_defensive", False)
    cooldown_until = cb.get("cooldown_until", 0)
    remaining_cooldown_mins = 0

    if today_losses >= MAX_DAILY_HARD_STOP_LOSSES:
        # Stage 3: Daily Hard Stop (3 losses today -> freeze until tomorrow 00:00 UTC)
        cb_status = "HARD_STOP"
        is_tripped = True
        is_defensive = False
        if not cb.get("notified_hard_stop"):
            send_telegram_resolution(None, "CIRCUIT_HARD_STOP")
            cb["notified_hard_stop"] = True

    elif today_losses >= CIRCUIT_COOLDOWN_LOSSES:
        # Stage 1 & 2: 4-Hour Volatility Cooldown & Defensive Resume
        if cooldown_until == 0 or cb.get("cooldown_date") != today_utc:
            ref_ts = last_loss_ts if last_loss_ts > 0 else now_ts
            cooldown_until = ref_ts + cooldown_duration_ms
            cb["cooldown_until"] = cooldown_until
            cb["cooldown_date"] = today_utc
            cb["notified_cooldown"] = False
            cb["notified_resume"] = False

        if now_ts < cooldown_until:
            # Still in 4-hour cooldown
            cb_status = "COOLDOWN"
            is_tripped = True
            remaining_cooldown_mins = max(1, int((cooldown_until - now_ts) / 60000))
            if not cb.get("notified_cooldown"):
                resume_dt = datetime.fromtimestamp(cooldown_until / 1000, tz=timezone.utc).strftime('%H:%M')
                send_telegram_resolution({"mins": remaining_cooldown_mins, "resume_time": resume_dt}, "CIRCUIT_COOLDOWN")
                cb["notified_cooldown"] = True
        else:
            # Cooldown expired! Auto-resume in Defensive Mode (5% Risk)
            cb_status = "DEFENSIVE_RESUME"
            is_tripped = False
            is_defensive = cb.get("is_defensive", True)
            if not cb.get("notified_resume"):
                send_telegram_resolution(None, "CIRCUIT_RESUME")
                cb["notified_resume"] = True
    else:
        # Normal state (0 or 1 loss)
        cb_status = "NORMAL"
        is_tripped = False
        is_defensive = False
        cooldown_until = 0

    history_data["circuit_breaker"] = {
        "status": cb_status,
        "is_tripped": is_tripped,
        "is_defensive": is_defensive,
        "losses_today": today_losses,
        "max_allowed": MAX_DAILY_HARD_STOP_LOSSES,
        "cooldown_losses": CIRCUIT_COOLDOWN_LOSSES,
        "cooldown_until": cooldown_until,
        "cooldown_date": cb.get("cooldown_date", today_utc),
        "remaining_cooldown_mins": remaining_cooldown_mins,
        "notified_cooldown": cb.get("notified_cooldown", False),
        "notified_resume": cb.get("notified_resume", False),
        "notified_hard_stop": cb.get("notified_hard_stop", False)
    }

    real_positions = None
    active_rp_syms = set()
    pos_sync_success = False
    if auto_trader and auto_trader.is_live_enabled():
        try:
            res_details = auto_trader.get_open_positions_detail()
            if res_details is not None:
                real_positions = res_details
                active_rp_syms = {rp.get("symbol") for rp in real_positions if float(rp.get("position_amt", 0.0)) != 0}
                pos_sync_success = True
        except Exception as e_pos:
            print(f"[!] Warning fetching open positions for resolution: {e_pos}")

    for s in existing_signals:
        curr_status = s.get("status", "OPEN")
        if curr_status in ["OPEN", "PENDING_LIMIT", "TP1_BE_RUNNING", "TP2_LOCKED_RUNNING"]:
            sym = s.get("symbol")

            # -----------------------------------------------------------------
            # 0. GROUND TRUTH: Sync all OPEN signals against Binance Futures live positions
            # -----------------------------------------------------------------
            if pos_sync_success and auto_trader and auto_trader.is_live_enabled() and curr_status in ["OPEN", "TP1_BE_RUNNING", "TP2_LOCKED_RUNNING"]:
                if sym not in active_rp_syms:
                    # Position closed or not open on Binance Futures matching engine!
                    user_trades = auto_trader.get_user_trades(sym, limit=10)
                    realized_pnl = 0.0
                    if user_trades:
                        entry_ref = s.get("live_entry_ts", 0) - 60000
                        recent_ut = [tr for tr in user_trades if int(tr.get("time", 0)) >= entry_ref] or user_trades
                        realized_pnl = sum(float(tr.get("realizedPnl", 0.0)) - float(tr.get("commission", 0.0)) for tr in recent_ut)

                    s["realized_pnl_usdt"] = round(realized_pnl, 2)
                    now_utc_str = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')
                    s["resolved_at"] = now_utc_str
                    s["resolved_at_utc"] = now_utc_str
                    s["resolved_ts"] = int(datetime.now(timezone.utc).timestamp() * 1000)

                    if realized_pnl > 0:
                        s["status"] = f"WIN (+${realized_pnl:.2f} USDT)"
                        s["outcome_pnl"] = round(realized_pnl / float(s.get("executed_margin", 2.5) or 2.5), 2)
                    elif realized_pnl < 0:
                        s["status"] = "LOSS (SL Hit)"
                        s["outcome_pnl"] = -1.0
                    else:
                        s["status"] = "CLOSED (Break-Even)"
                        s["outcome_pnl"] = 0.0

                    if s.get("executed_live") and not s.get("notified_live_close"):
                        s["notified_live_close"] = True
                        send_telegram_live_close(sym, realized_pnl, s["status"], s.get("outcome_pnl", 0.0))
                    continue

            sig_ts = s.get("timestamp", 0)
            kl = get_klines(s["symbol"], interval="1h", limit=160)
            if not kl:
                continue

            # Auto-expire stale trades open for > 96 hours (4 days) without resolution
            if curr_status in ["OPEN", "TP1_BE_RUNNING", "TP2_LOCKED_RUNNING"] and (now_ts - sig_ts) > (96 * 3600 * 1000):
                s["status"] = "CLOSED (Time Expired / Stale)"
                s["outcome_pnl"] = 0.0
                s["resolved_at"] = datetime.now().strftime('%Y-%m-%d %H:%M')
                s["resolved_ts"] = now_ts
                continue

            # Ensure trailing_sl exists
            if "trailing_sl" not in s or s["trailing_sl"] == 0:
                s["trailing_sl"] = s.get("sl", 0)
                s["trailing_sl_str"] = fmt_price(s["trailing_sl"])

            # -----------------------------------------------------------------
            # 1. PENDING LIMIT ORDERS: Check if filled or expired
            # -----------------------------------------------------------------
            if curr_status == "PENDING_LIMIT":
                ls = s.get("limit_setup")
                if not ls:
                    s["status"] = "OPEN"
                    continue
                    
                l_entry = ls.get("raw_limit_entry", 0)
                # Check if 24 hours passed
                if (now_ts - sig_ts) > (LIMIT_EXPIRY_HOURS * 3600 * 1000):
                    s["status"] = "EXPIRED (Limit Unfilled)"
                    s["resolved_at"] = datetime.now().strftime('%Y-%m-%d %H:%M')
                    continue


                future_bars = [bar for bar in kl if (bar[0] + 3600000) >= sig_ts]
                is_filled = False
                for bar in future_bars:
                    bar_low = float(bar[3])
                    bar_high = float(bar[2])
                    if "BUY" in s["type"] or "LONG" in s["type"]:
                        if bar_low <= l_entry:
                            is_filled = True
                            break
                    elif "SELL" in s["type"] or "SHORT" in s["type"]:
                        if bar_high >= l_entry:
                            is_filled = True
                            break

                if is_filled:
                    s["status"] = "OPEN"
                    s["entry"] = l_entry
                    s["entry_str"] = fmt_price(l_entry)
                    s["sl"] = ls.get("raw_limit_sl", s["sl"])
                    s["sl_str"] = fmt_price(s["sl"])
                    s["trailing_sl"] = s["sl"]
                    s["trailing_sl_str"] = fmt_price(s["trailing_sl"])
                    s["tp1"] = ls.get("raw_limit_tp1", s["tp1"])
                    s["tp1_str"] = fmt_price(s["tp1"])
                    s["tp2"] = ls.get("raw_limit_tp2", s["tp2"])
                    s["tp2_str"] = fmt_price(s["tp2"])
                    s["tp"] = ls.get("raw_limit_tp", s["tp"])
                    s["tp_str"] = fmt_price(s["tp"])
                    s["filled_at"] = datetime.now().strftime('%Y-%m-%d %H:%M')
                    s["fill_ts"] = now_ts
                    if not s.get("notified_fill"):
                        s["notified_fill"] = True
                        
                        # --- SMC 2.0 CONFIRMED RETEST LIVE EXECUTION ---
                        executed_live_retest = False
                        if auto_trader and auto_trader.is_live_enabled() and not s.get("executed_live"):
                            sym = s["symbol"]
                            cb = history_data.get("circuit_breaker", {})
                            is_cb_tripped = cb.get("is_tripped", False)
                            is_news_active = bool(news_shield and news_shield.get("is_shield_active"))
                            
                            real_positions = auto_trader.get_all_open_positions()
                            active_positions_count = len(real_positions)
                            existing_pos = auto_trader.get_open_position(sym)
                            
                            if is_cb_tripped:
                                print(f"[!] Retest execution skipped for {sym}: Circuit Breaker is active.")
                            elif is_news_active:
                                print(f"[!] Retest execution skipped for {sym}: High-Impact News Shield active.")
                            elif active_positions_count >= MAX_ACTIVE_POSITIONS:
                                print(f"[!] Retest execution skipped for {sym}: Max {MAX_ACTIVE_POSITIONS} active positions already open.")
                            elif existing_pos:
                                print(f"[!] Retest execution skipped for {sym}: Position already exists on Binance.")
                            else:
                                is_confirmed, conf_reason = verify_retest_confirmation(sym, s["type"], l_entry, s["sl"])
                                if is_confirmed:
                                    act_payload = {
                                        "symbol": sym,
                                        "type": s["type"],
                                        "signal": s["type"],
                                        "raw_entry": l_entry,
                                        "entry": s["entry_str"],
                                        "raw_sl": s["sl"],
                                        "stop_loss": s["sl_str"],
                                        "raw_tp": s["tp"],
                                        "take_profit_1_3": s["tp_str"],
                                        "raw_tp1": s.get("tp1", 0),
                                        "tp_1": s.get("tp1_str", "-"),
                                        "raw_tp2": s.get("tp2", 0),
                                        "tp_2": s.get("tp2_str", "-"),
                                        "mtf_status": s.get("mtf_status", "BULLISH" if ("BUY" in s["type"] or "LONG" in s["type"]) else "BEARISH"),
                                        "is_defensive": cb.get("is_defensive", False),
                                        "risk_pct_override": 0.75 if cb.get("is_defensive") else 1.5,
                                        "funding_rate": s.get("funding_rate", 0.0)
                                    }
                                    print(f"[🎯] SMC Retest Confirmed for {sym} ({conf_reason}). Executing on Binance Futures...")
                                    exec_res = auto_trader.execute_signal(act_payload)
                                    if exec_res:
                                        s["binance_order_id"] = exec_res.get("order_id")
                                        s["binance_sl_order_id"] = exec_res.get("sl_order_id")
                                        s["executed_live"] = True
                                        s["executed_margin"] = exec_res.get("margin_usdt")
                                        s["executed_qty"] = exec_res.get("qty")
                                        s["live_entry_ts"] = int(time.time() * 1000)
                                        if exec_res.get("entry_price"):
                                            s["entry"] = exec_res.get("entry_price")
                                            s["entry_str"] = fmt_price(s["entry"])
                                        executed_live_retest = True
                                        send_telegram_execution_alert(exec_res, act_payload, is_retest=True)
                                    else:
                                        print(f"[!] Auto-trader execution failed on Binance for retest {sym}.")
                                else:
                                    print(f"[*] Retest for {sym} awaiting confirmation or invalid: {conf_reason}")

                        if not executed_live_retest:
                            send_telegram_resolution(s, "LIMIT_FILLED")

                    if s.get("live_entry_ts") and (now_ts - s["live_entry_ts"]) < 120000:
                        continue

            # -----------------------------------------------------------------
            # 2. ACTIVE TRADES: Multi-Stage Trailing Break-Even & Safe Execution
            # -----------------------------------------------------------------
            if s["status"] in ["OPEN", "TP1_BE_RUNNING", "TP2_LOCKED_RUNNING"]:
                if pos_sync_success and (s.get("symbol") in active_rp_syms):
                    s["executed_live"] = True
                    continue
                ref_ts = s.get("fill_ts") or s.get("live_entry_ts") or sig_ts
                future_bars = [bar for bar in kl if (bar[0] + 3600000) >= ref_ts]
                for bar in future_bars:
                    high = float(bar[2])
                    low = float(bar[3])
                    is_long = "BUY" in s["type"] or "LONG" in s["type"]
                    current_sl = s.get("trailing_sl", s["sl"])

                    # CONSERVATIVE SEQUENCING: Prioritize Stop Loss over Take Profit
                    # If intra-candle flash wick touches SL, execute SL exit first!
                    is_sl_touched = (low <= current_sl) if is_long else (high >= current_sl)
                    
                    if is_sl_touched:
                        if auto_trader and auto_trader.is_live_enabled():
                            auto_trader.close_position_market(s["symbol"], is_long, fraction=1.0)

                        now_utc_str = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')
                        now_utc_ts = int(datetime.now(timezone.utc).timestamp() * 1000)

                        if s["status"] == "TP2_LOCKED_RUNNING":
                            s["status"] = "WIN (TP2 Trailed / Locked +1.5R)"
                            s["outcome_pnl"] = +1.5
                            s["resolved_at"] = now_utc_str
                            s["resolved_at_utc"] = now_utc_str
                            s["resolved_ts"] = now_utc_ts
                            if not s.get("notified_loss"):
                                s["notified_loss"] = True
                                send_telegram_resolution(s, "WIN_LOCKED")
                        elif s["status"] == "TP1_BE_RUNNING":
                            s["status"] = "WIN (TP1 Hit / Break-Even Exit)"
                            s["outcome_pnl"] = +0.75
                            s["resolved_at"] = now_utc_str
                            s["resolved_at_utc"] = now_utc_str
                            s["resolved_ts"] = now_utc_ts
                            if not s.get("notified_loss"):
                                s["notified_loss"] = True
                                send_telegram_resolution(s, "WIN_BE")
                        else:
                            s["status"] = "LOSS (SL Hit)"
                            s["outcome_pnl"] = -1.0
                            s["resolved_at"] = now_utc_str
                            s["resolved_at_utc"] = now_utc_str
                            s["resolved_ts"] = now_utc_ts
                            if not s.get("notified_loss"):
                                s["notified_loss"] = True
                                send_telegram_resolution(s, "LOSS_SL")
                        break

                    # Check Take Profit Stages:
                    # Stage 3: Full TP3 Hit (1:3 Target)
                    is_tp3_touched = (high >= s["tp"]) if is_long else (low <= s["tp"])
                    if is_tp3_touched:
                        s["status"] = "WIN (1:3 TP3 Hit)"
                        s["outcome_pnl"] = +3.0
                        s["resolved_at"] = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')
                        s["resolved_at_utc"] = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')
                        s["resolved_ts"] = int(datetime.now(timezone.utc).timestamp() * 1000)
                        if auto_trader and auto_trader.is_live_enabled():
                            auto_trader.close_position_market(s["symbol"], is_long, fraction=1.0)
                        if not s.get("notified_win"):
                            s["notified_win"] = True
                            send_telegram_resolution(s, "WIN_TP3")
                        break

                    # Stage 2: TP2 Hit (1:2.0 Target) -> TRAIL SL UP TO TP1 (LOCK +1.5R)
                    is_tp2_touched = (s.get("tp2", 0) > 0) and ((high >= s["tp2"]) if is_long else (low <= s["tp2"]))
                    if is_tp2_touched and s["status"] in ["OPEN", "TP1_BE_RUNNING"]:
                        s["status"] = "TP2_LOCKED_RUNNING"
                        s["tp2_hit"] = True
                        s["trailing_sl"] = s.get("tp1", s["entry"])
                        s["trailing_sl_str"] = fmt_price(s["trailing_sl"])
                        if auto_trader and auto_trader.is_live_enabled():
                            auto_trader.close_position_market(s["symbol"], is_long, fraction=0.50)
                            auto_trader.trail_stop_loss(s["symbol"], is_long, s["trailing_sl"])
                        if not s.get("notified_tp2"):
                            s["notified_tp2"] = True
                            send_telegram_resolution(s, "WIN_TP2")

                    # Stage 1: TP1 Hit (1:1.5 Target) -> MOVE SL TO TRUE BREAK-EVEN
                    is_tp1_touched = (s.get("tp1", 0) > 0) and ((high >= s["tp1"]) if is_long else (low <= s["tp1"]))
                    if is_tp1_touched and s["status"] == "OPEN":
                        s["status"] = "TP1_BE_RUNNING"
                        s["tp1_hit"] = True
                        true_be = s["entry"] * (1.0 + FEE_BUFFER_PCT) if is_long else s["entry"] * (1.0 - FEE_BUFFER_PCT)
                        s["trailing_sl"] = true_be
                        s["trailing_sl_str"] = fmt_price(true_be)
                        if auto_trader and auto_trader.is_live_enabled():
                            auto_trader.close_position_market(s["symbol"], is_long, fraction=0.50)
                            auto_trader.trail_stop_loss(s["symbol"], is_long, true_be)
                        if not s.get("notified_tp1"):
                            s["notified_tp1"] = True
                            send_telegram_resolution(s, "WIN_TP1")
                            if history_data.get("circuit_breaker", {}).get("is_defensive"):
                                history_data["circuit_breaker"]["is_defensive"] = False
                                send_telegram_resolution(s, "DEFENSIVE_CLEARED")

    # Ensure physical Stop Loss & TP protection on Binance for all active positions
    if auto_trader and auto_trader.is_live_enabled():
        try:
            real_positions = auto_trader.get_open_positions_detail()
            for rp in real_positions:
                rp_sym = rp.get("symbol")
                rp_amt = float(rp.get("position_amt", 0.0))
                if rp_amt == 0:
                    continue
                rp_is_long = "BUY" in rp.get("side", "") or "LONG" in rp.get("side", "")
                matching = [s for s in existing_signals if s.get("symbol") == rp_sym and s.get("status") in ["OPEN", "TP1_BE_RUNNING", "TP2_LOCKED_RUNNING"]]
                if matching:
                    m_sig = matching[0]
                    m_sl = m_sig.get("trailing_sl") or m_sig.get("sl", 0)
                    m_tp = m_sig.get("tp", 0)
                    auto_trader.ensure_position_protection(rp_sym, rp_is_long, m_sl, m_tp, abs(rp_amt))
                else:
                    entry_p = float(rp.get("entry_price", 0.0))
                    if entry_p > 0:
                        safe_sl = entry_p * 0.96 if rp_is_long else entry_p * 1.04
                        risk = abs(safe_sl - entry_p)
                        safe_tp = entry_p + (risk * 3.0) if rp_is_long else entry_p - (risk * 3.0)
                        auto_trader.ensure_position_protection(rp_sym, rp_is_long, safe_sl, safe_tp, abs(rp_amt))
        except Exception as e_prot:
            print(f"[!] Protection auto-shield sync error: {e_prot}")

        # Sync live positions that closed on Binance matching engine & send Telegram Live Close Alerts
        try:
            if real_positions is not None:
                active_rp_syms = {rp.get("symbol") for rp in real_positions if float(rp.get("position_amt", 0.0)) != 0}
                for s in existing_signals:
                    if s.get("executed_live") and s.get("status") in ["OPEN", "TP1_BE_RUNNING", "TP2_LOCKED_RUNNING"]:
                        sym = s.get("symbol")
                        if sym not in active_rp_syms:
                            # Position closed on Binance Futures! Fetch exact trade fills & realized PnL
                            user_trades = auto_trader.get_user_trades(sym, limit=10)
                            realized_pnl = 0.0
                            if user_trades:
                                realized_pnl = sum(float(tr.get("realizedPnl", 0.0)) - float(tr.get("commission", 0.0)) for tr in user_trades)
                            
                            s["realized_pnl_usdt"] = round(realized_pnl, 2)
                            now_utc_str = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')
                            s["resolved_at"] = now_utc_str
                            s["resolved_at_utc"] = now_utc_str
                            s["resolved_ts"] = int(datetime.now(timezone.utc).timestamp() * 1000)

                            margin_val = float(s.get("executed_margin") or 2.5)
                            entry_p = float(s.get("entry") or s.get("raw_price") or 0.0)
                            sl_p = float(s.get("sl") or s.get("raw_limit_sl") or 0.0)
                            risk_pct = abs(entry_p - sl_p) / entry_p if (entry_p > 0 and sl_p > 0) else 0.025
                            risk_usdt = margin_val * 10.0 * risk_pct if risk_pct > 0 else (margin_val * 0.25)

                            if realized_pnl > 0:
                                calc_r = round(realized_pnl / risk_usdt, 2) if risk_usdt > 0 else round(realized_pnl / margin_val, 2)
                                s["status"] = f"WIN (+${realized_pnl:.2f} USDT)"
                                s["outcome_pnl"] = calc_r
                            elif realized_pnl < 0:
                                s["status"] = "LOSS (SL Hit)"
                                s["outcome_pnl"] = -1.0
                            else:
                                s["status"] = "CLOSED (Break-Even)"
                                s["outcome_pnl"] = 0.0

                            if s.get("executed_live") and not s.get("notified_live_close"):
                                s["notified_live_close"] = True
                                send_telegram_live_close(sym, realized_pnl, s["status"], s.get("outcome_pnl", 0.0))
        except Exception as e_close_sync:
            print(f"[!] Live closed position sync error: {e_close_sync}")

    recalculate_history_stats(history_data)
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history_data, f, indent=2)

    return history_data

def recalculate_history_stats(history_data):
    existing_signals = history_data.get("signals", [])
    active_statuses = ["OPEN", "TP1_BE_RUNNING", "TP2_LOCKED_RUNNING"]
    
    # Strictly isolate real trades executed live on Binance Futures
    live_signals = [s for s in existing_signals if s.get("executed_live")]
    
    # Target signals for primary dashboard statistics: strictly live Binance trades if any exist
    target_signals = live_signals if live_signals else existing_signals
    closed = [s for s in target_signals if s.get("status") not in active_statuses]
    wins = len([s for s in closed if "WIN" in s.get("status", "")])
    losses = len([s for s in closed if "LOSS" in s.get("status", "")])
    pending = len([s for s in target_signals if s.get("status") in active_statuses])
    total_closed = len(closed)
    win_rate = (wins / total_closed * 100) if total_closed > 0 else 0.0

    # Calculate Realized Net PnL in USDT against starting capital ($100.00) or from Binance API balance
    realized_usdt = 0.0
    initial_capital = 100.0
    
    if auto_trader and auto_trader.is_configured():
        try:
            b_info = auto_trader.get_account_balances()
            if b_info and b_info.get("wallet_balance", 0.0) > 0:
                realized_usdt = round(b_info["wallet_balance"] - initial_capital, 2)
        except Exception as e_bal:
            print(f"[!] Warning: Balance calculation sync error: {e_bal}")

    first_sig_ts = min([s.get("timestamp", 0) for s in target_signals]) if target_signals else 0
    if realized_usdt == 0.0 and first_sig_ts > 0 and auto_trader and auto_trader.is_configured():
        try:
            recent_inc = auto_trader.get_recent_income(limit=100)
            if recent_inc:
                bot_inc = [
                    float(inc.get("income", 0.0)) for inc in recent_inc
                    if inc.get("incomeType") in ["REALIZED_PNL", "COMMISSION", "FUNDING_FEE"]
                    and int(inc.get("time", 0)) >= (first_sig_ts - 60000)
                ]
                if bot_inc:
                    realized_usdt = round(sum(bot_inc), 2)
        except Exception as e_inc:
            print(f"[!] Warning: Income sync error: {e_inc}")
            
    if realized_usdt == 0.0 and closed:
        realized_usdt = round(sum(
            (s.get("outcome_pnl", 0.0) * float(s.get("executed_margin", 1.0) or 1.0))
            for s in closed
        ), 2)

    STANDARD_RISK_DOLLARS = 0.85
    net_r = round(sum(float(s.get("outcome_pnl") if s.get("outcome_pnl") is not None else 0.0) for s in closed), 1)
    if realized_usdt != 0.0:
        net_r = round(realized_usdt / STANDARD_RISK_DOLLARS, 1)

    history_data.update({
        "updated_at": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        "total_signals": len(target_signals),
        "total_closed": total_closed,
        "wins": wins,
        "losses": losses,
        "pending": pending,
        "win_rate_pct": round(win_rate, 1),
        "net_pnl_r": net_r,
        "net_pnl_usdt": realized_usdt,
        "signals": existing_signals
    })
    return history_data

def record_new_signals_to_history(actionable_signals, history_data, open_symbols_before_scan):
    existing_signals = history_data.get("signals", [])
    current_ts = int(datetime.now(timezone.utc).timestamp() * 1000)
    today_str = datetime.now().strftime('%Y-%m-%d')
    existing_keys = {f"{s['symbol']}_{s['date']}_{s['type']}" for s in existing_signals}

    if auto_trader and auto_trader.is_live_enabled():
        real_open = auto_trader.get_all_open_positions()
        active_positions_count = len(real_open)
    else:
        # Strictly count trades carrying open downside risk (exclude zero-risk break-even runners)
        active_positions_count = len([s for s in existing_signals if s.get("status") in ["OPEN", "PENDING_LIMIT"]])
    circuit_breaker = history_data.get("circuit_breaker", {})
    is_circuit_tripped = circuit_breaker.get("is_tripped", False)

    for act in actionable_signals:
        sym = act["symbol"]
        sig_type = act["signal"]
        
        # If already open, sync limit setup if available
        if sym in open_symbols_before_scan:
            for s in existing_signals:
                if s["symbol"] == sym and s.get("status") in ["OPEN", "PENDING_LIMIT", "TP1_BE_RUNNING", "TP2_LOCKED_RUNNING"]:
                    if not s.get("limit_setup") and act.get("limit_setup"):
                        s["limit_setup"] = act.get("limit_setup")
            continue
            
        # 1. Check Circuit Breaker
        if is_circuit_tripped:
            act["signal"] = "WATCHLIST"
            cb_st = circuit_breaker.get("status", "COOLDOWN")
            if cb_st == "COOLDOWN":
                rem_m = circuit_breaker.get("remaining_cooldown_mins", 0)
                act["tier_badge"] = f"⏳ 4H COOLDOWN ({rem_m}m left)"
                act["reasons"].append(f"Pro-Trader Circuit Breaker: 4-Hour Volatility Cooldown active ({rem_m}m remaining). Resumes in Defensive Mode.")
            else:
                act["tier_badge"] = "🛑 DAILY HARD STOP (3 Losses)"
                act["reasons"].append("Daily Drawdown Hard Stop: Max 3 daily losses hit. Trading stopped until 00:00 UTC to protect capital.")
            continue

        if circuit_breaker.get("is_defensive"):
            act["is_defensive"] = True
            act["risk_pct_override"] = 5.0
            act["tier_badge"] = "🛡️ DEFENSIVE RE-ENTRY (5% Risk)"

        # 2. Check Portfolio Heat Governor (Max 4 active trades)
        if active_positions_count >= MAX_ACTIVE_POSITIONS:
            act["signal"] = "WATCHLIST"
            act["tier_badge"] = "🔥 PORTFOLIO CAP REACHED"
            act["reasons"].append(f"Risk Governor: Maximum {MAX_ACTIVE_POSITIONS} active positions already open. Setup held in Watchlist.")
            continue

        # Fresh signal
        key = f"{sym}_{today_str}_{sig_type}"
        if key not in existing_keys:
            act["type"] = sig_type
            is_limit = bool(act.get("limit_setup"))
            initial_status = "PENDING_LIMIT" if is_limit else "OPEN"
            initial_sl = act["raw_sl"]

            new_item = {
                "id": len(existing_signals) + 1,
                "symbol": sym,
                "type": sig_type,
                "tier_badge": act.get("tier_badge", "⭐ TIER 1: INSTITUTIONAL SMC"),
                "setup": act["setup"],
                "session": act.get("session_name", "Global"),
                "fvg": act.get("fvg_str", "None"),
                "sweep": act.get("sweep_str", "None"),
                "date": today_str,
                "timestamp": current_ts,
                "entry": act["raw_entry"],
                "entry_str": act["entry"],
                "sl": initial_sl,
                "sl_str": act["stop_loss"],
                "trailing_sl": initial_sl,
                "trailing_sl_str": act["stop_loss"],
                "tp1": act.get("raw_tp1", 0),
                "tp1_str": act.get("tp_1", "-"),
                "tp2": act.get("raw_tp2", 0),
                "tp2_str": act.get("tp_2", "-"),
                "tp": act["raw_tp"],
                "tp_str": act["take_profit_1_3"],
                "limit_setup": act.get("limit_setup"),
                "funding_rate": act.get("funding_rate", 0.0),
                "open_interest": act.get("open_interest", 0.0),
                "status": initial_status,
                "outcome_pnl": 0.0
            }
            if initial_status == "OPEN":
                active_positions_count += 1
                if auto_trader and auto_trader.is_live_enabled():
                    # Iron-clad Risk Governor: Check real-time active positions on Binance Futures
                    live_pos = auto_trader.get_open_positions_detail()
                    if live_pos is not None and len(live_pos) >= MAX_ACTIVE_POSITIONS:
                        print(f"[!] Auto-Trader Risk Shield: Real Binance positions ({len(live_pos)}) reached max {MAX_ACTIVE_POSITIONS}. Blocked execution for {sym}.")
                        continue

                    cb = history_data.get("circuit_breaker", {})
                    act["is_defensive"] = cb.get("is_defensive", False)
                    risk_val = float(os.environ.get("BINANCE_RISK_PCT_PER_TRADE", 3.0))
                    act["risk_pct_override"] = (risk_val / 2.0) if cb.get("is_defensive") else risk_val
                    exec_res = auto_trader.execute_signal(act)
                    if exec_res and exec_res.get("executed"):
                        new_item["binance_order_id"] = exec_res.get("order_id")
                        new_item["binance_sl_order_id"] = exec_res.get("sl_order_id")
                        new_item["executed_live"] = True
                        new_item["executed_margin"] = exec_res.get("margin_usdt")
                        new_item["executed_qty"] = exec_res.get("qty")
                        if exec_res.get("entry_price"):
                            new_item["entry"] = exec_res.get("entry_price")
                            new_item["entry_str"] = fmt_price(new_item["entry"])
                            act["entry"] = fmt_price(new_item["entry"])
                        act["executed_live"] = True
                        act["executed_margin"] = exec_res.get("margin_usdt")
                        act["executed_qty"] = exec_res.get("qty")
                        send_telegram_execution_alert(exec_res, act)
                    else:
                        send_telegram_new_signal(act)
                else:
                    send_telegram_new_signal(act)
            elif initial_status == "PENDING_LIMIT":
                # For Limit Setups: Alert via Telegram, but wait for pullback in check_retest_and_execute_limit_orders to preserve exact 1:3 R:R
                send_telegram_new_signal(act)

            existing_signals.append(new_item)
            existing_keys.add(key)

    recalculate_history_stats(history_data)
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history_data, f, indent=2)

    return history_data

def get_top_pairs(limit=None):
    if limit is None:
        try:
            limit = int(os.environ.get("SCAN_PAIRS_LIMIT", 150))
        except Exception:
            limit = 150
    try:
        # Prefer USDT-M Futures 24hr tickers so 100% of symbols are tradable on Futures
        url = "https://fapi.binance.com/fapi/v1/ticker/24hr"
        resp = session.get(url, timeout=10)
        if resp.status_code != 200:
            url = "https://fapi1.binance.com/fapi/v1/ticker/24hr"
            resp = session.get(url, timeout=10)

        if resp.status_code == 200:
            data = resp.json()
            real_b_cryptos = {'BNBUSDT', 'DGBUSDT', 'TRBUSDT', 'CKBUSDT', 'SHIBUSDT', 'MOBUSDT', 'PHBUSDT', 'VIBUSDT', 'AMBUSDT', 'ARBUSDT', 'BBUSDT', 'YBUSDT', 'STXUSDT'}
            stables_and_junk = {'USDCUSDT', 'FDUSDUSDT', 'TUSDUSDT', 'EURUSDT', 'USDPUSDT', 'AEURUSDT', 'USD1USDT', 'RLUSDUSDT', 'XUSDUSDT', 'USDSBUSDT', 'BFDUSDT', 'EURIUSDT', 'USDEUSDT'}
            non_crypto_exclusions = {'NATGASUSDT', 'XAUUSDT', 'XAGUSDT', 'CLUSDT', 'SPCXUSDT', 'SOXLUSDT', 'SOXSUSDT', 'INTCUSDT', 'SAMSUNGUSDT', 'MUUSDT', 'SNDKUSDT', 'SKHYNIXUSDT', 'KORUUSDT', 'BZUSDT', 'CRCLUSDT', 'SNXXUSDT'}
            
            # Fetch official Binance exchangeInfo to strictly verify underlyingType == 'COIN' (100% Pure Crypto)
            pure_crypto_set = set()
            try:
                ex_resp = session.get("https://fapi.binance.com/fapi/v1/exchangeInfo", timeout=8)
                if ex_resp.status_code == 200:
                    for s in ex_resp.json().get("symbols", []):
                        if s.get("underlyingType") == "COIN" and s.get("status") == "TRADING" and s.get("contractType") == "PERPETUAL":
                            pure_crypto_set.add(s.get("symbol"))
            except Exception as _ex_err:
                pass

            usdt_pairs = []
            for t in data:
                sym = t.get('symbol', '')
                if not sym.endswith('USDT'):
                    continue
                if pure_crypto_set and sym not in pure_crypto_set:
                    continue
                if sym in non_crypto_exclusions:
                    continue
                if any(x in sym for x in ['UPUSDT', 'DOWNUSDT', 'BULLUSDT', 'BEARUSDT', '_']):
                    continue
                if sym in stables_and_junk:
                    continue
                if sym.endswith('BUSDT') and sym not in real_b_cryptos:
                    continue

                # Sniper Liquidity Filter: require >= $20M (20,000,000 USDT) 24h quote volume
                # Eliminates illiquid low-cap pairs prone to whale manipulation, spread slippage, and stop-hunts
                try:
                    q_vol = float(t.get('quoteVolume', 0))
                except Exception:
                    q_vol = 0.0
                if q_vol < 20_000_000:
                    continue

                usdt_pairs.append(t)

            # 1. Top Pure Crypto Volume Leaders (strictly >= $20M)
            usdt_pairs.sort(key=lambda x: float(x.get('quoteVolume', 0)), reverse=True)
            top_volume_symbols = [t['symbol'] for t in usdt_pairs[:limit]]

            # 2. Top 20 Pure Crypto Price & Volume Gainers/Movers of the day (strictly >= $20M)
            usdt_pairs_by_gainer = sorted(usdt_pairs, key=lambda x: abs(float(x.get('priceChangePercent', 0))), reverse=True)
            top_gainer_symbols = [t['symbol'] for t in usdt_pairs_by_gainer[:20]]

            # 3. Merge & Deduplicate
            combined_symbols = list(dict.fromkeys(top_volume_symbols + top_gainer_symbols))

            if len(combined_symbols) >= 20:
                min_vol = float(usdt_pairs[min(limit-1, len(usdt_pairs)-1)].get('quoteVolume', 0)) / 1e6
                print(f"[+] Selected {len(combined_symbols)} 100% Pure Crypto Futures Pairs with >= $20M Volume ({len(top_volume_symbols)} Leaders [Min: ${min_vol:.1f}M] + Top 20 Gainers)")
                return combined_symbols
    except Exception as e:
        print(f"[!] Warning: Dynamic Top {limit} fetch failed ({e}). Using curated list.")

    return [
        "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
        "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "LINKUSDT", "SUIUSDT",
        "NEARUSDT", "DOTUSDT", "APTUSDT", "FETUSDT", "RENDERUSDT",
        "ARBUSDT", "OPUSDT", "INJUSDT", "TIAUSDT", "PEPEUSDT",
        "SHIBUSDT", "LTCUSDT", "UNIUSDT", "AAVEUSDT", "FTMUSDT",
        "ONEUSDT", "GALAUSDT", "SEIUSDT", "WLDUSDT", "TAOUSDT",
        "KASUSDT", "STXUSDT", "FILUSDT", "ICPUSDT", "SANDUSDT"
    ]

def get_btc_macro_sentiment():
    kl_1h = get_klines("BTCUSDT", interval="1h", limit=50)
    if not kl_1h or len(kl_1h) < 20:
        return {"status": "NEUTRAL ⚪", "allow_longs": True, "allow_shorts": True, "reason": "BTC data unavailable"}
    
    closes = [float(k[4]) for k in kl_1h]
    ema20 = calculate_ema(closes, 20)[-1]
    ema50 = calculate_ema(closes, 50)[-1]
    rsi = calculate_rsi(closes, 14)
    current_p = closes[-1]
    
    change_3h = ((current_p - closes[-4]) / closes[-4]) * 100 if len(closes) >= 4 else 0.0
    
    # Realistic market crash/dump thresholds (avoids false alarm freezes on micro-fluctuations):
    is_dumping = change_3h < -1.8 or (current_p < ema50 and rsi < 38)
    is_pumping = change_3h > 2.5 or (current_p > ema50 and rsi > 75)
    
    btc_bullish_trend = (current_p >= ema50) or (ema20 >= ema50) or (rsi >= 46)
    btc_bearish_trend = (current_p <= ema50) or (ema20 <= ema50) or (rsi <= 54)
    
    allow_longs = (not is_dumping) and btc_bullish_trend
    allow_shorts = (not is_pumping) and btc_bearish_trend
    
    status_label = "DUMPING / BEARISH 🔴" if is_dumping else ("PARABOLIC PUMP 🟢" if is_pumping else ("BULLISH 🟢" if ema20 > ema50 else "BEARISH 🔴"))
    
    return {
        "status": status_label,
        "is_dumping": is_dumping,
        "is_pumping": is_pumping,
        "allow_longs": allow_longs,
        "allow_shorts": allow_shorts,
        "btc_price": current_p,
        "change_3h": change_3h,
        "rsi_1h": rsi,
        "reason": f"BTC 3h: {change_3h:+.2f}% | 1H RSI: {rsi:.1f} | 20/50 EMA: ${ema20:,.0f}/${ema50:,.0f}"
    }

NEWS_BUFFER_MINUTES = 45

CURATED_NEWS_EVENTS = [
    # September 2026
    {"title": "🇺🇸 US Non-Farm Payrolls (NFP)", "impact": "HIGH", "time_utc": "2026-09-04 12:30:00", "currency": "USD"},
    {"title": "🇺🇸 US CPI Inflation Rate (MoM/YoY)", "impact": "HIGH", "time_utc": "2026-09-11 12:30:00", "currency": "USD"},
    {"title": "🇺🇸 US Producer Price Index (PPI)", "impact": "HIGH", "time_utc": "2026-09-12 12:30:00", "currency": "USD"},
    {"title": "🏛️ FOMC Interest Rate Decision", "impact": "HIGH", "time_utc": "2026-09-16 18:00:00", "currency": "USD"},
    {"title": "🎙️ Fed Chair Powell Press Conference", "impact": "HIGH", "time_utc": "2026-09-16 18:30:00", "currency": "USD"},
    {"title": "🇺🇸 US Core PCE Price Index", "impact": "HIGH", "time_utc": "2026-09-25 12:30:00", "currency": "USD"},
    {"title": "💼 US Unemployment Claims & GDP", "impact": "HIGH", "time_utc": "2026-09-28 12:30:00", "currency": "USD"},
    
    # October 2026
    {"title": "💼 US Non-Farm Payrolls (NFP)", "impact": "HIGH", "time_utc": "2026-10-02 12:30:00", "currency": "USD"},
    {"title": "🇺🇸 US CPI Inflation Rate", "impact": "HIGH", "time_utc": "2026-10-14 12:30:00", "currency": "USD"},
    {"title": "🇺🇸 US Producer Price Index (PPI)", "impact": "HIGH", "time_utc": "2026-10-15 12:30:00", "currency": "USD"},
    {"title": "🏛️ FOMC Interest Rate Decision", "impact": "HIGH", "time_utc": "2026-10-28 18:00:00", "currency": "USD"},
    {"title": "🎙️ Fed Chair Powell Press Conference", "impact": "HIGH", "time_utc": "2026-10-28 18:30:00", "currency": "USD"},
    
    # November 2026
    {"title": "💼 US Non-Farm Payrolls (NFP)", "impact": "HIGH", "time_utc": "2026-11-06 12:30:00", "currency": "USD"},
    {"title": "🇺🇸 US CPI Inflation Rate", "impact": "HIGH", "time_utc": "2026-11-13 12:30:00", "currency": "USD"},
    {"title": "🏛️ FOMC Interest Rate Decision", "impact": "HIGH", "time_utc": "2026-11-18 18:00:00", "currency": "USD"},
    
    # December 2026
    {"title": "💼 US Non-Farm Payrolls (NFP)", "impact": "HIGH", "time_utc": "2026-12-04 12:30:00", "currency": "USD"},
    {"title": "🇺🇸 US CPI Inflation Rate", "impact": "HIGH", "time_utc": "2026-12-11 12:30:00", "currency": "USD"},
    {"title": "🏛️ FOMC Interest Rate Decision", "impact": "HIGH", "time_utc": "2026-12-16 18:00:00", "currency": "USD"}
]

def get_upcoming_economic_news():
    now_utc = datetime.now(timezone.utc)
    events = []
    
    for ev in CURATED_NEWS_EVENTS:
        try:
            dt = datetime.strptime(ev["time_utc"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            if now_utc - timedelta(days=2) <= dt <= now_utc + timedelta(days=14):
                ts_ms = int(dt.timestamp() * 1000)
                diff_sec = (dt - now_utc).total_seconds()
                diff_mins = int(diff_sec // 60)
                
                time_str = dt.strftime("%b %d, %H:%M UTC")
                
                events.append({
                    "title": ev["title"],
                    "impact": ev["impact"],
                    "currency": ev["currency"],
                    "time_utc": ev["time_utc"],
                    "timestamp": ts_ms,
                    "time_str": time_str,
                    "mins_away": diff_mins,
                    "is_past": diff_sec < -NEWS_BUFFER_MINUTES * 60
                })
        except Exception:
            pass
            
    events.sort(key=lambda x: x["timestamp"])
    return events

def evaluate_news_shield(events, buffer_minutes=NEWS_BUFFER_MINUTES):
    now_utc = datetime.now(timezone.utc)
    now_ts_ms = int(now_utc.timestamp() * 1000)
    buffer_ms = buffer_minutes * 60 * 1000
    
    is_shield_active = False
    active_event = None
    block_until_str = ""
    mins_remaining = 0
    
    for ev in events:
        ev_ts = ev["timestamp"]
        start_shield = ev_ts - buffer_ms
        end_shield = ev_ts + buffer_ms
        
        if start_shield <= now_ts_ms <= end_shield:
            is_shield_active = True
            active_event = ev
            end_dt = datetime.fromtimestamp(end_shield / 1000, tz=timezone.utc)
            block_until_str = end_dt.strftime("%H:%M UTC")
            mins_remaining = int((end_shield - now_ts_ms) / 60000)
            break
            
    return {
        "is_shield_active": is_shield_active,
        "active_event": active_event,
        "block_until_str": block_until_str,
        "mins_remaining": mins_remaining,
        "shield_status_str": "TRADES PAUSED (SHIELD ACTIVE)" if is_shield_active else "NORMAL TRADING",
        "buffer_minutes": buffer_minutes
    }

def scan_all_pairs():
    # 1. Get active Market Session & News Shield Status
    session_info = get_market_session()
    upcoming_news = get_upcoming_economic_news()
    news_shield = evaluate_news_shield(upcoming_news)

    # 2. Resolve open trades against latest candles & handle trailing stops
    history_data = check_and_resolve_open_trades(news_shield=news_shield)
    active_statuses = ["OPEN", "TP1_BE_RUNNING", "TP2_LOCKED_RUNNING"]
    open_signals_map = {s["symbol"]: s for s in history_data.get("signals", []) if s.get("status") in active_statuses}
    open_symbols_before_scan = set(open_signals_map.keys())

    # 3. Check BTC Macro Market Sentiment
    btc_sentiment = get_btc_macro_sentiment()
    circuit_breaker = history_data.get("circuit_breaker", {})

    # 4. Get Top liquid pairs (Configurable via SCAN_PAIRS_LIMIT, default 100)
    pairs = get_top_pairs()
    for s_sym in open_symbols_before_scan:
        if s_sym not in pairs:
            pairs.append(s_sym)

    print("=" * 95)
    print(" 🎯 DMD SMC PRO 3.0 ENGINE (DYNAMIC DUAL-DIRECTION MTF + FVG + 2.8x ATR STOPS)")
    print(f" Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} (Local)")
    print(f" Active Session: {session_info['badge']} ({session_info['desc']})")
    cb_txt = "🟢 NORMAL SHIELD"
    if circuit_breaker.get("status") == "HARD_STOP":
        cb_txt = "🛑 HARD STOP (Max 3 Losses Hit - Paused until 00:00 UTC)"
    elif circuit_breaker.get("status") == "COOLDOWN":
        cb_txt = f"⏳ 4H COOLDOWN ({circuit_breaker.get('remaining_cooldown_mins', 0)}m remaining)"
    elif circuit_breaker.get("is_defensive"):
        cb_txt = "🛡️ DEFENSIVE RESUME (5% Half-Risk Active)"
    print(f" Circuit Breaker: {cb_txt} [Today Losses: {circuit_breaker.get('losses_today', 0)}/{MAX_DAILY_HARD_STOP_LOSSES}]")
    
    news_txt = "🟢 NORMAL (No High-Impact News)"
    if news_shield["is_shield_active"]:
        act_ev = news_shield["active_event"]
        news_txt = f"🛡️ SHIELD ACTIVE [{act_ev['title']}] (Paused until {news_shield['block_until_str']})"
    print(f" News Safety Shield: {news_txt}")
    
    print(f" Portfolio Heat: {len(open_symbols_before_scan)} / {MAX_ACTIVE_POSITIONS} Max Positions")
    print(f" Total Symbols to Scan: {len(pairs)} | Active Monitored: {len(open_signals_map)}")
    print(f" 🌐 BTC Macro Sentiment: {btc_sentiment['status']} ({btc_sentiment['reason']})")
    if not btc_sentiment['allow_longs']:
        print("    ⚠️  [BTC DUMP SHIELD ACTIVE] Altcoin BUY / LONG signals are strictly BLOCKED.")
    if not btc_sentiment['allow_shorts']:
        print("    ⚠️  [BTC PUMP SHIELD ACTIVE] Altcoin SELL / SHORT signals are strictly BLOCKED.")
    print("=" * 95)

    results = []
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(analyze_symbol, p, open_signals_map.get(p), btc_sentiment, session_info): p for p in pairs}
        for future in futures:
            res = future.result()
            if res:
                results.append(res)

    if len(results) == 0:
        print("[!] Warning: 0 pairs fetched. Preserving previous data!")
        return [], []

    actionable = [r for r in results if r["signal"] in ["BUY / LONG", "SELL / SHORT"]]
    watchlist = [r for r in results if r["signal"] == "WATCHLIST"]

    if news_shield["is_shield_active"]:
        print(f"[🛡️] News Shield Active: Pausing new trade executions for {news_shield['active_event']['title']}")
        actionable = []

    # 5. Record brand new signals respecting Portfolio Heat & Circuit Breaker
    history = record_new_signals_to_history(actionable, history_data, open_symbols_before_scan)

    print("-" * 95)
    print(f" 🚀 ACTIVE TRADE SIGNALS (1:3 R:R): {len(actionable)} Found")
    print("-" * 95)

    for idx, a in enumerate(actionable, 1):
        print(f"\n[{idx}] 🪙 {a['symbol']}  |  {a['signal']}  |  {a['tier_badge']}")
        print(f"    ├─ Setup            : {a['setup']}")
        print(f"    ├─ 4H MTF Bias      : {a['mtf_status']} | Active Session: {a['session_name']}")
        print(f"    ├─ Live Price       : ${a['current_price']} (Entry: ${a['entry']})")
        print(f"    ├─ Dynamic SL (ATR) : ${a['stop_loss']} (-{a['risk_pct']})")
        print(f"    ├─ Trailing Stop    : ${a.get('trailing_sl_str', a['stop_loss'])}")
        print(f"    ├─ TP Targets       : TP1: ${a['tp_1']} (50%) | TP2: ${a['tp_2']} (25%) | TP3: ${a['take_profit_1_3']} (Full 1:3)")
        print(f"    ├─ FVG Confluence   : {a['fvg_str']}")
        print(f"    ├─ Liquidity Sweep  : {a['sweep_str']}")
        print(f"    └─ Key Confirmations:")
        for r in a['reasons']:
            print(f"       • {r}")

    # Write output to json and html
    json_path = os.path.join(SCRIPT_DIR, "latest_signals.json")
    html_path = os.path.join(SCRIPT_DIR, "dashboard.html")
    index_path = os.path.join(SCRIPT_DIR, "index.html")
    parent_dir = os.path.dirname(SCRIPT_DIR)
    json_parent_path = os.path.join(parent_dir, "latest_signals.json")
    html_parent_path = os.path.join(parent_dir, "dashboard.html")
    index_parent_path = os.path.join(parent_dir, "index.html")

    # Fetch real live Binance account balance & positions
    account_info = None
    real_positions = []
    if auto_trader and auto_trader.is_configured():
        account_info = auto_trader.get_account_balances()
        real_positions = auto_trader.get_open_positions_detail()
        if account_info:
            tot_pnl = sum(p.get("unrealized_pnl", 0.0) for p in real_positions)
            wb = account_info.get("wallet_balance", 0.0)
            account_info["unrealized_pnl"] = round(tot_pnl, 4)
            account_info["equity"] = round(wb + tot_pnl, 2)

    payload = {
        "updated_at": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        "filter": "DMD SMC Pro 3.0: Dynamic Dual-Direction MTF + FVG + Liquidity Sweep + 2.8x ATR Stops (1:3 R:R)",
        "session": session_info,
        "circuit_breaker": history.get("circuit_breaker", {}),
        "news_shield": news_shield,
        "upcoming_news": upcoming_news,
        "btc_sentiment": btc_sentiment,
        "portfolio_heat": f"{len(open_symbols_before_scan)} / {MAX_ACTIVE_POSITIONS} Max",
        "active_count": len(actionable),
        "active_signals": actionable,
        "all_monitored": results,
        "history": history,
        "binance_account": account_info,
        "real_positions": real_positions
    }
    
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

    generate_html_dashboard(payload, html_path)
    generate_html_dashboard(payload, index_path)
    try:
        generate_html_dashboard(payload, html_parent_path)
        generate_html_dashboard(payload, index_parent_path)
        with open(json_parent_path, "w", encoding="utf-8") as f_p:
            json.dump(payload, f_p, indent=2)
    except Exception:
        pass
    print(f"\n[+] Updated {json_path} & {json_parent_path}")
    print(f"[+] Updated {html_path} & {html_parent_path}")
    print(f"[+] Updated {index_path} & {index_parent_path}")

    return actionable, results

def generate_html_dashboard(data, output_path):
    data_json_str = json.dumps(data, indent=2)
    history = data.get("history", {})
    win_rate = history.get("win_rate_pct", 0.0)
    wins = history.get("wins", 0)
    losses = history.get("losses", 0)
    net_r = history.get("net_pnl_r", 0.0)
    net_usdt = history.get("net_pnl_usdt", 0.0)
    net_usdt_sign = "+" if net_usdt >= 0 else "-"
    if net_usdt == 0.0:
        net_usdt_sign = ""
    net_usdt_color = "val-green" if net_usdt >= 0 else "val-red"
    net_usdt_display = f"{net_usdt_sign}${abs(net_usdt):,.2f} USDT"

    btc_sentiment = data.get("btc_sentiment", {})
    btc_status = btc_sentiment.get("status", "BULLISH 🟢")
    session_info = data.get("session", {})
    session_badge = session_info.get("badge", "🌐 GLOBAL MARKET")
    circuit_breaker = data.get("circuit_breaker", {})
    cb_tripped = circuit_breaker.get("is_tripped", False)
    cb_losses = circuit_breaker.get("losses_today", 0)
    heat_str = data.get("portfolio_heat", f"0 / {MAX_ACTIVE_POSITIONS} Max")
    binance_account = data.get("binance_account") or {}
    wallet_bal = binance_account.get("wallet_balance")
    avail_margin = binance_account.get("available_balance")
    equity = binance_account.get("equity", wallet_bal)
    tot_unrealized = binance_account.get("unrealized_pnl", 0.0)
    
    equity_str = f"${equity:,.2f} USDT" if equity is not None else "--"
    wallet_bal_str = f"${wallet_bal:,.2f} USDT" if wallet_bal is not None else "--"
    avail_margin_str = f"${avail_margin:,.2f} USDT" if avail_margin is not None else "--"
    
    pnl_sign = "+" if tot_unrealized >= 0 else ""
    pnl_color = "text-success" if tot_unrealized >= 0 else "text-danger"
    floating_badge = f'<span class="{pnl_color} fw-bold ms-1" id="binance-floating-pnl">({pnl_sign}${tot_unrealized:,.2f} Floating)</span>' if tot_unrealized != 0 else '<span class="text-muted ms-1" id="binance-floating-pnl">(+$0.00)</span>'

    if cb_tripped:
        if circuit_breaker.get("status") == "COOLDOWN":
            rem_m = circuit_breaker.get("remaining_cooldown_mins", 0)
            cb_html = f'<span class="badge bg-warning text-dark"><i class="fa-solid fa-hourglass-half me-1"></i>⏳ 4H COOLDOWN ({rem_m}m left)</span>'
        else:
            cb_html = f'<span class="badge bg-danger text-white"><i class="fa-solid fa-hand me-1"></i>🛑 HARD STOP ACTIVE ({cb_losses}/3 Losses)</span>'
    elif circuit_breaker.get("is_defensive"):
        cb_html = f'<span class="badge bg-info text-dark"><i class="fa-solid fa-shield-halved me-1"></i>🛡️ DEFENSIVE RESUME (5% Risk)</span>'
    else:
        cb_html = f'<span class="badge bg-success bg-opacity-25 text-success border border-success"><i class="fa-solid fa-shield-halved me-1"></i>ADAPTIVE SHIELD ACTIVE ({cb_losses}/3)</span>'

    news_shield = data.get("news_shield", {})
    upcoming_news = data.get("upcoming_news", [])
    is_news_active = news_shield.get("is_shield_active", False)
    news_active_event = news_shield.get("active_event")
    
    if is_news_active and news_active_event:
        news_nav_html = f'<span class="badge bg-danger text-white border border-danger"><i class="fa-solid fa-hand me-1"></i>🛡️ NEWS SHIELD ACTIVE (Paused until {news_shield.get("block_until_str")})</span>'
    else:
        news_nav_html = f'<span class="badge bg-success bg-opacity-25 text-success border border-success"><i class="fa-solid fa-newspaper me-1"></i>NEWS GUARD ACTIVE (Normal)</span>'

    news_rows = []
    if upcoming_news:
        for ev in upcoming_news[:5]:
            mins = ev.get("mins_away", 0)
            if mins < -45:
                time_away_str = '<span class="badge bg-secondary opacity-50">Past Event</span>'
                sys_action_str = '<span class="text-muted">Cleared</span>'
            elif -45 <= mins <= 45:
                time_away_str = f'<span class="badge bg-danger animate-pulse">🔥 ACTIVE NOW ({abs(mins)}m)</span>'
                sys_action_str = '<span class="badge bg-danger text-white fw-bold"><i class="fa-solid fa-hand me-1"></i>🛡️ TRADES PAUSED</span>'
            elif mins <= 180:
                hours_away = round(mins / 60, 1)
                time_away_str = f'<span class="badge bg-warning text-dark">In {hours_away} hrs ({mins}m)</span>'
                sys_action_str = '<span class="badge bg-dark text-warning border border-warning">🛡️ Shield Scheduled</span>'
            else:
                days_away = round(mins / 1440, 1)
                time_away_str = f'<span class="badge bg-dark border border-secondary text-info">In {days_away} days</span>'
                sys_action_str = '<span class="text-success"><i class="fa-solid fa-check me-1"></i>Normal Execution</span>'

            news_rows.append(f"""
            <tr>
                <td class="fw-bold text-white"><i class="fa-solid fa-newspaper text-warning me-2"></i>{ev.get('title')}</td>
                <td class="text-warning font-monospace">{ev.get('time_str')}</td>
                <td><span class="badge bg-danger text-white fw-bold"><i class="fa-solid fa-fire me-1"></i>{ev.get('impact')}</span></td>
                <td><span class="badge bg-dark border border-secondary text-light">{ev.get('currency')}</span></td>
                <td>{time_away_str}</td>
                <td>{sys_action_str}</td>
            </tr>
            """)
    news_rows_html = "".join(news_rows) if news_rows else '<tr><td colspan="6" class="text-center text-muted italic">No upcoming High-Impact economic events detected in calendar.</td></tr>'

    real_positions = data.get("real_positions") or []
    pos_count = len(real_positions)
    pos_count_text = f"{pos_count} Active" if pos_count > 0 else "0 Active (Protected)"
    pos_badge_class = "badge bg-success ms-2" if pos_count > 0 else "badge bg-secondary ms-2"

    active_signals = data.get("active_signals", [])
    init_limit = 0
    init_stopped = 0
    init_running = 0
    init_ready = 0
    for sig in active_signals:
        if sig.get("limit_setup"):
            init_limit += 1
        elif sig.get("action_status") == "STOPPED":
            init_stopped += 1
        elif sig.get("action_status") == "RUNNING":
            init_running += 1
        else:
            init_ready += 1

    html_content = f"""<!DOCTYPE html>
<html lang="en" data-bs-theme="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">
    <meta http-equiv="Pragma" content="no-cache">
    <meta http-equiv="Expires" content="0">
    <title>DMD SMC Pro 3.0 - Institutional Live Scanner</title>
    <link rel="preconnect" href="https://cdn.jsdelivr.net">
    <link rel="preconnect" href="https://cdnjs.cloudflare.com">
    <link rel="dns-prefetch" href="https://data-api.binance.vision">
    <link rel="dns-prefetch" href="https://api.binance.com">
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <style>
        :root {{
            --bg-dark: #080b11;
            --card-dark: #121824;
            --border-color: #1e293b;
            --accent-green: #0ecb81;
            --accent-red: #f6465d;
            --accent-yellow: #f0b90b;
            --accent-purple: #9b51e0;
            --accent-cyan: #00f2fe;
        }}
        html, body {{
            background-color: #080b11 !important;
            color: #f8fafc !important;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
            padding-bottom: 50px;
        }}
        /* Master High-Contrast Rule: Force ALL muted & small text to be bright readable slate blue */
        .text-muted, small.text-muted, span.text-muted, div.text-muted, p.text-muted, td.text-muted {{
            color: #94a3b8 !important;
        }}
        .stat-title, .metric-title {{
            font-size: 0.76rem !important;
            color: #cbd5e1 !important;
            font-weight: 600 !important;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            margin-bottom: 4px;
        }}
        .stat-card {{
            background-color: #121824 !important;
            border: 1px solid #1e293b !important;
            border-radius: 12px;
            padding: 16px 20px;
            box-shadow: 0 4px 20px rgba(0, 0, 0, 0.4);
        }}
        .position-card {{
            background-color: #121824 !important;
            border: 1px solid #1e293b !important;
            border-radius: 10px;
            padding: 12px 14px;
            box-shadow: 0 4px 15px rgba(0, 0, 0, 0.35);
            transition: transform 0.15s ease, border-color 0.15s ease;
        }}
        .position-card:hover {{
            transform: translateY(-2px);
            border-color: #334155 !important;
        }}
        .signal-card {{
            background-color: #121824 !important;
            border: 1px solid #1e293b !important;
            border-radius: 12px;
            padding: 20px;
            transition: all 0.3s ease;
            position: relative;
            overflow: hidden;
            box-shadow: 0 4px 20px rgba(0, 0, 0, 0.4);
        }}
        .signal-card:hover {{
            transform: translateY(-4px);
            border-color: #3b82f6 !important;
            box-shadow: 0 10px 30px rgba(0, 0, 0, 0.6);
        }}
        .pos-label {{
            color: #94a3b8 !important;
            font-weight: 600 !important;
        }}
        .pos-val {{
            color: #f8fafc !important;
            font-weight: 700 !important;
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
        .price-hero {{
            background: linear-gradient(145deg, #0d121a, #131a24);
            border: 1px solid #232c3a;
            border-radius: 10px;
            padding: 12px 16px;
            margin-bottom: 12px;
        }}
        .metric-title {{
            font-size: 0.75rem;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            color: #848e9c;
            margin-bottom: 4px;
        }}
        .val-green {{ color: var(--accent-green); }}
        .val-red {{ color: var(--accent-red); }}
        .val-yellow {{ color: var(--accent-yellow); }}
        .val-cyan {{ color: var(--accent-cyan); }}
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
        .confluence-chip {{
            font-size: 0.72rem;
            padding: 3px 8px;
            border-radius: 4px;
            font-weight: 600;
        }}
    </style>
</head>
<body>

    <nav class="navbar navbar-dark px-4 py-3 mb-4">
        <div class="container-fluid">
            <span class="navbar-brand mb-0 h1 d-flex align-items-center">
                <i class="fa-solid fa-shield-halved text-warning me-2 fs-3"></i>
                <div>
                    <span class="fw-bold">DMD SMC Pro 3.0</span>
                    <span class="badge bg-warning text-dark ms-2" style="font-size: 0.7rem;">Institutional Quantitative Engine</span>
                </div>
            </span>
            <div class="d-flex align-items-center flex-wrap gap-2">
                <span class="badge bg-dark border border-secondary text-light">
                    <i class="fa-regular fa-clock text-warning me-1"></i> Session: <strong>{session_badge}</strong>
                </span>
                <span class="badge bg-dark border border-secondary text-light">
                    <i class="fa-solid fa-fire text-danger me-1"></i> Heat: <strong>{heat_str}</strong>
                </span>
                {cb_html}
                {news_nav_html}
                <span class="badge bg-dark border border-secondary text-light">
                    <i class="fa-brands fa-bitcoin text-warning me-1"></i> BTC: <strong class="ms-1">{btc_status}</strong>
                </span>
                <span id="update-time" class="text-muted small">Updated: <span class="text-warning">{data.get('updated_at')}</span></span>
                <!-- AUTH_NAV_SLOT -->
            </div>
        </div>
    </nav>

    <div class="container-fluid px-4">
        
        <!-- Live Real DMD Account Balance & Auto-Trader Status -->
        <div class="row g-3 mb-4">
            <div class="col-12 col-md-3">
                <div class="stat-card" style="border-left: 4px solid #f0b90b;">
                    <div class="stat-title"><i class="fa-solid fa-wallet text-warning me-1"></i> Total Account Equity (Live)</div>
                    <div class="stat-value text-warning" id="binance-wallet-bal">{equity_str}</div>
                    <small class="text-muted">Realized: <span id="binance-realized-cash">{wallet_bal_str}</span> {floating_badge}</small>
                </div>
            </div>
            <div class="col-12 col-md-3">
                <div class="stat-card" style="border-left: 4px solid #0ecb81;">
                    <div class="stat-title"><i class="fa-solid fa-shield-halved text-success me-1"></i> Available Margin</div>
                    <div class="stat-value val-green" id="binance-avail-margin">{avail_margin_str}</div>
                    <small class="text-muted">Available for 10% Auto-Allocation</small>
                </div>
            </div>
            <div class="col-12 col-md-3">
                <div class="stat-card" style="border-left: 4px solid #00f2fe;">
                    <div class="stat-title"><i class="fa-solid fa-sliders text-info me-1"></i> Leverage & Margin Mode</div>
                    <div class="stat-value text-info" style="font-size: 1.35rem;">10x ISOLATED</div>
                    <small class="text-muted">Strict Zero-Cross Capital Protection</small>
                </div>
            </div>
            <div class="col-12 col-md-3">
                <div class="stat-card" style="border-left: 4px solid #9b51e0;">
                    <div class="stat-title"><i class="fa-solid fa-robot text-purple me-1" style="color: #a855f7;"></i> Auto-Trading Engine</div>
                    <div class="stat-value text-white" style="font-size: 1.35rem;"><span class="badge bg-success" style="font-size: 0.95rem;">🟢 LIVE AUTONOMOUS</span></div>
                    <small class="text-muted">Auto-Execute 10% Margin on A+ Setups</small>
                </div>
            </div>
        </div>

        <!-- Section: High-Impact Economic News & Shield Widget -->
        <div class="row g-3 mb-4">
            <div class="col-12">
                <div class="stat-card" style="border-left: 4px solid {'#f6465d' if is_news_active else '#0ecb81'};">
                    <div class="d-flex align-items-center justify-content-between flex-wrap gap-2 mb-3">
                        <div>
                            <h5 class="fw-bold mb-1 text-white d-flex align-items-center">
                                <i class="fa-solid fa-newspaper text-warning me-2"></i>Upcoming High-Impact Economic News & Shield Status
                                <span class="badge {'bg-danger text-white ms-2' if is_news_active else 'bg-success bg-opacity-25 text-success ms-2'}" style="font-size: 0.8rem;">
                                    {'🛡️ SHIELD ACTIVE (TRADES PAUSED)' if is_news_active else '🟢 NORMAL TRADING'}
                                </span>
                            </h5>
                            <small class="text-muted">Automatic ±45 Minute Protection Window around US High-Impact Events (CPI, FOMC, NFP, PPI)</small>
                        </div>
                        <div>
                            <span class="badge bg-dark border border-secondary text-info p-2" style="font-size: 0.85rem;">
                                <i class="fa-solid fa-shield-cat me-1"></i>Shield Status: <strong class="{'text-warning' if is_news_active else 'text-success'}">{'PAUSED until ' + news_shield.get('block_until_str', '') if is_news_active else 'NO NEWS BLOCK ACTIVE'}</strong>
                            </span>
                        </div>
                    </div>

                    <div class="table-responsive">
                        <table class="table table-dark table-hover align-middle mb-0" style="font-size: 0.85rem;">
                            <thead>
                                <tr>
                                    <th>Event Title</th>
                                    <th>Release Time (UTC)</th>
                                    <th>Impact</th>
                                    <th>Currency</th>
                                    <th>Time Status</th>
                                    <th>System Execution Action</th>
                                </tr>
                            </thead>
                            <tbody>
                                {news_rows_html}
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>
        </div>


        <!-- Section: Active DMD Positions (Live Execution) -->
        <div class="mb-4" id="live-positions-section">
            <div class="d-flex align-items-center justify-content-between mb-2">
                <h4 class="fw-bold mb-0 text-white">
                    <i class="fa-solid fa-chart-line text-success me-2"></i>Active DMD Positions (Live Execution)
                    <span class="{pos_badge_class}" id="live-pos-count-badge">{pos_count_text}</span>
                </h4>
                <small class="text-muted">Live matching engine stream from DMD Futures</small>
            </div>
            <div id="live-positions-container">
            </div>
        </div>

        <!-- Live Real Performance Bar (Strictly Real Binance Executed Trades) -->
        <div class="row g-3 mb-4">
            <div class="col-12 col-md-3">
                <div class="stat-card">
                    <div class="stat-title"><i class="fa-solid fa-trophy text-warning me-1"></i> Real Live Win Rate</div>
                    <div class="stat-value val-yellow" id="stat-win-rate">{win_rate}%</div>
                    <small class="text-muted">Binance Executed ({wins}W / {losses}L)</small>
                </div>
            </div>
            <div class="col-12 col-md-3">
                <div class="stat-card">
                    <div class="stat-title"><i class="fa-solid fa-check-double text-success me-1"></i> Real Live Wins</div>
                    <div class="stat-value val-green" id="stat-wins">{wins}</div>
                    <small class="text-muted">Scaled Exits (+3.0R & Trailed +1.5R)</small>
                </div>
            </div>
            <div class="col-12 col-md-3">
                <div class="stat-card">
                    <div class="stat-title"><i class="fa-solid fa-xmark text-danger me-1"></i> Real Live Losses</div>
                    <div class="stat-value val-red" id="stat-losses">{losses}</div>
                    <small class="text-muted">Binance Hardware SL Hit (-1.0R)</small>
                </div>
            </div>
            <div class="col-12 col-md-3">
                <div class="stat-card">
                    <div class="stat-title"><i class="fa-solid fa-scale-balanced text-info me-1"></i> Net Realized Return ($ USDT)</div>
                    <div class="stat-value {net_usdt_color}" id="stat-net-r">{net_usdt_display} <span style="font-size: 0.95rem;" class="text-info font-monospace">({'+' if net_r >= 0 else ''}{net_r} R)</span></div>
                    <small class="text-muted">Binance Realized Cash PnL</small>
                </div>
            </div>
        </div>

        <!-- Section 1: Active Signals -->
        <div class="mb-4">
            <div class="d-flex align-items-center justify-content-between mb-2">
                <h4 class="fw-bold mb-0 text-white">
                    <i class="fa-solid fa-bolt text-warning me-2"></i>Institutional SMC Setups (4H MTF + FVG + ATR Stops)
                    <span class="badge bg-warning text-dark ms-2" id="active-total-badge">{data.get('active_count', 0)}</span>
                </h4>
                <small class="text-muted">Live 4s ticker streaming directly from DMD API</small>
            </div>

            <!-- Category Filter Tabs -->
            <div class="d-flex flex-wrap gap-2 mb-3 mt-3">
                <button class="btn btn-sm btn-outline-light active filter-tab-btn" id="btn-filter-all" onclick="filterSignals('ALL')">
                    <i class="fa-solid fa-layer-group me-1"></i> All Setups (<span id="cnt-all">{data.get('active_count', 0)}</span>)
                </button>
                <button class="btn btn-sm btn-outline-success filter-tab-btn" id="btn-filter-ready" onclick="filterSignals('READY')">
                    <i class="fa-solid fa-circle-check me-1"></i> 🟢 Ganna Puluwan (Ready) (<span id="cnt-ready">{init_ready}</span>)
                </button>
                <button class="btn btn-sm btn-outline-warning filter-tab-btn" id="btn-filter-limit" onclick="filterSignals('LIMIT')">
                    <i class="fa-solid fa-clock me-1"></i> 🟡 Pending Limit Retest (<span id="cnt-limit">{init_limit}</span>)
                </button>
                <button class="btn btn-sm btn-outline-info filter-tab-btn" id="btn-filter-running" onclick="filterSignals('RUNNING')">
                    <i class="fa-solid fa-rocket me-1"></i> 🚀 Setups Running in Profit (<span id="cnt-running">{init_running}</span>)
                </button>
                <button class="btn btn-sm btn-outline-danger filter-tab-btn" id="btn-filter-stopped" onclick="filterSignals('STOPPED')">
                    <i class="fa-solid fa-triangle-exclamation me-1"></i> 🔴 SL Breached (<span id="cnt-stopped">{init_stopped}</span>)
                </button>
                <button type="button" onclick="scrollToHistory()" class="btn btn-sm btn-outline-secondary filter-tab-btn">
                    <i class="fa-solid fa-clock-rotate-left me-1"></i> 🛑 Closed History (<span id="cnt-expired">{losses + wins}</span>)
                </button>
            </div>

            <div id="signals-container" class="row g-3">
            </div>
        </div>

        <!-- Section 2: Watchlist -->
        <div class="mt-5">
            <h4 class="fw-bold mb-3 text-white">
                <i class="fa-solid fa-binoculars text-info me-2"></i>Multi-Timeframe Structure Watchlist (150 Pairs)
            </h4>
            <div class="table-responsive table-dark-custom">
                <table class="table mb-0">
                    <thead>
                        <tr>
                            <th>Pair</th>
                            <th>Current Price</th>
                            <th>4H Bias</th>
                            <th>1H FVG / Sweep</th>
                            <th>1H Order Block</th>
                            <th>Daily S&R Base</th>
                            <th>RSI (1D/1H)</th>
                            <th>Status</th>
                        </tr>
                    </thead>
                    <tbody id="watchlist-body">
                    </tbody>
                </table>
            </div>
        </div>

        <!-- Section 3: Closed Trade Outcomes -->
        <div class="mt-5 mb-5" id="history-section">
            <div class="d-flex align-items-center justify-content-between mb-3">
                <h4 class="fw-bold mb-0 text-white">
                    <i class="fa-solid fa-clock-rotate-left text-warning me-2"></i>Real Live Trade Execution Ledger (Binance Futures)
                </h4>
                <span class="badge bg-dark border border-secondary text-success">Verified Binance Futures Executions Only</span>
            </div>
            <div class="table-responsive table-dark-custom">
                <table class="table mb-0">
                    <thead>
                        <tr>
                            <th>Signal Date</th>
                            <th>Pair</th>
                            <th>Type</th>
                            <th>Setup</th>
                            <th>Executed Entry</th>
                            <th>Margin / Size</th>
                            <th>Trailing / Safe SL</th>
                            <th>TP1 (1:1.5)</th>
                            <th>TP3 (1:3.0)</th>
                            <th>Status / Outcome</th>
                            <th>PnL</th>
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
            if (!data || typeof data !== 'object' || !data.active_signals) return;
            const active = data.active_signals || [];
            const container = document.getElementById('signals-container');
            if (!container) return;
            container.innerHTML = "";

            let cReady = 0;
            let cLimit = 0;
            let cRunning = 0;
            let cStopped = 0;

            if (active.length === 0) {{
                container.innerHTML = `
                    <div class="col-12">
                        <div class="signal-card text-center py-5">
                            <i class="fa-solid fa-shield-halved fs-1 text-muted mb-3"></i>
                            <h5 class="text-white">No active pairs meeting strict 4H+1H SMC confluence right now</h5>
                            <p class="text-muted small">High-probability filter active: Requires 4H MTF alignment, 1H FVG/OB retest, and ATR structural buffer. See Watchlist below.</p>
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
                                    <i class="fa-solid fa-arrow-turn-down me-1"></i>Pullback: +${{sig.limit_setup.dist_pct}}
                                </span>
                            </div>

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

                            <div class="row g-2 text-center">
                                <div class="col-4">
                                    <div class="p-1 rounded" style="background: #161b22; border: 1px solid rgba(246, 70, 93, 0.3);">
                                        <div class="text-white-50 fw-bold" style="font-size: 0.68rem;">LIMIT SL (ATR)</div>
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
                    let initialCat = "READY";
                    let initialBorder = "#0ecb81";
                    let initialBadge = '<span class="badge bg-success text-white py-1 px-2"><i class="fa-solid fa-circle-check me-1"></i>🟢 GANNA PULUWAN</span>';
                    let initialDesc = '<span class="text-success fw-bold">Price in Entry Zone</span>';

                    if (sig.limit_setup) {{
                        initialCat = "LIMIT";
                        initialBorder = "#f0b90b";
                        initialBadge = '<span class="badge bg-warning text-dark py-1 px-2"><i class="fa-solid fa-clock me-1"></i>🟡 SET LIMIT ORDER</span>';
                        initialDesc = `<span class="text-warning fw-bold">Pending Limit @ $${{sig.limit_setup.limit_entry}}</span>`;
                    }} else if (sig.action_status === 'STOPPED') {{
                        initialCat = "STOPPED";
                        initialBorder = "#f6465d";
                        initialBadge = '<span class="badge bg-danger text-white py-1 px-2"><i class="fa-solid fa-triangle-exclamation me-1"></i>🔴 SL HIT / INVALID</span>';
                        initialDesc = `<span class="text-danger fw-bold">${{sig.action_label || 'Stop Loss Breached'}}</span>`;
                    }} else if (sig.action_status === 'RUNNING') {{
                        initialCat = "RUNNING";
                        initialBorder = "#0dcaf0";
                        initialBadge = '<span class="badge bg-info text-dark py-1 px-2"><i class="fa-solid fa-rocket me-1"></i>🚀 RUNNING IN PROFIT</span>';
                        initialDesc = `<span class="text-info fw-bold">${{sig.action_label || 'Running in Profit'}}</span>`;
                    }} else if (sig.action_status === 'WARNING') {{
                        initialCat = "WARNING";
                        initialBorder = "#f0b90b";
                        initialBadge = '<span class="badge bg-warning text-dark py-1 px-2"><i class="fa-solid fa-triangle-exclamation me-1"></i>🟡 NEAR SL / CAUTION</span>';
                        initialDesc = `<span class="text-warning fw-bold">${{sig.action_label || 'Price Near SL'}}</span>`;
                    }}
                    col.setAttribute('data-category', initialCat);
                    if (initialCat === 'LIMIT') cLimit++;
                    else if (initialCat === 'STOPPED') cStopped++;
                    else if (initialCat === 'RUNNING') cRunning++;
                    else cReady++;

                    col.innerHTML = `
                        <div class="signal-card ${{cardClass}}">
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

                            <!-- Institutional Confluence Tags -->
                            <div class="d-flex flex-wrap gap-1 mb-2">
                                <span class="badge bg-dark border border-secondary text-info confluence-chip">
                                    <i class="fa-solid fa-cubes me-1"></i>4H: ${{sig.mtf_status || 'Aligned'}}
                                </span>
                                <span class="badge bg-dark border border-secondary text-warning confluence-chip">
                                    <i class="fa-regular fa-clock me-1"></i>${{sig.session_name || 'Global'}}
                                </span>
                                ${{sig.fvg_str && sig.fvg_str !== 'None' ? `<span class="badge bg-dark border border-primary text-primary confluence-chip"><i class="fa-solid fa-droplet me-1"></i>FVG</span>` : ''}}
                                ${{sig.sweep_str && sig.sweep_str !== 'None' ? `<span class="badge bg-dark border border-success text-success confluence-chip"><i class="fa-solid fa-bolt me-1"></i>Sweep</span>` : ''}}
                            </div>

                            <!-- Live Price vs Entry Hero -->
                            <div class="price-hero d-flex justify-content-between align-items-center">
                                <div>
                                    <div class="metric-title"><span class="live-pulse"></span>Live Price</div>
                                    <div class="h4 mb-0 fw-bold text-white live-price-val" id="live-${{sig.symbol}}">$${{sig.current_price}}</div>
                                </div>
                                <div class="text-end border-start border-secondary ps-3">
                                    <div class="metric-title">Signal Entry</div>
                                    <div class="h5 mb-0 fw-bold val-yellow">$${{sig.entry}}</div>
                                    <small class="fw-bold live-diff-val" id="diff-${{sig.symbol}}">0.00%</small>
                                </div>
                            </div>

                            ${{limitBlock}}

                            <!-- Standard Pricing Row -->
                            <div class="row g-2 mb-2 text-center">
                                <div class="col-4">
                                    <div class="p-2 rounded bg-dark border border-secondary border-opacity-25">
                                        <div class="metric-title">Safe SL (ATR)</div>
                                        <div class="val-red fw-bold">$${{sig.stop_loss}}</div>
                                        <small class="text-danger">-${{sig.risk_pct}}</small>
                                    </div>
                                </div>
                                <div class="col-4">
                                    <div class="p-2 rounded bg-dark border border-secondary border-opacity-25">
                                        <div class="metric-title">Trailing SL</div>
                                        <div class="text-warning fw-bold" id="trail-${{sig.symbol}}">$${{sig.trailing_sl_str || sig.stop_loss}}</div>
                                        <small class="text-muted">Dynamic</small>
                                    </div>
                                </div>
                                <div class="col-4">
                                    <div class="p-2 rounded bg-dark border border-secondary border-opacity-25">
                                        <div class="metric-title">1:3 Full Target</div>
                                        <div class="val-green fw-bold">$${{sig.take_profit_1_3}}</div>
                                        <small class="text-success">+${{sig.reward_pct}}</small>
                                    </div>
                                </div>
                            </div>

                            <!-- Confirmations List -->
                            <div class="border-top border-secondary border-opacity-25 pt-2 mt-2">
                                <div class="fw-bold mb-1 d-flex align-items-center" style="font-size: 0.8rem;">
                                    <i class="fa-solid fa-circle-check text-success me-1"></i>
                                    <span style="color: #f0b90b !important; font-weight: 700; letter-spacing: 0.5px;">CONFIRMATIONS:</span>
                                </div>
                                ${{sig.reasons && sig.reasons.length > 0 ? sig.reasons.map(r => `<div class="mb-1" style="color: #f1f5f9 !important; font-size: 0.78rem !important; font-weight: 500 !important; line-height: 1.35; word-break: break-word;" title="${{r}}">• ${{r}}</div>`).join('') : '<div style="color: #94a3b8 !important; font-size: 0.75rem; font-style: italic;">• Technical Confluence Active</div>'}}
                            </div>

                            <!-- Action Button -->
                            <div class="mt-3">
                                <a href="https://www.binance.com/en/trade/${{sig.symbol}}" target="_blank" class="btn btn-sm btn-outline-warning w-100 fw-bold">
                                    <i class="fa-solid fa-arrow-up-right-from-square me-1"></i>Trade on DMD
                                </a>
                            </div>
                        </div>
                    `;
                    container.appendChild(col);
                }});
            }}

            const elAll = document.getElementById('cnt-all');
            const elReady = document.getElementById('cnt-ready');
            const elLimit = document.getElementById('cnt-limit');
            const elRunning = document.getElementById('cnt-running');
            const elStopped = document.getElementById('cnt-stopped');
            if (elAll) elAll.innerText = active.length;
            if (elReady) elReady.innerText = cReady;
            if (elLimit) elLimit.innerText = cLimit;
            if (elRunning) elRunning.innerText = cRunning;
            if (elStopped) elStopped.innerText = cStopped;

            // Render Watchlist
            const watchlistBody = document.getElementById('watchlist-body');
            if (watchlistBody) {{
                watchlistBody.innerHTML = "";
                const monitored = data.all_monitored || [];
                const watchOnly = monitored.filter(m => m.signal === "WATCHLIST");
                watchOnly.forEach(w => {{
                    const tr = document.createElement('tr');
                    tr.innerHTML = `
                        <td class="fw-bold text-white">${{w.symbol}}</td>
                        <td id="watch-${{w.symbol}}">$${{w.current_price}}</td>
                        <td><span class="badge bg-dark border border-secondary text-info">${{w.mtf_status || 'NEUTRAL'}}</span></td>
                        <td><small class="text-muted">${{w.fvg_str !== 'None' ? w.fvg_str : (w.sweep_str !== 'None' ? w.sweep_str : '-')}}</small></td>
                        <td><small class="text-muted">${{w.order_block_1h || '-'}}</small></td>
                        <td><small>${{w.daily_support}} / ${{w.daily_resistance}}</small></td>
                        <td><small>${{w.rsi_daily}} / ${{w.rsi_1h}}</small></td>
                        <td><span class="badge bg-secondary bg-opacity-25 text-white-50">${{w.tier_badge}}</span></td>
                    `;
                    watchlistBody.appendChild(tr);
                }});
            }}

            // Render History Metrics Cards
            if (data.history) {{
                const hist = data.history;
                const winRateEl = document.getElementById('stat-win-rate');
                if (winRateEl && hist.win_rate_pct !== undefined) {{
                    winRateEl.innerText = `${{hist.win_rate_pct.toFixed(1)}}%`;
                }}
                const winsEl = document.getElementById('stat-wins');
                if (winsEl && hist.wins !== undefined) {{
                    winsEl.innerText = hist.wins;
                }}
                const lossesEl = document.getElementById('stat-losses');
                if (lossesEl && hist.losses !== undefined) {{
                    lossesEl.innerText = hist.losses;
                }}
                const netREl = document.getElementById('stat-net-r');
                if (netREl && hist.net_pnl_r !== undefined) {{
                    const rSign = hist.net_pnl_r >= 0 ? '+' : '';
                    const usdtVal = hist.net_pnl_usdt !== undefined ? hist.net_pnl_usdt : 0.0;
                    const uSign = usdtVal > 0 ? '+' : (usdtVal < 0 ? '-' : '');
                    const uColor = usdtVal >= 0 ? 'val-green' : 'val-red';
                    netREl.className = `stat-value ${{uColor}}`;
                    netREl.innerHTML = `${{uSign}}$${{Math.abs(usdtVal).toFixed(2)}} USDT <span style="font-size: 0.95rem;" class="text-info font-monospace">(${{rSign}}${{hist.net_pnl_r.toFixed(1)}} R)</span>`;
                }}
            }}

            // Render Binance Account
            if (data.binance_account) {{
                const wb = document.getElementById('binance-wallet-bal');
                const ab = document.getElementById('binance-avail-margin');
                const rc = document.getElementById('binance-realized-cash');
                const fp = document.getElementById('binance-floating-pnl');
                
                const pnl = data.binance_account.unrealized_pnl || 0.0;
                const eq = data.binance_account.equity !== undefined ? data.binance_account.equity : data.binance_account.wallet_balance;
                const cash = data.binance_account.wallet_balance;

                if (wb && eq !== undefined) {{
                    wb.innerText = `$${{eq.toLocaleString('en-US', {{minimumFractionDigits: 2, maximumFractionDigits: 2}})}} USDT`;
                }}
                if (rc && cash !== undefined) {{
                    rc.innerText = `$${{cash.toLocaleString('en-US', {{minimumFractionDigits: 2, maximumFractionDigits: 2}})}} USDT`;
                }}
                if (fp && pnl !== undefined) {{
                    const sign = pnl >= 0 ? '+' : '';
                    fp.innerText = `(${{sign}}$${{pnl.toFixed(2)}} Floating)`;
                    fp.className = pnl >= 0 ? "text-success fw-bold ms-1" : "text-danger fw-bold ms-1";
                }}
                if (ab && data.binance_account.available_balance !== undefined) {{
                    ab.innerText = `$${{data.binance_account.available_balance.toLocaleString('en-US', {{minimumFractionDigits: 2, maximumFractionDigits: 2}})}} USDT`;
                }}
            }}

            // Render Active Live Positions
            const posContainer = document.getElementById('live-positions-container');
            const posCountBadge = document.getElementById('live-pos-count-badge');
            const realPositions = data.real_positions || [];
            
            if (posCountBadge) {{
                posCountBadge.innerText = realPositions.length > 0 ? `${{realPositions.length}} Active` : '0 Active (Protected)';
                posCountBadge.className = realPositions.length > 0 ? 'badge bg-success ms-2' : 'badge bg-secondary ms-2';
            }}

            if (posContainer) {{
                posContainer.innerHTML = "";
                if (realPositions.length === 0) {{
                    posContainer.innerHTML = `
                        <div class="p-3 rounded text-center" style="background: rgba(14, 203, 129, 0.05); border: 1px dashed rgba(14, 203, 129, 0.3);">
                            <div class="d-flex align-items-center justify-content-center gap-2 text-success fw-bold mb-1">
                                <i class="fa-solid fa-shield-halved"></i>
                                <span>Capital 100% Protected in Available Margin</span>
                            </div>
                            <small class="text-white-50">10x Isolated engine is scanning 150 pairs every 60s for institutional A+ setups. When triggered, active positions will appear here with live PnL & DMD Order ID.</small>
                        </div>
                    `;
                }} else {{
                    const row = document.createElement('div');
                    row.className = "row g-2";
                    realPositions.forEach(p => {{
                        const isLong = p.side.includes("LONG") || p.side.includes("BUY");
                        const pnlColor = p.unrealized_pnl >= 0 ? "val-green" : "val-red";
                        const pnlSign = p.unrealized_pnl >= 0 ? "+" : "";
                        const card = document.createElement('div');
                        card.className = "col-12 col-sm-6 col-lg-3";
                        card.innerHTML = `
                            <div class="position-card" style="border-top: 4px solid ${{isLong ? '#0ecb81' : '#f6465d'}};">
                                <div class="d-flex justify-content-between align-items-center mb-1 pb-1 border-bottom border-secondary border-opacity-25">
                                    <span class="fw-bold text-white fs-6">${{p.symbol}}</span>
                                    <span class="badge ${{isLong ? 'bg-success' : 'bg-danger'}}" style="font-size: 0.70rem; padding: 2px 6px;">${{p.side}} ${{p.leverage}}x</span>
                                </div>
                                <div class="d-flex justify-content-between py-1 border-bottom border-secondary border-opacity-10" style="font-size: 0.74rem;">
                                    <span class="pos-label text-muted">Entry / Mark:</span>
                                    <span class="text-white fw-semibold">$${{p.entry_price.toLocaleString()}} / $${{p.mark_price.toLocaleString()}}</span>
                                </div>
                                <div class="d-flex justify-content-between py-1 border-bottom border-secondary border-opacity-10" style="font-size: 0.74rem;">
                                    <span class="pos-label text-muted">Isolated Margin:</span>
                                    <span class="text-warning fw-semibold">$${{p.isolated_margin.toFixed(2)}} USDT (${{p.position_amt}})</span>
                                </div>
                                <div class="d-flex justify-content-between py-1 border-bottom border-secondary border-opacity-10" style="font-size: 0.74rem;">
                                    <span class="pos-label text-muted">Unrealized PnL:</span>
                                    <span class="fw-bold ${{pnlColor}}">${{pnlSign}}$${{p.unrealized_pnl.toFixed(4)}} USDT (${{pnlSign}}${{p.pnl_pct.toFixed(2)}}%)</span>
                                </div>
                                <div class="mt-2 text-center">
                                    <span class="badge bg-dark bg-opacity-75 border border-success text-success d-inline-flex align-items-center gap-1" style="font-size: 0.68rem; padding: 2px 8px; font-weight: 500;">
                                        <i class="fa-solid fa-shield-halved" style="font-size: 0.65rem;"></i>
                                        <span>Hardware Stop Loss Active on DMD</span>
                                    </span>
                                </div>
                            </div>
                        `;
                        row.appendChild(card);
                    }});
                    posContainer.appendChild(row);
                }}
            }}

            // Render History
            const histBody = document.getElementById('history-body');
            if (histBody) {{
                histBody.innerHTML = "";
                const allSignals = (data.history && data.history.signals) ? data.history.signals : [];
                const liveSignals = allSignals.filter(s => s.executed_live);
                const signals = liveSignals.length > 0 ? liveSignals : allSignals;
                if (signals.length === 0) {{
                    histBody.innerHTML = `
                        <tr>
                            <td colspan="11" class="text-center py-4 text-muted">
                                <i class="fa-solid fa-shield-halved text-success me-2 fs-5"></i>
                                <span><strong>Real Trading Mode Active:</strong> Dummy history cleared. All real trades placed on Binance Futures by the auto-trader will be logged here with real execution prices, margin, and verified PnL.</span>
                            </td>
                        </tr>
                    `;
                }} else {{
                    signals.slice().reverse().forEach(s => {{
                        const tr = document.createElement('tr');
                        const isWin = s.status.includes("WIN");
                        const isLoss = s.status.includes("LOSS");
                        const isPending = s.status.includes("PENDING");
                        const badgeClass = isWin ? "bg-success" : (isLoss ? "bg-danger" : (isPending ? "bg-warning text-dark" : "bg-info text-dark"));
                        const pnlColor = isWin ? "text-success" : (isLoss ? "text-danger" : "text-muted");
                        const pnlSign = (s.outcome_pnl > 0) ? "+" : "";
                        const liveBadge = s.executed_live ? `<span class="badge bg-success bg-opacity-25 text-success border border-success me-1" style="font-size:0.65rem;">🟢 REAL LIVE</span>` : "";
                        const marginInfo = s.executed_margin ? `<small class="text-warning">$${{s.executed_margin.toFixed(2)}}</small>` : `<small class="text-muted">10%</small>`;

                        tr.innerHTML = `
                            <td><small class="text-muted">${{s.date || '-'}}</small></td>
                            <td class="fw-bold text-white">${{s.symbol}} ${{liveBadge}}</td>
                            <td><span class="badge ${{s.type.includes('BUY') ? 'bg-success bg-opacity-25 text-success' : 'bg-danger bg-opacity-25 text-danger'}}">${{s.type}}</span></td>
                            <td><small>${{s.setup || '-'}}</small></td>
                            <td>$${{s.entry_str || s.entry}}</td>
                            <td>${{marginInfo}}</td>
                            <td class="text-warning">$${{s.trailing_sl_str || s.sl_str || s.sl}}</td>
                            <td class="text-success">$${{s.tp1_str || s.tp1 || '-'}}</td>
                            <td class="text-success">$${{s.tp_str || s.tp}}</td>
                            <td><span class="badge ${{badgeClass}}">${{s.status}}</span></td>
                            <td class="fw-bold ${{pnlColor}}">${{pnlSign}}$${{s.outcome_pnl ? s.outcome_pnl.toFixed(2) : '0.00'}} R</td>
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
                if (cat === 'ALL' || cCat === cat || (cat === 'READY' && cCat === 'WARNING')) {{
                    col.style.display = 'block';
                }} else {{
                    col.style.display = 'none';
                }}
            }});
        }}

        function scrollToHistory() {{
            const el = document.getElementById('history-section');
            if (el) {{
                el.scrollIntoView({{ behavior: 'smooth' }});
            }}
        }}

        // Live Price Polling from Binance API every 4 seconds (Non-blocking with 2.5s Timeout)
        async function fetchLivePrices() {{
            try {{
                let resp;
                const controller = new AbortController();
                const timeoutId = setTimeout(() => controller.abort(), 2500);
                try {{
                    resp = await fetch('https://fapi.binance.com/fapi/v1/ticker/price', {{ signal: controller.signal }});
                }} catch (e) {{}}
                if (!resp || !resp.ok) {{
                    try {{
                        resp = await fetch('https://fapi1.binance.com/fapi/v1/ticker/price', {{ signal: controller.signal }});
                    }} catch (e) {{}}
                }}
                clearTimeout(timeoutId);
                if (!resp || !resp.ok) return;
                const tickerList = await resp.json();
                const priceMap = {{}};
                tickerList.forEach(t => priceMap[t.symbol] = parseFloat(t.price));

                let countReady = 0;
                let countLimit = 0;
                let countRunning = 0;
                let countStopped = 0;

                const active = EMBEDDED_DATA.active_signals || [];
                active.forEach(sig => {{
                    const liveP = priceMap[sig.symbol];
                    const entry = sig.raw_entry;
                    if (liveP !== undefined && entry > 0 && Math.abs((liveP - entry) / entry) <= 0.40) {{
                        const liveEl = document.getElementById(`live-${{sig.symbol}}`);
                        const diffEl = document.getElementById(`diff-${{sig.symbol}}`);
                        const watchEl = document.getElementById(`watch-${{sig.symbol}}`);

                        let pStr = liveP >= 1000 ? liveP.toLocaleString('en-US', {{minimumFractionDigits: 2, maximumFractionDigits: 2}}) :
                                   (liveP >= 1 ? liveP.toFixed(4) : (liveP >= 0.0001 ? liveP.toFixed(6) : liveP.toFixed(8)));

                        if (liveEl) liveEl.innerText = `$${{pStr}}`;
                        if (watchEl) watchEl.innerText = `$${{pStr}}`;

                        const entry = sig.raw_entry;
                        const sl = sig.trailing_sl || sig.raw_sl;
                        const tp = sig.raw_tp;
                        const isLong = sig.signal.includes("BUY") || sig.signal.includes("LONG");
                        const rawDiffPct = ((liveP - entry) / entry) * 100;
                        const pnlPct = isLong ? rawDiffPct : -rawDiffPct;

                        const isSlHit = (sl > 0) && (isLong ? (liveP <= sl) : (liveP >= sl));
                        const isTpHit = (tp > 0) && (isLong ? (liveP >= tp) : (liveP <= tp));
                        
                        let diffText = "";
                        let diffColor = "text-muted";

                        if (isSlHit) {{
                            diffText = `🔴 SL Breached (${{rawDiffPct >= 0 ? '+' : ''}}${{rawDiffPct.toFixed(2)}}%)`;
                            diffColor = "val-red";
                        }} else if (isTpHit) {{
                            diffText = `🎯 Target Hit (+${{pnlPct.toFixed(2)}}%)`;
                            diffColor = "val-green";
                        }} else if (pnlPct > 0.75) {{
                            diffText = `🚀 Floating Profit (+${{pnlPct.toFixed(2)}}%)`;
                            diffColor = "val-green";
                        }} else if (Math.abs(rawDiffPct) <= 0.75) {{
                            diffText = `🎯 In Entry Zone (${{rawDiffPct >= 0 ? '+' : ''}}${{rawDiffPct.toFixed(2)}}%)`;
                            diffColor = isLong ? "val-green" : "val-red";
                        }} else if (pnlPct < -0.75) {{
                            diffText = isLong ? `📉 Drawdown (${{rawDiffPct.toFixed(2)}}%)` : `📈 Adverse Pump (+${{rawDiffPct.toFixed(2)}}%)`;
                            diffColor = "val-yellow";
                        }} else {{
                            diffText = `${{rawDiffPct >= 0 ? '+' : ''}}${{rawDiffPct.toFixed(2)}}%`;
                            diffColor = "val-yellow";
                        }}

                        if (diffEl) {{
                            diffEl.innerText = diffText;
                            diffEl.className = `fw-bold live-diff-val ${{diffColor}}`;
                        }}

                        let cat = "READY";
                        let barBorder = "#0ecb81";
                        let badgeHtml = "";
                        let descHtml = "";

                        if (isSlHit) {{
                            cat = "STOPPED";
                            countStopped++;
                            barBorder = "#f6465d";
                            badgeHtml = '<span class="badge bg-danger text-white py-1 px-2"><i class="fa-solid fa-triangle-exclamation me-1"></i>🔴 SL HIT / INVALID</span>';
                            descHtml = '<span class="text-danger fw-bold">Stop Loss Breached</span>';
                        }} else if (sig.limit_setup) {{
                            cat = "LIMIT";
                            countLimit++;
                            barBorder = "#f0b90b";
                            badgeHtml = '<span class="badge bg-warning text-dark py-1 px-2"><i class="fa-solid fa-clock me-1"></i>🟡 SET LIMIT ORDER</span>';
                            descHtml = `<span class="text-warning fw-bold">Pending Limit @ $${{sig.limit_setup.limit_entry}}</span>`;
                        }} else if (isTpHit) {{
                            cat = "RUNNING";
                            countRunning++;
                            barBorder = "#0ecb81";
                            badgeHtml = '<span class="badge bg-success text-white py-1 px-2"><i class="fa-solid fa-trophy me-1"></i>🎯 TARGET HIT</span>';
                            descHtml = `<span class="text-success fw-bold">+${{pnlPct.toFixed(2)}}% Target Hit</span>`;
                        }} else if (pnlPct > 0.75) {{
                            cat = "RUNNING";
                            countRunning++;
                            barBorder = "#0dcaf0";
                            badgeHtml = '<span class="badge bg-info text-dark py-1 px-2"><i class="fa-solid fa-rocket me-1"></i>🚀 RUNNING IN PROFIT</span>';
                            descHtml = `<span class="text-info fw-bold">+${{pnlPct.toFixed(2)}}% Away (Do Not Chase)</span>`;
                        }} else if (Math.abs(rawDiffPct) <= 0.75) {{
                            cat = "READY";
                            countReady++;
                            barBorder = "#0ecb81";
                            badgeHtml = '<span class="badge bg-success text-white py-1 px-2"><i class="fa-solid fa-circle-check me-1"></i>🟢 GANNA PULUWAN</span>';
                            descHtml = `<span class="text-success fw-bold">In Entry Zone (${{rawDiffPct >= 0 ? '+' : ''}}${{rawDiffPct.toFixed(2)}}%)</span>`;
                        }} else {{
                            cat = "WARNING";
                            countReady++;
                            barBorder = "#f0b90b";
                            badgeHtml = '<span class="badge bg-warning text-dark py-1 px-2"><i class="fa-solid fa-triangle-exclamation me-1"></i>🟡 NEAR SL / CAUTION</span>';
                            descHtml = `<span class="text-warning fw-bold">Out of Entry Zone (${{rawDiffPct >= 0 ? '+' : ''}}${{rawDiffPct.toFixed(2)}}%)</span>`;
                        }}

                        const barEl = document.getElementById(`action-bar-${{sig.symbol}}`);
                        if (barEl) {{
                            barEl.style.borderLeftColor = barBorder;
                            barEl.innerHTML = `<div id="action-badge-${{sig.symbol}}">${{badgeHtml}}</div><div id="action-desc-${{sig.symbol}}"><small>${{descHtml}}</small></div>`;
                        }}

                        const colEl = document.getElementById(`card-col-${{sig.symbol}}`);
                        if (colEl) {{
                            colEl.setAttribute('data-category', cat);
                            if (currentFilter === 'ALL' || currentFilter === cat || (currentFilter === 'READY' && cat === 'WARNING')) {{
                                colEl.style.display = 'block';
                            }} else {{
                                colEl.style.display = 'none';
                            }}
                        }}
                    }}
                }});

                const elAll = document.getElementById('cnt-all');
                const elReady = document.getElementById('cnt-ready');
                const elLimit = document.getElementById('cnt-limit');
                const elRunning = document.getElementById('cnt-running');
                const elStopped = document.getElementById('cnt-stopped');
                if (elAll) elAll.innerText = active.length;
                if (elReady) elReady.innerText = countReady;
                if (elLimit) elLimit.innerText = countLimit;
                if (elRunning) elRunning.innerText = countRunning;
                if (elStopped) elStopped.innerText = countStopped;

            }} catch (e) {{
                console.log("Binance direct ticker fetch skipped:", e);
            }}
        }}

        document.addEventListener('DOMContentLoaded', () => {{
            if (window.location.hash) {{
                history.replaceState(null, null, window.location.pathname + window.location.search);
            }}
            window.scrollTo(0, 0);
            renderUI(EMBEDDED_DATA);
            fetchLivePrices();
            setInterval(fetchLivePrices, 4000);
            async function reloadDashboardData() {{
                try {{
                    let res;
                    const ctrl = new AbortController();
                    const tid = setTimeout(() => ctrl.abort(), 3500);
                    try {{
                        res = await fetch('latest_signals.json?t=' + Date.now(), {{ signal: ctrl.signal }});
                    }} catch(e) {{
                        res = await fetch('/latest_signals.json?t=' + Date.now(), {{ signal: ctrl.signal }});
                    }}
                    clearTimeout(tid);
                    if (res && res.ok) {{
                        const freshData = await res.json();
                        if (freshData && (freshData.active_signals || freshData.real_positions)) {{
                            renderUI(freshData);
                        }}
                    }}
                }} catch(e) {{}}
            }}
            setInterval(reloadDashboardData, 10000);
        }});
    </script>
</body>
</html>
"""
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html_content)

if __name__ == "__main__":
    scan_all_pairs()
