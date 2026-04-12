"""
Phase 1.5: Ctrl+S 저장 자동화 + 델타(신규 내용만) 추출

뱃지가 감지된 방을 클릭 → Ctrl+S → 저장 → txt 읽기 →
이전 내용과 비교하여 신규 라인만 추출 → ESC
"""
from __future__ import annotations

import glob
import hashlib
import json
import os
import time
from datetime import datetime
from pathlib import Path

import pyautogui

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

# Ctrl+S 저장 경로
KAKAO_SAVE_DIR = Path(os.getenv("KAKAO_SAVE_DIR", "C:/Users/USER/Documents/KakaoTalk Downloads"))

# 이전 처리 해시 저장
DATA_DIR = Path(__file__).parent.parent / "data"
USAGE_STATS = DATA_DIR / "usage_stats.json"
COLLECTED_DATA = DATA_DIR / "collected_data.jsonl"
# 방별 마지막 내용 저장 (델타 비교용)
LAST_CONTENT_DIR = DATA_DIR / "last_content"


def _load_usage_stats() -> dict:
    """처리 이력 로드"""
    if USAGE_STATS.exists():
        with open(USAGE_STATS, encoding="utf-8") as f:
            return json.load(f)
    return {"processed_hashes": []}


def _save_usage_stats(stats: dict):
    """처리 이력 저장"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(USAGE_STATS, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)


def _get_last_content(room_name: str) -> str:
    """방의 마지막 저장 내용을 가져온다"""
    LAST_CONTENT_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = hashlib.md5(room_name.encode()).hexdigest()
    path = LAST_CONTENT_DIR / f"{safe_name}.txt"
    if path.exists():
        return path.read_text(encoding="utf-8", errors="ignore")
    return ""


def _save_last_content(room_name: str, content: str):
    """방의 현재 전체 내용을 저장 (다음 비교용)"""
    LAST_CONTENT_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = hashlib.md5(room_name.encode()).hexdigest()
    path = LAST_CONTENT_DIR / f"{safe_name}.txt"
    path.write_text(content, encoding="utf-8")


def extract_delta(old_content: str, new_content: str) -> str:
    """
    이전 내용과 새 내용을 비교하여 신규 라인만 추출.

    카카오톡 Ctrl+S 파일은 시간순 누적이므로,
    이전 마지막 라인 이후의 내용이 신규.
    """
    if not old_content.strip():
        return new_content  # 최초 수집 시 전체 반환

    old_lines = old_content.strip().splitlines()
    new_lines = new_content.strip().splitlines()

    if not new_lines:
        return ""

    # 이전 내용의 마지막 몇 줄로 매칭 포인트 찾기
    # (정확한 매칭을 위해 마지막 3줄 사용)
    match_lines = old_lines[-3:] if len(old_lines) >= 3 else old_lines

    # 새 내용에서 매칭 포인트를 찾는다
    match_target = "\n".join(match_lines)
    for i in range(len(new_lines) - len(match_lines), -1, -1):
        candidate = "\n".join(new_lines[i:i + len(match_lines)])
        if candidate == match_target:
            # 매칭 지점 이후가 신규
            delta_lines = new_lines[i + len(match_lines):]
            if delta_lines:
                return "\n".join(delta_lines)
            return ""  # 변경 없음

    # 매칭 실패 시 (대화가 크게 달라진 경우) 전체를 신규로 간주
    return new_content


def _get_latest_saved_file() -> Path | None:
    """카카오톡 저장 폴더에서 가장 최근 txt 파일 찾기"""
    pattern = str(KAKAO_SAVE_DIR / "**" / "*.txt")
    files = glob.glob(pattern, recursive=True)
    if not files:
        return None
    return Path(max(files, key=os.path.getmtime))


def click_room(x: int, y: int):
    """방 클릭 (절대 좌표) — 더블 클릭으로 방 열기"""
    # 카톡 창 활성화 후 클릭
    pyautogui.click(x, y)
    time.sleep(0.3)
    pyautogui.doubleClick(x, y)
    time.sleep(1.5)


def save_chat_with_ctrl_s() -> Path | None:
    """
    현재 열린 채팅방에서 Ctrl+S → 저장 다이얼로그 → Enter → 완료 → Enter.
    저장된 txt 파일 경로를 반환.
    """
    # 저장 전 기존 파일 기록 (새로 생성된 파일 식별용)
    before_files = set()
    if KAKAO_SAVE_DIR.exists():
        before_files = set(str(p) for p in KAKAO_SAVE_DIR.rglob("*.txt"))

    # Ctrl+S 입력
    pyautogui.hotkey("ctrl", "s")
    time.sleep(2.0)

    # 저장 다이얼로그 → Enter (기본 경로에 저장)
    pyautogui.press("enter")
    time.sleep(2.0)

    # "저장 폴더 열기 / 완료" 팝업 → Enter (완료 선택)
    pyautogui.press("enter")
    time.sleep(1.0)

    # 새로 생성된 파일 찾기
    if KAKAO_SAVE_DIR.exists():
        after_files = set(str(p) for p in KAKAO_SAVE_DIR.rglob("*.txt"))
        new_files = after_files - before_files
        if new_files:
            return Path(max(new_files, key=os.path.getmtime))

    # 새 파일을 못 찾으면 가장 최근 파일 반환
    return _get_latest_saved_file()


def close_chat_room():
    """채팅방 닫기 (ESC)"""
    pyautogui.press("escape")
    time.sleep(0.5)


def read_and_process_saved_file(file_path: Path) -> dict | None:
    """
    저장된 txt 파일을 읽고, 이전 대비 신규 내용만 추출.

    Returns:
        {"room_name": str, "content": str, "delta": str,
         "has_new": bool, "timestamp": str, "file_path": str}
        또는 None (파일 없음/비어있음/변경 없음)
    """
    if not file_path or not file_path.exists():
        return None

    content = file_path.read_text(encoding="utf-8", errors="ignore")
    if not content.strip():
        return None

    # 방 이름 추출: 파일 내용 첫 줄 우선 (예: "수입방 임과 카카오톡 대화")
    room_name = file_path.stem
    first_line = content.strip().splitlines()[0] if content.strip() else ""
    if "카카오톡 대화" in first_line:
        # "방이름 님과 카카오톡 대화" 또는 "방이름 임과 카카오톡 대화"
        parts = first_line.split("카카오톡 대화")[0].strip()
        # "님과" 또는 "임과" 제거
        for suffix in ["님과", "임과", "과"]:
            if parts.endswith(suffix):
                parts = parts[:-len(suffix)].strip()
                break
        if parts:
            room_name = parts
    elif "카카오톡" in room_name:
        parts = room_name.split(" - ", 1)
        if len(parts) > 1:
            room_name = parts[1]

    # MD5 해시로 전체 내용 변경 여부 확인
    content_hash = hashlib.md5(content.encode("utf-8")).hexdigest()
    stats = _load_usage_stats()

    if content_hash in stats.get("processed_hashes", []):
        return None  # 완전히 동일한 내용 — 변경 없음

    # 이전 내용과 비교하여 델타(신규분만) 추출
    old_content = _get_last_content(room_name)
    delta = extract_delta(old_content, content)

    if not delta.strip():
        # 델타 없음 (중복) — 해시만 저장하고 스킵
        stats.setdefault("processed_hashes", []).append(content_hash)
        if len(stats["processed_hashes"]) > 1000:
            stats["processed_hashes"] = stats["processed_hashes"][-1000:]
        _save_usage_stats(stats)
        return None

    # 현재 전체 내용 저장 (다음 비교 기준)
    _save_last_content(room_name, content)

    # 해시 저장
    stats.setdefault("processed_hashes", []).append(content_hash)
    if len(stats["processed_hashes"]) > 1000:
        stats["processed_hashes"] = stats["processed_hashes"][-1000:]
    _save_usage_stats(stats)

    result = {
        "room_name": room_name,
        "content": content,        # 전체 내용 (참조용)
        "delta": delta,             # 신규 내용만 (전송/기록용)
        "has_new": True,
        "timestamp": datetime.now().isoformat(),
        "file_path": str(file_path),
        "content_hash": content_hash,
    }

    # collected_data.jsonl에 신규분만 누적
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    record = {
        "room_name": room_name,
        "delta": delta,
        "timestamp": result["timestamp"],
        "content_hash": content_hash,
    }
    with open(COLLECTED_DATA, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

    return result


def extract_from_room(x: int, y: int) -> dict | None:
    """
    방 클릭 → Ctrl+S → 저장 → 읽기 → 닫기 전체 시퀀스.

    Returns:
        처리 결과 dict 또는 None (중복/실패)
    """
    click_room(x, y)
    saved_file = save_chat_with_ctrl_s()
    result = read_and_process_saved_file(saved_file)
    close_chat_room()
    return result
