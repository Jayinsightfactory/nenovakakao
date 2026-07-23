@echo off
setlocal
cd /d "%~dp0"
if not exist ".env" (
  echo [ERROR] .env is missing. Run the MOYI Kakao installer first.
  pause
  exit /b 1
)
if not exist "data" mkdir "data"
if not exist "captures" mkdir "captures"
start "MOYI Operations Console" cmd /k python main.py moyi-console
python -u main.py moyi-worker
pause
