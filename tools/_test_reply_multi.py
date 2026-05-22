"""워크→카톡 답장 우선순위 멀티 테스트.

전산테스트팀 + 네노바 영업 두 방에 '.' 답장을 시간차로 보내,
monitor 작업 중간에 끼어들어 (1) 양보 (2) 각 방 정확히 송신 (3) 직렬 처리
되는지 확인한다.
"""
import json
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
secret = (ROOT / "data" / "reactive_secret.txt").read_text(encoding="utf-8").strip()
rooms = json.loads((ROOT / "data" / "selected_rooms.json").read_text(encoding="utf-8"))
names = [(r.get("name") if isinstance(r, dict) else r) for r in rooms]
names = [n for n in names if n]

# 보낼 대상 (정확 일치 우선, 없으면 부분 일치)
WANT = ["전산테스트팀", "네노바 영업"]
GAP = 6.0  # 두 답장 사이 간격(초) — 그 사이 monitor 가 잠깐 재개

def resolve(want: str) -> str | None:
    if want in names:
        return want
    cands = [n for n in names if want in n]
    return cands[0] if cands else None

url = f"http://localhost:5000/{secret}/callback"
for i, want in enumerate(WANT):
    target = resolve(want)
    if not target:
        print(f"[TEST] '{want}' 방 못 찾음 — 스킵", flush=True)
        continue
    body = {"value": f"room={target}", "actions": {"reply_text": "."}}
    print(f"[TEST] ({i+1}/{len(WANT)}) → {target!r} 송신 요청", flush=True)
    t0 = time.time()
    try:
        resp = requests.post(url, json=body, timeout=120)
        print(f"[TEST]   응답 {resp.status_code} ({time.time()-t0:.1f}s)", flush=True)
    except Exception as e:
        print(f"[TEST]   예외 {type(e).__name__}: {e}", flush=True)
    if i < len(WANT) - 1:
        time.sleep(GAP)
