@echo off
chcp 65001 >nul
cd /d "C:\Users\USER\nenova_agent\.claude\worktrees\cranky-yalow-f3379c"
set NENOVA_NO_OVERLAY=1
del /q "data\_reply_tunnel.log" 2>nul

echo 워크->카톡 답장 서버 + 터널을 별도 창 2개로 띄웁니다.

start "REPLY-TUNNEL" "C:\Users\USER\AppData\Local\Microsoft\WinGet\Packages\Cloudflare.cloudflared_Microsoft.Winget.Source_8wekyb3d8bbwe\cloudflared.exe" tunnel --url http://localhost:5000 --logfile "data\_reply_tunnel.log"

start "REPLY-SERVER-5000" "C:\Users\USER\AppData\Local\Programs\Python\Python312\python.exe" -m core.kakaowork_reactive --port 5000

echo.
echo 두 창이 떴습니다. 터널 URL 은 data\_reply_tunnel.log 에 기록됩니다.
echo (이 창은 닫아도 됩니다)
timeout /t 5 >nul
