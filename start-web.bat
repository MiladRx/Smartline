@echo off
cd /d "%~dp0"
start "" "http://localhost:8137/"
python -m http.server 8137
pause
