"""
완전 Agentic Monitor.

기존 monitor: 결정론 좌표 클릭 + 가드 + 사후 회복
Agentic monitor: 매 cycle 화면 캡처 → Claude가 모든 UI 결정 → 우리는 텍스트 처리만

흐름:
  1. (결정론) hash 가드 — 5초 폴링, 변화 없으면 idle
  2. (Agentic) 변화 감지 시 Claude에게 위임:
     "현재 카톡 화면을 봐서 안 읽은 메시지 있는 방을 모두 처리해.
      각 방 → Ctrl+S → KAKAO_SAVE_DIR/{ts}.txt 저장."
  3. (결정론) 저장된 txt 파일들 → 델타 추출 → Bot API 전송 → 사진 메시지 발견
  4. (Agentic) 사진 메시지 → "이 방의 사진 다운로드해서 카카오워크 미러방으로 업로드"
  5. 반복

UI 자동화 100% Claude Computer Use. 텍스트/파일 처리는 결정론.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent
DATA_DIR = ROOT / "data"

POLL_INTERVAL = 5
SWEEP_EVERY = 3
HASH_DOWNSAMPLE = (250, 500)


def _compute_room_list_hash(window) -> str:
    """방 리스트 영역 hash (idle 가드용)."""
    try:
        from PIL import ImageGrab, Image
        import hashlib
        l, t, r, b = window.room_list_bbox()
        narrow = (l, t, max(l + 50, r - 80), b)
        img = ImageGrab.grab(bbox=narrow)
        small = img.convert("L").resize(HASH_DOWNSAMPLE, Image.Resampling.LANCZOS)
        return hashlib.md5(small.tobytes()).hexdigest()
    except Exception:
        return ""


def agentic_collect_unread_rooms(window, save_dir: Path) -> list[Path]:
    """Claude Computer Use로 모든 안 읽은 방의 텍스트를 저장.
    Returns: 새로 저장된 txt 파일 목록.
    """
    from core.computer_use_recovery import agentic_action

    save_dir.mkdir(parents=True, exist_ok=True)
    before = {p.name for p in save_dir.glob("*.txt")}

    goal = (
        f"카카오톡 메인창에서 다음 작업을 수행해줘:\n"
        f"1. 빨간 뱃지(안 읽은 메시지)가 있는 모든 방을 위에서부터 차례로 더블클릭으로 열어줘.\n"
        f"2. 방이 열리면 Ctrl+S를 눌러 텍스트로 저장 다이얼로그를 띄워.\n"
        f"3. 저장 다이얼로그가 뜨면 파일명 필드에 절대경로 형식으로 입력:\n"
        f"   {save_dir}/{{timestamp_ms}}.txt (timestamp_ms는 현재 시각의 밀리초)\n"
        f"4. Enter로 저장. '이미 있습니다 - 바꾸시겠습니까' 팝업 뜨면 'Y' 누름.\n"
        f"5. 다음 방 처리. 모든 안 읽은 방 처리 완료하면 'DONE'.\n\n"
        f"중요:\n"
        f"- 카톡 광고 영역(화면 하단)은 절대 클릭 금지.\n"
        f"- 친구 추가/비밀번호 변경 등 모달이 뜨면 즉시 ESC로 닫고 진행.\n"
        f"- 사용자의 다른 앱(브라우저, VSCode)은 건드리지 말 것."
    )
    # max_loop 작게 (rate limit 대응 + 한 cycle 한 방씩)
    ok = agentic_action(goal, max_loop=15)

    after = {p.name for p in save_dir.glob("*.txt")}
    new_files = [save_dir / n for n in (after - before)]
    print(f"  [AGENTIC-COLLECT] {len(new_files)}개 새 파일 저장 (success={ok})", flush=True)
    return new_files


def agentic_upload_photo(kakaotalk_room: str, photo_paths: list[Path]) -> bool:
    """Claude Computer Use로 사진을 카카오워크 미러방에 업로드."""
    from core.computer_use_recovery import agentic_action

    if not photo_paths:
        return True
    goal = (
        f"카카오워크 앱에서 다음 작업을 수행해줘:\n"
        f"1. 사이드바에서 'NV' 또는 'N' 그룹 탭으로 이동.\n"
        f"2. 좌측 채팅방 리스트에서 '[미러] {kakaotalk_room}' 방을 찾아 클릭.\n"
        f"3. 채팅 헤더에 '[미러] {kakaotalk_room}'이 표시되는지 확인.\n"
        f"4. 채팅 입력란 클릭 → Ctrl+T로 파일 첨부 다이얼로그.\n"
        f"5. 파일명 필드에 다음 경로들을 차례로 입력 후 전송:\n"
        + "\n".join(f"   - {p}" for p in photo_paths)
        + "\n6. 모든 파일 전송 완료하면 'DONE'.\n\n"
        "중요:\n"
        "- 다른 미러방을 클릭하지 말 것.\n"
        "- 친구 추가/광고 모달 뜨면 즉시 ESC.\n"
    )
    ok = agentic_action(goal, max_loop=30)
    return ok
