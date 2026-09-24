#!/usr/bin/env bash
# ========================================================
# Binance SMC Signal Scanner - 24/7 Auto Scan Loop for Linux
# Optimized for Oracle Linux / Ubuntu / RHEL
# ========================================================

# Change directory to the script's directory
cd "$(dirname "$0")"

# Default interval in seconds (180 seconds = 3 minutes)
# 3 minutes is optimal for 1H & 4H SMC setups, preventing Binance 429 rate limit bans
INTERVAL=${SCAN_INTERVAL:-180}

echo "========================================================="
echo "   Binance SMC Signal Scanner (24/7 Linux Auto-Scan)"
echo "   Scan Interval: ${INTERVAL}s"
echo "========================================================="

# Check Python3 installation
if ! command -v python3 &> /dev/null; then
    echo "[ERROR] Python 3 is not installed!"
    echo "On Oracle Linux, install with: sudo dnf install -y python3 python3-pip"
    exit 1
fi

# Activate virtual environment if present
if [ -d "venv" ]; then
    source venv/bin/activate
fi

# Check requests library
if ! python3 -c "import requests" &> /dev/null; then
    echo "[!] Installing dependencies from requirements.txt..."
    python3 -m pip install -r requirements.txt
fi

# Check config files
if [ ! -f "telegram_config.json" ]; then
    echo "  [⚠️ WARNING] telegram_config.json not found in $(pwd)!"
    echo "  Telegram alerts will be skipped until telegram_config.json is created."
else
    echo "  [✓] Telegram config detected."
fi

if [ ! -f "binance_api_config.json" ]; then
    echo "  [*] binance_api_config.json not found (Dry-run / Signal mode)."
else
    echo "  [✓] Binance API config detected (Live Auto-Trader active)."
fi

# Trap Ctrl+C (SIGINT) to exit cleanly
trap "echo -e '\n[!] Scanner stopped by user.'; exit 0" SIGINT SIGTERM

while true; do
    echo "---------------------------------------------------------"
    echo "  [LIVE] Scanning Binance Pairs: $(date '+%Y-%m-%d %H:%M:%S')"
    echo "---------------------------------------------------------"
    
    # Auto-pull only if new commits exist on GitHub (never wipe runtime/config files)
    git fetch origin main -q 2>/dev/null
    LOCAL=$(git rev-parse HEAD 2>/dev/null)
    REMOTE=$(git rev-parse origin/main 2>/dev/null)
    if [ -n "$LOCAL" ] && [ -n "$REMOTE" ] && [ "$LOCAL" != "$REMOTE" ]; then
        echo "[+] New code update detected from GitHub! Syncing..."
        [ -f "latest_signals.json" ] && cp latest_signals.json /tmp/_ls.json 2>/dev/null
        [ -f "dashboard.html" ] && cp dashboard.html /tmp/_db.html 2>/dev/null
        [ -f "index.html" ] && cp index.html /tmp/_idx.html 2>/dev/null
        [ -f "trade_history.json" ] && cp trade_history.json /tmp/_th.json 2>/dev/null
        [ -f "telegram_config.json" ] && cp telegram_config.json /tmp/_tg.json 2>/dev/null
        [ -f "binance_api_config.json" ] && cp binance_api_config.json /tmp/_bac.json 2>/dev/null

        git reset --hard origin/main -q 2>/dev/null

        [ -f "/tmp/_ls.json" ] && cp /tmp/_ls.json latest_signals.json 2>/dev/null
        [ -f "/tmp/_db.html" ] && cp /tmp/_db.html dashboard.html 2>/dev/null
        [ -f "/tmp/_idx.html" ] && cp /tmp/_idx.html index.html 2>/dev/null
        [ -f "/tmp/_th.json" ] && cp /tmp/_th.json trade_history.json 2>/dev/null
        [ -f "/tmp/_tg.json" ] && cp /tmp/_tg.json telegram_config.json 2>/dev/null
        [ -f "/tmp/_bac.json" ] && cp /tmp/_bac.json binance_api_config.json 2>/dev/null
    fi
    
    python3 binance_signal_scanner.py
    
    echo ""
    echo "  [+] Scan finished. Next scan in ${INTERVAL} seconds..."
    echo "  Press Ctrl+C to stop."
    echo ""
    sleep "$INTERVAL"
done
