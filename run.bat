@echo off
REM 네노바 에이전트 실행 런처
set PYTHON="C:\Users\USER\AppData\Local\Programs\Python\Python312\python.exe"
cd /d "%~dp0"

if "%1"=="scan" (
    %PYTHON% main.py scan
) else if "%1"=="select" (
    %PYTHON% main.py select
) else if "%1"=="install" (
    %PYTHON% -m pip install -r requirements.txt
) else if "%1"=="moyi-worker" (
    %PYTHON% main.py moyi-worker
) else if "%1"=="moyi-console" (
    %PYTHON% main.py moyi-console
) else (
    %PYTHON% main.py
)
