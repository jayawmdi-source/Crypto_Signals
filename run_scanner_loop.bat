@echo off
title Binance SMC Signal Scanner (24/7 Auto-Scan Loop)
cd /d "%~dp0"

:: Check Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python is not installed or not added to PATH!
    echo Please install Python from https://www.python.org/
    pause
    exit /b 1
)

:: Check requests
python -c "import requests" >nul 2>&1
if %errorlevel% neq 0 (
    echo Installing required "requests" library...
    pip install requests
)

echo Opening Dashboard in your browser...
start "" "dashboard.html"

:loop
cls
echo =========================================================
echo   [LIVE] Scanning Binance Top 150 SMC Signals...
echo   Started at: %TIME%
echo =========================================================
python binance_signal_scanner.py
echo.
echo =========================================================
echo   Done! Next scan in 15 minutes (900s).
echo   Keep this window open to scan 24/7.
echo   Press Ctrl+C to stop.
echo =========================================================
timeout /t 900 /nobreak
goto loop
