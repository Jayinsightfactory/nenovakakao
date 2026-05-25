"""
카카오워크 다중 방 라우팅 시스템

카카오톡 방과 1:1로 매칭되는 카카오워크 방을 생성하고,
메시지를 해당 방으로 라우팅한다.

Bot API 사용 (conversations.open + messages.send)
"""
from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env", override=True)

DATA_DIR = Path(__file__).parent.parent / "data"
ROOM_MAP_FILE = DATA_DIR / "room_mapping.json"
DETECTED_FILE = DATA_DIR / "rooms_detected.json"

# 관리자 유저 ID (임재용 - dlaww584@gmail.com)
# 진짜 관리자 user_id (CLAUDE.md). 이전 11826656 은 stale 계정.
ADMIN_USER_ID = 11854018

API_BASE = "https://api.kakaowork.com/v1"


def _headers() -> dict:
    token = os.getenv("KAKAOWORK_BOT_TOKEN")
    if not token:
        raise RuntimeError("KAKAOWORK_BOT_TOKEN이 .env에 설정되지 않았습니다.")
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


def _load_room_mapping() -> dict[str, str]:
    """카톡방→워크방 매핑 로드. {카톡방이름: 워크conversation_id}"""
    if ROOM_MAP_FILE.exists():
        with open(ROOM_MAP_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_room_mapping(mapping: dict[str, str]):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(ROOM_MAP_FILE, "w", encoding="utf-8") as f:
        json.dump(mapping, f, ensure_ascii=False, indent=2)


def create_mirror_room(kakaotalk_name: str, user_ids: list[int] | None = None) -> str:
    """
    카카오톡 방 이름에 대응하는 카카오워크 방을 생성.

    Returns:
        생성된 conversation_id
    """
    if user_ids is None:
        user_ids = [ADMIN_USER_ID]

    payload = {
        "user_ids": user_ids,
        "conversation_name": f"[미러] {kakaotalk_name}",
    }
    resp = requests.post(
        f"{API_BASE}/conversations.open",
        headers=_headers(),
        json=payload,
        timeout=10,
    )
    data = resp.json()

    if not data.get("success"):
        raise RuntimeError(f"방 생성 실패: {data}")

    conv_id = str(data["conversation"]["id"])
    return conv_id


def verify_mirror_room(conv_id: str) -> bool:
    """
    conversation_id가 실제로 유효한지 확인.
    conversations.info는 Bot API에 없음 → 전체 봇 대화 목록 조회로 검증.

    페이징 안전장치: cursor 가 끝날 때까지 끝까지 순회. 봇이 300+ 방에 있으면
    이전 3-page 하드 캡으로 false-negative → ensure_mirror_for_rooms 가 중복 미러 방
    재생성하던 회귀 차단. 무한루프 방지를 위해 page_max(50, 5000방 = 100/page) 상한.
    """
    target = str(conv_id)
    try:
        cursor: str | None = None
        page_max = 50
        for _page in range(page_max):
            params = {"limit": 100}
            if cursor:
                params["cursor"] = cursor
            resp = requests.get(
                f"{API_BASE}/conversations.list",
                headers=_headers(),
                params=params,
                timeout=15,
            )
            data = resp.json()
            if not data.get("success"):
                return False
            for c in data.get("conversations", []):
                if str(c.get("id")) == target:
                    return True
            cursor = data.get("cursor")
            if not cursor:
                return False
        # page_max 도달 — 5000+ 방 시나리오. 결정 보류 후 caller 책임.
        return False
    except Exception:
        return False


_conv_cache = {"ids": None, "ts": 0}


def _get_all_bot_conv_ids(force: bool = False) -> set[str]:
    """봇이 속한 모든 방 id 캐싱 (60초)."""
    if not force and _conv_cache["ids"] and time.time() - _conv_cache["ts"] < 60:
        return _conv_cache["ids"]
    ids = set()
    cursor = None
    try:
        for _ in range(5):
            params = {"limit": 100}
            if cursor:
                params["cursor"] = cursor
            r = requests.get(f"{API_BASE}/conversations.list", headers=_headers(), params=params, timeout=15)
            d = r.json()
            for c in d.get("conversations", []):
                ids.add(str(c.get("id")))
            cursor = d.get("cursor")
            if not cursor:
                break
    except Exception:
        pass
    _conv_cache["ids"] = ids
    _conv_cache["ts"] = time.time()
    return ids


def verify_mirror_room_cached(conv_id: str) -> bool:
    """캐시 기반 빠른 검증."""
    return str(conv_id) in _get_all_bot_conv_ids()


def ensure_mirror_for_rooms(room_names: list[str]) -> dict:
    """
    주어진 카톡방 리스트에 대해 워크 미러 방이 모두 존재하도록 보장.
    - 매핑 없으면 생성
    - 매핑 있는데 유효성 검증 실패 시 재생성
    - 이미 유효한 건 skip

    Returns:
        {"mapping": 전체매핑, "created": 신규생성수, "revalidated": 재생성수, "invalid": 여전히유효X수}
    """
    mapping = _load_room_mapping()
    created = 0
    revalidated = 0
    invalid = 0

    # 한 번만 전체 봇 방 목록 조회 (캐시)
    all_ids = _get_all_bot_conv_ids(force=True)

    for name in room_names:
        conv_id = mapping.get(name)
        if conv_id and str(conv_id) in all_ids:
            continue  # 이미 유효
        if conv_id:
            print(f"  [재생성] {name} (기존 {conv_id} 없음)", flush=True)
            try:
                conv_id = create_mirror_room(name)
                mapping[name] = conv_id
                revalidated += 1
            except Exception as e:
                print(f"  [ERROR] {name} 재생성 실패: {e}", flush=True)
                invalid += 1
        else:
            # 신규 생성
            try:
                conv_id = create_mirror_room(name)
                mapping[name] = conv_id
                created += 1
                print(f"  [생성] {name} → {conv_id}", flush=True)
            except Exception as e:
                print(f"  [ERROR] {name} 생성 실패: {e}", flush=True)
                invalid += 1

    _save_room_mapping(mapping)
    return {
        "mapping": mapping,
        "created": created,
        "revalidated": revalidated,
        "invalid": invalid,
    }


def create_all_mirror_rooms() -> dict[str, str]:
    """
    rooms_detected.json의 모든 방에 대해 카카오워크 미러 방 생성.
    이미 매핑된 방은 스킵.

    Returns:
        전체 매핑 딕셔너리
    """
    if not DETECTED_FILE.exists():
        raise RuntimeError("rooms_detected.json이 없습니다. 먼저 scan을 실행하세요.")

    with open(DETECTED_FILE, encoding="utf-8") as f:
        rooms = json.load(f)

    mapping = _load_room_mapping()
    created = 0

    for room in rooms:
        name = room["name"]
        if name in mapping:
            print(f"  [SKIP] {name} - already mapped")
            continue

        try:
            conv_id = create_mirror_room(name)
            mapping[name] = conv_id
            print(f"  [OK] {name} → {conv_id}")
            created += 1
        except Exception as e:
            print(f"  [ERROR] {name}: {e}")

    _save_room_mapping(mapping)
    print(f"\n총 {created}개 방 생성, {len(mapping)}개 매핑 완료")
    return mapping


def send_reply_button(kakaotalk_name: str) -> bool:
    """미러방에 [📤 카톡 답장] 버튼 메시지 1개 송신 (양방향 챗봇 트리거).

    사용자가 이 버튼을 누르면 카카오워크가 reactive Request URL 호출 →
    모달 → Callback URL → core.kakao_win32.send_message_to_room 으로 카톡 송신.
    (core/kakaowork_reactive.py 참조)

    value 는 'room=<카톡방이름>' — callback 에서 어느 카톡 방으로 보낼지 식별.
    """
    mapping = _load_room_mapping()
    conv_id = mapping.get(kakaotalk_name)
    if not conv_id:
        normalized = kakaotalk_name.replace(" ", "")
        for k, v in mapping.items():
            if k.replace(" ", "") == normalized:
                conv_id = v
                break
    if not conv_id:
        return False
    payload = {
        "conversation_id": conv_id,
        "text": f"💬 '{kakaotalk_name}' 방으로 답장",
        "blocks": [
            {
                "type": "button",
                "text": "📤 카톡 답장",
                "action_type": "call_modal",
                "value": f"room={kakaotalk_name}",
            }
        ],
    }
    try:
        resp = requests.post(
            f"{API_BASE}/messages.send",
            headers=_headers(), json=payload, timeout=10,
        )
        return resp.json().get("success", False)
    except Exception as e:
        print(f"  [REPLY-BTN] {kakaotalk_name} 버튼 송신 실패: {e}", flush=True)
        return False


def send_to_mirror_room(kakaotalk_name: str, text: str, max_length: int = 3000) -> bool:
    """
    카카오톡 방 이름에 대응하는 카카오워크 방에 메시지 전송.
    매핑이 없으면 관리자전용톡방(Webhook)으로 폴백.

    Args:
        kakaotalk_name: 카카오톡 방 이름
        text: 전송할 텍스트
        max_length: 최대 메시지 길이 (초과 시 잘라서 전송)
    """
    mapping = _load_room_mapping()
    conv_id = mapping.get(kakaotalk_name)

    if not conv_id:
        # 매핑 없으면 Webhook 폴백
        from core.kakaowork_notifier import notify_new_message
        return notify_new_message(kakaotalk_name, text)

    # 메시지 길이 제한 — suffix 추가 후에도 max_length 이하가 되도록 본문을 먼저 잘라냄
    if len(text) > max_length:
        omitted = len(text) - max_length
        suffix = f"\n... ({omitted}자 생략)"
        text = text[: max_length - len(suffix)] + suffix

    # 헤더 추가
    full_text = (
        f"[카톡 미러] {kakaotalk_name}\n"
        f"시각: {datetime.now().strftime('%H:%M:%S')}\n"
        f"---\n"
        f"{text}"
    )

    payload = {
        "conversation_id": conv_id,
        "text": full_text,
    }

    try:
        resp = requests.post(
            f"{API_BASE}/messages.send",
            headers=_headers(),
            json=payload,
            timeout=10,
        )
        data = resp.json()
        return data.get("success", False)
    except Exception as e:
        print(f"[ERROR] 미러 전송 실패 ({kakaotalk_name}): {e}")
        return False


# ─── 개별 메시지 파싱 + 전송 ───

# 카톡 메시지 패턴: [발신자] [시각] 내용
_MSG_RE = re.compile(r"^\[(.+?)\]\s*\[(.+?)\]\s*(.+)$")

# 날짜 구분선
_DATE_RE = re.compile(r"^-+\s*\d{4}년.*-+$")

# 시스템 메시지 (무시)
_SYS_MSGS = {"메시지가 삭제되었습니다.", "님이 들어왔습니다.", "님이 나갔습니다."}

# ── 사진 메시지 판정 ──
# 카톡 저장 포맷에서 사진 메시지는 다음 형태로 나타난다:
#   "[사진]"            — 단일 사진
#   "사진 3장"           — 묶음 N장
#   "[Photo]" / "Photo N" — 영어 버전
# 메시지 본문이 이 토큰과 정확히 일치할 때만 사진 메시지로 판정 (오탐 방지).
# 단일 사진: "[사진]", "사진", "[Photo]", "Photo" (대괄호/공백 모두 허용)
_PHOTO_SINGLE_RE = re.compile(r"^\s*\[?\s*(?:사진|Photo)\s*\]?\s*$", re.IGNORECASE)
# 묶음 사진: "사진 3장", "Photos 3", "[사진 3장]"
_PHOTO_BATCH_RE = re.compile(r"^\s*\[?\s*(?:사진\s*(\d+)\s*장|Photos?\s*(\d+))\s*\]?\s*$", re.IGNORECASE)


def _photo_info(content: str) -> tuple[bool, int]:
    """
    메시지 본문이 사진 메시지인지, 몇 장인지 반환.

    Returns:
        (is_photo_msg, count)
        - 일반 텍스트 → (False, 0)
        - "[사진]" / "[Photo]" → (True, 1)
        - "사진 N장" → (True, N)
    """
    if not content:
        return False, 0
    first_line = content.strip().splitlines()[0].strip()
    if _PHOTO_SINGLE_RE.match(first_line):
        return True, 1
    m = _PHOTO_BATCH_RE.match(first_line)
    if m:
        n = int(m.group(1) or m.group(2) or 1)
        return True, max(n, 1)
    return False, 0


def parse_delta_to_messages(delta: str) -> list[dict]:
    """
    delta 텍스트를 개별 메시지 단위로 파싱.

    메시지 경계는 카톡 저장 포맷 헤더 `[발신자] [시각] ...`로 구분.
    연속 줄은 이전 메시지 본문에 누적.

    Returns:
        [{"sender": str, "time": str, "content": str,
          "has_photo": bool, "photo_count": int}, ...]
    """
    messages = []
    current = None

    for line in delta.splitlines():
        line = line.strip()
        if not line:
            if current:
                current["content"] += "\n"
            continue

        # 날짜 구분선 스킵
        if _DATE_RE.match(line):
            continue

        # 시스템 메시지 스킵
        if line in _SYS_MSGS:
            continue

        # 헤더 라인 스킵 (카톡 저장 시 붙는 헤더)
        if "님과 카카오톡 대화" in line or "저장한 날짜" in line:
            continue

        m = _MSG_RE.match(line)
        if m:
            # 새 메시지 시작 → 이전 메시지 저장
            if current:
                current["content"] = current["content"].strip()
                if current["content"]:
                    has_p, n = _photo_info(current["content"])
                    current["has_photo"] = has_p
                    current["photo_count"] = n
                    messages.append(current)

            sender, time_str, content = m.groups()
            current = {
                "sender": sender,
                "time": time_str,
                "content": content,
                "has_photo": False,   # 아래 finalize에서 판정
                "photo_count": 0,
            }
        elif current:
            # 이전 메시지의 연속 줄
            current["content"] += "\n" + line

    # 마지막 메시지
    if current:
        current["content"] = current["content"].strip()
        if current["content"]:
            has_p, n = _photo_info(current["content"])
            current["has_photo"] = has_p
            current["photo_count"] = n
            messages.append(current)

    return messages


def count_photo_messages(delta: str) -> int:
    """
    delta에서 실제 사진 메시지 개수를 반환.

    주의: substring `[사진]` 카운트가 아닌 **메시지 경계** 기반.
    묶음 메시지 "사진 3장"은 3으로 계산.
    """
    return sum(m["photo_count"] for m in parse_delta_to_messages(delta))


_BUMP_LAST_TS: dict[str, float] = {}
_BUMP_MIN_INTERVAL_SEC = 1.5
_RATE_LIMITED_UNTIL = 0.0  # 429 발생 시 이 시각까지 모든 _send_single 호출 거부


def _send_single(conv_id: str, text: str, max_retries: int = 3) -> bool:
    """단일 메시지 전송 (내부용).

    - 429 (rate limit): 재시도 금지, 30초 backoff (review 규칙 §4)
    - ConnectionError / Timeout (RemoteDisconnected 등): exponential backoff 재시도
      카카오워크가 대량/긴 메시지에 연결을 끊는 경우 대응 (2026-05-19 RemoteDisconnected 다발).
    """
    global _RATE_LIMITED_UNTIL
    if time.time() < _RATE_LIMITED_UNTIL:
        return False
    payload = {"conversation_id": conv_id, "text": text}
    for attempt in range(max_retries):
        try:
            resp = requests.post(
                f"{API_BASE}/messages.send",
                headers=_headers(),
                json=payload,
                timeout=15,
            )
            if resp.status_code == 429:
                _RATE_LIMITED_UNTIL = time.time() + 30.0
                print("  [RATE-LIMIT] 카카오워크 Bot API 429 — 30초 송신 일시중단", flush=True)
                return False
            return resp.json().get("success", False)
        except (requests.ConnectionError, requests.Timeout) as e:
            wait = 1.5 * (attempt + 1)  # 1.5, 3.0, 4.5
            if attempt < max_retries - 1:
                print(f"  [RETRY] 연결 실패 ({attempt+1}/{max_retries}) {wait:.1f}s 후 재시도: "
                      f"{type(e).__name__}", flush=True)
                time.sleep(wait)
            else:
                print(f"  [ERROR] 연결 실패 (재시도 {max_retries}회 소진): {type(e).__name__}", flush=True)
                return False
        except Exception as e:
            print(f"  [ERROR] 전송 실패: {e}")
            return False
    return False


def _bump(conv_id: str) -> bool:
    """미러 방을 워크 목록 맨 위로 올리는 invisible bump.

    같은 conv_id 에 대해 _BUMP_MIN_INTERVAL_SEC 이내 재호출은 묵음 스킵 →
    한 delta 에 사진 5장 처럼 연속 흐름에서 5회 bump → 1회로 압축.
    Bot API 호출 절약 + 429 위험 감소.
    """
    now = time.time()
    last = _BUMP_LAST_TS.get(conv_id, 0.0)
    if now - last < _BUMP_MIN_INTERVAL_SEC:
        return True  # 최근에 bump 됨 — 조용히 건너뛰고 성공으로 간주
    _BUMP_LAST_TS[conv_id] = now
    return _send_single(conv_id, "⁣")


def send_image_block(conv_id: str, image_url: str) -> bool:
    """
    text URL 단독 전송 → 카카오워크 자동 link preview 카드 (큰 이미지 + 클릭 시 원본 확대).

    이력:
      - (구) text + blocks.image_link 동시 전송 → 인라인 썸네일 클릭 시 '일정등록' UX
        가 떠서 확대 불가 (Bot API image_link 블록의 알려진 한계).
      - (현) blocks.image_link 제거. text URL 단독 → 워크가 자동으로 link preview 카드
        생성, 카드 클릭 시 브라우저로 원본 이미지 열림 (= 확대 가능).

    Args:
        conv_id: 대상 방 conversation_id
        image_url: 공개 접근 가능한 이미지 URL
    """
    payload = {
        "conversation_id": conv_id,
        "text": image_url,
    }
    try:
        resp = requests.post(
            f"{API_BASE}/messages.send",
            headers=_headers(),
            json=payload,
            timeout=15,
        )
        result = resp.json()
        if result.get("success"):
            return True
        print(f"  [ERROR] 이미지 블록 전송 실패: {result}")
        return False
    except Exception as e:
        print(f"  [ERROR] 이미지 블록 전송 예외: {e}")
        return False


def send_image_to_mirror(kakaotalk_name: str, image_url: str, caption: str = "") -> bool:
    """카톡방 이름으로 미러 방 찾아서 이미지만 전송 (caption 무시)."""
    mapping = _load_room_mapping()
    conv_id = mapping.get(kakaotalk_name)
    if not conv_id:
        normalized = kakaotalk_name.replace(" ", "")
        for k, v in mapping.items():
            if k.replace(" ", "") == normalized:
                conv_id = v
                break
    if not conv_id:
        print(f"  [WARN] '{kakaotalk_name}' 미러 매핑 없음")
        return False
    return send_image_block(conv_id, image_url)


def send_delta_interleaved(
    kakaotalk_name: str,
    delta: str,
    photo_files: list | None = None,
    *,
    delay: float = 0.3,
) -> dict:
    """
    카톡 delta의 **시간순**을 보존하며 NV 미러 방에 전송.

    동작:
      - 텍스트 메시지: Bot API messages.send
      - 사진 메시지: Bot API로 "[발신자] [시각] [사진]" 헤더 먼저 전송
                     → 카카오워크 앱에서 Ctrl+T 로 파일 업로드
      - 결과 타임라인: [...텍스트] → [헤더][사진파일] → [텍스트] → [헤더][사진파일] ...

    photo_files 할당 규칙:
      - photo_files는 시간순 리스트로 들어온다고 가정 (서랍 mtime 정렬)
      - 각 사진 메시지의 photo_count만큼 FIFO로 꺼내 배정
      - 부족하면 "다운로드 실패" 표시만 남기고 진행
      - 초과분은 마지막에 trailing batch로 몰아서 업로드

    Args:
        kakaotalk_name: 카톡 방 이름 (room_mapping 키)
        delta: 수집된 신규 텍스트
        photo_files: 다운로드된 사진 경로 리스트 (None이면 사진 없음으로 간주)
        delay: Bot API 호출 간 딜레이 (초)

    Returns:
        {total_messages, text_sent, text_failed,
         photos_uploaded, photos_missing, trailing_uploaded}
    """
    from pathlib import Path

    def _status(msg: str) -> None:
        try:
            from core.status_overlay import get_overlay
            get_overlay().set_status(msg)
        except Exception:
            pass

    _status(f"워크 전송 시작: {kakaotalk_name[:14]}")

    mapping = _load_room_mapping()
    conv_id = mapping.get(kakaotalk_name)
    if not conv_id:
        # 공백 무시 폴백
        normalized = kakaotalk_name.replace(" ", "")
        for k, v in mapping.items():
            if k.replace(" ", "") == normalized:
                conv_id = v
                break
    if not conv_id:
        print(f"  [WARN] {kakaotalk_name}: 매핑 없음 - 스킵")
        return {
            "total_messages": 0, "text_sent": 0, "text_failed": 0,
            "photos_uploaded": 0, "photos_missing": 0, "trailing_uploaded": 0,
        }

    messages = parse_delta_to_messages(delta)
    photo_list = list(photo_files or [])
    photo_iter = iter(photo_list)

    # 멱등 원장: 이미 워크에 보낸 동일 텍스트 메시지는 재전송 안 함
    # (extract_delta 매칭 실패로 대화 전체가 다시 delta로 와도 중복 차단)
    from core.sent_ledger import SentLedger
    _ledger = SentLedger(kakaotalk_name)

    stats = {
        "total_messages": len(messages),
        "text_sent": 0,
        "text_failed": 0,
        "text_skipped": 0,
        "photos_uploaded": 0,
        "photos_missing": 0,
        "trailing_uploaded": 0,
    }

    app_window = None  # lazy: 첫 사진 메시지에서 초기화

    def _ensure_app_window():
        nonlocal app_window
        if app_window is not None:
            return app_window
        try:
            from core.kakaowork_app import find_kakaowork_window
            app_window = find_kakaowork_window()
        except Exception as e:
            print(f"  [ERROR] Kakaowork 앱 없음: {e}")
            app_window = None
        return app_window

    def _click_first_room(window):
        import pyautogui
        pyautogui.click(window.left + 80, window.top + 60)
        time.sleep(1.5)

    def _verify_room(window, expected_label: str) -> bool:
        """카카오워크 헤더 OCR 검증 (Haiku→Opus 자동 fallback, 퍼지 매칭).

        후보군에 `NV##:방이름`/`nv_name`/`NV##` 단독도 포함 (미러방이 NV prefix로 rename된 경우 대응).
        한글 전체 오인식이어도 NV 코드가 읽히면 OK로 판정(강매칭 보조).
        """
        from core.kakaowork_app import (
            _ocr_chat_header, _rooms_match,
            _build_header_candidates, _header_has_nv_code,
        )
        header = _ocr_chat_header(window, expected=expected_label)
        candidates = _build_header_candidates(expected_label)
        ok = any(_rooms_match(header or "", c) for c in candidates)
        if not ok and _header_has_nv_code(header or "", expected_label):
            ok = True
            print(f"  [OCR-NVCODE] 한글 MISMATCH이나 NV 코드 일치 → OK", flush=True)
        print(f"  [OCR] 헤더='{header}' 후보={candidates} → {'OK' if ok else 'MISMATCH'}", flush=True)
        return ok

    def _search_in_kakaowork(window, term: str, *, wrap_mirror: bool = False) -> bool:
        """카카오워크 Ctrl+K 전역 검색으로 방 직접 찾기.

        term은 사용자가 넣고 싶은 검색어 그대로. wrap_mirror=True면 '[미러] ' prefix 자동 추가.
        (후방 호환: 이전엔 항상 '[미러] '를 붙였음. 이제 호출자가 명시)
        """
        import pyautogui
        try:
            import pyperclip
        except ImportError:
            return False
        try:
            # 잔여 다이얼로그 정리
            pyautogui.press("escape")
            time.sleep(0.3)
            # 카카오워크 활성화 보장
            try:
                window.activate()
            except Exception:
                pass
            time.sleep(0.3)
            # 전역 검색 열기 (Ctrl+K)
            pyautogui.hotkey("ctrl", "k")
            time.sleep(1.2)
            query = f"[미러] {term}" if wrap_mirror else term
            pyperclip.copy(query)
            pyautogui.hotkey("ctrl", "v")
            time.sleep(1.5)
            # 첫 결과 선택
            pyautogui.press("enter")
            time.sleep(2.0)
            return True
        except Exception as e:
            print(f"  [SEARCH] 실패: {e}", flush=True)
            return False

    def _ensure_nv_tab(window) -> None:
        """카카오워크 사이드바의 NV(네노바 그룹) 탭으로 전환."""
        from core.vision_clicker import find_and_click
        sidebar_bbox = (window.left, window.top + 40,
                        window.left + 55, window.top + 400)
        find_and_click(
            sidebar_bbox,
            "'N' 또는 'NV' 글자가 있는 원형 아이콘 (카카오워크 사이드바 네노바 그룹 탭). "
            "색상은 파란색/녹색/회색 등 상태에 따라 다를 수 있음. "
            "다른 메뉴(체크/말풍선/그리드/메일/설정 등)는 클릭 금지.",
            tag="kakaowork.nv_tab",
            min_confidence=0.55,
        )
        time.sleep(1.0)

    def _click_and_verify(window, expected_label: str, max_retries: int = 3) -> bool:
        """Vision 기반: NV 탭 → 좌측 패널에서 미러방 행 클릭 → OCR 헤더 검증.

        실패 시 계단식 fallback:
          (a) max_retries 번 vision 재시도 (bump 포함)
          (b) Ctrl+K 전역 검색으로 방 직접 진입 후 헤더 재검증
          반환 False시 호출자가 Computer Use agentic recovery로 위임.
        """
        from core.vision_clicker import find_and_click
        from core.kakaowork_app import _load_nv_mapping

        # NV 코드/이름을 vision 프롬프트에 주입 (NV## prefix로 rename된 방 인식)
        info = (_load_nv_mapping() or {}).get(expected_label) or {}
        nv_code = (info.get("nv_code") or "").strip()
        nv_name = (info.get("nv_name") or "").strip()

        # 1) NV 탭 전환 보장
        _ensure_nv_tab(window)

        # 2) bump → 방 목록 맨 위로 올라옴
        time.sleep(1.0)

        for attempt in range(max_retries + 1):
            # 좌측 방 리스트 영역 (사이드바 폭 50 제외)
            bbox = (window.left + 55, window.top + 50,
                    window.left + 320, window.top + window.height - 30)
            # 미러방 이름은 '[미러] X' 또는 'NV##:X' 또는 'NV##' 형태 가능
            code_hint = f" 또는 '{nv_name}' 또는 '{nv_code}'로 시작" if nv_code else ""
            # 한 글자 차이로 헷갈리는 방 이름들을 명시 — 화면 인식 모델이 비슷한
            # 글자(불량/물량, 수입/수출 등)를 구별하도록 강조. 기존 프롬프트는
            # 수입방 vs 수입(불량 공유방) 만 다루어 "불량 공유방" vs "물량 공유방"
            # 충돌(한 글자 차이) 에 대해 38.6% 인식률 / 14.3% (4/20일) 까지 떨어짐.
            target_desc = (
                f"'[미러] {expected_label}' 텍스트가 표시된 채팅방 행{code_hint}. "
                f"정확히 '{expected_label}'와 **글자 단위로** 일치하는 것만 선택. "
                f"한 글자만 달라도 다른 방이다. 특히 주의할 충돌 사례:\n"
                f"  - '불량 공유방' vs '물량 공유방' (불/물 한 글자 차이)\n"
                f"  - '수입방' vs '수입(불량 공유방)' vs '수입(물량 공유방)'\n"
                f"  - '수입/영업/현장' vs '영업/현장' vs '수입/영업'\n"
                f"비슷하지만 다른 방은 반드시 found=false. 확실하지 않으면 found=false."
            )
            r = find_and_click(
                bbox, target_desc,
                tag=f"send.find_mirror.{expected_label[:20]}",
                min_confidence=0.7,
            )
            if not r.found:
                print(f"  [VISION-RETRY] {attempt+1}/{max_retries+1} - 못찾음, bump 재시도", flush=True)
                _send_single(conv_id, "\u2063")
                time.sleep(2.0)
                continue

            # 클릭 후 헤더 검증
            time.sleep(1.5)
            # 충돌 위험 방(한 글자 차이) 은 confidence 무관하게 헤더 검증 강제 —
            # Vision 이 0.9 이상 확신해도 '불량/물량' 같은 한 글자 차이 오인이 빈번.
            CONFLICT_PRONE = ("불량 공유방", "물량 공유방", "수입/영업", "영업/현장")
            high_risk = any(k in expected_label for k in CONFLICT_PRONE)
            if r.confidence >= 0.9 and not high_risk:
                print(f"  [TRUST-VISION] conf={r.confidence:.2f} ≥ 0.9 → OCR 검증 스킵", flush=True)
                return True
            if _verify_room(window, expected_label):
                return True
            if high_risk:
                print(f"  [VERIFY-FORCED] '{expected_label}' 충돌 위험 방 — 헤더 불일치, 재시도", flush=True)

            print(f"  [VISION-RETRY] {attempt+1}/{max_retries+1} - 클릭 후 헤더 불일치", flush=True)
            _send_single(conv_id, "\u2063")
            time.sleep(2.0)

        # (b) Ctrl+K 전역 검색 fallback — 방이 NV## prefix로 rename된 경우 대응
        search_terms: list[str] = []
        if nv_code:
            search_terms.append(nv_code)            # "NV04" — 가장 안정적 (영문+숫자)
        if nv_name:
            search_terms.append(nv_name)            # "NV04:네노바 수입(불량 공유방)"
        # 마지막: '[미러] 원본이름' (이름이 바뀌지 않은 방 대응)
        search_terms.append(f"[미러] {expected_label}")

        for term in search_terms:
            try:
                print(f"  [SEARCH-FALLBACK] Ctrl+K '{term}'", flush=True)
                if _search_in_kakaowork(window, term, wrap_mirror=False):
                    if _verify_room(window, expected_label):
                        print(f"  [SEARCH-FALLBACK] '{term}' 진입 성공", flush=True)
                        return True
            except Exception as e:
                print(f"  [SEARCH-FALLBACK] '{term}' 예외: {e}", flush=True)
            # 다음 시도 전 검색창 닫기
            try:
                import pyautogui
                pyautogui.press("escape")
                time.sleep(0.3)
            except Exception:
                pass

        return False

    def _quarantine_photos(file_paths):
        """업로드 못 한 사진들을 격리 폴더로 이동 (누적 방지)."""
        from shutil import move
        fail_dir = DATA_DIR / "upload_failed" / time.strftime("%Y%m%d")
        fail_dir.mkdir(parents=True, exist_ok=True)
        for fp in file_paths:
            p = Path(fp)
            try:
                if p.exists():
                    target = fail_dir / p.name
                    if target.exists():
                        target = fail_dir / f"{int(time.time())}_{p.name}"
                    move(str(p), str(target))
            except Exception:
                pass

    def _upload_one(file_path, window) -> bool:
        """파일 업로드 후 성공 시 원본 삭제, 실패 시 격리 폴더로 이동 + 텔레메트리."""
        from core.kakaowork_app import upload_file_to_room
        from shutil import move
        p = Path(file_path)
        err_text = ""
        try:
            ok = bool(upload_file_to_room(p, window))
        except Exception as e:
            print(f"  [ERROR] {p.name} 업로드 실패: {e}")
            err_text = f"{type(e).__name__}: {e}"
            ok = False
        try:
            if ok and p.exists():
                p.unlink()
            elif not ok and p.exists():
                # 실패한 사진은 관리자 확인 위해 격리 보관
                fail_dir = DATA_DIR / "upload_failed" / time.strftime("%Y%m%d")
                fail_dir.mkdir(parents=True, exist_ok=True)
                target = fail_dir / p.name
                if target.exists():
                    target = fail_dir / f"{int(time.time())}_{p.name}"
                move(str(p), str(target))
        except Exception as e:
            print(f"  [CLEANUP] {p.name} 후처리 실패: {e}", flush=True)
        # 텔레메트리: 성공/실패 양쪽 room 컨텍스트로 기록
        try:
            if ok:
                from core.upload_telemetry import log_upload_success
                log_upload_success(room=kakaotalk_name, file_name=p.name)
            else:
                from core.upload_telemetry import log_upload_failure
                log_upload_failure(
                    room=kakaotalk_name, file_name=p.name,
                    step="upload.per_photo",
                    reason=err_text or "upload_file_to_room returned False",
                )
        except Exception:
            pass
        return ok

    total_msgs = len(messages)
    for i, msg in enumerate(messages):
        sender = msg["sender"]
        tstr = msg["time"]

        if msg.get("has_photo") and msg.get("photo_count", 0) > 0:
            _status(f"워크 사진 {i+1}/{total_msgs}")
        else:
            _status(f"워크 텍스트 {i+1}/{total_msgs}")
        _ = msg  # keep mypy happy

        if msg.get("has_photo") and msg.get("photo_count", 0) > 0:
            # ── 사진 메시지: 헤더 먼저 + 파일 업로드 ──
            want = msg["photo_count"]
            photos_for_this: list = []
            for _ in range(want):
                try:
                    photos_for_this.append(next(photo_iter))
                except StopIteration:
                    break

            if not photos_for_this:
                # 다운로드된 파일 없음 — 실패 텍스트로 남김
                fallback = f"[{sender}] [{tstr}] [사진 {want}장 — 다운로드 실패]"
                if _send_single(conv_id, fallback):
                    stats["text_sent"] += 1
                else:
                    stats["text_failed"] += 1
                stats["photos_missing"] += want
                time.sleep(delay)
                continue

            # 사진 전 invisible bump (미러방 목록 맨 위로 — 텍스트 알림 안 남김)
            # _bump() 가 동일 conv_id 의 1.5초 이내 연속 호출을 묵음 스킵 → 다중 사진
            # 처리 시 Bot API 호출 압축 + rate-limit 회피.
            _bump(conv_id)
            time.sleep(delay)

            # 요청 수량보다 적게 다운로드된 경우 부족분을 기록
            shortfall = want - len(photos_for_this)
            if shortfall > 0:
                stats["photos_missing"] += shortfall

            # ═══════════════════════════════════════════════
            # nenovaweb 호스팅 업로드 + image_link 블록 전송 (GUI 자동화 불필요)
            # ═══════════════════════════════════════════════
            try:
                from core.photo_uploader import upload_many as _nw_upload_many
                from core.photo_uploader import delete_from_nenovaweb as _nw_delete
                from core.photo_uploader import _get_client_id as _nw_check
                if _nw_check():
                    # 헤더 메시지 먼저 (텍스트 순서 보존)
                    header_txt = f"[{sender}] [{tstr}] [사진]"
                    if _send_single(conv_id, header_txt):
                        stats["text_sent"] += 1
                    time.sleep(delay)
                    # 각 사진 nenovaweb 업로드 + image_link 블록 전송 + 성공시 서버에서 삭제
                    urls = _nw_upload_many(photos_for_this, room=kakaotalk_name)
                    for j, (f, url) in enumerate(zip(photos_for_this, urls)):
                        if url:
                            work_sent = send_image_block(conv_id, url)
                            if work_sent:
                                stats["photos_uploaded"] += 1
                                # 워크 전송 성공 → nenovaweb 서버 용량 관리 위해 즉시 삭제
                                try:
                                    _nw_delete(url)
                                except Exception as _de:
                                    print(f"  [NENOVAWEB] 삭제 예외 (무시): {_de}", flush=True)
                            else:
                                stats["photos_missing"] += 1
                            time.sleep(delay)
                        else:
                            # 업로드 실패 → 실패 표시
                            _send_single(conv_id, f"[사진 {j+1}/{len(photos_for_this)} - 업로드 실패: {f.name}]")
                            stats["photos_missing"] += 1
                            time.sleep(delay)
                    continue
                else:
                    print(f"  [NENOVAWEB] 자격증명 미설정 -> GUI 업로드 경로로 폴백", flush=True)
            except Exception as e:
                print(f"  [NENOVAWEB] 예외 ({e}) -> GUI 업로드 경로로 폴백", flush=True)

            # ═══════════════════════════════════════════════
            # GUI 업로드 경로 (레거시 - Imgur 미설정 시만)
            # ═══════════════════════════════════════════════
            # 2) 앱 업로드: 첫 방 클릭 + OCR 검증 (엉뚱한 방 방지)
            w = _ensure_app_window()
            if w is None:
                stats["photos_missing"] += len(photos_for_this)
                continue
            if not _click_and_verify(w, kakaotalk_name):
                # 결정론 fail → Computer Use agentic fallback
                print(f"  [ABORT-FALLBACK] '{kakaotalk_name}' vision/search 실패 → Computer Use 위임", flush=True)
                try:
                    from core.upload_telemetry import log_upload_failure
                    log_upload_failure(
                        room=kakaotalk_name, file_name=None,
                        step="upload.click_and_verify",
                        reason="vision retries + Ctrl+K search all failed → CU fallback",
                    )
                except Exception:
                    pass
                try:
                    from core.computer_use_recovery import recover as _cu_recover
                    from core.kakaowork_app import _load_nv_mapping
                    _info = (_load_nv_mapping() or {}).get(kakaotalk_name) or {}
                    _nv_code = (_info.get("nv_code") or "").strip()
                    _nv_name = (_info.get("nv_name") or "").strip()
                    _alt = f" (미러방은 '{_nv_name}' 또는 '{_nv_code}'로 이름이 바뀌었을 수 있음)" if _nv_code else ""
                    cu_ok = _cu_recover(
                        f"카카오워크 앱에서 사이드바의 'NV' 또는 'N' 그룹 탭으로 이동한 다음, "
                        f"좌측 채팅방 리스트에서 '[미러] {kakaotalk_name}' 방을 찾아 클릭해줘{_alt}. "
                        f"채팅 헤더에 '[미러] {kakaotalk_name}' 또는 '{_nv_name or kakaotalk_name}'이 표시되면 성공이라고 'DONE' 답변. "
                        f"카톡이나 다른 앱은 건들지 말 것."
                    )
                    if cu_ok and _verify_room(w, kakaotalk_name):
                        print(f"  [RECOVER] '{kakaotalk_name}' 방 진입 성공 → 업로드 재개", flush=True)
                    else:
                        print(f"  [ABORT] '{kakaotalk_name}' Computer Use도 실패 - 사진 {len(photos_for_this)}장 격리", flush=True)
                        stats["photos_missing"] += len(photos_for_this)
                        # 누적 방지: 업로드 못 한 사진을 격리 폴더로 이동
                        _quarantine_photos(photos_for_this)
                        time.sleep(delay)
                        continue
                except Exception as e:
                    print(f"  [ABORT] '{kakaotalk_name}' CU fallback 에러 ({e}) - 격리", flush=True)
                    stats["photos_missing"] += len(photos_for_this)
                    _quarantine_photos(photos_for_this)
                    time.sleep(delay)
                    continue
            # 사진 배치 업로드 + 중간 실패시 방 재진입
            # (한 장 실패했는데 남은 장수를 계속 밀어넣으면 엉뚱한 창에 쌓이는 사고 방지)
            consecutive_fail = 0
            for idx, f in enumerate(photos_for_this):
                if _upload_one(f, w):
                    stats["photos_uploaded"] += 1
                    consecutive_fail = 0
                else:
                    stats["photos_missing"] += 1
                    consecutive_fail += 1
                    remaining_after = len(photos_for_this) - idx - 1
                    if consecutive_fail >= 2 and remaining_after > 0:
                        # 연속 2장 실패 → 방이 바뀌었거나 다이얼로그가 막힘.
                        # 남은 사진은 방 재진입 후 재시도.
                        print(f"  [RE-ENTER] 연속 실패 {consecutive_fail}회 - 방 재진입 후 나머지 {remaining_after}장 재시도", flush=True)
                        try:
                            from core.upload_telemetry import log_upload_failure
                            log_upload_failure(
                                room=kakaotalk_name, file_name=None,
                                step="upload.batch.re_enter",
                                reason=f"consecutive_fail={consecutive_fail}, remaining={remaining_after}",
                                capture_frame=False,
                            )
                        except Exception:
                            pass
                        _send_single(conv_id, "\u2063")
                        time.sleep(1.0)
                        if not _click_and_verify(w, kakaotalk_name):
                            # 재진입 실패 → 남은 사진 격리하고 다음 메시지로
                            print(f"  [GIVE-UP] 방 재진입 실패 - 잔여 {remaining_after}장 격리", flush=True)
                            try:
                                from core.upload_telemetry import log_upload_failure
                                log_upload_failure(
                                    room=kakaotalk_name, file_name=None,
                                    step="upload.batch.give_up",
                                    reason=f"re-enter failed, quarantined={remaining_after}",
                                )
                            except Exception:
                                pass
                            _quarantine_photos(photos_for_this[idx + 1:])
                            stats["photos_missing"] += remaining_after
                            break
                        consecutive_fail = 0

            # 업로드 직후 짧은 쉼 — 다음 Bot API 호출 순서 보장
            time.sleep(0.5)

        else:
            # ── 텍스트 메시지 ──
            text = f"[{sender}] [{tstr}] {msg['content']}"
            if len(text) > 3000:
                text = text[:3000] + "..."
            # 멱등 dedup: 이미 워크에 보낸 동일 메시지면 스킵
            _h = _ledger.hash_msg(msg)
            if _ledger.seen(_h):
                stats["text_skipped"] += 1
                continue
            if _send_single(conv_id, text):
                stats["text_sent"] += 1
                _ledger.add(_h)
            else:
                stats["text_failed"] += 1
            time.sleep(delay)

    # 남은 사진 (파싱이 놓친 경우) — nenovaweb 업로드 + image_link
    # GUI 업로드 경로는 Vision 실패 빈발 → nenovaweb 경로로 통일.
    remaining = list(photo_iter)
    if remaining:
        try:
            from core.photo_uploader import upload_many as _nw_um
            from core.photo_uploader import delete_from_nenovaweb as _nw_del
            from core.photo_uploader import _get_client_id as _nw_ok
        except Exception as e:
            _nw_ok = None
            print(f"  [NENOVAWEB] 꼬리 업로더 import 실패: {e}", flush=True)

        if _nw_ok and _nw_ok():
            print(f"  [꼬리] {len(remaining)}장 nenovaweb 경로", flush=True)
            urls = _nw_um(remaining, room=kakaotalk_name)
            for f, url in zip(remaining, urls):
                if url and send_image_block(conv_id, url):
                    stats["trailing_uploaded"] += 1
                    stats["photos_uploaded"] += 1
                    try:
                        _nw_del(url)
                    except Exception:
                        pass
                    time.sleep(delay)
                else:
                    _send_single(conv_id, f"[꼬리 사진 업로드 실패: {f.name}]")
                    stats["photos_missing"] += 1
                    time.sleep(delay)
        else:
            # 자격증명 없을 때만 GUI 폴백 (거의 발생 안 함)
            w = _ensure_app_window()
            if w is not None:
                _send_single(conv_id, "\u2063")
                time.sleep(1.0)
                if _click_and_verify(w, kakaotalk_name):
                    for f in remaining:
                        if _upload_one(f, w):
                            stats["trailing_uploaded"] += 1
                            stats["photos_uploaded"] += 1
                        else:
                            stats["photos_missing"] += 1
                else:
                    print(f"  [ABORT] 꼬리 사진 {len(remaining)}장 vision 검증 실패 - 중단", flush=True)
                    stats["photos_missing"] += len(remaining)

    # 멱등 원장 저장 (이번에 보낸 메시지 해시 영구화)
    try:
        _ledger.flush()
    except Exception:
        pass
    if stats.get("text_skipped", 0):
        print(f"  [DEDUP] {kakaotalk_name}: 이미 워크에 있는 {stats['text_skipped']}건 제외", flush=True)

    # 양방향: 미러방에 [📤 카톡 답장] 버튼 첨부 (텍스트 전송된 경우만)
    if stats.get("text_sent", 0) > 0:
        try:
            send_reply_button(kakaotalk_name)
        except Exception as e:
            print(f"  [REPLY-BTN] 첨부 실패: {e}", flush=True)

    return stats


def send_messages_individually(
    kakaotalk_name: str,
    delta: str,
    *,
    delay: float = 0.3,
    last_n: int | None = None,
) -> dict:
    """
    delta를 개별 메시지 단위로 파싱하여 하나씩 카카오워크 미러 방에 전송.

    Args:
        kakaotalk_name: 카카오톡 방 이름
        delta: 수집된 대화 텍스트
        delay: 메시지 간 딜레이 (초)
        last_n: None이면 전체, 숫자면 최근 N건만

    Returns:
        {"total": int, "sent": int, "failed": int, "photo_messages": list[dict]}
    """
    mapping = _load_room_mapping()
    conv_id = mapping.get(kakaotalk_name)

    if not conv_id:
        print(f"  [WARN] {kakaotalk_name}: 매핑 없음 - 스킵")
        return {"total": 0, "sent": 0, "failed": 0, "photo_messages": []}

    messages = parse_delta_to_messages(delta)

    if last_n is not None and len(messages) > last_n:
        messages = messages[-last_n:]

    # 멱등 원장: 이미 워크에 보낸 동일 메시지는 재전송 안 함
    from core.sent_ledger import SentLedger
    _ledger = SentLedger(kakaotalk_name)

    sent = 0
    failed = 0
    skipped = 0
    photo_messages = []

    for i, msg in enumerate(messages):
        text = f"[{msg['sender']}] [{msg['time']}] {msg['content']}"

        # 3000자 제한 — suffix 포함해 한도 초과 안 되도록 본문 먼저 잘라냄
        if len(text) > 3000:
            text = text[: 3000 - 3] + "..."

        # 멱등 dedup: 이미 워크에 보낸 동일 메시지면 스킵
        # (텍스트 앵커를 안 보냈으므로 photo_messages 에도 넣지 않음)
        _h = _ledger.hash_msg(msg)
        if _ledger.seen(_h):
            skipped += 1
            continue

        ok = _send_single(conv_id, text)
        if ok:
            sent += 1
            _ledger.add(_h)
        else:
            failed += 1

        if msg["has_photo"]:
            photo_messages.append(msg)

        # 진행률 (50건마다)
        if (i + 1) % 50 == 0:
            print(f"     진행: {i + 1}/{len(messages)} ({sent}성공/{failed}실패/{skipped}중복스킵)")

        if i < len(messages) - 1:
            time.sleep(delay)

    try:
        _ledger.flush()
    except Exception:
        pass
    if skipped:
        print(f"  [DEDUP] {kakaotalk_name}: 이미 워크에 있는 {skipped}건 제외", flush=True)

    return {
        "total": len(messages),
        "sent": sent,
        "failed": failed,
        "skipped": skipped,
        "photo_messages": photo_messages,
    }


if __name__ == "__main__":
    print("[미러 방 생성] 카카오워크에 미러 방을 생성합니다...")
    create_all_mirror_rooms()
