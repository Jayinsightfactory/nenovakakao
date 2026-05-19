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
        f"화면은 카카오톡 PC 메인창 (정사각형 1024×1024).\n"
        f"좌측 약 0~330 폭 = 채팅 리스트 (각 행 한 줄에 방 이름 + 우측 끝에 빨간 동그라미 뱃지/시간).\n"
        f"중앙~우측 = 채팅 내용 패널.\n"
        f"좌측 사이드바 (x<55) = 카톡 탭 아이콘.\n\n"
        f"📌 카톡 저장 폴더 = {save_dir}. 이미 설정돼 있음. 경로 paste 불필요.\n\n"
        f"수행 (한 사이클 = 한 방씩):\n"
        f"  Step 1. screenshot → 좌측 채팅 리스트에서 **빨간 원형 뱃지** (안 읽음 카운트 숫자) 가 있는 방을\n"
        f"          캡쳐로 직접 찾아. 가장 위에 있는 뱃지 방을 선택.\n"
        f"          못 찾으면 답변 'DONE' (한 단어).\n"
        f"  Step 2. 그 방 이름이 표시된 줄의 **가운데를 더블클릭** (한 번에 정확히).\n"
        f"          더블클릭 = double_click action. 좌표는 캡쳐에서 실제 본 위치.\n"
        f"  Step 3. screenshot → 채팅창이 열렸는지 (우측 패널에 대화 표시 또는 별도 분리창) 확인.\n"
        f"          안 열렸으면 다른 방 시도 (Step 1 부터).\n"
        f"  Step 4. 채팅창 열림 확인되면 → key action: 'ctrl+s'\n"
        f"  Step 5. screenshot → '다른 이름으로 저장' 다이얼로그 떴는지 확인.\n"
        f"          떴으면 → **즉시 key action: 'Return'** (Enter 키)\n"
        f"          ⛔ type / click / triple_click 절대 X. 파일명 필드 절대 건드리지 마.\n"
        f"  Step 6. screenshot → 다이얼로그 사라지고 카톡 메인 보이면 성공. 답변 'DONE'.\n"
        f"          (또는 다음 안 읽은 방 처리하려면 Step 1 부터 반복)\n\n"
        f"⚠️ 절대 규칙:\n"
        f"- Ctrl+S 후엔 무조건 key 'Return' 단발. 다른 키/클릭 X.\n"
        f"- 친구 추가 / 통합검색 다이얼로그 뜨면 즉시 key 'Escape' 2번.\n"
        f"- 광고 영역 (하단) 클릭 금지.\n"
        f"- 같은 좌표 2번 이상 시도해서 안 되면 다른 행 시도."
    )
    # max_loop 30 (한 방 처리 6 step + 여유)
    ok = agentic_action(goal, max_loop=30)

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
