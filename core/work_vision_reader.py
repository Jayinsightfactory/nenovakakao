"""
카카오워크 채팅창 Claude Vision 메시지 추출.

봇 API 가 메시지 읽기를 안 줘서 워크→카톡 자동 양방향이 막혔던 문제 해결책.
KW 창을 캡처해 Claude Opus 가 메시지(발신자/시각/내용)를 JSON 으로 추출 → 우리
state tracker 가 새 메시지만 골라 카톡으로 포워딩.

설계:
  - find_kakaowork_window: KW 메인창 hwnd (제목 "카카오워크" 기준)
  - capture_chat_panel: hwnd 영역을 PNG 로 저장 (우측 채팅 패널 / 또는 전창)
  - extract_messages: 이미지 → Opus → list[dict] (sender/time/content/has_image)
  - read_new_messages(state): 위 결과를 hash 기준 신규 필터링, state 갱신

참고: 모델은 room_scanner 와 동일 claude-opus-4-7 (한글 정확도 검증됨).
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=True)

CLAUDE_MODEL = "claude-opus-4-7"
ROOT = Path(__file__).resolve().parent.parent
CAPTURES = ROOT / "captures"
KW_TITLE = "카카오워크"

PROMPT = """이 이미지는 카카오워크 데스크톱 앱의 채팅창 화면입니다.

오른쪽 채팅 패널에 보이는 모든 채팅 메시지를 위에서 아래 순서로 JSON 배열로 추출해주세요.
각 메시지 항목 필드:
- "sender": 발신자 이름 (정확히 표시된 그대로)
- "time": 표시된 시각 (예: "오전 10:04", "오후 2:30"). 시각이 안 보이면 "".
- "content": 메시지 본문 텍스트 (이미지/파일만 있으면 "[사진]"·"[파일: 이름]" 표기)
- "has_image": 메시지에 이미지 첨부 boolean
- "is_system": 시스템 메시지(입장/나감/공지) 여부 boolean

주의사항:
- 광고/배너/사이드바/입력창은 제외
- 같은 발신자의 연속 메시지가 묶여 있어도 각 메시지를 별도 항목으로
- 시각이 메시지 그룹의 마지막에만 표시되는 경우, 그 그룹의 모든 메시지에 같은 시각 부여
- 반드시 JSON 배열만 반환 (코드블록·다른 텍스트 없이)
"""


def _client():
    import anthropic
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY 미설정")
    return anthropic.Anthropic(api_key=key)


def find_kakaowork_window() -> int | None:
    """카카오워크 메인창 hwnd (정확 제목 '카카오워크', visible 우선)."""
    import win32gui
    vis: list[int] = []
    hidden: list[int] = []
    def _f(h, _):
        try:
            if win32gui.GetWindowText(h) == KW_TITLE:
                (vis if win32gui.IsWindowVisible(h) else hidden).append(h)
        except Exception:
            pass
    win32gui.EnumWindows(_f, None)
    if vis:
        return vis[0]
    return hidden[0] if hidden else None


def _minimize_kw_separate_windows(main_hwnd: int) -> int:
    """KW 분리 채팅창(메인 '카카오워크' 외 KW 프로세스의 보조 창)을 최소화.

    분리창이 룸리스트 위에 떠 가리면 Vision 이 방이름을 잘라 읽는다(브릿지 오작동).
    메인창과 같은 프로세스(pid)이면서 제목이 '카카오워크'가 아닌 visible 창을 최소화.
    반환: 최소화한 창 수.
    """
    import win32con
    import win32gui
    import win32process
    try:
        _, main_pid = win32process.GetWindowThreadProcessId(main_hwnd)
    except Exception:
        return 0
    targets: list[int] = []

    def _f(h, _):
        try:
            if h == main_hwnd or not win32gui.IsWindowVisible(h):
                return
            _, pid = win32process.GetWindowThreadProcessId(h)
            if pid != main_pid:
                return
            t = win32gui.GetWindowText(h) or ""
            if t == KW_TITLE or not t:
                return
            r = win32gui.GetWindowRect(h)
            if (r[2] - r[0]) < 150 or (r[3] - r[1]) < 150:
                return  # 작은 토스트/위젯 제외
            targets.append(h)
        except Exception:
            pass

    win32gui.EnumWindows(_f, None)
    for h in targets:
        try:
            win32gui.ShowWindow(h, win32con.SW_MINIMIZE)
        except Exception:
            pass
    if targets:
        time.sleep(0.3)
    return len(targets)


def capture_chat_panel(hwnd: int, out_path: Path | None = None,
                       *, right_panel_only: bool = True) -> Path:
    """KW 창 캡처. right_panel_only=True 면 좌측 사이드바 제외(우측 채팅만)."""
    import win32gui
    from PIL import ImageGrab
    if not win32gui.IsWindow(hwnd):
        raise RuntimeError("KW 창 hwnd 무효")
    # 분리 채팅창이 룸리스트를 가리지 않도록 먼저 최소화
    try:
        n = _minimize_kw_separate_windows(hwnd)
        if n:
            print(f"  [WORK-VISION] KW 분리창 {n}개 최소화", flush=True)
    except Exception:
        pass
    # 전면화 (간단)
    try:
        win32gui.SetForegroundWindow(hwnd)
        time.sleep(0.3)
    except Exception:
        pass
    l, t, r, b = win32gui.GetWindowRect(hwnd)
    if right_panel_only:
        # 좌측 사이드바 폭 ≈ 320px (KW 표준)
        l = min(l + 320, r - 200)
    CAPTURES.mkdir(parents=True, exist_ok=True)
    if out_path is None:
        out_path = CAPTURES / f"kw_chat_{int(time.time()*1000)}.png"
    img = ImageGrab.grab(bbox=(l, t, r, b))
    img.save(out_path)
    return out_path


def _parse_json_array(raw: str) -> list[dict]:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    # 첫 [ 부터 마지막 ] 까지 (방어적 추출)
    a = raw.find("[")
    b = raw.rfind("]")
    if a >= 0 and b > a:
        raw = raw[a:b+1]
    try:
        v = json.loads(raw)
        return v if isinstance(v, list) else []
    except Exception:
        return []


def extract_messages(image_path: Path, *, max_retries: int = 2) -> list[dict]:
    """이미지 → Opus → 메시지 dict 리스트. 실패 시 [] 반환."""
    img_b64 = base64.standard_b64encode(image_path.read_bytes()).decode()
    cli = _client()
    last_err = ""
    for attempt in range(max_retries + 1):
        try:
            msg = cli.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=4096,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "image",
                         "source": {"type": "base64", "media_type": "image/png", "data": img_b64}},
                        {"type": "text", "text": PROMPT},
                    ],
                }],
            )
            txt = msg.content[0].text
            arr = _parse_json_array(txt)
            return arr
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
            if "rate" in last_err.lower() or "429" in last_err:
                time.sleep(10 * (attempt + 1))
            elif attempt < max_retries:
                time.sleep(2)
            else:
                print(f"  [WORK-VISION] Opus 호출 실패: {last_err}", flush=True)
    return []


def _msg_hash(m: dict) -> str:
    s = "|".join((m.get("sender", ""), m.get("time", ""), m.get("content", "")[:120]))
    return hashlib.md5(s.encode("utf-8", errors="ignore")).hexdigest()


def filter_new_messages(messages: list[dict], seen: set[str]) -> tuple[list[dict], set[str]]:
    """seen(이전 사이클 해시들) 대비 새 메시지만 골라 반환 + 갱신된 seen."""
    out = []
    new_seen = set(seen)
    for m in messages:
        if not (m.get("content") or m.get("has_image")):
            continue
        h = _msg_hash(m)
        if h in new_seen:
            continue
        out.append(m)
        new_seen.add(h)
    return out, new_seen


def read_new_messages_once(state_seen: set[str] | None = None) -> tuple[list[dict], set[str], Path | None]:
    """1회 캡처+추출+신규필터. 반환: (new_messages, updated_seen, capture_path)."""
    hwnd = find_kakaowork_window()
    if not hwnd:
        print("[WORK-VISION] KW 창 못 찾음", flush=True)
        return ([], state_seen or set(), None)
    try:
        cap = capture_chat_panel(hwnd)
    except Exception as e:
        print(f"[WORK-VISION] 캡처 실패: {e}", flush=True)
        return ([], state_seen or set(), None)
    msgs = extract_messages(cap)
    new, seen = filter_new_messages(msgs, state_seen or set())
    return (new, seen, cap)


# ─────────────────────────────────────────────────────────
# 룸리스트(채팅방 목록 뷰) 추출 — 새 메시지 감지에 사용
# ─────────────────────────────────────────────────────────

ROOM_LIST_PROMPT = """이 이미지는 카카오워크 데스크톱 앱의 채팅방 목록 화면입니다.
각 채팅방 행을 위에서 아래 순서로 JSON 배열로 추출:
- "room": 방 이름 (정확히 표시 그대로, 끝의 멤버수 제외)
- "members": 방이름 옆 작은 숫자(멤버수). 없으면 0
- "preview": 방이름 아래 줄의 마지막 메시지 미리보기 텍스트. 없으면 ""
- "time": 오른쪽 시각/날짜("오전 9:40", "어제" 등). 없으면 ""
- "unread": 시각 아래 파란 동그라미 안 숫자. 없으면 0. "300+" 같으면 300

광고/배너/사이드바 아이콘은 제외. JSON 배열만, 코드블록·다른 텍스트 없이."""


def read_room_list_state(*, capture_path: Path | None = None) -> tuple[list[dict], Path | None]:
    """KW 룸리스트 뷰를 캡처+추출. 반환: (rows, capture_path).
    rows = [{room, members, preview, time, unread}, ...]
    """
    hwnd = find_kakaowork_window()
    if not hwnd:
        print("[WORK-VISION] KW 창 못 찾음", flush=True)
        return ([], None)
    try:
        cap = capture_chat_panel(hwnd, capture_path, right_panel_only=False)
    except Exception as e:
        print(f"[WORK-VISION] 캡처 실패: {e}", flush=True)
        return ([], None)
    img_b64 = base64.standard_b64encode(cap.read_bytes()).decode()
    cli = _client()
    try:
        m = cli.messages.create(
            model=CLAUDE_MODEL, max_tokens=4096,
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64",
                                              "media_type": "image/png", "data": img_b64}},
                {"type": "text", "text": ROOM_LIST_PROMPT},
            ]}],
        )
        rows = _parse_json_array(m.content[0].text)
        return (rows, cap)
    except Exception as e:
        print(f"[WORK-VISION] 룸리스트 추출 실패: {e}", flush=True)
        return ([], cap)


def diff_room_list(prev: list[dict], curr: list[dict]) -> list[dict]:
    """이전·현재 룸리스트 비교 → preview 가 바뀐 방(=새 메시지 도착) 반환."""
    by_room_prev = {r.get("room", ""): r for r in prev if r.get("room")}
    changed = []
    for r in curr:
        name = r.get("room", "")
        if not name:
            continue
        p = by_room_prev.get(name)
        if p is None:
            # 새로 보이는 방 — 처음 보면 알릴지 선택
            changed.append({**r, "_kind": "new_room"})
            continue
        if (r.get("preview") or "") != (p.get("preview") or ""):
            changed.append({**r, "_kind": "preview_changed",
                            "_prev_preview": p.get("preview", "")})
    return changed
