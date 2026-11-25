@echo off
title SodaAdAuto - Start
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo [!] Run setup.bat first.
  pause
  exit /b 1
)
start "SodaAdAuto" /min ".venv\Scripts\python.exe" -I qishui_auto.py
timeout /t 10 /nobreak >nul
start "" "http://127.0.0.1:8765"
echo [OK] Started, dashboard opening. This window closes itself.
timeout /t 6 /nobreak >nul
