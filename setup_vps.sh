#!/usr/bin/env bash
# ==============================================================================
# Binance SMC Signal Scanner - One-Click Oracle Linux VPS Installer
# ==============================================================================
set -e

echo "=========================================================="
echo "  🚀 Starting Binance SMC Auto-Setup on Oracle Linux...   "
echo "=========================================================="

# 1. Update system & install Python 3, Git, Pip
echo "[1/5] Installing Git, Python 3, and Pip..."
sudo dnf update -y
sudo dnf install -y git python3 python3-pip

# 2. Setup project directory
APP_DIR="$HOME/Crypto_Signals"
if [ -d "$APP_DIR" ]; then
    echo "[2/5] Updating existing Crypto_Signals repository..."
    cd "$APP_DIR"
    git reset --hard HEAD
    git pull origin main
else
    echo "[2/5] Cloning Crypto_Signals repository..."
    cd "$HOME"
    git clone https://github.com/jayawmdi-source/Crypto_Signals.git
    cd "$APP_DIR"
fi

# 3. Install Python requirements
echo "[3/5] Installing Python requirements..."
pip3 install -r requirements.txt

# 4. Configure Telegram
echo "[4/5] Setting up telegram_config.json..."
cat << 'EOF' > telegram_config.json
{
  "bot_token": "8954515044:AAEkfBKoZ3x_Ky26T9ZFK-9lssGFYxD5Zz0",
  "chat_id": "5448711923"
}
EOF

# Ensure scripts have executable permissions and LF line endings
chmod +x run_scanner_loop.sh

# 5. Setup Systemd Service
echo "[5/5] Configuring systemd background service..."
CURRENT_USER=$(whoami)
SERVICE_PATH="/etc/systemd/system/crypto-scanner.service"

sudo bash -c "cat << 'EOF' > $SERVICE_PATH
[Unit]
Description=Binance SMC Signal Scanner 24/7 Service
After=network.target

[Service]
Type=simple
User=$CURRENT_USER
WorkingDirectory=$APP_DIR
ExecStart=/bin/bash $APP_DIR/run_scanner_loop.sh
Restart=always
RestartSec=10
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF"

sudo systemctl daemon-reload
sudo systemctl enable --now crypto-scanner

echo ""
echo "=========================================================="
echo "  ✅ Setup Successfully Completed! Scanner is Live 24/7   "
echo "=========================================================="
echo "  • Telegram Alerts are ACTIVE"
echo "  • Scan Interval: Every 60 seconds"
echo "  • Service Status: Active & Running"
echo ""
echo "  To view live scanner output, run:"
echo "    sudo journalctl -u crypto-scanner -f"
echo "=========================================================="
