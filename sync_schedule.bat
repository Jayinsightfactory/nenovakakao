@echo off
chcp 65001 > nul
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
cd /d C:\Users\USER\nenova_agent
"C:\Users\USER\AppData\Local\Programs\Python\Python312\python.exe" -X utf8 scripts/incremental_sync.py >> logs\sync_schedule.log 2>&1
