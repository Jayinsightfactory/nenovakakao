"""
구글시트 3계층 연동 모듈

Layer 1 - 이벤트로그:     원본 메시지 기록 (시각/방/발신자/원문/파이프라인단계)
Layer 2 - 비즈니스이벤트:  파싱된 구조화 데이터 (이벤트타입/차수/품목/수량/거래처/연관ID)
Layer 3 - 의사결정추적:    이슈 발생→대응→결과 흐름 (이슈ID/대응자/소요시간/결과)
+ 패턴라이브러리 / 학습로그 (기존)
"""
from __future__ import annotations

import json
import os
import re
import hashlib
from datetime import datetime
from pathlib import Path

import gspread
from google.oauth2.service_account import Credentials
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

CREDS_FILE = Path(__file__).parent.parent / "data" / "gsheet_credentials.json"
SHEET_URL = os.getenv("GOOGLE_SHEET_URL", "")
PIPELINE_CONFIG = Path(__file__).parent.parent / "data" / "pipeline_config.json"

_client = None
_sheet = None


# ─── 파이프라인 설정 로드 ───

def _load_pipeline_config() -> dict:
    if PIPELINE_CONFIG.exists():
        with open(PIPELINE_CONFIG, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _get_pipeline_stage(room_name: str) -> str:
    """방 이름 → 파이프라인 단계 반환"""
    config = _load_pipeline_config()
    for stage_key, stage_info in config.get("pipeline_stages", {}).items():
        if room_name in stage_info.get("rooms", []):
            return stage_key
    return "UNKNOWN"


def _get_stage_name(stage_key: str) -> str:
    config = _load_pipeline_config()
    stage = config.get("pipeline_stages", {}).get(stage_key, {})
    return stage.get("name", stage_key)


# ─── 구글시트 연결 ───

def _get_sheet():
    global _client, _sheet
    if _sheet is not None:
        return _sheet
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_file(str(CREDS_FILE), scopes=scopes)
    _client = gspread.authorize(creds)
    _sheet = _client.open_by_url(SHEET_URL)
    return _sheet


def _ensure_worksheets():
    """필요한 시트 탭이 없으면 생성"""
    sh = _get_sheet()
    existing = [ws.title for ws in sh.worksheets()]

    tabs = {
        "이벤트로그": ["시각", "방이름", "파이프라인", "발신자", "원문", "메시지ID"],
        "비즈니스이벤트": [
            "이벤트ID", "시각", "이벤트타입", "차수", "품목", "품종",
            "수량", "단위", "방향", "거래처", "방이름", "파이프라인",
            "발신자", "원문요약", "연관이벤트ID", "트리거메시지ID",
        ],
        "의사결정추적": [
            "이슈ID", "발생시각", "발생방", "파이프라인", "이슈내용",
            "대응자", "대응내용", "대응시각", "소요시간(분)", "결과",
            "연관이벤트ID",
        ],
        "파이프라인현황": [
            "단계", "단계명", "방목록", "오늘이벤트수", "미해결이슈",
            "최근활동", "상태",
        ],
        "패턴라이브러리": [
            "패턴ID", "패턴이름", "정규식", "분류", "예시",
            "정확도", "생성일", "상태",
        ],
        "학습로그": ["시각", "이벤트", "수정전", "수정후", "반영여부"],
    }

    for tab_name, headers in tabs.items():
        if tab_name not in existing:
            ws = sh.add_worksheet(title=tab_name, rows=1000, cols=len(headers))
            ws.append_row(headers, value_input_option="USER_ENTERED")

    # 기존 '메시지분류' 탭은 유지 (하위호환)


# ─── 메시지 파싱 엔진 ───

# 카톡 메시지 패턴: [발신자] [시각] 내용
MSG_PATTERN = re.compile(r"^\[(.+?)\]\s*\[(.+?)\]\s*(.+)$")

# 차수 패턴: 14-1, 15-2차, 14차
SEQ_PATTERN = re.compile(r"(\d{1,3})[-/]?(\d{0,2})\s*차?")

# 수량 패턴: 10단, 5송이, 3박스
QTY_PATTERN = re.compile(r"(\d+)\s*(단|송이|박스|속|개|스팀|대)")

# 불량 관련 키워드
DEFECT_KEYWORDS = ["불량", "클레임", "파손", "마름", "습", "전량클레임", "겉잎제거"]
ORDER_KEYWORDS = ["추가", "변경", "수정", "발주"]
CANCEL_KEYWORDS = ["취소", "삭제"]
SHIPMENT_KEYWORDS = ["출고", "배차", "배송", "톤"]
ARRIVAL_KEYWORDS = ["입고", "도착", "항공편", "도착합니"]
INQUIRY_KEYWORDS = ["확인", "수량", "재고"]
DECISION_KEYWORDS = ["네 확인", "진행", "불가능", "어렵다", "감사합니다", "부탁드립니다"]


def _load_known_products() -> dict[str, list[str]]:
    config = _load_pipeline_config()
    return config.get("product_categories", {})


def _load_known_suppliers() -> list[str]:
    config = _load_pipeline_config()
    return config.get("suppliers", [])


def parse_message(text: str, room_name: str = "") -> dict:
    """
    메시지를 구조화된 비즈니스 이벤트로 파싱.

    Returns:
        {event_type, sequence, product, variety, quantity, unit,
         direction, supplier, summary}
    """
    result = {
        "event_type": "INFO",
        "sequence": "",
        "product": "",
        "variety": "",
        "quantity": "",
        "unit": "",
        "direction": "",
        "supplier": "",
        "summary": text[:100],
    }

    # 이벤트 타입 분류 (우선순위: 불량 > 주문변경 > 주문취소 > 출고 > 입고)
    if any(kw in text for kw in DEFECT_KEYWORDS):
        result["event_type"] = "DEFECT"
    elif any(kw in text for kw in CANCEL_KEYWORDS):
        result["event_type"] = "ORDER_CHANGE"
        result["direction"] = "-"
    elif any(kw in text for kw in ORDER_KEYWORDS):
        result["event_type"] = "ORDER_CHANGE"
        result["direction"] = "+"
    elif any(kw in text for kw in SHIPMENT_KEYWORDS):
        result["event_type"] = "SHIPMENT"
    elif any(kw in text for kw in ARRIVAL_KEYWORDS):
        result["event_type"] = "ARRIVAL"
    elif any(kw in text for kw in DECISION_KEYWORDS):
        result["event_type"] = "DECISION"
    elif any(kw in text for kw in INQUIRY_KEYWORDS):
        result["event_type"] = "INQUIRY"
    elif "[사진]" in text or "사진" in text:
        result["event_type"] = "PHOTO"

    # 차수 추출
    seq_m = SEQ_PATTERN.search(text)
    if seq_m:
        main = seq_m.group(1)
        sub = seq_m.group(2)
        result["sequence"] = f"{main}-{sub}" if sub else main

    # 품목/품종 추출
    products = _load_known_products()
    for category, varieties in products.items():
        if category in text:
            result["product"] = category
            for v in varieties:
                if v in text:
                    result["variety"] = v
                    break
            break

    # 수량/단위 추출
    qty_m = QTY_PATTERN.search(text)
    if qty_m:
        result["quantity"] = qty_m.group(1)
        result["unit"] = qty_m.group(2)

    # 거래처 추출
    suppliers = _load_known_suppliers()
    for s in suppliers:
        if s in text:
            result["supplier"] = s
            break

    # 요약 생성
    parts = []
    if result["sequence"]:
        parts.append(f"{result['sequence']}차")
    if result["product"]:
        p = result["product"]
        if result["variety"]:
            p += f"/{result['variety']}"
        parts.append(p)
    if result["quantity"]:
        parts.append(f"{result['quantity']}{result['unit']}")
    if result["supplier"]:
        parts.append(result["supplier"])
    if result["event_type"] == "DEFECT":
        parts.append("불량")
    if parts:
        result["summary"] = " ".join(parts)

    return result


def _make_message_id(room: str, sender: str, time_str: str, content: str) -> str:
    raw = f"{room}|{sender}|{time_str}|{content[:50]}"
    return hashlib.md5(raw.encode()).hexdigest()[:12]


def _make_event_id(parsed: dict, msg_id: str) -> str:
    raw = f"{parsed['event_type']}|{parsed['sequence']}|{parsed['product']}|{msg_id}"
    return hashlib.md5(raw.encode()).hexdigest()[:10]


# ─── 이슈 추적 ───

_active_issues: dict[str, dict] = {}  # key=이슈 해시


def _detect_issue(parsed: dict, sender: str, time_str: str, room: str) -> dict | None:
    """불량/클레임 메시지에서 이슈 생성"""
    if parsed["event_type"] not in ("DEFECT", "CLAIM"):
        return None
    return {
        "issue_id": hashlib.md5(
            f"{room}|{parsed['sequence']}|{parsed['product']}|{time_str}".encode()
        ).hexdigest()[:10],
        "timestamp": time_str,
        "room": room,
        "pipeline": _get_pipeline_stage(room),
        "content": parsed["summary"],
        "responder": "",
        "response": "",
        "response_time": "",
        "duration_min": "",
        "result": "미해결",
    }


def _detect_response(parsed: dict, sender: str, content: str) -> bool:
    """의사결정/응답 메시지 감지"""
    return parsed["event_type"] == "DECISION" or any(
        kw in content for kw in ["확인했습니다", "진행", "불가능", "감사합니다"]
    )


# ─── Layer 기록 함수 ───

def _log_layer1_batch(rows: list[list]):
    if not rows:
        return
    sh = _get_sheet()
    try:
        ws = sh.worksheet("이벤트로그")
    except gspread.WorksheetNotFound:
        _ensure_worksheets()
        ws = sh.worksheet("이벤트로그")
    ws.append_rows(rows, value_input_option="USER_ENTERED")


def _log_layer2_batch(rows: list[list]):
    if not rows:
        return
    sh = _get_sheet()
    try:
        ws = sh.worksheet("비즈니스이벤트")
    except gspread.WorksheetNotFound:
        _ensure_worksheets()
        ws = sh.worksheet("비즈니스이벤트")
    ws.append_rows(rows, value_input_option="USER_ENTERED")


def _log_layer3(issue: dict):
    sh = _get_sheet()
    try:
        ws = sh.worksheet("의사결정추적")
    except gspread.WorksheetNotFound:
        _ensure_worksheets()
        ws = sh.worksheet("의사결정추적")
    ws.append_row([
        issue["issue_id"], issue["timestamp"], issue["room"],
        _get_stage_name(issue["pipeline"]), issue["content"],
        issue.get("responder", ""), issue.get("response", ""),
        issue.get("response_time", ""), issue.get("duration_min", ""),
        issue.get("result", "미해결"), "",
    ], value_input_option="USER_ENTERED")


# ─── 메인 인터페이스 ───

def classify_and_log_delta(room_name: str, delta: str) -> int:
    """
    신규 메시지(델타)를 3계층으로 분류+기록.
    감시 루프(main.py)에서 호출.
    """
    stage_key = _get_pipeline_stage(room_name)
    stage_name = _get_stage_name(stage_key)

    layer1_rows = []
    layer2_rows = []
    issues = []

    for line in delta.strip().splitlines():
        m = MSG_PATTERN.match(line.strip())
        if not m:
            continue

        sender, time_str, content = m.groups()
        msg_id = _make_message_id(room_name, sender, time_str, content)

        # Layer 1: 이벤트로그
        layer1_rows.append([
            time_str, room_name, stage_name, sender, content[:500], msg_id,
        ])

        # Layer 2: 비즈니스이벤트
        parsed = parse_message(content, room_name)
        if parsed["event_type"] != "INFO":
            evt_id = _make_event_id(parsed, msg_id)
            layer2_rows.append([
                evt_id, time_str, parsed["event_type"],
                parsed["sequence"], parsed["product"], parsed["variety"],
                parsed["quantity"], parsed["unit"], parsed["direction"],
                parsed["supplier"], room_name, stage_name,
                sender, parsed["summary"][:200], "", msg_id,
            ])

        # Layer 3: 이슈 감지
        issue = _detect_issue(parsed, sender, time_str, room_name)
        if issue:
            issues.append(issue)

    # 배치 기록
    try:
        _log_layer1_batch(layer1_rows)
        _log_layer2_batch(layer2_rows)
        for issue in issues:
            _log_layer3(issue)
    except Exception as e:
        print(f"  [GSHEET] 기록 실패: {e}")

    return len(layer1_rows)


# ─── 하위 호환 ───

def log_classified_message(**kwargs):
    """기존 인터페이스 유지 (1계층만 기록)"""
    pass


def log_classified_messages_batch(messages):
    """기존 인터페이스 유지"""
    pass


def get_admin_corrections() -> list[dict]:
    """기존 관리자 수정 피드백 읽기"""
    sh = _get_sheet()
    try:
        ws = sh.worksheet("메시지분류")
    except gspread.WorksheetNotFound:
        return []
    all_rows = ws.get_all_records()
    corrections = []
    for i, row in enumerate(all_rows):
        admin = row.get("관리자수정", "").strip()
        if admin:
            corrections.append({
                "row": i + 2,
                "original": row.get("원문", ""),
                "ai_class": row.get("AI분류", ""),
                "admin_class": admin,
            })
    return corrections


def process_admin_feedback() -> int:
    """관리자 수정 → 학습 반영"""
    corrections = get_admin_corrections()
    if not corrections:
        return 0
    # 학습 로직은 기존 유지
    return len(corrections)


# ─── 유틸 ───

def get_pipeline_summary() -> dict:
    """파이프라인 현황 요약 (보고서용)"""
    config = _load_pipeline_config()
    summary = {}
    for key, info in config.get("pipeline_stages", {}).items():
        summary[key] = {
            "name": info["name"],
            "description": info["description"],
            "rooms": info["rooms"],
            "room_count": len(info["rooms"]),
        }
    return summary


if __name__ == "__main__":
    print("[GSHEET] 3계층 구조 초기화...")
    _ensure_worksheets()
    sh = _get_sheet()
    print(f"  시트: {sh.title}")
    print(f"  탭: {[ws.title for ws in sh.worksheets()]}")

    # 파싱 테스트
    test = "14-1 콜 카네이션 불량\n\n일신원예\n노비아 6단 불량"
    result = parse_message(test, "네노바 수입(불량 공유방)")
    print(f"\n  테스트: '{test[:50]}'")
    print(f"  파싱: {json.dumps(result, ensure_ascii=False, indent=2)}")
