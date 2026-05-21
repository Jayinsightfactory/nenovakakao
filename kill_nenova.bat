@echo off
REM ============================================================
REM 네노바 monitor 강제 종료 (하드킬)
REM
REM   진행 중 작업(사진 다운로드/저장 등)까지 즉시 중단하고 monitor
REM   프로세스(python main.py)를 강제 종료한다. GUI 가 멈춰서 액션로그
REM   창의 💀 강제정지 버튼조차 안 먹힐 때의 최후 수단.
REM
REM   reactive(워크->카톡) 서버(kakaowork_reactive)는 건드리지 않음.
REM   다시 시작: run_nenova_realtime.bat
REM ============================================================

set PROJ=C:\Users\USER\nenova_agent\.claude\worktrees\cranky-yalow-f3379c

REM 정지 신호도 같이 남겨 재시작 로직과 일관성 유지
echo hard kill %DATE% %TIME%> "%PROJ%\data\_STOP"

echo.
echo  [HARD-KILL] monitor(python main.py) 강제 종료 중...
echo.

REM main.py 를 실행 중인 python 프로세스만 골라 종료 (reactive 보존)
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"name='python.exe'\" | Where-Object { $_.CommandLine -like '*main.py*' } | ForEach-Object { Write-Host ('  killed PID ' + $_.ProcessId); Stop-Process -Id $_.ProcessId -Force }"

echo.
echo  완료. (reactive 서버는 유지됨)
echo  다시 시작: run_nenova_realtime.bat
echo.
timeout /t 4 /nobreak >nul
