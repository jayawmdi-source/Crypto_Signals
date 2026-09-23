#!/usr/bin/env bash
# ========================================================
# Binance SMC Signal Scanner - 24/7 Auto Scan Loop for Linux
# Optimized for Oracle Linux / Ubuntu / RHEL
# ========================================================

# Change directory to the script's directory
cd "$(dirname "$0")"

# Default interval in seconds (60 seconds = 1 minute)
# Change this if you want longer or shorter scan intervals
INTERVAL=60

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

# Trap Ctrl+C (SIGINT) to exit cleanly
trap "echo -e '\n[!] Scanner stopped by user.'; exit 0" SIGINT SIGTERM

while true; do
    echo "---------------------------------------------------------"
    echo "  [LIVE] Scanning Binance Pairs: $(date '+%Y-%m-%d %H:%M:%S')"
    echo "---------------------------------------------------------"
    
    python3 binance_signal_scanner.py
    
    echo ""
    echo "  [+] Scan finished. Next scan in ${INTERVAL} seconds..."
    echo "  Press Ctrl+C to stop."
    echo ""
    sleep "$INTERVAL"
done
