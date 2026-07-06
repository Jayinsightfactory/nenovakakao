@echo off
title nenova bidirectional monitor
REM ============================================================
REM Nenova bidirectional monitoring (monitor + desktop STOP button)
REM   K->W mirror + W->K relay (both directions)
REM   misclick-crash guard: overlay / action-log GUI off
REM   W->K priority: reverse pass every 20s, 10 rooms per pass
REM
REM Stop (4 ways, all safe):
REM   1. Ctrl+Alt+Q          (global hotkey, instant)
REM   2. red STOP button     (bottom-right)
REM   3. stop_nenova.bat
REM   4. create data\_STOP
REM
REM   Full setup (reply-button W->K + cloudflared tunnel): run_nenova_realtime.bat
REM ============================================================

set PYTHON=C:\Users\USER\AppData\Local\Programs\Python\Python312\python.exe
set PROJ=C:\Users\USER\nenova_agent\.claude\worktrees\cranky-yalow-f3379c
cd /d "%PROJ%"

set NENOVA_NO_OVERLAY=1
set NENOVA_NO_ACTION_LOG=1
REM === W->K (work->kakao reverse) DISABLED -> K->W mirroring ONLY (safe; never sends to suppliers) ===
REM   To re-enable later (ONLY after dry-run verify): change to set NENOVA_WORKBRIDGE=1
set NENOVA_WORKBRIDGE=0
REM === K->W (kakao->work mirror UPLOAD) DISABLED -> SHEETS ONLY (kakao read -> Google Sheets) ===
REM   Per user: stop uploading kakao content to Work; keep only Google Sheets upload.
REM   To re-enable Work mirroring later: change to set NENOVA_MIRROR_TO_WORK=1
set NENOVA_MIRROR_TO_WORK=0
REM inline sheet-sync OFF: separate sync_worker handles Sheets (parallel, no mirror blocking)
set NENOVA_INLINE_SHEETSYNC=0
set NENOVA_SYNC_INTERVAL=300

if exist "%PROJ%\data\_STOP" del "%PROJ%\data\_STOP"

echo.
echo  ============================================
echo   Nenova SHEETS-ONLY (kakao read -^> Google Sheets; no Work upload) - STARTING
echo   Stop: Ctrl+Alt+Q / red STOP button / stop_nenova.bat
echo  ============================================
echo.

start "nenova-monitor" "%PYTHON%" -u main.py
start "nenova-stop-btn" "%PYTHON%" -u tools\desktop_stop_button.py
start "nenova-sync" "%PYTHON%" -u -m core.sync_worker

echo  Started. You can close this window; the monitor keeps running.
timeout /t 6 /nobreak >nul
