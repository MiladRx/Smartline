@echo off
title Cloudflare tunnel - Smartline bridge
cd /d "%~dp0"
echo.
echo Starting tunnel for the local bridge (port 8138)...
echo Copy the https://....trycloudflare.com URL into the web app (gear icon) on your phone.
echo Keep this window open. Close it to stop remote access.
echo.
cloudflared.exe tunnel --url http://127.0.0.1:8138
pause
