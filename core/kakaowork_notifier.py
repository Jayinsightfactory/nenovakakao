"""
Phase 1.7: 카카오워크 Incoming Webhook 전송

파싱된 메시지를 카카오워크 관리자전용톡방에 전송.
MD5 중복 차단은 message_extractor에서 이미 처리.
"""
from __future__ import annotations

import os
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")


def send_to_kakaowork(text: str) -> bool:
    """
    카카오워크 관리자전용톡방에 메시지 전송.

    Returns:
        성공 여부
    """
    url = os.getenv("KAKAOWORK_WEBHOOK_URL")
    if not url:
        print("[WARN] KAKAOWORK_WEBHOOK_URL이 .env에 설정되지 않았습니다.")
        return False

    try:
        resp = requests.post(url, json={"text": text}, timeout=10)
        return resp.status_code == 200
    except Exception as e:
        print(f"[ERROR] 카카오워크 전송 실패: {e}")
        return False


def notify_new_message(room_name: str, content: str, max_preview: int = 500):
    """새 메시지 알림을 카카오워크에 전송"""
    # 내용이 너무 길면 미리보기만
    preview = content[:max_preview]
    if len(content) > max_preview:
        preview += f"\n... ({len(content) - max_preview}자 더)"

    text = (
        f"[네노바 에이전트] 새 메시지 감지\n"
        f"방: {room_name}\n"
        f"시각: {__import__('datetime').datetime.now().strftime('%H:%M:%S')}\n"
        f"---\n"
        f"{preview}"
    )
    return send_to_kakaowork(text)


def notify_error(error_msg: str):
    """에러 알림을 카카오워크에 전송 (모든 에러 투명 보고)"""
    text = f"[네노바 에이전트] 에러 발생\n{error_msg}"
    return send_to_kakaowork(text)
