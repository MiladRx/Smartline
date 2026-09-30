@echo off
cd /d "%~dp0"
start "Smartline bridge" cmd /k python bulb-bridge.py
start "Smartline web" cmd /k python -m http.server 8137
timeout /t 3 /nobreak >nul
start "" "http://localhost:8137/"
