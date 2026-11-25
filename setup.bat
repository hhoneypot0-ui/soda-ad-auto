@echo off
title SodaAdAuto - Setup
cd /d "%~dp0"
python -m venv .venv || (echo [!] Need Python 3.10-3.14 installed & pause & exit /b 1)
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt
echo.
echo [OK] Environment ready. Double-click the launcher next.
pause
