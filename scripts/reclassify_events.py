# -*- coding: utf-8 -*-
"""
재분류 스크립트 — 개선된 classifier v2.0으로 기존 1,332건 재분류

카톡 저장 파일 전체를 다시 파싱 → classify_delta() → 구글시트 4탭 덮어쓰기
  - 이벤트로그: 원본 메시지 기록
  - 비즈니스이벤트: 파싱 구조화 데이터
  - 의사결정추적: DEFECT 이벤트에서 이슈 자동 생성
  - 메시지분류: 개선된 분류 결과 (관리자 피드백용)
"""
from __future__ import annotations

import glob
import hashlib
import json
import os
import sys
import time
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, "C:/Users/USER/nenova_agent")

import gspread
from google.oauth2.service_account import Credentials
from core.classifier import classify_delta, ParsedMessage

CREDS_FILE = "C:/Users/USER/nenova_agent/data/gsheet_credentials.json"
SHEET_URL = "https://docs.google.com/spreadsheets/d/1pXLVZqiMwWt6Vh0IhWwASBvgLtZqLnbHXMWqOLNwAXU/edit"
CHAT_DIR = "C:/Users/USER/Downloads/카톡대화데이터"
PIPELINE_CONFIG = "C:/Users/USER/nenova_agent/data/pipeline_config.json"

BATCH_SIZE = 1000  # 한 번에 append할 최대 행 수


# ─── 파이프라인 단계 조회 ───

_pipeline_cache = None

def _load_pipeline():
    global _pipeline_cache
    if _pipeline_cache is None:
        with open(PIPELINE_CONFIG, encoding="utf-8") as f:
            _pipeline_cache = json.load(f)
    return _pipeline_cache


def get_pipeline_stage(room_name: str) -> str:
    """방 이름 → 파이프라인 단계명 반환"""
    config = _load_pipeline()
    for stage_key, info in config.get("pipeline_stages", {}).items():
        if room_name in info.get("rooms", []):
            return info.get("name", stage_key)
    return "기타"


# ─── 구글시트 연결 ───

def get_sheet():
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_file(CREDS_FILE, scopes=scopes)
    client = gspread.authorize(creds)
    return client.open_by_url(SHEET_URL)


# ─── ID 생성 ───

def make_message_id(room: str, sender: str, time_str: str, content: str) -> str:
    raw = f"{room}|{sender}|{time_str}|{content[:50]}"
    return hashlib.md5(raw.encode()).hexdigest()[:6]


def make_event_id(major: str, sequence: str, product: str, msg_id: str) -> str:
    raw = f"{major}|{sequence}|{product}|{msg_id}"
    return hashlib.md5(raw.encode()).hexdigest()[:10]


# ─── 카톡 파일 파싱 ───

def extract_room_name(filepath: str) -> str:
    """파일 첫 줄에서 방이름 추출: 'XXX 님과 카카오톡 대화' → 'XXX'"""
    with open(filepath, encoding="utf-8") as f:
        first_line = f.readline().strip()
    # "XXX 님과 카카오톡 대화" 패턴
    suffix = " 님과 카카오톡 대화"
    if first_line.endswith(suffix):
        return first_line[: -len(suffix)]
    return first_line


def read_chat_content(filepath: str) -> str:
    """파일 내용 읽기 (첫 2줄 헤더 제외)"""
    with open(filepath, encoding="utf-8") as f:
        lines = f.readlines()
    # 첫 줄: 방이름, 둘째 줄: 저장 날짜 → 나머지가 대화 내용
    return "".join(lines[2:])


def load_all_chat_files() -> list[tuple[str, str]]:
    """
    모든 카톡 txt 파일을 로드.
    Returns: [(room_name, content), ...]
    """
    pattern = os.path.join(CHAT_DIR, "*.txt")
    files = sorted(glob.glob(pattern))
    result = []
    for fp in files:
        try:
            room = extract_room_name(fp)
            content = read_chat_content(fp)
            if content.strip():
                result.append((room, content))
        except Exception as e:
            print(f"  [SKIP] {os.path.basename(fp)}: {e}")
    return result


# ─── 재분류 실행 ───

def reclassify_all() -> dict:
    """
    모든 카톡 파일 → classify_delta → 4탭 데이터 생성.

    Returns:
        {
            "event_log_rows": [...],
            "business_event_rows": [...],
            "decision_rows": [...],
            "message_class_rows": [...],
            "stats": {...},
        }
    """
    chat_files = load_all_chat_files()
    print(f"\n[1/4] 카톡 파일 {len(chat_files)}개 로드 완료")

    event_log_rows = []       # 이벤트로그
    business_event_rows = []  # 비즈니스이벤트
    decision_rows = []        # 의사결정추적
    message_class_rows = []   # 메시지분류

    total_messages = 0
    total_business = 0
    total_defects = 0

    for room_name, content in chat_files:
        pipeline = get_pipeline_stage(room_name)
        messages = classify_delta(room_name, content)

        for msg in messages:
            if msg.is_date_separator:
                continue

            total_messages += 1
            msg_id = make_message_id(room_name, msg.sender, msg.time_str, msg.content)

            # ── 이벤트로그 ──
            event_log_rows.append([
                msg.time_str,
                room_name,
                pipeline,
                msg.sender,
                msg.content[:500],
                msg_id,
            ])

            # ── 메시지분류 ──
            message_class_rows.append([
                msg.time_str,
                room_name,
                msg.sender,
                msg.content[:500],
                msg.minor,          # AI분류 (minor)
                msg.product,
                msg.sequence,
                msg.quantity,
                "",                 # 관리자수정 (빈칸)
                msg.direction,      # 비고 (direction)
            ])

            # ── 비즈니스이벤트 (COMMS/INFO 제외) ──
            if msg.major and msg.major != "COMMS":
                total_business += 1
                evt_id = make_event_id(msg.major, msg.sequence, msg.product, msg_id)

                business_event_rows.append([
                    evt_id,
                    msg.time_str,
                    msg.major,          # 이벤트타입 (major)
                    msg.sequence,
                    msg.product,
                    msg.variety,
                    msg.quantity,
                    msg.unit,
                    msg.direction,
                    msg.supplier,
                    room_name,
                    pipeline,
                    msg.sender,
                    msg.content[:200],  # 원문요약
                    msg.thread_id,      # 연관이벤트ID
                    msg_id,             # 트리거메시지ID
                ])

                # ── 의사결정추적 (DEFECT 이벤트 → 이슈) ──
                if msg.major == "DEFECT":
                    total_defects += 1
                    issue_id = hashlib.md5(
                        f"{room_name}|{msg.sequence}|{msg.product}|{msg.time_str}".encode()
                    ).hexdigest()[:10]

                    # 요약 생성
                    parts = []
                    if msg.sequence:
                        parts.append(f"{msg.sequence}차")
                    if msg.product:
                        p = msg.product
                        if msg.variety:
                            p += f"/{msg.variety}"
                        parts.append(p)
                    if msg.supplier:
                        parts.append(msg.supplier)
                    parts.append("불량")
                    summary = " ".join(parts) if parts else msg.content[:50]

                    decision_rows.append([
                        issue_id,
                        msg.time_str,
                        room_name,
                        pipeline,
                        summary,
                        "",   # 대응자
                        "",   # 대응내용
                        "",   # 대응시각
                        "",   # 소요시간(분)
                        "미해결",  # 결과
                        evt_id,    # 연관이벤트ID
                    ])

    stats = {
        "files": len(chat_files),
        "total_messages": total_messages,
        "business_events": total_business,
        "defect_issues": total_defects,
        "message_classifications": len(message_class_rows),
    }

    print(f"[2/4] 분류 완료:")
    print(f"  - 전체 메시지: {total_messages}")
    print(f"  - 비즈니스이벤트: {total_business}")
    print(f"  - 불량 이슈: {total_defects}")
    print(f"  - 메시지분류: {len(message_class_rows)}")

    return {
        "event_log_rows": event_log_rows,
        "business_event_rows": business_event_rows,
        "decision_rows": decision_rows,
        "message_class_rows": message_class_rows,
        "stats": stats,
    }


# ─── 시트 업데이트 ───

def batch_append(ws, rows: list[list], batch_size: int = BATCH_SIZE):
    """대량 행을 batch_size씩 나눠 append (API 할당량 보호)"""
    for i in range(0, len(rows), batch_size):
        chunk = rows[i : i + batch_size]
        ws.append_rows(chunk, value_input_option="USER_ENTERED")
        if i + batch_size < len(rows):
            print(f"    ... {i + batch_size}/{len(rows)}행 전송 완료, 잠시 대기...")
            time.sleep(5)  # API 할당량 보호


def clear_and_write_tab(sh, tab_name: str, headers: list[str], rows: list[list]):
    """탭 클리어 → 헤더 + 데이터 쓰기"""
    try:
        ws = sh.worksheet(tab_name)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=tab_name, rows=max(1000, len(rows) + 10), cols=len(headers))

    print(f"  [{tab_name}] 기존 데이터 클리어...")
    ws.clear()

    # 행 수 부족하면 확장
    needed = len(rows) + 10
    if ws.row_count < needed:
        ws.resize(rows=needed)

    # 헤더 쓰기
    ws.append_row(headers, value_input_option="USER_ENTERED")

    # 데이터 배치 쓰기
    if rows:
        print(f"  [{tab_name}] {len(rows)}행 쓰기 시작...")
        batch_append(ws, rows)
        print(f"  [{tab_name}] 완료!")
    else:
        print(f"  [{tab_name}] 데이터 없음 (헤더만 기록)")


def update_sheets(data: dict):
    """구글시트 4탭 업데이트"""
    sh = get_sheet()
    print(f"\n[3/4] 구글시트 업데이트 시작: {sh.title}")

    # 1. 이벤트로그
    clear_and_write_tab(sh, "이벤트로그",
        ["시각", "방이름", "파이프라인", "발신자", "원문", "메시지ID"],
        data["event_log_rows"])
    time.sleep(3)

    # 2. 비즈니스이벤트
    clear_and_write_tab(sh, "비즈니스이벤트",
        ["이벤트ID", "시각", "이벤트타입", "차수", "품목", "품종",
         "수량", "단위", "방향", "거래처", "방이름", "파이프라인",
         "발신자", "원문요약", "연관이벤트ID", "트리거메시지ID"],
        data["business_event_rows"])
    time.sleep(3)

    # 3. 의사결정추적
    clear_and_write_tab(sh, "의사결정추적",
        ["이슈ID", "발생시각", "발생방", "파이프라인", "이슈내용",
         "대응자", "대응내용", "대응시각", "소요시간(분)", "결과", "연관이벤트ID"],
        data["decision_rows"])
    time.sleep(3)

    # 4. 메시지분류
    clear_and_write_tab(sh, "메시지분류",
        ["시각", "방이름", "발신자", "원문", "AI분류", "품목", "차수", "수량", "관리자수정", "비고"],
        data["message_class_rows"])

    print(f"\n[4/4] 업데이트 완료!")


# ─── 메인 ───

def main():
    print("=" * 60)
    print("  재분류 스크립트 - classifier v2.0")
    print("  개선사항: 멀티라인, 복합품명, 거래처별칭, 방향성, 원산지")
    print("=" * 60)

    # 1. 재분류 실행 (시트 쓰기 전)
    data = reclassify_all()

    # 2. 통계 확인
    stats = data["stats"]
    print(f"\n{'─' * 40}")
    print(f"  파일: {stats['files']}개")
    print(f"  메시지: {stats['total_messages']}건")
    print(f"  비즈니스이벤트: {stats['business_events']}건")
    print(f"  불량이슈: {stats['defect_issues']}건")
    print(f"  메시지분류: {stats['message_classifications']}건")
    print(f"{'─' * 40}")

    # 3. 확인 프롬프트
    if sys.stdin.isatty():
        confirm = input("\n기존 데이터를 삭제하고 재분류합니다. 계속? (y/n): ").strip().lower()
        if confirm != "y":
            print("취소되었습니다.")
            return
    else:
        print("\n비대화 모드 - 자동 진행")

    # 4. 시트 업데이트
    update_sheets(data)

    print("\n완료! 구글시트를 확인하세요.")
    print(f"  {SHEET_URL}")


if __name__ == "__main__":
    main()
