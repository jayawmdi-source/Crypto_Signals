"""
=============================================================================
Binance USDT-M Futures Automated Execution Engine
Institutional SMC 2.0 Auto-Trader
=============================================================================
Enforces:
  1. ISOLATED Margin (Strict Capital Protection - Zero Cross Margin)
  2. 10x Max Leverage
  3. Dynamic 10% Position Sizing of Available USDT Balance
  4. Instant Hardware STOP_MARKET Placement on Binance Matching Engine
  5. Multi-Stage Take-Profit (TP1 50% + BE, TP2 25% + Lock +1.5R, TP3 Full)
=============================================================================
"""

import os
import time
import json
import hmac
import hashlib
import math
import urllib.parse
from decimal import Decimal, ROUND_DOWN
import requests

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(SCRIPT_DIR, "binance_api_config.json")
BASE_URL = "https://fapi.binance.com"

class BinanceFuturesTrader:
    def __init__(self, config_path=CONFIG_FILE):
        self.config_path = config_path
        self.api_key = os.environ.get("BINANCE_API_KEY", "")
        self.api_secret = os.environ.get("BINANCE_API_SECRET", "")
        self.leverage = int(os.environ.get("BINANCE_LEVERAGE", 10))
        self.position_size_pct = float(os.environ.get("BINANCE_POSITION_SIZE_PCT", 10.0))
        self.enable_trading = os.environ.get("BINANCE_ENABLE_TRADING", "false").lower() == "true"
        self.margin_type = "ISOLATED"
        self.is_hedge_mode = True  # Default to Hedge Mode (Dual-Side)
        
        self.load_config()
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "BinanceSMC-AutoTrader/2.0",
            "Content-Type": "application/x-www-form-urlencoded"
        })
        if self.api_key:
            self.session.headers.update({"X-MBX-APIKEY": self.api_key})
            
        self._exchange_info_cache = {}
        self._time_offset = 0
        if self.is_configured():
            self.sync_time()
            self.check_position_mode()

    def load_config(self):
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                    self.api_key = self.api_key or cfg.get("api_key", "")
                    self.api_secret = self.api_secret or cfg.get("api_secret", "")
                    self.leverage = int(cfg.get("leverage", self.leverage))
                    self.position_size_pct = float(cfg.get("position_size_pct", self.position_size_pct))
                    self.enable_trading = bool(cfg.get("enable_live_trading", self.enable_trading))
            except Exception as e:
                print(f"[!] Error reading {self.config_path}: {e}")

    def is_configured(self):
        return bool(self.api_key and self.api_secret)

    def is_live_enabled(self):
        return self.is_configured() and self.enable_trading

    def sync_time(self):
        """Synchronize local time with Binance server time to avoid recvWindow errors"""
        try:
            resp = self.session.get(f"{BASE_URL}/fapi/v1/time", timeout=5)
            if resp.status_code == 200:
                server_time = int(resp.json().get("serverTime", 0))
                local_time = int(time.time() * 1000)
                self._time_offset = server_time - local_time
        except Exception:
            self._time_offset = 0

    def check_position_mode(self):
        """Detect whether account is in Hedge Mode (dualSidePosition=True) or One-Way Mode"""
        try:
            params = self._sign_request()
            resp = self.session.get(f"{BASE_URL}/fapi/v1/positionSide/dual", params=params, timeout=6)
            if resp.status_code == 200:
                self.is_hedge_mode = bool(resp.json().get("dualSidePosition", True))
                mode_str = "Hedge Mode (Dual-Side)" if self.is_hedge_mode else "One-Way Mode"
                print(f"[+] Binance Account Position Mode: {mode_str}")
        except Exception as e:
            print(f"[!] Note: Error checking position side mode (defaulting to Hedge Mode): {e}")

    def _sign_request(self, params=None):
        if params is None:
            params = {}
        now_ms = int(time.time() * 1000) + self._time_offset
        params["timestamp"] = now_ms
        params["recvWindow"] = 60000
        
        query_string = urllib.parse.urlencode(params)
        signature = hmac.new(
            self.api_secret.encode("utf-8"),
            query_string.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()
        params["signature"] = signature
        return params

    def get_account_balances(self):
        """Fetch available margin in USDT"""
        if not self.is_configured():
            return None
        try:
            params = self._sign_request()
            resp = self.session.get(f"{BASE_URL}/fapi/v2/balance", params=params, timeout=8)
            if resp.status_code == 200:
                for b in resp.json():
                    if b.get("asset") == "USDT":
                        return {
                            "wallet_balance": float(b.get("balance", 0.0)),
                            "available_balance": float(b.get("availableBalance", 0.0)),
                            "unrealized_pnl": float(b.get("crossUnPnl", 0.0))
                        }
            else:
                print(f"[!] Binance API Error balance ({resp.status_code}): {resp.text}")
        except Exception as e:
            print(f"[!] Exception fetching Binance balance: {e}")
        return None

    def get_symbol_rules(self, symbol):
        """Fetch step size, tick size, minNotional for a symbol"""
        if symbol in self._exchange_info_cache:
            return self._exchange_info_cache[symbol]

        try:
            resp = self.session.get(f"{BASE_URL}/fapi/v1/exchangeInfo", timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                for s in data.get("symbols", []):
                    sym = s.get("symbol")
                    step_size = "0.001"
                    tick_size = "0.0001"
                    min_qty = "0.001"
                    min_notional = 5.0

                    for f in s.get("filters", []):
                        if f.get("filterType") == "LOT_SIZE":
                            step_size = f.get("stepSize", "0.001")
                            min_qty = f.get("minQty", "0.001")
                        elif f.get("filterType") == "PRICE_FILTER":
                            tick_size = f.get("tickSize", "0.0001")
                        elif f.get("filterType") == "MIN_NOTIONAL":
                            min_notional = float(f.get("notional", 5.0))

                    self._exchange_info_cache[sym] = {
                        "step_size": float(step_size),
                        "step_decimals": self._get_decimals(step_size),
                        "tick_size": float(tick_size),
                        "tick_decimals": self._get_decimals(tick_size),
                        "min_qty": float(min_qty),
                        "min_notional": min_notional
                    }
                return self._exchange_info_cache.get(symbol)
        except Exception as e:
            print(f"[!] Error fetching exchangeInfo: {e}")
        return None

    def _get_decimals(self, str_val):
        str_val = str(str_val).rstrip('0')
        if '.' in str_val:
            return len(str_val.split('.')[1])
        return 0

    def round_qty(self, qty, step_size, decimals):
        """Floor quantity strictly to symbol stepSize"""
        if step_size <= 0:
            return float(round(qty, decimals))
        d_qty = Decimal(str(qty))
        d_step = Decimal(str(step_size))
        stepped = (d_qty // d_step) * d_step
        return float(stepped.quantize(Decimal(10) ** -decimals, rounding=ROUND_DOWN))

    def round_price(self, price, tick_size, decimals):
        """Round price to tickSize"""
        if tick_size <= 0:
            return float(round(price, decimals))
        d_price = Decimal(str(price))
        d_tick = Decimal(str(tick_size))
        stepped = round(d_price / d_tick) * d_tick
        return float(stepped.quantize(Decimal(10) ** -decimals))

    def set_margin_type_isolated(self, symbol):
        """Enforces ISOLATED margin for the symbol"""
        params = self._sign_request({
            "symbol": symbol,
            "marginType": "ISOLATED"
        })
        try:
            resp = self.session.post(f"{BASE_URL}/fapi/v1/marginType", data=params, timeout=8)
            if resp.status_code == 200:
                print(f"[+] {symbol}: Margin set to ISOLATED")
                return True
            else:
                err = resp.json()
                # -4046 = No need to change margin type (already isolated)
                if err.get("code") == -4046:
                    return True
                print(f"[!] {symbol} set margin type error ({resp.status_code}): {resp.text}")
        except Exception as e:
            print(f"[!] Exception setting margin type for {symbol}: {e}")
        return False

    def set_leverage(self, symbol, leverage=10):
        """Sets leverage (default: 10x)"""
        params = self._sign_request({
            "symbol": symbol,
            "leverage": leverage
        })
        try:
            resp = self.session.post(f"{BASE_URL}/fapi/v1/leverage", data=params, timeout=8)
            if resp.status_code == 200:
                print(f"[+] {symbol}: Leverage set to {leverage}x")
                return True
            else:
                print(f"[!] {symbol} set leverage error ({resp.status_code}): {resp.text}")
        except Exception as e:
            print(f"[!] Exception setting leverage for {symbol}: {e}")
        return False

    def get_all_open_positions(self):
        """Check all currently open positions on Binance Futures"""
        if not self.is_configured():
            return []
        try:
            params = self._sign_request()
            resp = self.session.get(f"{BASE_URL}/fapi/v2/positionRisk", params=params, timeout=8)
            if resp.status_code == 200:
                open_symbols = []
                for p in resp.json():
                    amt = float(p.get("positionAmt", 0.0))
                    if abs(amt) > 0:
                        open_symbols.append(p.get("symbol"))
                return open_symbols
        except Exception as e:
            print(f"[!] Error fetching positionRisk for all symbols: {e}")
        return []

    def get_open_positions_detail(self):
        """Fetch full details for all actively open positions on Binance Futures"""
        if not self.is_configured():
            return []
        try:
            params = self._sign_request()
            resp = self.session.get(f"{BASE_URL}/fapi/v2/positionRisk", params=params, timeout=8)
            if resp.status_code == 200:
                open_positions = []
                for p in resp.json():
                    amt = float(p.get("positionAmt", 0.0))
                    if abs(amt) > 0:
                        entry_p = float(p.get("entryPrice", 0.0))
                        mark_p = float(p.get("markPrice", 0.0))
                        unrealized_pnl = float(p.get("unRealizedProfit", 0.0))
                        lev = int(p.get("leverage", self.leverage))
                        margin = float(p.get("isolatedMargin", 0.0)) or (abs(amt) * entry_p / lev if lev > 0 else 0.0)
                        side = "BUY / LONG" if amt > 0 else "SELL / SHORT"
                        pnl_pct = ((mark_p - entry_p) / entry_p * 100 * lev) if (side == "BUY / LONG" and entry_p > 0) else (((entry_p - mark_p) / entry_p * 100 * lev) if entry_p > 0 else 0.0)
                        open_positions.append({
                            "symbol": p.get("symbol"),
                            "side": side,
                            "position_amt": abs(amt),
                            "entry_price": entry_p,
                            "mark_price": mark_p,
                            "unrealized_pnl": round(unrealized_pnl, 4),
                            "pnl_pct": round(pnl_pct, 2),
                            "leverage": lev,
                            "margin_type": p.get("marginType", "isolated").upper(),
                            "isolated_margin": round(margin, 2),
                            "liquidation_price": float(p.get("liquidationPrice", 0.0))
                        })
                return open_positions
        except Exception as e:
            print(f"[!] Error fetching position details: {e}")
        return []

    def get_recent_income(self, limit=10):
        """Fetch recent realized PnL and trade income from Binance Futures"""
        if not self.is_configured():
            return []
        try:
            params = self._sign_request({"incomeType": "REALIZED_PNL", "limit": limit})
            resp = self.session.get(f"{BASE_URL}/fapi/v1/income", params=params, timeout=8)
            if resp.status_code == 200:
                return resp.json()
        except Exception as e:
            print(f"[!] Error fetching Binance income: {e}")
        return []

    def get_open_position(self, symbol):
        """Check if an open position already exists on Binance"""
        try:
            params = self._sign_request({"symbol": symbol})
            resp = self.session.get(f"{BASE_URL}/fapi/v2/positionRisk", params=params, timeout=8)
            if resp.status_code == 200:
                for p in resp.json():
                    amt = float(p.get("positionAmt", 0.0))
                    if abs(amt) > 0:
                        return p
        except Exception as e:
            print(f"[!] Error fetching positionRisk: {e}")
    def get_open_algo_orders(self, symbol=None):
        """Fetch open conditional/algo orders from Binance (/fapi/v1/openAlgoOrders)"""
        if not self.is_configured():
            return []
        try:
            params = {}
            if symbol:
                params["symbol"] = symbol
            signed = self._sign_request(params)
            resp = self.session.get(f"{BASE_URL}/fapi/v1/openAlgoOrders", params=signed, timeout=6)
            if resp.status_code == 200:
                res = resp.json()
                if isinstance(res, list):
                    return res
                if isinstance(res, dict) and "orders" in res:
                    return res.get("orders", [])
                return []
        except Exception as e:
            print(f"[!] Error fetching openAlgoOrders: {e}")
        return []

    def cancel_all_symbol_orders(self, symbol):
        """Cancels all open normal and algo orders for a symbol"""
        if not self.is_configured():
            return
        # 1. Cancel open algo orders
        try:
            p1 = self._sign_request({"symbol": symbol})
            self.session.delete(f"{BASE_URL}/fapi/v1/algoOpenOrders", data=p1, timeout=6)
        except Exception:
            pass
        # 2. Cancel standard open orders
        try:
            p2 = self._sign_request({"symbol": symbol})
            self.session.delete(f"{BASE_URL}/fapi/v1/allOpenOrders", data=p2, timeout=6)
        except Exception:
            pass

    def _place_conditional_order(self, symbol, side, order_type, trigger_price, pos_side="BOTH", qty=None):
        """
        Places a conditional order (STOP_MARKET or TAKE_PROFIT_MARKET) using Binance's
        official Algo Order Service (/fapi/v1/algoOrder) with automatic fallback.
        """
        algo_params = {
            "algoType": "CONDITIONAL",
            "symbol": symbol,
            "side": side,
            "type": order_type,
            "triggerPrice": str(trigger_price),
            "workingType": "MARK_PRICE"
        }
        if self.is_hedge_mode and pos_side and pos_side != "BOTH":
            algo_params["positionSide"] = pos_side

        # Attempt 1: /fapi/v1/algoOrder with closePosition="true"
        try:
            params_close = dict(algo_params, closePosition="true")
            signed_close = self._sign_request(params_close)
            resp = self.session.post(f"{BASE_URL}/fapi/v1/algoOrder", data=signed_close, timeout=8)
            if resp.status_code == 200:
                res = resp.json()
                oid = res.get("algoId") or res.get("orderId")
                return {"success": True, "order_id": oid, "endpoint": "algoOrder_closePosition"}
            else:
                print(f"[!] algoOrder ({order_type} closePosition=true) rejected ({resp.status_code}): {resp.text}")
        except Exception as e:
            print(f"[!] Exception calling /fapi/v1/algoOrder closePosition: {e}")

        # Attempt 2: /fapi/v1/algoOrder with explicit quantity & reduceOnly="true"
        if qty:
            try:
                params_qty = dict(algo_params, quantity=str(qty))
                if not self.is_hedge_mode:
                    params_qty["reduceOnly"] = "true"
                signed_qty = self._sign_request(params_qty)
                resp = self.session.post(f"{BASE_URL}/fapi/v1/algoOrder", data=signed_qty, timeout=8)
                if resp.status_code == 200:
                    res = resp.json()
                    oid = res.get("algoId") or res.get("orderId")
                    return {"success": True, "order_id": oid, "endpoint": "algoOrder_qty"}
                else:
                    print(f"[!] algoOrder ({order_type} qty={qty}) rejected ({resp.status_code}): {resp.text}")
            except Exception as e:
                print(f"[!] Exception calling algoOrder with qty: {e}")

        # Attempt 3: Legacy /fapi/v1/order with stopPrice
        try:
            legacy_params = {
                "symbol": symbol,
                "side": side,
                "type": order_type,
                "stopPrice": str(trigger_price),
                "closePosition": "true",
                "workingType": "MARK_PRICE"
            }
            if self.is_hedge_mode and pos_side and pos_side != "BOTH":
                legacy_params["positionSide"] = pos_side
            signed_legacy = self._sign_request(legacy_params)
            resp = self.session.post(f"{BASE_URL}/fapi/v1/order", data=signed_legacy, timeout=8)
            if resp.status_code == 200:
                res = resp.json()
                oid = res.get("orderId")
                return {"success": True, "order_id": oid, "endpoint": "legacy_order"}
            else:
                print(f"[!] Legacy /fapi/v1/order ({order_type}) rejected ({resp.status_code}): {resp.text}")
        except Exception as e:
            print(f"[!] Exception calling legacy /fapi/v1/order: {e}")

        return {"success": False, "order_id": None}

    def ensure_position_protection(self, symbol, is_long, sl_price, tp_price=None, qty=None):
        """
        Verifies that an open position on Binance has an active STOP_MARKET order.
        If missing, places the STOP_MARKET and optional TAKE_PROFIT_MARKET orders.
        """
        if not self.is_live_enabled():
            return

        open_algos = self.get_open_algo_orders(symbol)
        has_sl = any(o.get("orderType") == "STOP_MARKET" or o.get("type") == "STOP_MARKET" for o in open_algos)
        
        exit_side = "SELL" if is_long else "BUY"
        pos_side = ("LONG" if is_long else "SHORT") if self.is_hedge_mode else "BOTH"
        rules = self.get_symbol_rules(symbol)
        if not rules:
            return

        if not has_sl and sl_price and sl_price > 0:
            sl_formatted = self.round_price(sl_price, rules["tick_size"], rules["tick_decimals"])
            print(f"[🛡️] Auto-Shield: Missing STOP_MARKET detected for {symbol}! Placing @ ${sl_formatted}...")
            sl_res = self._place_conditional_order(symbol, exit_side, "STOP_MARKET", sl_formatted, pos_side, qty)
            if sl_res.get("success"):
                print(f"[✅] Auto-Shield: STOP_MARKET placed for {symbol} (Order ID: {sl_res.get('order_id')})")
            else:
                print(f"[!] Auto-Shield: Could not place STOP_MARKET for {symbol}")

        has_tp = any(o.get("orderType") == "TAKE_PROFIT_MARKET" or o.get("type") == "TAKE_PROFIT_MARKET" for o in open_algos)
        if not has_tp and tp_price and tp_price > 0:
            tp_formatted = self.round_price(tp_price, rules["tick_size"], rules["tick_decimals"])
            print(f"[🎯] Auto-Shield: Missing TAKE_PROFIT_MARKET detected for {symbol}! Placing @ ${tp_formatted}...")
            tp_res = self._place_conditional_order(symbol, exit_side, "TAKE_PROFIT_MARKET", tp_formatted, pos_side, qty)
            if tp_res.get("success"):
                print(f"[✅] Auto-Shield: TAKE_PROFIT_MARKET placed for {symbol} (Order ID: {tp_res.get('order_id')})")

    def execute_signal(self, sig):
        """
        Executes a newly triggered READY signal on Binance Futures.
        1. Verifies no existing position.
        2. Sets ISOLATED margin mode & 10x leverage.
        3. Calculates 10% of Available USDT balance.
        4. Places MARKET entry order.
        5. Instantly places STOP_MARKET protection order on Binance.
        """
        if not self.is_live_enabled():
            print(f"[*] Auto-Trader: Signal {sig.get('symbol')} detected but LIVE TRADING is disabled (Dry-Run / Signal-Only).")
            return None

        symbol = sig.get("symbol")
        sig_type = str(sig.get("type") or sig.get("signal") or "").upper()
        is_long = "BUY" in sig_type or "LONG" in sig_type
        side = "BUY" if is_long else "SELL"
        exit_side = "SELL" if is_long else "BUY"
        pos_side = ("LONG" if is_long else "SHORT") if self.is_hedge_mode else "BOTH"

        entry_price = float(sig.get("raw_entry") or sig.get("entry", 0))
        sl_price = float(sig.get("raw_sl") or sig.get("sl", 0))
        tp_price = float(sig.get("raw_tp") or sig.get("tp", 0))

        # Always fetch live Futures market price to avoid any Spot vs Futures redenomination discrepancies
        try:
            ticker_resp = self.session.get(f"{BASE_URL}/fapi/v1/ticker/price", params={"symbol": symbol}, timeout=5)
            if ticker_resp.status_code == 200:
                live_price = float(ticker_resp.json().get("price", 0))
                if live_price > 0:
                    if entry_price > 0 and abs(live_price - entry_price) / entry_price > 0.2:
                        scale = live_price / entry_price
                        sl_price *= scale
                        tp_price *= scale
                    entry_price = live_price
        except Exception:
            pass

        if entry_price <= 0 or sl_price <= 0:
            print(f"[!] Auto-Trader: Invalid entry/SL prices for {symbol}.")
            return None

        # 1. Prevent duplicate positions
        existing_pos = self.get_open_position(symbol)
        if existing_pos:
            print(f"[*] Auto-Trader: Open position already exists for {symbol}. Skipping duplicate entry.")
            return None

        # 2. Check Available Balance
        balances = self.get_account_balances()
        if not balances:
            print("[!] Auto-Trader: Unable to fetch Binance balance.")
            return None

        avail_usdt = balances.get("available_balance", 0.0)
        wallet_usdt = balances.get("wallet_balance", avail_usdt)
        
        # Effective base balance: strictly the smaller of available or wallet USDT
        # This prevents Multi-Assets Mode or portfolio margin from inflating position size
        base_bal = min(avail_usdt, wallet_usdt) if wallet_usdt > 0 else avail_usdt
        
        if base_bal < 3.0:
            print(f"[!] Auto-Trader: Insufficient balance (${base_bal:.2f} USDT). Minimum $3.00 required.")
            return None

        # 3. Enforce ISOLATED margin and leverage FIRST before calculating sizes
        self.set_margin_type_isolated(symbol)
        self.set_leverage(symbol, self.leverage)

        # Calculate Position Margin: strictly 10% of base balance (e.g. 10% of $43.32 = $4.33 USDT)
        margin_allocated = base_bal * (self.position_size_pct / 100.0)
        notional_value = margin_allocated * self.leverage
        raw_qty = notional_value / entry_price

        # Symbol precision rules
        rules = self.get_symbol_rules(symbol)
        if not rules:
            print(f"[!] Auto-Trader: Could not fetch symbol trading rules for {symbol}.")
            return None

        order_qty = self.round_qty(raw_qty, rules["step_size"], rules["step_decimals"])
        sl_formatted = self.round_price(sl_price, rules["tick_size"], rules["tick_decimals"])
        tp_formatted = self.round_price(tp_price, rules["tick_size"], rules["tick_decimals"])

        raw_tp1 = sig.get("raw_tp1")
        raw_tp2 = sig.get("raw_tp2")
        tp1_formatted = self.round_price(raw_tp1, rules["tick_size"], rules["tick_decimals"]) if raw_tp1 else "-"
        tp2_formatted = self.round_price(raw_tp2, rules["tick_size"], rules["tick_decimals"]) if raw_tp2 else "-"

        if order_qty < rules["min_qty"]:
            print(f"[!] Auto-Trader: Order qty {order_qty} is below symbol minQty {rules['min_qty']}.")
            return None

        final_notional = order_qty * entry_price
        if final_notional < rules["min_notional"]:
            print(f"[!] Auto-Trader: Notional value ${final_notional:.2f} is below minNotional ${rules['min_notional']:.2f}.")
            return None

        # 4. Place MARKET Entry Order
        entry_params = self._sign_request({
            "symbol": symbol,
            "side": side,
            "positionSide": pos_side,
            "type": "MARKET",
            "quantity": order_qty
        })

        try:
            print(f"[🚀] Placing LIVE {side} ({pos_side}) MARKET order for {symbol} | Qty: {order_qty} (~${margin_allocated:.2f} Margin @ {self.leverage}x)...")
            resp = self.session.post(f"{BASE_URL}/fapi/v1/order", data=entry_params, timeout=10)
            if resp.status_code != 200:
                print(f"[!] Binance Order Error ({resp.status_code}): {resp.text}")
                return None

            order_result = resp.json()
            avg_price = float(order_result.get("avgPrice") or entry_price)
            print(f"[✅] {symbol} FILLED @ ${avg_price:.6f} | Order ID: {order_result.get('orderId')}")

            # 5. Instantly place Hardware STOP_MARKET Order directly on Binance Matching Engine
            sl_res = self._place_conditional_order(symbol, exit_side, "STOP_MARKET", sl_formatted, pos_side, order_qty)
            sl_order_id = sl_res.get("order_id") if sl_res.get("success") else None
            if sl_order_id:
                print(f"[🛡️] {symbol} STOP_MARKET placed @ ${sl_formatted} (ID: {sl_order_id} via {sl_res.get('endpoint')})")
            else:
                print(f"[!] WARNING: Failed to place STOP_MARKET for {symbol}.")

            # 6. Instantly place Hardware TAKE_PROFIT_MARKET Order directly on Binance Matching Engine
            tp_res = self._place_conditional_order(symbol, exit_side, "TAKE_PROFIT_MARKET", tp_formatted, pos_side, order_qty)
            tp_order_id = tp_res.get("order_id") if tp_res.get("success") else None
            if tp_order_id:
                print(f"[🎯] {symbol} TAKE_PROFIT_MARKET placed @ ${tp_formatted} (ID: {tp_order_id} via {tp_res.get('endpoint')})")
            else:
                print(f"[!] Note: Could not place TAKE_PROFIT_MARKET for {symbol}.")

            execution_summary = {
                "symbol": symbol,
                "side": side,
                "qty": order_qty,
                "margin_usdt": margin_allocated,
                "leverage": self.leverage,
                "entry_price": avg_price,
                "sl_price": sl_formatted,
                "sl_order_id": sl_order_id,
                "tp1_price": tp1_formatted,
                "tp2_price": tp2_formatted,
                "tp_price": tp_formatted,
                "tp_order_id": tp_order_id,
                "order_id": order_result.get("orderId"),
                "status": "FILLED"
            }
            return execution_summary

        except Exception as e:
            print(f"[!] Exception during order placement: {e}")
            return None

    def trail_stop_loss(self, symbol, is_long, new_sl_price, qty=None):
        """Cancels old conditional orders and places updated Stop Loss at Break-Even or TP1"""
        if not self.is_live_enabled():
            return False

        rules = self.get_symbol_rules(symbol)
        if not rules:
            return False
        sl_formatted = self.round_price(new_sl_price, rules["tick_size"], rules["tick_decimals"])
        exit_side = "SELL" if is_long else "BUY"
        pos_side = ("LONG" if is_long else "SHORT") if self.is_hedge_mode else "BOTH"

        try:
            # Cancel all existing open conditional and normal orders for this symbol
            self.cancel_all_symbol_orders(symbol)
            
            # Place new trailed STOP_MARKET via Algo Order service
            res = self._place_conditional_order(symbol, exit_side, "STOP_MARKET", sl_formatted, pos_side, qty)
            if res.get("success"):
                print(f"[🔒] {symbol}: Stop Loss trailed to ${sl_formatted} (ID: {res.get('order_id')})")
                return True
            else:
                print(f"[!] Failed to place trailed Stop Loss for {symbol}")
        except Exception as e:
            print(f"[!] Error trailing stop loss for {symbol}: {e}")
        return False

    def close_position_market(self, symbol, is_long, fraction=1.0):
        """Closes 50% on TP1, 25% on TP2, or 100% on Exit"""
        if not self.is_live_enabled():
            return False

        pos = self.get_open_position(symbol)
        if not pos:
            return False

        current_amt = abs(float(pos.get("positionAmt", 0.0)))
        if current_amt <= 0:
            return False

        rules = self.get_symbol_rules(symbol)
        if not rules:
            return False

        qty_to_close = self.round_qty(current_amt * fraction, rules["step_size"], rules["step_decimals"])
        if qty_to_close < rules["min_qty"]:
            qty_to_close = current_amt

        exit_side = "SELL" if is_long else "BUY"
        pos_side = ("LONG" if is_long else "SHORT") if self.is_hedge_mode else "BOTH"

        order_data = {
            "symbol": symbol,
            "side": exit_side,
            "positionSide": pos_side,
            "type": "MARKET",
            "quantity": qty_to_close
        }
        if not self.is_hedge_mode:
            order_data["reduceOnly"] = "true"

        params = self._sign_request(order_data)

        try:
            resp = self.session.post(f"{BASE_URL}/fapi/v1/order", data=params, timeout=8)
            if resp.status_code == 200:
                print(f"[💰] {symbol}: Partial/Full Close ({fraction*100:.0f}%) executed: {qty_to_close}")
                # If closing 100%, also cancel open normal and algo orders
                if fraction >= 0.95:
                    self.cancel_all_symbol_orders(symbol)
                return True
            else:
                print(f"[!] Close order error: {resp.text}")
        except Exception as e:
            print(f"[!] Exception closing position {symbol}: {e}")
        return False
