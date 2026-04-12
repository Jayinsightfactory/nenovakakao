"""
카카오워크 다중 방 라우팅 시스템

카카오톡 방과 1:1로 매칭되는 카카오워크 방을 생성하고,
메시지를 해당 방으로 라우팅한다.

Bot API 사용 (conversations.open + messages.send)
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

DATA_DIR = Path(__file__).parent.parent / "data"
ROOM_MAP_FILE = DATA_DIR / "room_mapping.json"
DETECTED_FILE = DATA_DIR / "rooms_detected.json"

# 관리자 유저 ID (임재용 - dlaww584@gmail.com)
ADMIN_USER_ID = 11826656

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

    # 메시지 길이 제한
    if len(text) > max_length:
        text = text[:max_length] + f"\n... ({len(text) - max_length}자 생략)"

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


if __name__ == "__main__":
    print("[미러 방 생성] 카카오워크에 미러 방을 생성합니다...")
    create_all_mirror_rooms()
