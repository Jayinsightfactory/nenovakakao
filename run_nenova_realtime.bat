@echo off
REM ============================================================
REM 네노바 실시간 양방향 미러링 상시 가동
REM   1. monitor      : 카톡 새 톡 → 워크 미러방 (+ [답장] 버튼)
REM   2. reactive 서버 : 워크 버튼 클릭 → 모달 → 카톡 송신 (Flask :5000)
REM   3. cloudflared  : public URL (봇 Request/Callback)
REM
REM 사용:
REM   - 더블클릭 또는 작업 스케줄러(로그온 시 실행)에 등록
REM   - 종료: 각 창 닫기 또는 작업관리자 python.exe / cloudflared.exe
REM
REM 주의:
REM   - cloudflared quick tunnel 은 재시작마다 URL 이 바뀜.
REM     URL 바뀌면 _cloudflared.log 에서 새 URL 확인 후 봇 대시보드 재등록 필요.
REM   - 고정 URL 원하면 cloudflare named tunnel (도메인 필요) 로 전환.
REM ============================================================

set PYTHON=C:\Users\USER\AppData\Local\Programs\Python\Python312\python.exe
set PROJ=C:\Users\USER\nenova_agent\.claude\worktrees\cranky-yalow-f3379c
set CF=C:\Users\USER\AppData\Local\Microsoft\WinGet\Packages\Cloudflare.cloudflared_Microsoft.Winget.Source_8wekyb3d8bbwe\cloudflared.exe

cd /d "%PROJ%"

REM 이전 STOP 신호 제거
if exist "%PROJ%\data\_STOP" del "%PROJ%\data\_STOP"

echo [1/3] reactive Flask 서버 (워크 -^> 카톡)
start "nenova-reactive" "%PYTHON%" -u -m core.kakaowork_reactive --port 5000

REM Flask 가 뜰 시간
timeout /t 3 /nobreak >nul

echo [2/3] cloudflare tunnel (public URL)
start "nenova-tunnel" "%CF%" tunnel --url http://localhost:5000

REM tunnel URL 발급 시간
timeout /t 8 /nobreak >nul

echo [3/3] monitor (카톡 -^> 워크)
start "nenova-monitor" "%PYTHON%" -u main.py

echo.
echo === 3개 프로세스 가동 ===
echo   reactive : http://localhost:5000
echo   tunnel   : data\_cloudflared.log 에서 https://...trycloudflare.com 확인
echo   monitor  : 카톡 감시 시작
echo.
echo 봇 대시보드에 등록할 URL (secret = data\reactive_secret.txt):
echo   Request URL : https://^<tunnel^>/^<secret^>/request_modal
echo   Callback URL: https://^<tunnel^>/^<secret^>/callback
echo.
pause
