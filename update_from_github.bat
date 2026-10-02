@echo off
title Crypto Signals - GitHub Auto Updater
color 0b

echo =========================================================
echo    CRYPTO SIGNALS - 1-CLICK GITHUB SYNC & UPDATER
echo =========================================================
echo.

python "%~dp0update_from_github.py"

echo.
pause
