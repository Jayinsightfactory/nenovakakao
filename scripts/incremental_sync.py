# -*- coding: utf-8 -*-
"""
30분 주기 증분 동기화 스크립트

카톡 Ctrl+S 저장 파일에서 신규 메시지만 추출(델타)하여
구글시트 4개 탭(이벤트로그, 비즈니스이벤트, 의사결정추적, 메시지분류)에 append.

- 기존 행 삭제/덮어쓰기 절대 금지 (append only)
- sync_state.json으로 마지막 처리 위치 기억
- 시트 API 실패 시 state 업데이트 안 함 (다음 실행에서 재시도)

실행: python scripts/incremental_sync.py
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sys
import time
import uuid
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

# ─── 프로젝트 루트 ───

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.classifier import classify_delta, ParsedMessage

# ─── 설정 ───

CREDS_FILE = "C:/Users/USER/nenova_agent/data/gsheet_credentials.json"
SHEET_URL = "https://docs.google.com/spreadsheets/d/1pXLVZqiMwWt6Vh0IhWwASBvgLtZqLnbHXMWqOLNwAXU/edit"
KAKAO_DATA_DIR = Path("C:/Users/USER/Downloads/카톡대화데이터")
SYNC_STATE_FILE = PROJECT_ROOT / "data" / "sync_state.json"
PIPELINE_CONFIG_FILE = PROJECT_ROOT / "data" / "pipeline_config.json"
LOG_DIR = PROJECT_ROOT / "logs"
LOG_FILE = LOG_DIR / "sync.log"

BATCH_SIZE = 1000       # append_rows 1회 최대 행 수
BATCH_DELAY = 2         # 배치 간 대기 초

ROOM_NAME_PATTERN = re.compile(r"^(.+?)\s*님과 카카오톡 대화$")

# ─── 로깅 ───

LOG_DIR.mkdir(parents=True, exist_ok=True)

logger = logging.getLogger("incremental_sync")
logger.setLevel(logging.INFO)

file_handler = logging.FileHandler(str(LOG_FILE), encoding="utf-8")
file_handler.setFormatter(logging.Formatter("[%(asctime)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
logger.addHandler(file_handler)

console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(logging.Formatter("[%(asctime)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
logger.addHandler(console_handler)

# ─── 파이프라인 매핑 ───

_pipeline_config = None


def _load_pipeline_config() -> dict:
    global _pipeline_config
    if _pipeline_config is None:
        with open(PIPELINE_CONFIG_FILE, encoding="utf-8") as f:
            _pipeline_config = json.load(f)
    return _pipeline_config


def get_pipeline(room: str) -> str:
    """방 이름 -> 파이프라인 단계명 반환."""
    config = _load_pipeline_config()
    for key, info in config.get("pipeline_stages", {}).items():
        if room in info.get("rooms", []):
            return info.get("name", key)
    return "기타"


# ─── sync_state.json 관리 ───


def load_sync_state() -> dict:
    """sync_state.json 로드. 없으면 빈 구조 반환."""
    if SYNC_STATE_FILE.exists():
        try:
            with open(SYNC_STATE_FILE, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            logger.warning("sync_state.json 손상 -- 초기화합니다")
    return {"last_sync": None, "rooms": {}}


def save_sync_state(state: dict) -> None:
    """sync_state.json 저장."""
    state["last_sync"] = datetime.now().isoformat(timespec="seconds")
    with open(SYNC_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


# ─── 카톡 파일 스캔 ───


def _extract_room_name(filepath: Path) -> str | None:
    """카톡 txt 파일의 첫 줄에서 방 이름 추출."""
    try:
        with open(filepath, encoding="utf-8") as f:
            first_line = f.readline().strip()
        m = ROOM_NAME_PATTERN.match(first_line)
        return m.group(1).strip() if m else None
    except Exception:
        return None


def _md5_line(line: str) -> str:
    """문자열의 MD5 해시 (16자리)."""
    return hashlib.md5(line.encode("utf-8")).hexdigest()[:16]


def scan_kakao_files() -> dict[str, list[Path]]:
    """
    카톡 저장 폴더를 스캔하여 {방이름: [파일목록(오래된순)]} 반환.
    같은 방의 파일이 여러 개 있을 수 있음 (Ctrl+S 시마다 새 파일).
    """
    room_files: dict[str, list[Path]] = {}
    if not KAKAO_DATA_DIR.exists():
        logger.error("카톡 데이터 폴더 없음: %s", KAKAO_DATA_DIR)
        return room_files

    for filepath in sorted(KAKAO_DATA_DIR.glob("*.txt")):
        room = _extract_room_name(filepath)
        if room:
            room_files.setdefault(room, []).append(filepath)

    return room_files


# ─── 델타 추출 ───


def _read_lines(filepath: Path) -> list[str]:
    """파일 전체 라인 읽기 (헤더 2줄 제외)."""
    with open(filepath, encoding="utf-8") as f:
        lines = f.readlines()
    # 첫 2줄은 헤더 (방이름, 저장일시)
    return lines[2:] if len(lines) > 2 else []


def _compute_last_message_md5(lines: list[str]) -> str:
    """마지막 비어있지 않은 줄의 MD5."""
    for line in reversed(lines):
        stripped = line.strip()
        if stripped:
            return _md5_line(stripped)
    return ""


def extract_delta(room: str, files: list[Path], room_state: dict) -> tuple[str, dict]:
    """
    방의 파일 목록과 이전 상태를 비교하여 새 메시지(델타 텍스트) 추출.

    Returns:
        (delta_text, new_state)
        delta_text가 빈 문자열이면 새 메시지 없음.
    """
    if not files:
        return "", room_state

    # 가장 최신 파일 사용
    latest_file = files[-1]
    latest_name = latest_file.name
    latest_size = latest_file.stat().st_size

    prev_file = room_state.get("file", "")
    prev_size = room_state.get("file_size", 0)
    prev_line_count = room_state.get("last_line_count", 0)
    prev_md5 = room_state.get("last_message_md5", "")
    prev_total = room_state.get("total_synced", 0)

    # Case 1: 같은 파일, 같은 크기 -> 변경 없음
    if latest_name == prev_file and latest_size == prev_size:
        return "", room_state

    lines = _read_lines(latest_file)

    if not lines:
        return "", room_state

    # Case 2: 같은 파일, 크기가 커짐 -> 이전 라인 수 이후만 읽기
    if latest_name == prev_file and latest_size > prev_size:
        if prev_line_count > 0 and prev_line_count <= len(lines):
            delta_lines = lines[prev_line_count:]
        else:
            delta_lines = lines
        delta_text = "".join(delta_lines)
        new_md5 = _compute_last_message_md5(lines)
        new_state = {
            "file": latest_name,
            "file_size": latest_size,
            "last_line_count": len(lines),
            "last_message_md5": new_md5,
            "total_synced": prev_total,  # append 성공 후 업데이트
        }
        return delta_text, new_state

    # Case 3: 새 파일 (Ctrl+S로 새로 저장됨)
    # 이전 파일의 마지막 MD5 이후 메시지만 추출
    if prev_md5:
        # prev_md5를 찾아서 그 이후부터 추출
        found_idx = -1
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped and _md5_line(stripped) == prev_md5:
                found_idx = i
                # 같은 MD5가 여러 번 나올 수 있으므로 마지막 매칭을 사용
                # (더 안전: 뒤에서부터 찾기)

        # 뒤에서부터 다시 검색하여 마지막 매칭 위치 찾기
        for i in range(len(lines) - 1, -1, -1):
            stripped = lines[i].strip()
            if stripped and _md5_line(stripped) == prev_md5:
                found_idx = i
                break

        if found_idx >= 0 and found_idx < len(lines) - 1:
            delta_lines = lines[found_idx + 1:]
            delta_text = "".join(delta_lines)
        elif found_idx == len(lines) - 1:
            # 마지막 줄이 이전 마지막 -> 새 메시지 없음
            delta_text = ""
        else:
            # MD5를 찾지 못함 -> 전체를 델타로 (안전 폴백)
            logger.warning("%s: 이전 MD5를 새 파일에서 찾지 못함 -- 전체 처리", room)
            delta_text = "".join(lines)
    else:
        # 첫 동기화 -> 전체 처리
        delta_text = "".join(lines)

    new_md5 = _compute_last_message_md5(lines)
    new_state = {
        "file": latest_name,
        "file_size": latest_size,
        "last_line_count": len(lines),
        "last_message_md5": new_md5,
        "total_synced": prev_total,
    }
    return delta_text, new_state


# ─── 메시지 -> 시트 행 변환 ───


def _gen_id(prefix: str) -> str:
    """짧은 고유 ID 생성."""
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _truncate(text: str, max_len: int) -> str:
    """텍스트를 max_len까지 자르기."""
    text = text.replace("\n", " ").replace("\r", "")
    if len(text) > max_len:
        return text[:max_len]
    return text


def _format_time(msg: ParsedMessage) -> str:
    """메시지 시각을 ISO-ish 문자열로 변환."""
    now_date = datetime.now().strftime("%Y-%m-%d")
    if msg.time_str:
        return f"{now_date} {msg.time_str}"
    return now_date


def messages_to_sheet_rows(
    messages: list[ParsedMessage], room: str, pipeline: str
) -> dict[str, list[list[str]]]:
    """
    분류된 메시지 리스트를 시트 탭별 행 리스트로 변환.

    Returns:
        {
            "이벤트로그": [[...], ...],
            "비즈니스이벤트": [[...], ...],
            "의사결정추적": [[...], ...],
            "메시지분류": [[...], ...],
        }
    """
    event_log_rows = []
    biz_event_rows = []
    decision_rows = []
    classify_rows = []

    for msg in messages:
        if msg.is_date_separator:
            continue
        if not msg.sender:
            continue

        time_str = _format_time(msg)
        msg_id = _gen_id("M")
        content_500 = _truncate(msg.content, 500)
        content_200 = _truncate(msg.content, 200)

        # 1) 이벤트로그: 모든 메시지
        event_log_rows.append([
            time_str,           # 시각
            room,               # 방이름
            pipeline,           # 파이프라인
            msg.sender,         # 발신자
            content_500,        # 원문(500자)
            msg_id,             # 메시지ID
        ])

        # 2) 비즈니스이벤트: COMMS 제외 (비즈니스 관련 분류만)
        if msg.major and msg.major != "COMMS":
            event_id = _gen_id("E")
            biz_event_rows.append([
                event_id,       # 이벤트ID
                time_str,       # 시각
                msg.major,      # major
                msg.sequence,   # 차수
                msg.product,    # 품목
                msg.variety,    # 품종
                msg.quantity,   # 수량
                msg.unit,       # 단위
                msg.direction,  # 방향
                msg.supplier,   # 거래처
                room,           # 방이름
                pipeline,       # 파이프라인
                msg.sender,     # 발신자
                content_200,    # 원문요약(200자)
                msg.thread_id,  # thread_id
                msg_id,         # 트리거메시지ID
            ])

            # 3) 의사결정추적: DEFECT만
            if msg.major == "DEFECT":
                issue_id = _gen_id("I")
                issue_content = _truncate(msg.content, 300)
                decision_rows.append([
                    issue_id,       # 이슈ID
                    time_str,       # 시각
                    room,           # 방이름
                    pipeline,       # 파이프라인
                    issue_content,  # 이슈내용
                    "",             # 대응자
                    "",             # 대응내용
                    "",             # 대응시각
                    "",             # 소요시간(분)
                    "미해결",       # 결과
                    event_id,       # 연관이벤트ID
                ])

        # 4) 메시지분류: 모든 메시지
        classify_rows.append([
            time_str,           # 시각
            room,               # 방이름
            msg.sender,         # 발신자
            content_500,        # 원문
            msg.minor,          # minor
            msg.product,        # 품목
            msg.sequence,       # 차수
            msg.quantity,       # 수량
            "",                 # (빈 열)
            msg.direction,      # direction
        ])

    return {
        "이벤트로그": event_log_rows,
        "비즈니스이벤트": biz_event_rows,
        "의사결정추적": decision_rows,
        "메시지분류": classify_rows,
    }


# ─── 구글시트 append ───


def _get_gspread_client():
    """gspread 클라이언트 및 스프레드시트 객체 반환."""
    import gspread
    from google.oauth2.service_account import Credentials

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_file(CREDS_FILE, scopes=scopes)
    gc = gspread.authorize(creds)
    sh = gc.open_by_url(SHEET_URL)
    return sh


def append_to_sheet(sh, tab_name: str, rows: list[list[str]]) -> bool:
    """
    시트 탭에 행 append. 배치 단위로 나눠서 전송.
    성공 시 True, 실패 시 False.
    """
    if not rows:
        return True

    try:
        ws = sh.worksheet(tab_name)
    except Exception as e:
        logger.error("%s 탭 열기 실패: %s", tab_name, e)
        return False

    total = len(rows)
    for start in range(0, total, BATCH_SIZE):
        batch = rows[start:start + BATCH_SIZE]
        try:
            ws.append_rows(batch, value_input_option="USER_ENTERED")
        except Exception as e:
            logger.error(
                "%s 탭 append 실패 (행 %d~%d): %s",
                tab_name, start, start + len(batch), e,
            )
            return False
        # 배치 간 대기 (마지막 배치 후에는 불필요)
        if start + BATCH_SIZE < total:
            time.sleep(BATCH_DELAY)

    return True


# ─── 메인 동기화 로직 ───


def run_sync() -> None:
    """증분 동기화 1회 실행."""
    logger.info("=== 증분 동기화 시작 ===")

    # 1. 상태 로드
    state = load_sync_state()

    # 2. 카톡 파일 스캔
    room_files = scan_kakao_files()
    if not room_files:
        logger.info("카톡 파일 없음 -- 종료")
        save_sync_state(state)
        return

    # 3. 방별 델타 추출 + 분류
    all_tab_rows: dict[str, list[list[str]]] = {
        "이벤트로그": [],
        "비즈니스이벤트": [],
        "의사결정추적": [],
        "메시지분류": [],
    }
    room_deltas: dict[str, tuple[int, dict]] = {}  # room -> (delta_count, new_state)
    total_new = 0

    for room, files in room_files.items():
        room_state = state.get("rooms", {}).get(room, {})

        try:
            delta_text, new_state = extract_delta(room, files, room_state)
        except Exception as e:
            logger.error("%s: 파일 읽기 실패 -- 스킵: %s", room, e)
            continue

        if not delta_text.strip():
            # 변경 없음 -- state도 갱신 불필요
            continue

        # 분류
        try:
            messages = classify_delta(room, delta_text)
        except Exception as e:
            logger.error("%s: 분류 실패 -- 스킵: %s", room, e)
            continue

        if not messages:
            continue

        pipeline = get_pipeline(room)
        tab_rows = messages_to_sheet_rows(messages, room, pipeline)

        # 탭별 행 누적
        for tab_name, rows in tab_rows.items():
            all_tab_rows[tab_name].extend(rows)

        delta_count = len([m for m in messages if not m.is_date_separator and m.sender])
        room_deltas[room] = (delta_count, new_state)
        total_new += delta_count

    # 4. 새 메시지가 0건이면 시트 API 호출 안 함
    if total_new == 0:
        logger.info("새 메시지 0건 -- 시트 API 호출 없이 종료")
        save_sync_state(state)
        return

    # 5. 구글시트 append
    logger.info("총 %d건 신규 메시지 -> 시트 append 시작", total_new)

    sh = None
    try:
        sh = _get_gspread_client()
    except Exception as e:
        logger.error("구글시트 연결 실패: %s", e)
        # state 업데이트하지 않음 (다음 실행에서 재시도)
        return

    append_ok = True
    for tab_name, rows in all_tab_rows.items():
        if not rows:
            continue
        success = append_to_sheet(sh, tab_name, rows)
        if not success:
            append_ok = False
            logger.error("%s 탭 append 실패 -- state 업데이트 중단", tab_name)
            break
        logger.info("  %s: +%d행", tab_name, len(rows))
        time.sleep(BATCH_DELAY)

    # 6. state 업데이트 (시트 append 성공 시만)
    if append_ok:
        if "rooms" not in state:
            state["rooms"] = {}
        for room, (delta_count, new_state) in room_deltas.items():
            new_state["total_synced"] = new_state.get("total_synced", 0) + delta_count
            state["rooms"][room] = new_state
        save_sync_state(state)
        logger.info("sync_state.json 업데이트 완료")
    else:
        logger.warning("시트 append 실패 -> state 미갱신 (다음 실행에서 재시도)")

    # 7. 요약 로그
    parts = []
    for room, (count, _) in sorted(room_deltas.items()):
        parts.append("%s: +%d건" % (room, count))
    summary = ", ".join(parts)
    logger.info("총 +%d건 append (%s)", total_new, summary)
    logger.info("=== 증분 동기화 완료 ===")


# ─── 엔트리포인트 ───

if __name__ == "__main__":
    run_sync()
