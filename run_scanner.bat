@echo off
title Binance SMC 1:3 RR Signal Scanner
cd /d "%~dp0"

echo =========================================================
echo   Checking environment...
echo =========================================================

:: Check if Python is installed
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python is not installed or not added to PATH!
    echo Please install Python from https://www.python.org/
    echo Make sure to tick "Add python.exe to PATH" during installation.
    echo.
    pause
    exit /b 1
)

:: Check if requests library is installed
python -c "import requests" >nul 2>&1
if %errorlevel% neq 0 (
    echo Installing required "requests" library...
    pip install requests
    if %errorlevel% neq 0 (
        echo [ERROR] Failed to install requests. Please run "pip install requests" manually.
        pause
        exit /b 1
    )
)

echo.
echo =========================================================
echo   Scanning Binance Live (Daily S&R + 1H OB + 1H CHoCH)...
echo =========================================================
python binance_signal_scanner.py

echo.
echo Opening Dashboard in your browser...
start "" "dashboard.html"

echo.
echo [Done] You can close this window or press any key.
pause
