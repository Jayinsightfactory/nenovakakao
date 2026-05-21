@echo off
REM ============================================================
REM 네노바 자동화 정지 (안전한 파일 기반 STOP)
REM
REM   data\_STOP 파일 생성 → monitor 가 다음 방/페이지 처리 직전에 감지하고
REM   감시 루프를 깨끗하게 종료. 화면 자동화가 실수로 누를 수 없는 방식.
REM
REM   reactive(워크->카톡) 서버와 tunnel 은 유지됨 (수동 동작이라 위험 없음).
REM   다시 시작하려면: run_nenova_realtime.bat
REM ============================================================

set PROJ=C:\Users\USER\nenova_agent\.claude\worktrees\cranky-yalow-f3379c

echo stop requested %DATE% %TIME%> "%PROJ%\data\_STOP"

echo.
echo  ===========================================
echo   [STOP] 정지 신호 전송됨  (data\_STOP 생성)
echo  ===========================================
echo.
echo  monitor 가 몇 초 내에 멈춥니다.
echo  (사진 처리 중이면 현재 사진 단계까지 마친 뒤 종료)
echo.
echo  다시 시작: run_nenova_realtime.bat 더블클릭
echo.
timeout /t 5 /nobreak >nul
