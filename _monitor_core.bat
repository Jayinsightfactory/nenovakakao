@echo off
REM Hidden monitor core - launched by start_monitor.vbs (no console window).
REM ASCII only (cmd codepage safe). Logs go to logs\monitor_live.log.
cd /d "C:\Users\USER\nenova_agent\.claude\worktrees\cranky-yalow-f3379c"
set PY=C:\Users\USER\AppData\Local\Programs\Python\Python312\python.exe
set PYW=C:\Users\USER\AppData\Local\Programs\Python\Python312\pythonw.exe

set NENOVA_NO_OVERLAY=1
set NENOVA_NO_ACTION_LOG=1
set NENOVA_WORKBRIDGE_INTERVAL=10
set NENOVA_WORKBRIDGE_MAXROOMS=10
REM inline sheet-sync OFF: the separate sync_worker handles Sheets (no mirror blocking, no double-sync)
set NENOVA_INLINE_SHEETSYNC=0
set NENOVA_SYNC_INTERVAL=300

if not exist logs mkdir logs
if exist data\_STOP del data\_STOP

REM stop button: pythonw -> Tk window only, no console
start "" "%PYW%" tools\desktop_stop_button.py

REM sheet-sync worker (Option A): separate process, periodic incremental sync.
REM screen-free (file read + Sheets API) -> runs parallel to mirroring, never blocks it.
start "" "%PYW%" -m core.sync_worker

REM monitor: runs inside this hidden cmd, output redirected -> no visible window
"%PY%" -u main.py > logs\monitor_live.log 2>&1
