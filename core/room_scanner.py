"""
Phase 1.2: Claude Vision으로 방 리스트 OCR (전체 스캔)

카카오톡 방 리스트를 스크롤하면서 전체 방을 스캔한다.
각 페이지를 캡처 → Claude Vision OCR → 중복 제거 → 병합.
결과를 data/rooms_detected.json에 저장.
"""
from __future__ import annotations

import base64
import json
import os
import time
from pathlib import Path

from dotenv import load_dotenv

# .env 로드
load_dotenv(Path(__file__).parent.parent / ".env")

CLAUDE_MODEL = os.getenv("ANTHROPIC_ROOM_OCR_MODEL", "claude-sonnet-4-6")

PROMPT = """이 이미지는 카카오톡 PC 앱의 채팅방 리스트를 캡처한 것입니다.

각 채팅방의 정보를 위에서 아래 순서대로 JSON 배열로 추출해주세요.
각 항목에는 다음 필드를 포함합니다:

- "name": 채팅방 이름 (정확히 보이는 텍스트 그대로)
- "last_message": 마지막 메시지 미리보기 (보이면)
- "unread": 읽지 않은 메시지 수 (빨간 뱃지 숫자, 없으면 0)
- "order": 위에서부터 순서 번호 (1부터 시작)

주의사항:
- 광고 배너는 무시하세요
- 방 이름이 잘려있으면 보이는 부분까지만 적으세요
- 숫자 뱃지가 있으면 정확히 읽어주세요
- 반드시 JSON 배열만 반환하세요. 다른 텍스트 없이."""


def _parse_json(raw: str) -> list[dict]:
    """응답에서 JSON 배열 추출"""
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1]
        raw = raw.rsplit("```", 1)[0]
        raw = raw.strip()
    return json.loads(raw)


def scan_rooms_single(image_path: Path, max_retries: int = 3) -> list[dict]:
    """Subscription CLI vision; no hidden paid API retry."""
    from core.subscription_ai import ask
    value = ask(PROMPT, {'task':'room_list'}, images=[image_path])
    if not isinstance(value, list) or any(not isinstance(r, dict) or
            not isinstance(r.get('name'), str) or type(r.get('unread')) is not int
            or r['unread'] < 0 or type(r.get('order')) is not int for r in value):
        raise ValueError('방 목록 JSON 형식 오류')
    return value


def scan_rooms_full(window, captures_dir: Path, max_pages: int = 20) -> list[dict]:
    """
    전체 방 리스트 스캔: 맨 위로 이동 → 캡처/OCR → 스크롤 → 반복 → 중복 제거.

    Args:
        window: KakaoWindow 인스턴스
        captures_dir: 캡처 이미지 저장 폴더
        max_pages: 최대 스크롤 페이지 수 (무한루프 방지)

    Returns:
        중복 제거된 전체 방 리스트
    """
    from core.window_detector import (
        capture_room_list,
        scroll_room_list,
        scroll_room_list_to_top,
    )

    captures_dir.mkdir(parents=True, exist_ok=True)

    # 1. 맨 위로 스크롤
    print("       맨 위로 스크롤 중...")
    scroll_room_list_to_top(window)
    time.sleep(0.5)

    all_rooms: list[dict] = []
    seen_names: set[str] = set()
    consecutive_no_new = 0

    for page in range(max_pages):
        # 2. 캡처
        img_path = capture_room_list(
            window, captures_dir / f"rooms_page_{page}.png"
        )

        # 3. Claude Vision OCR
        print(f"       페이지 {page + 1} 분석 중...")
        try:
            page_rooms = scan_rooms_single(img_path)
        except Exception as e:
            print(f"       [WARN] 페이지 {page + 1} OCR 실패: {e}")
            break

        # 4. 새 방 추가 (중복 제거)
        new_count = 0
        for room in page_rooms:
            name = room["name"]
            if name not in seen_names:
                seen_names.add(name)
                room["order"] = len(all_rooms) + 1
                all_rooms.append(room)
                new_count += 1

        print(f"       → {len(page_rooms)}개 감지, {new_count}개 신규")

        # 5. 종료 조건: 신규 방이 없으면 2회 연속 시 종료
        if new_count == 0:
            consecutive_no_new += 1
            if consecutive_no_new >= 2:
                print("       바닥 도달 (신규 방 없음)")
                break
        else:
            consecutive_no_new = 0

        # 6. 스크롤 다운
        scroll_room_list(window, direction=-5)
        time.sleep(1)

    return all_rooms


def save_rooms(rooms: list[dict], save_path: Path) -> Path:
    """rooms_detected.json으로 저장"""
    save_path.parent.mkdir(parents=True, exist_ok=True)
    with open(save_path, "w", encoding="utf-8") as f:
        json.dump(rooms, f, ensure_ascii=False, indent=2)
    return save_path
