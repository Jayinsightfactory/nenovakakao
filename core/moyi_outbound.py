"""MOYI→카톡 발송 워커 (이슈 ⑦).

MOYI(talkhub) 서버의 KakaoOutbox 큐를 폴링해서 카카오톡 PC로 발송하고 ack한다.

계약 (talkhub 서버, 2026-07-09 라이브):
- GET  {MOYI_API_BASE}/bridge/kakao/outbound/pending?limit=20  (헤더 X-Bridge-Secret)
  → {"items": [{id, external_room_id, content, sender_name, attachments[{name,url,mime}], attempts}]}
- POST {MOYI_API_BASE}/bridge/kakao/outbound/{id}/ack  {"ok": true} 또는 {"ok": false, "error": "..."}
  → at-least-once: ack 전까지 같은 항목이 재노출되므로 발송 성공 후 반드시 ack.

발송 방식: 카카오톡 메인 창에서 Ctrl+F 검색 → 방 이름(external_room_id) 입력 → Enter로 방 열기
→ 입력란 클릭 → 클립보드 붙여넣기 → Enter. 첨부는 URL을 본문에 병기(파일 자동 업로드는 후속).

.env:
- MOYI_API_BASE      (기본 https://api.nowlink.kr)
- MOYI_BRIDGE_SECRET (talkhub Railway의 BRIDGE_SECRET와 동일 값)
"""
from __future__ import annotations

import os
import time

import pyautogui
import pyperclip
import requests

from core.window_manager import focus_kakaotalk

POLL_INTERVAL_SEC = 10
SEND_COOLDOWN_SEC = 2.0


def _base() -> str:
    return (os.getenv("MOYI_API_BASE") or "https://api.nowlink.kr").rstrip("/")


def _secret() -> str:
    s = os.getenv("MOYI_BRIDGE_SECRET") or ""
    if not s:
        raise RuntimeError("MOYI_BRIDGE_SECRET가 .env에 없습니다 (talkhub BRIDGE_SECRET와 동일 값)")
    return s


def fetch_pending(limit: int = 20) -> list[dict]:
    r = requests.get(
        f"{_base()}/bridge/kakao/outbound/pending",
        params={"limit": limit},
        headers={"X-Bridge-Secret": _secret()},
        timeout=15,
    )
    r.raise_for_status()
    data = r.json()
    return data.get("items", data if isinstance(data, list) else [])


def ack(item_id: str, ok: bool, error: str | None = None) -> None:
    requests.post(
        f"{_base()}/bridge/kakao/outbound/{item_id}/ack",
        json={"ok": ok, "error": error},
        headers={"X-Bridge-Secret": _secret()},
        timeout=15,
    ).raise_for_status()


def compose_text(item: dict) -> str:
    sender = (item.get("sender_name") or "").strip()
    content = (item.get("content") or "").strip()
    lines = [f"[MOYI/{sender}] {content}" if sender else content]
    for att in item.get("attachments") or []:
        name = att.get("name") or "첨부"
        url = att.get("url") or ""
        if url:
            lines.append(f"📎 {name}: {url}")
    return "\n".join(x for x in lines if x).strip()


def open_room_by_name(room_name: str) -> bool:
    """카카오톡 메인 창에서 방 이름 검색으로 방을 연다."""
    focus_kakaotalk()
    pyautogui.hotkey("ctrl", "f")
    time.sleep(0.6)
    pyperclip.copy(room_name)
    pyautogui.hotkey("ctrl", "v")
    time.sleep(1.2)  # 검색 결과 대기
    pyautogui.press("enter")  # 첫 결과 열기
    time.sleep(1.5)
    return True


def send_text_to_open_room(text: str) -> None:
    """현재 열린 채팅방 입력란에 텍스트 붙여넣고 전송."""
    pyperclip.copy(text)
    pyautogui.hotkey("ctrl", "v")
    time.sleep(0.4)
    pyautogui.press("enter")
    time.sleep(0.6)
    pyautogui.press("esc")  # 방 창 닫기 (다음 발송 간섭 방지)
    time.sleep(0.3)


def send_item(item: dict) -> None:
    room = (item.get("external_room_id") or "").strip()
    if not room:
        raise RuntimeError("external_room_id 없음")
    text = compose_text(item)
    if not text:
        raise RuntimeError("발송할 내용 없음")
    open_room_by_name(room)
    send_text_to_open_room(text)


def run_worker() -> None:
    """폴링 루프 — main.py moyi-worker 로 실행."""
    print(f"[moyi-outbound] 시작 — {_base()} (폴링 {POLL_INTERVAL_SEC}s)")
    _secret()  # 시크릿 미설정이면 즉시 종료
    while True:
        try:
            items = fetch_pending()
        except Exception as e:  # 서버/네트워크 오류 — 다음 주기에 재시도
            print(f"[moyi-outbound] pending 조회 실패: {e}")
            time.sleep(POLL_INTERVAL_SEC)
            continue
        if items:
            print(f"[moyi-outbound] 대기 {len(items)}건")
        for item in items:
            item_id = item.get("id")
            try:
                send_item(item)
                ack(item_id, True)
                print(f"[moyi-outbound] 발송 OK → {item.get('external_room_id')} ({item_id})")
            except Exception as e:
                # 실패 ack — 서버가 attempts를 올리고 5회 초과 시 held로 보류
                print(f"[moyi-outbound] 발송 실패 ({item_id}): {e}")
                try:
                    ack(item_id, False, str(e)[:300])
                except Exception as e2:
                    print(f"[moyi-outbound] 실패 ack도 실패: {e2}")
            time.sleep(SEND_COOLDOWN_SEC)
        time.sleep(POLL_INTERVAL_SEC)
