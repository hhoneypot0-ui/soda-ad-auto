@echo off
title SodaAdAuto - Stop
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File kill_script.ps1 >nul
adb shell svc power stayon false 2>nul
for /l %%i in (1,1,25) do adb shell input keyevent 25 2>nul
echo [OK] Stopped. Phone kept silent; press volume-up to restore sound.
timeout /t 5 /nobreak >nul
