@echo off
chcp 65001 >nul
title 네노바 양방향 모니터링
REM ============================================================
REM 네노바 양방향 모니터링 시작 (모니터 + 바탕화면 강제정지 버튼)
REM   - 카톡<->워크 양방향 (K->W 미러 + W->K Vision 중계)
REM   - 미스클릭 크래시 방지: 오버레이/액션로그 GUI off
REM   - W->K 우선: 역방향 패스 20초 주기, 패스당 10개 방
REM
REM 정지(4가지, 모두 안전):
REM   1) Ctrl+Alt+Q  (키보드 전역 핫키 - 즉시)
REM   2) 우하단 빨간 [강제정지] 버튼
REM   3) stop_nenova.bat
REM   4) data\_STOP 파일 생성
REM
REM   (워크 [답장] 버튼 기반 W->K + 봇 터널까지 쓰려면: run_nenova_realtime.bat)
REM ============================================================

set PYTHON=C:\Users\USER\AppData\Local\Programs\Python\Python312\python.exe
set PROJ=C:\Users\USER\nenova_agent\.claude\worktrees\cranky-yalow-f3379c
cd /d "%PROJ%"

REM 미스클릭 크래시 방지 (정지는 위 4가지 수단으로)
set NENOVA_NO_OVERLAY=1
set NENOVA_NO_ACTION_LOG=1

REM W->K(워크->카톡) 우선
set NENOVA_WORKBRIDGE_INTERVAL=20
set NENOVA_WORKBRIDGE_MAXROOMS=10

REM 이전 정지 신호 제거 (재개 의도)
if exist "%PROJ%\data\_STOP" del "%PROJ%\data\_STOP"

echo.
echo  ============================================
echo   네노바 양방향 모니터링 시작
echo   정지: Ctrl+Alt+Q / 우하단 빨간 버튼 / stop_nenova.bat
echo  ============================================
echo.

start "nenova-monitor" "%PYTHON%" -u main.py
start "nenova-stop-btn" "%PYTHON%" -u tools\desktop_stop_button.py

echo  시작됨. (이 창은 닫아도 모니터는 계속 작동합니다)
timeout /t 6 /nobreak >nul
