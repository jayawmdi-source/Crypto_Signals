=============================================================================
BINANCE INSTITUTIONAL SMC 2.0 SNIPER TRADING BOT & SCANNER
COMPLETE SYSTEM PACKAGE (Updated: 2026-10-02)
=============================================================================

1. WHAT IS IN THIS PACKAGE (LATEST SNIPER 2.0 UPDATE):
   - binance_signal_scanner.py: 24/7 SMC Scanner with Sniper Retest Execution Engine,
     Dynamic 2.2x ATR Stop Loss, Funding Rate Shield (0.035%), $25M Volume Liquidity Filter,
     News Shield (FOMC/CPI/NFP), Volume Expansion Guard, and Pro-Trader Circuit Breaker
   - binance_futures_trader.py: Binance Futures execution engine (Algo Orders, 1.5% Risk Sizing, 10x Isolated, 1:3 TP)
   - dashboard_auth_server.py: Secure dashboard web server with authentication
   - binance_api_config.json: Your active Binance API Key & Secret
   - telegram_config.json: Your active Telegram Bot Token & Chat ID
   - trade_history.json: Live trade tracking & status database (Isolated strictly to real Binance executions)
   - dashboard.html & index.html: Live interactive SMC dashboard
   - vps_credentials/ssh-key-2026-09-23.key: Your Oracle Cloud VPS SSH Private Key
   - update_from_github.bat & .py: 1-click update tools to sync latest code from GitHub

2. ORACLE CLOUD 24/7 VPS DETAILS:
   - Host IP: 138.2.80.252
   - Username: opc
   - OS: Oracle Linux
   - Connect via PowerShell / Terminal:
     ssh -i vps_credentials/ssh-key-2026-09-23.key opc@138.2.80.252
   - VPS App Folder: /home/opc/Crypto_Signals
   - Service Name: crypto-scanner.service
   - Check VPS Status:
     sudo systemctl status crypto-scanner.service
   - View Live VPS Logs:
     journalctl -u crypto-scanner.service -f

3. RUNNING LOCALLY ON PERSONAL LAPTOP (OPTIONAL):
   - Install Python 3.10+
   - Open folder in terminal:
     pip install -r requirements.txt
   - Double-click 'run_scanner.bat' (single scan) or 'run_scanner_loop.bat' (24/7 loop).
   - Double-click 'run_dashboard_server.bat' to run the secure local dashboard server.
   - Open 'dashboard.html' in any web browser to view the live dashboard.
=============================================================================
