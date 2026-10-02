@echo off
title Binance SMC Secure Dashboard Web Server
cd /d "%~dp0"

echo =========================================================
echo   Binance SMC Secure Dashboard Web Server
echo =========================================================
echo Starting on port 80 (or your configured port)...
echo Open browser at: http://localhost:80 or http://127.0.0.1:80
echo Press Ctrl+C to stop.
echo =========================================================
echo.

python dashboard_auth_server.py

pause
