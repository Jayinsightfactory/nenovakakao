@echo off
title nenova monitor - TALKHUB(MOYI) mode
REM ============================================================
REM Nenova monitoring - MOYI(talkhub) mirror mode
REM   forward  : K -> MOYI   (MIRROR_TARGET=talkhub, /bridge/kakao/inbound + /files/upload)
REM   reverse  : MOYI -> K   (OFF by default; supplier-facing => flip REVERSE_RELAY=1 only when verified)
REM   secrets  : TALKHUB_BASE_URL / TALKHUB_BRIDGE_SECRET come from .env (dotenv)
REM   Stop     : Ctrl+Alt+Q / red STOP button / data\_STOP
REM
REM   NOTE: talkhub backend is text-only until issues T1..T5 land (attachments/dedup/auth/ensure-room).
REM         Until then inbound may 404 without a BridgeMapping. This launcher is ready for cutover.
REM   ASCII only (cmd cp949 safe).
REM ============================================================
set PYTHON=C:\Users\USER\AppData\Local\Programs\Python\Python312\python.exe
set PROJ=C:\Users\USER\nenova_agent\.claude\worktrees\cranky-yalow-f3379c
cd /d "%PROJ%"

REM --- mirror step ON, target = talkhub (MOYI) ---
set NENOVA_MIRROR_TO_WORK=1
set MIRROR_TARGET=talkhub
set TALKHUB_MIRROR_ENABLED=1
set TALKHUB_SELF_LABEL=MOYI

REM --- reverse (MOYI->kakao). OFF by default (real send to suppliers). Set to 1 only after verify. ---
set REVERSE_RELAY=0
set REVERSE_INTERVAL=5

REM --- common ---
set NENOVA_NO_OVERLAY=1
set NENOVA_NO_ACTION_LOG=1
set NENOVA_INLINE_SHEETSYNC=0
set NENOVA_SYNC_INTERVAL=300

if exist "%PROJ%\data\_STOP" del "%PROJ%\data\_STOP"

echo.
echo  ============================================
echo   Nenova TALKHUB(MOYI) mode - STARTING
echo   forward K-^>MOYI ON  /  reverse MOYI-^>K = %REVERSE_RELAY%
echo   Stop: Ctrl+Alt+Q / red STOP button / stop_nenova.bat
echo  ============================================
echo.

start "nenova-monitor" "%PYTHON%" -u main.py
start "nenova-sync" "%PYTHON%" -u -m core.sync_worker
start "nenova-stop-btn" "%PYTHON%" -u tools\desktop_stop_button.py
if "%REVERSE_RELAY%"=="1" start "nenova-reverse" "%PYTHON%" -u -m core.talkhub_reverse

echo  Started. Close this window if you like; processes keep running.
timeout /t 6 /nobreak >nul
