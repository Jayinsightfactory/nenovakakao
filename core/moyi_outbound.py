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


def _exact_room_windows(title: str) -> list:
    import pygetwindow as gw
    return [w for w in gw.getAllWindows()
            if w.visible and w.title == title and w.width > 300 and w.height > 300]


def open_room_verified(room_name: str, attempts: int = 2, timeout: float = 6.0) -> int:
    """방을 열고, 정확히 그 제목의 창이 하나 뜰 때까지 기다린 뒤 앞으로 가져온다.

    고정 대기(1.5초) 뒤 바로 제목을 검사하면 카톡이 느린 아침에 메인 창('카카오톡')만 보여
    발송이 막혔다(2026-11-10~13 일일 보고 실패). 여기서는 아무것도 입력하지 않으므로
    검색·열기만 다시 시도해도 안전하다. 끝내 못 열면 RuntimeError.
    """
    from core.safe_worker_room import open_unique_exact_room
    last = "창 없음"
    for attempt in range(max(1, attempts)):
        if not _exact_room_windows(room_name):
            open_room_by_name(room_name)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            found = _exact_room_windows(room_name)
            if len(found) == 1:
                return open_unique_exact_room(room_name, allow_main_activation=False)
            if len(found) > 1:
                raise RuntimeError(f"방 '{room_name}' 창이 {len(found)}개: 발송 중단")
            time.sleep(0.3)
        try:
            import win32gui
            last = win32gui.GetWindowText(win32gui.GetForegroundWindow()) or "제목 없는 창"
        except Exception:
            pass
    raise RuntimeError(f"방 '{room_name}'을(를) 열지 못함 - 앞에 떠 있는 창: '{last}' (카톡 잠금·로그아웃 여부 확인)")


class SendLimitExceeded(RuntimeError):
    """재시도 금지 — 한도 초과는 다음 날까지 같은 결과."""


_LIMIT_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "send_limits.json")
DAILY_LIMIT_PER_ROOM = int(os.getenv("KAKAO_SEND_LIMIT_ROOM", "30"))
DAILY_LIMIT_TOTAL = int(os.getenv("KAKAO_SEND_LIMIT_TOTAL", "200"))


def _limits_load() -> dict:
    import json
    from datetime import datetime
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        with open(_LIMIT_FILE, encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        d = {}
    if d.get("date") != today:
        d = {"date": today, "total": 0, "rooms": {}}
    return d


def check_send_limit(room: str) -> None:
    """방별·일별 발송 상한(파일 영속 — 재시작해도 유지). 초과 시 SendLimitExceeded."""
    d = _limits_load()
    if d["total"] >= DAILY_LIMIT_TOTAL:
        raise SendLimitExceeded(f"일일 전체 발송 한도 {DAILY_LIMIT_TOTAL}건 초과")
    if d["rooms"].get(room, 0) >= DAILY_LIMIT_PER_ROOM:
        raise SendLimitExceeded(f"방 '{room}' 일일 발송 한도 {DAILY_LIMIT_PER_ROOM}건 초과")


def record_send(room: str) -> None:
    import json
    d = _limits_load()
    d["total"] += 1
    d["rooms"][room] = d["rooms"].get(room, 0) + 1
    os.makedirs(os.path.dirname(_LIMIT_FILE), exist_ok=True)
    tmp = _LIMIT_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False)
    os.replace(tmp, _LIMIT_FILE)


def verify_open_room(expected_title: str) -> int:
    """발송 직전 검증: 앞에 떠 있는 방 창 제목이 목표와 정확히 같아야 함. 다르면 Enter 없이 중단(오발송 방지)."""
    import win32con
    import win32gui
    fg = win32gui.GetForegroundWindow()
    root = win32gui.GetAncestor(fg, win32con.GA_ROOTOWNER) if fg else 0
    titles = {win32gui.GetWindowText(h) for h in (fg, root) if h}
    if expected_title not in titles:
        raise RuntimeError(f"대상 방 불일치 - 기대 '{expected_title}', 실제 {sorted(t for t in titles if t)}: 발송 중단")
    return root or fg


def send_text_to_open_room(text: str, expected_title: str | None = None) -> None:
    """현재 열린 채팅방 입력란에 텍스트 붙여넣고 전송. expected_title 주면 제목 검증 후에만 붙여넣기."""
    if expected_title:
        verify_open_room(expected_title)
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
    check_send_limit(room)
    open_room_by_name(room)
    send_text_to_open_room(text, expected_title=room)
    record_send(room)


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
            except SendLimitExceeded as e:
                print(f"[moyi-outbound] 한도 초과 — 오늘 발송 중단 ({item_id}): {e}")
                break  # ack 안 함 → 내일 다시 노출(소진 방지)
            except Exception as e:
                # 실패 ack — 서버가 attempts를 올리고 5회 초과 시 held로 보류
                print(f"[moyi-outbound] 발송 실패 ({item_id}): {e}")
                try:
                    ack(item_id, False, str(e)[:300])
                except Exception as e2:
                    print(f"[moyi-outbound] 실패 ack도 실패: {e2}")
            time.sleep(SEND_COOLDOWN_SEC)
        time.sleep(POLL_INTERVAL_SEC)
