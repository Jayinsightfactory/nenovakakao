"""워크→카톡 답장 우선순위 테스트 — reactive 콜백에 '.' 를 직접 POST.

전산테스트팀(테스트 방)으로 '.' 송신을 시뮬레이션한다.
monitor 가 작업 중이면 양보 후 답장이 먼저 처리되는지 확인용.
"""
import json
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
secret = (ROOT / "data" / "reactive_secret.txt").read_text(encoding="utf-8").strip()

# 테스트 방 이름: selected_rooms 에서 '전산' 포함 방 자동 매칭
rooms = json.loads((ROOT / "data" / "selected_rooms.json").read_text(encoding="utf-8"))
target = None
for r in rooms:
    name = r.get("name") if isinstance(r, dict) else r
    if name and "전산" in name:
        target = name
        break

if not target:
    print("[TEST] '전산' 포함 방을 selected_rooms 에서 못 찾음", flush=True)
    sys.exit(1)

url = f"http://localhost:5000/{secret}/callback"
body = {"value": f"room={target}", "actions": {"reply_text": "."}}

print(f"[TEST] 대상 방: {target!r}", flush=True)
print(f"[TEST] POST {url}", flush=True)
t0 = time.time()
try:
    resp = requests.post(url, json=body, timeout=120)
    dt = time.time() - t0
    print(f"[TEST] 응답: {resp.status_code} ({dt:.1f}s) body={resp.text!r}", flush=True)
except Exception as e:
    print(f"[TEST] 예외: {type(e).__name__}: {e}", flush=True)
