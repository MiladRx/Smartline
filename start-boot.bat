@echo off
cd /d "%~dp0"
start "Smartline bridge" /min cmd /k python bulb-bridge.py
start "Smartline web" /min cmd /k python -m http.server 8137
