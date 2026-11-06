@echo off
rem Put this file in C:\Users\LG\Desktop\cctv-main\ and double-click it.
rem Change the two values below. Do NOT commit this file to GitHub.
chcp 65001 > nul
cd /d "%~dp0"
set CCTV_ADMIN_PASSWORD=guard2026
set CCTV_SESSION_SECRET=gusdnd02!@
uvicorn server.api.main:app --reload --port 8000
pause
