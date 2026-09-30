@echo off
cd /d "%~dp0"
start "" /b pythonw bulb-bridge.py > bridge.log 2>&1
start "" /b pythonw -m http.server 8137 > web.log 2>&1
