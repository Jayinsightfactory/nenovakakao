"""KW 채팅 Vision 추출 라이브 PoC."""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from core.work_vision_reader import (
    find_kakaowork_window, capture_chat_panel, extract_messages,
)
import win32gui

hwnd = find_kakaowork_window()
print(f"[PROBE] KW hwnd = {hwnd}")
if not hwnd:
    raise SystemExit("KW 창 없음")
l,t,r,b = win32gui.GetWindowRect(hwnd)
print(f"[PROBE] KW rect = ({l},{t},{r},{b}) size={r-l}x{b-t}")

print("[PROBE] 캡처 (right panel) ...")
cap = capture_chat_panel(hwnd)
print(f"  → {cap.name} ({cap.stat().st_size} bytes)")

print("[PROBE] Opus 메시지 추출 (Claude API 호출, ~5-10초) ...")
import time
t0 = time.time()
msgs = extract_messages(cap)
print(f"  → {len(msgs)} 메시지, {int(time.time()-t0)}초")

for i, m in enumerate(msgs[:15], 1):
    s = m.get("sender","")[:14]
    tt = m.get("time","")[:10]
    c = (m.get("content","") or "").replace("\n"," ")[:80]
    img = "🖼" if m.get("has_image") else ""
    sysM = "(sys)" if m.get("is_system") else ""
    print(f"  {i:2}. [{s:<14}] [{tt:<10}] {c} {img}{sysM}")
if len(msgs) > 15:
    print(f"  ... +{len(msgs)-15}개")
