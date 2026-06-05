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

REM 우하단 상태 오버레이의 '중지' 버튼을 자동화 스크롤/클릭이 실수로 눌러
REM 프로세스가 os._exit(1) 되는 버그 방지 → 오버레이를 no-op stub 으로.
REM 정지는 data\_STOP 파일(=stop_nenova.bat)로 안전하게 수행.
set NENOVA_NO_OVERLAY=1

REM 액션 로그 창의 '강제정지' 버튼도 같은 미스클릭 사고(카톡 탭 클릭이 로그창에 떨어짐)를
REM 일으키므로 함께 끔 → GUI 창 없음, 로그는 콘솔/파일로. (2026-06-04 크래시 2건 원인)
set NENOVA_NO_ACTION_LOG=1

REM W→K(워크→카톡) 트래픽이 K→W 보다 많을 예정 → 역방향 패스를 더 자주(기본 60s→20s),
REM 패스당 더 많은 방(기본 4→10개) 처리해 응답성·처리량 확보. (2026-06-05)
set NENOVA_WORKBRIDGE_INTERVAL=10
set NENOVA_WORKBRIDGE_MAXROOMS=10

REM 강제정지: 실행 중 언제든 Ctrl+Alt+Q (키보드 전역 핫키, 마우스 미스클릭 무관) 또는
REM           stop_nenova.bat 더블클릭(data\_STOP 생성). 둘 다 graceful, 종료코드 0.

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

REM 바탕화면 강제정지 버튼 (모니터와 별개 프로세스 → 오클릭돼도 graceful 정지만, 크래시 X).
REM 우하단에 항상-위 작은 빨간 버튼. 모니터 멈추면 자동으로 닫힘.
start "nenova-stop-btn" "%PYTHON%" -u tools\desktop_stop_button.py

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
