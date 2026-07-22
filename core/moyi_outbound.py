"""MOYI→카톡 발송 워커 (이슈 ⑦) — 회사별 연결(트랙1 P2).

MOYI(talkhub) 서버의 회사별 KakaoOutbox 큐를 폴링해서 카카오톡 PC로 발송하고 ack한다.
카톡→MOYI 수신(inbound)도 이 회사 시크릿으로 보낸다.

계약 (talkhub 서버 /kakao/agent/*, 회사별 시크릿 = 헤더 X-Company-Secret):
- GET  {SERVER}/kakao/agent/pending?limit=20
  → {"items": [{id, external_room_id, content, sender_name, attachments[{name,url,mime}], attempts}]}
- POST {SERVER}/kakao/agent/ack/{id}  {"ok": true} 또는 {"ok": false, "error": "..."}
  → at-least-once: ack 전까지 같은 항목이 재노출되므로 발송 성공 후 반드시 ack.
- POST {SERVER}/kakao/agent/inbound  {external_room_id, sender_name, content, attachments}
  → 카톡방(제목)이 이 회사의 승인·import 연결일 때만 MOYI 방에 게시.

발송 방식: 카카오톡 메인 창에서 Ctrl+F 검색 → 방 이름(external_room_id=카톡방 제목) 입력 → Enter로 방 열기
→ 입력란 클릭 → 클립보드 붙여넣기 → Enter. 첨부는 URL을 본문에 병기(파일 자동 업로드는 후속).

.env (설치파일이 자동 작성):
- MOYI_SERVER 또는 MOYI_API_BASE   (기본 https://api.nowlink.kr)
- MOYI_BRIDGE_SECRET               (회사 전용 시크릿 = 연결코드 교환값. X-Company-Secret로 전송)
"""
from __future__ import annotations

import os
import time

import pyautogui
import pyperclip
import requests

from core.kakao_search import replace_room_search
from core.window_detector import activate_kakaotalk, switch_to_chat_tab

POLL_INTERVAL_SEC = 10
SEND_COOLDOWN_SEC = 2.0


def _base() -> str:
    return (os.getenv("MOYI_SERVER") or os.getenv("MOYI_API_BASE") or "https://api.nowlink.kr").rstrip("/")


def _secret() -> str:
    s = os.getenv("MOYI_BRIDGE_SECRET") or ""
    if not s:
        raise RuntimeError("MOYI_BRIDGE_SECRET가 .env에 없습니다 (회사 연결코드로 교환한 회사 전용 시크릿)")
    return s


def _headers() -> dict:
    return {"X-Company-Secret": _secret()}


def fetch_pending(limit: int = 20) -> list[dict]:
    r = requests.get(
        f"{_base()}/kakao/agent/pending",
        params={"limit": limit},
        headers=_headers(),
        timeout=15,
    )
    r.raise_for_status()
    data = r.json()
    return data.get("items", data if isinstance(data, list) else [])


def ack(item_id: str, ok: bool, error: str | None = None) -> None:
    requests.post(
        f"{_base()}/kakao/agent/ack/{item_id}",
        json={"ok": ok, "error": error},
        headers=_headers(),
        timeout=15,
    ).raise_for_status()


def push_inbound(external_room_id: str, sender_name: str, content: str, attachments: list | None = None) -> dict | None:
    """카톡 → MOYI. 이 회사의 승인·import 연결 방에만 게시(서버가 검증). 매핑 없으면 404(무시)."""
    try:
        r = requests.post(
            f"{_base()}/kakao/agent/inbound",
            json={"external_room_id": external_room_id, "sender_name": sender_name,
                  "content": content, "attachments": attachments or []},
            headers=_headers(),
            timeout=15,
        )
        if r.status_code == 404:
            return None  # 이 회사에 연결·승인된 방이 아님 — 조용히 무시
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"[moyi-inbound] 전송 실패: {e}")
        return None


def compose_text(item: dict) -> str:
    # content는 서버가 이미 평문 변환(발신자명 + ↳ 본문). 태그 재부착 없이 그대로 사용.
    content = (item.get("content") or "").strip()
    lines = [content] if content else []
    for att in item.get("attachments") or []:
        name = att.get("name") or "첨부"
        url = att.get("url") or ""
        if url:
            lines.append(f"📎 {name}: {url}")
    return "\n".join(x for x in lines if x).strip()


def open_room_by_name(room_name: str) -> bool:
    """카카오톡 메인 창에서 방 이름 검색으로 방을 연다."""
    window = activate_kakaotalk()
    switch_to_chat_tab(window)
    replace_room_search(window, room_name)
    time.sleep(0.4)  # 검색 결과 안정화
    # Enter only selects the result inside recent KakaoTalk versions. An exact
    # search followed by a double-click opens the one visible result as a
    # separate window, which safe_worker_room verifies before any I/O.
    pyautogui.doubleClick(
        window.left + int(window.width * 0.60),
        window.top + 145,
        interval=0.15,
    )
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
