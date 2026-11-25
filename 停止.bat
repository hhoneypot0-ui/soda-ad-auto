@echo off
title SodaAdAuto - Stop
cd /d "%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -File kill_script.ps1 >nul
adb shell svc power stayon false 2>nul
for /l %%i in (1,1,25) do adb shell input keyevent 25 2>nul
if exist ".venv\Scripts\python.exe" ".venv\Scripts\python.exe" -c "from qishui_auto import cleanup_images; n=cleanup_images(); print('screenshots removed:', n)"

echo [OK] Stopped. Silent kept. Screenshots purged.
timeout /t 5 /nobreak >nul
