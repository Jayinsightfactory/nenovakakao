# -*- coding: utf-8 -*-
"""
구글시트 '파이프라인현황' + '방프로파일' 탭 업데이트 스크립트

- 파이프라인현황: pipeline_config.json + 이벤트로그/의사결정추적 탭 집계
- 방프로파일: 카톡 저장 파일 분석 → 발신자/유형/자동화기회 도출

실행: python scripts/update_sheet_tabs.py
"""
from __future__ import annotations

import json
import os
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

# 프로젝트 루트를 sys.path에 추가 (core 모듈 import 용)
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import gspread
from google.oauth2.service_account import Credentials

from core.classifier import classify_delta

# ─── 설정 ───

CREDS_FILE = PROJECT_ROOT / "data" / "gsheet_credentials.json"
SHEET_URL = "https://docs.google.com/spreadsheets/d/1pXLVZqiMwWt6Vh0IhWwASBvgLtZqLnbHXMWqOLNwAXU/edit"
PIPELINE_CONFIG = PROJECT_ROOT / "data" / "pipeline_config.json"
KAKAO_DATA_DIR = Path("C:/Users/USER/Downloads/카톡대화데이터")

scopes = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]
creds = Credentials.from_service_account_file(str(CREDS_FILE), scopes=scopes)
gc = gspread.authorize(creds)
sh = gc.open_by_url(SHEET_URL)

# 카톡 메시지 패턴
MSG_PATTERN = re.compile(r"^\[(.+?)\]\s*\[(.+?)\]\s*(.+)$", re.DOTALL)
DATE_LINE = re.compile(r"^-+\s*\d{4}년.*-+$")
ROOM_NAME_PATTERN = re.compile(r"^(.+?)\s*님과 카카오톡 대화$")

# ─── 유틸 ───


def load_pipeline_config() -> dict:
    with open(PIPELINE_CONFIG, encoding="utf-8") as f:
        return json.load(f)


def get_today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def find_kakao_file_for_room(room_name: str) -> Path | None:
    """
    카톡 저장 파일 중 해당 방 이름과 매칭되는 파일 반환.
    여러 파일이 있으면 가장 최신(파일명 기준) 반환.
    """
    candidates = []
    for f in sorted(KAKAO_DATA_DIR.glob("*.txt"), reverse=True):
        try:
            with open(f, encoding="utf-8") as fp:
                first_line = fp.readline().strip()
            m = ROOM_NAME_PATTERN.match(first_line)
            if m and m.group(1).strip() == room_name:
                candidates.append(f)
        except Exception:
            continue
    return candidates[0] if candidates else None


def read_kakao_file(filepath: Path) -> str:
    """카톡 파일 전체 내용 읽기 (헤더 2줄 제거)"""
    with open(filepath, encoding="utf-8") as f:
        lines = f.readlines()
    # 첫 2줄은 헤더 (방이름, 저장일시)
    return "".join(lines[2:])


# ═══════════════════════════════════════════════════════
# 1. 파이프라인현황 탭
# ═══════════════════════════════════════════════════════


def _count_today_events_for_rooms(room_names: list[str], today: str) -> int:
    """이벤트로그 탭에서 해당 방들의 오늘 이벤트 수 카운트"""
    try:
        ws = sh.worksheet("이벤트로그")
        all_rows = ws.get_all_values()
    except gspread.WorksheetNotFound:
        return 0

    if len(all_rows) <= 1:
        return 0

    # 헤더: 시각 | 방이름 | 파이프라인 | 발신자 | 원문 | 메시지ID
    count = 0
    for row in all_rows[1:]:
        if len(row) < 2:
            continue
        time_str = row[0]
        room = row[1]
        if room in room_names and today in time_str:
            count += 1
    return count


def _count_today_events_for_rooms_all(
    all_rows: list[list[str]], room_names: list[str], today: str
) -> int:
    """미리 읽어둔 이벤트로그 데이터에서 카운트"""
    count = 0
    for row in all_rows:
        if len(row) < 2:
            continue
        if row[1] in room_names and today in row[0]:
            count += 1
    return count


def _count_open_issues_for_pipeline(
    all_rows: list[list[str]], stage_key: str, stage_name: str
) -> int:
    """
    의사결정추적 탭에서 해당 파이프라인의 미해결(결과 비어있는) 이슈 수.
    헤더: 이슈ID | 발생시각 | 발생방 | 파이프라인 | 이슈내용 |
          대응자 | 대응내용 | 대응시각 | 소요시간(분) | 결과 | 연관이벤트ID
    """
    count = 0
    for row in all_rows:
        if len(row) < 10:
            continue
        pipeline_col = row[3]
        result_col = row[9].strip()
        # 파이프라인이 매칭되고, 결과가 비어있거나 '미해결'이면
        if pipeline_col in (stage_key, stage_name):
            if not result_col or result_col == "미해결":
                count += 1
    return count


def _last_activity_for_rooms(
    all_rows: list[list[str]], room_names: list[str]
) -> str:
    """이벤트로그에서 해당 방들의 마지막 시각"""
    last = ""
    for row in all_rows:
        if len(row) < 2:
            continue
        if row[1] in room_names:
            if row[0] > last:
                last = row[0]
    return last if last else "-"


def update_pipeline_tab():
    """파이프라인현황 탭 전체 갱신"""
    print("[파이프라인현황] 데이터 수집 중...")
    config = load_pipeline_config()
    stages = config.get("pipeline_stages", {})
    today = get_today_str()

    # 이벤트로그, 의사결정추적 데이터를 한 번만 읽기
    try:
        event_ws = sh.worksheet("이벤트로그")
        event_rows = event_ws.get_all_values()[1:]  # 헤더 제외
    except gspread.WorksheetNotFound:
        event_rows = []

    try:
        decision_ws = sh.worksheet("의사결정추적")
        decision_rows = decision_ws.get_all_values()[1:]  # 헤더 제외
    except gspread.WorksheetNotFound:
        decision_rows = []

    # 파이프라인현황 탭 가져오기 (없으면 생성)
    try:
        ws = sh.worksheet("파이프라인현황")
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title="파이프라인현황", rows=20, cols=7)
        ws.append_row(
            ["단계", "단계명", "방목록", "오늘이벤트수", "미해결이슈", "최근활동", "상태"],
            value_input_option="USER_ENTERED",
        )

    rows_to_write = []
    for stage_key, stage_info in stages.items():
        name = stage_info["name"]
        rooms = stage_info.get("rooms", [])
        rooms_str = ", ".join(rooms)

        event_count = _count_today_events_for_rooms_all(event_rows, rooms, today)
        open_issues = _count_open_issues_for_pipeline(
            decision_rows, stage_key, name
        )
        last_activity = _last_activity_for_rooms(event_rows, rooms)
        status = "이슈있음" if open_issues > 0 else "정상"

        rows_to_write.append([
            stage_key, name, rooms_str,
            event_count, open_issues, last_activity, status,
        ])

    # 기존 데이터 클리어 후 헤더 + 데이터 쓰기
    ws.clear()
    header = ["단계", "단계명", "방목록", "오늘이벤트수", "미해결이슈", "최근활동", "상태"]
    all_data = [header] + rows_to_write
    ws.update(range_name="A1", values=all_data, value_input_option="USER_ENTERED")

    print(f"  -> {len(rows_to_write)}개 파이프라인 단계 기록 완료")
    for r in rows_to_write:
        print(f"     {r[0]:12s} | {r[1]:10s} | 이벤트={r[3]}, 이슈={r[4]}, 상태={r[6]}")


# ═══════════════════════════════════════════════════════
# 2. 방프로파일 탭
# ═══════════════════════════════════════════════════════


def analyze_room_file(room_name: str, filepath: Path) -> dict:
    """
    카톡 파일을 classifier.classify_delta로 분석하여 방 프로파일 생성.

    Returns:
        {purpose, main_types, top_senders, automation_opportunity, analyzed_date}
    """
    content = read_kakao_file(filepath)
    if not content.strip():
        return _empty_profile(room_name, "파일 비어있음")

    # classify_delta로 전체 메시지 분류
    messages = classify_delta(room_name, content)

    if not messages:
        return _empty_profile(room_name, "메시지 없음")

    # 발신자 통계 (날짜 구분선/빈 메시지 제외)
    sender_counter = Counter()
    major_counter = Counter()
    minor_counter = Counter()
    total_msg = 0

    for msg in messages:
        if msg.is_date_separator or not msg.sender:
            continue
        total_msg += 1
        sender_counter[msg.sender] += 1
        if msg.major:
            major_counter[msg.major] += 1
        if msg.minor:
            minor_counter[msg.minor] += 1

    # 핵심발신자 TOP 3
    top_senders = [s for s, _ in sender_counter.most_common(3)]

    # 주요유형: 대분류 TOP 3 (비율 포함)
    main_types = []
    for major, cnt in major_counter.most_common(3):
        pct = round(cnt / total_msg * 100) if total_msg else 0
        main_types.append(f"{major}({pct}%)")

    # 목적: 가장 많은 대분류 기반 추론
    purpose = _infer_purpose(room_name, major_counter, minor_counter)

    # 자동화기회: ORDER/DEFECT/INVENTORY 관련 패턴 기반
    automation = _infer_automation(room_name, major_counter, minor_counter, total_msg)

    return {
        "purpose": purpose,
        "main_types": ", ".join(main_types) if main_types else "-",
        "top_senders": ", ".join(top_senders) if top_senders else "-",
        "automation_opportunity": automation,
        "analyzed_date": get_today_str(),
        "total_messages": total_msg,
    }


def _empty_profile(room_name: str, reason: str) -> dict:
    return {
        "purpose": reason,
        "main_types": "-",
        "top_senders": "-",
        "automation_opportunity": "-",
        "analyzed_date": get_today_str(),
        "total_messages": 0,
    }


def _infer_purpose(
    room_name: str, major: Counter, minor: Counter
) -> str:
    """방 이름 + 메시지 분포로 목적 추론"""
    config = load_pipeline_config()
    stages = config.get("pipeline_stages", {})

    # 파이프라인 매칭
    for stage_key, info in stages.items():
        if room_name in info.get("rooms", []):
            base_purpose = info.get("description", info["name"])
            break
    else:
        base_purpose = "미분류"

    # 주요 대분류로 보완
    if not major:
        return base_purpose

    top_major = major.most_common(1)[0][0]
    purpose_map = {
        "ORDER": "발주/변경 관리",
        "DEFECT": "불량/클레임 관리",
        "INVENTORY": "재고/수량 관리",
        "SHIPMENT": "출고/배송 관리",
        "LOGISTICS": "물류/수입 관리",
        "FINANCE": "정산/회계",
        "SYSTEM": "전산/시스템",
        "COMMS": "커뮤니케이션",
    }

    detail = purpose_map.get(top_major, "")
    if detail and detail not in base_purpose:
        return f"{base_purpose} ({detail} 중심)"
    return base_purpose


def _infer_automation(
    room_name: str, major: Counter, minor: Counter, total: int
) -> str:
    """자동화 기회 분석"""
    if total == 0:
        return "데이터 부족"

    opportunities = []

    # ORDER 관련
    order_count = major.get("ORDER", 0)
    if order_count > 0:
        order_pct = round(order_count / total * 100)
        if order_pct >= 10:
            # 세부 분류 확인
            add_cnt = minor.get("ORDER_CHANGE_ADD", 0)
            reduce_cnt = minor.get("ORDER_CHANGE_REDUCE", 0)
            cancel_cnt = minor.get("ORDER_CHANGE_CANCEL", 0)
            replace_cnt = minor.get("ORDER_CHANGE_REPLACE", 0)
            change_total = add_cnt + reduce_cnt + cancel_cnt + replace_cnt
            if change_total > 0:
                opportunities.append(
                    f"주문변경 자동파싱→ERP ({order_pct}%, "
                    f"추가{add_cnt}/감소{reduce_cnt}/취소{cancel_cnt})"
                )
            else:
                opportunities.append(f"주문 자동 분류 ({order_pct}%)")

    # DEFECT 관련
    defect_count = major.get("DEFECT", 0)
    if defect_count > 0:
        defect_pct = round(defect_count / total * 100)
        if defect_pct >= 5:
            claim_cnt = minor.get("DEFECT_CLAIM", 0)
            report_cnt = minor.get("DEFECT_REPORT", 0)
            opportunities.append(
                f"불량/클레임 자동추적 ({defect_pct}%, "
                f"보고{report_cnt}/클레임{claim_cnt})"
            )

    # INVENTORY 관련
    inv_count = major.get("INVENTORY", 0)
    if inv_count > 0:
        inv_pct = round(inv_count / total * 100)
        if inv_pct >= 5:
            opportunities.append(f"재고수량 자동연동 ({inv_pct}%)")

    # SHIPMENT 관련
    ship_count = major.get("SHIPMENT", 0)
    if ship_count > 0:
        ship_pct = round(ship_count / total * 100)
        if ship_pct >= 5:
            opportunities.append(f"출고/배차 자동기록 ({ship_pct}%)")

    # FINANCE 관련
    fin_count = major.get("FINANCE", 0)
    if fin_count > 0:
        fin_pct = round(fin_count / total * 100)
        if fin_pct >= 5:
            opportunities.append(f"정산/단가 자동알림 ({fin_pct}%)")

    if not opportunities:
        # COMMS가 대부분이면
        comms_pct = round(major.get("COMMS", 0) / total * 100) if total else 0
        if comms_pct >= 70:
            return f"단순소통 위주 ({comms_pct}%) — 알림 필터링"
        return "패턴 분석 필요 (데이터 추가 수집)"

    return " / ".join(opportunities)


def update_room_profile_tab():
    """방프로파일 탭 전체 갱신 (14개 방 + 카톡 파일 분석)"""
    print("[방프로파일] 카톡 파일 분석 중...")
    config = load_pipeline_config()
    stages = config.get("pipeline_stages", {})

    # 모든 방 목록 수집 (파이프라인 설정 기준)
    all_rooms = []
    for stage_key, info in stages.items():
        for room in info.get("rooms", []):
            all_rooms.append(room)

    # room_mapping.json에서 추가 방 확인
    mapping_file = PROJECT_ROOT / "data" / "room_mapping.json"
    if mapping_file.exists():
        with open(mapping_file, encoding="utf-8") as f:
            mapping = json.load(f)
        for room in mapping:
            if room not in all_rooms:
                all_rooms.append(room)

    print(f"  분석 대상: {len(all_rooms)}개 방")

    # 방프로파일 탭 가져오기 (없으면 생성)
    try:
        ws = sh.worksheet("방프로파일")
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title="방프로파일", rows=30, cols=6)

    rows_to_write = []
    for room_name in all_rooms:
        filepath = find_kakao_file_for_room(room_name)
        if filepath:
            print(f"  [{room_name}] -> {filepath.name}")
            try:
                profile = analyze_room_file(room_name, filepath)
            except Exception as e:
                print(f"    분석 실패: {e}")
                profile = _empty_profile(room_name, f"분석 실패: {e}")
        else:
            print(f"  [{room_name}] -> 카톡 파일 없음")
            profile = _empty_profile(room_name, "카톡 파일 미수집")

        rows_to_write.append([
            room_name,
            profile["purpose"],
            profile["main_types"],
            profile["top_senders"],
            profile["automation_opportunity"],
            profile["analyzed_date"],
        ])

    # 기존 데이터 클리어 후 헤더 + 데이터 쓰기
    ws.clear()
    header = ["방이름", "목적", "주요유형", "핵심발신자", "자동화기회", "분석일"]
    all_data = [header] + rows_to_write
    ws.update(range_name="A1", values=all_data, value_input_option="USER_ENTERED")

    print(f"  -> {len(rows_to_write)}개 방 프로파일 기록 완료")
    for r in rows_to_write:
        print(f"     {r[0]:25s} | {r[1][:20]:20s} | 발신자: {r[3][:30]}")


# ═══════════════════════════════════════════════════════
# 메인
# ═══════════════════════════════════════════════════════


def main():
    print("=" * 60)
    print("구글시트 '파이프라인현황' + '방프로파일' 탭 업데이트")
    print(f"날짜: {get_today_str()}")
    print("=" * 60)

    # 1. 파이프라인현황
    update_pipeline_tab()

    print()

    # 2. 방프로파일
    update_room_profile_tab()

    print()
    print("완료!")


if __name__ == "__main__":
    main()
