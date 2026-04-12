# -*- coding: utf-8 -*-
"""
네노바 카카오톡 메시지 분류기 v2.0

3레벨 분류: 대분류 > 중분류 > 세부태그
문맥 인식: 사진+설명 병합, 연속메시지 병합, 방 컨텍스트, 스레드 추적
구조 추출: 차수, 품목/품종, 수량/단위, 거래처, 원산지, 금액
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path

SPEC_FILE = Path(__file__).parent.parent / "data" / "classification_spec.json"

# ─── 메시지 파싱 ───

MSG_PATTERN = re.compile(r"^\[(.+?)\]\s*\[(.+?)\]\s*(.+)$", re.DOTALL)
TIME_PATTERN = re.compile(r"(오전|오후)\s*(\d{1,2}):(\d{2})")
SEQ_PATTERNS = [
    re.compile(r"(\d{1,2})[-/](\d{1,2})\s*차?"),
    re.compile(r"(\d{1,2})\s*차"),
]
QTY_PATTERN = re.compile(r"(\d+)\s*(단|송이|박스|속|개|스팀|대|파렛트|BOX|box)")
AMOUNT_PATTERN = re.compile(r"([\d,]+)\s*원")
ARROW_PATTERN = re.compile(r"(\S+)\s*(?:→|->|>>)\s*(\S+)")
DATE_LINE = re.compile(r"^-+\s*\d{4}년.*-+$")
PHOTO_PATTERN = re.compile(r"^사진(\s*\d+장)?$")


def _load_spec() -> dict:
    if SPEC_FILE.exists():
        with open(SPEC_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}


_spec = None
def spec():
    global _spec
    if _spec is None:
        _spec = _load_spec()
    return _spec


# ─── 데이터 클래스 ───

@dataclass
class ParsedMessage:
    sender: str = ""
    time_str: str = ""
    time_minutes: int = -1  # 0시 기준 분
    content: str = ""
    room: str = ""

    # 분류 결과
    major: str = ""        # 대분류: ORDER, DEFECT, FINANCE, ...
    minor: str = ""        # 중분류: ORDER_CHANGE_ADD, DEFECT_REPORT, ...
    confidence: float = 0.0

    # 구조 추출
    sequence: str = ""     # 차수: "14-1", "15"
    product: str = ""      # 품목: "카네이션"
    variety: str = ""      # 품종: "문라이트"
    quantity: str = ""     # 수량: "10"
    unit: str = ""         # 단위: "단"
    supplier: str = ""     # 거래처: "주광"
    origin: str = ""       # 원산지: "콜롬비아"
    amount: str = ""       # 금액: "20,000,000"
    direction: str = ""    # 방향: "+", "-", ""

    # 메타
    is_photo: bool = False
    is_file: bool = False
    is_date_separator: bool = False
    thread_id: str = ""
    merged_with: list = field(default_factory=list)


def parse_time(time_str: str) -> int:
    """시각 문자열 → 0시 기준 분. 실패 시 -1."""
    m = TIME_PATTERN.search(time_str)
    if not m:
        return -1
    ampm, h, mm = m.group(1), int(m.group(2)), int(m.group(3))
    if ampm == "오후" and h != 12:
        h += 12
    elif ampm == "오전" and h == 12:
        h = 0
    return h * 60 + mm


# ─── 구조 추출 ───

def extract_sequence(text: str) -> str:
    for pat in SEQ_PATTERNS:
        m = pat.search(text)
        if m:
            groups = m.groups()
            if len(groups) == 2:
                return f"{groups[0]}-{groups[1]}"
            return groups[0]
    return ""


COMPOUND_PRODUCTS = {
    "스프레이카네이션": ("카네이션", "스프레이"),
    "미니카네이션": ("카네이션", "미니"),
    "콜 카네이션": ("카네이션", ""),
    "콜카네이션": ("카네이션", ""),
    "콜 장미": ("장미", ""),
    "콜장미": ("장미", ""),
    "중국 장미": ("장미", ""),
    "중국장미": ("장미", ""),
    "콜롬비아 장미": ("장미", ""),
    "콜 수국": ("수국", ""),
    "콜수국": ("수국", ""),
    "스프레이장미": ("장미", "스프레이"),
}

# 긴 키부터 매칭하기 위해 정렬된 리스트
_COMPOUND_KEYS_SORTED = sorted(COMPOUND_PRODUCTS.keys(), key=len, reverse=True)


def extract_product(text: str) -> tuple[str, str]:
    """(품목, 품종) 반환."""
    # 1. 복합 품명 우선 매칭 (긴 키부터)
    for compound in _COMPOUND_KEYS_SORTED:
        if compound in text:
            cat, prefix = COMPOUND_PRODUCTS[compound]
            # spec에서 해당 품목의 품종 목록 조회
            s = spec()
            categories = s.get("extraction_rules", {}).get("product", {}).get("categories", {})
            varieties = categories.get(cat, [])
            for v in varieties:
                if v in text:
                    # prefix가 있으면 품종 앞에 붙임 (예: "스프레이" + "화이트")
                    return cat, (prefix + v) if prefix else v
            return cat, prefix  # 품종 매칭 없으면 prefix 자체를 품종으로

    # 2. 기존 단순 매칭
    s = spec()
    categories = s.get("extraction_rules", {}).get("product", {}).get("categories", {})
    for cat, varieties in categories.items():
        if cat in text:
            for v in varieties:
                if v in text:
                    return cat, v
            return cat, ""
    return "", ""


def extract_quantity(text: str) -> tuple[str, str]:
    """(수량, 단위) 반환."""
    m = QTY_PATTERN.search(text)
    if m:
        return m.group(1), m.group(2)
    return "", ""


# ─── 거래처 별칭 정규화 테이블 ───
# 키(별칭) → 값(정규화된 거래처명)
SUPPLIER_ALIASES = {
    "주광농원": "주광",
    "소재장터": "소재2호",
    # 축약형 → 정식명
    "레바논": "레바논꽃방",
    "참좋은": "참좋은원예",
    "일신": "일신원예",
    "대지": "대지원예",
    "상희": "상희원예",
    "광주천사": "광주천사",
    "친구": "친구플라워",
}


def extract_supplier(text: str) -> str:
    s = spec()
    known = s.get("extraction_rules", {}).get("supplier", {}).get("known", [])
    # 길이 긴 것부터 매칭 (부분 매칭 방지)
    for sup in sorted(known, key=len, reverse=True):
        if sup in text:
            # known 리스트에서 매칭 → 별칭이면 정규화
            return SUPPLIER_ALIASES.get(sup, sup)

    # known에 없으면 별칭 키로도 검색 (길이 긴 것 우선)
    for alias in sorted(SUPPLIER_ALIASES.keys(), key=len, reverse=True):
        if alias in text:
            return SUPPLIER_ALIASES[alias]

    return ""


# ─── 원산지 별칭 정규화 테이블 ───
ORIGIN_ALIASES = {
    "콜": "콜롬비아",
    "콜롬": "콜롬비아",
    "에콰": "에콰도르",
    "멜로디": "중국",
}


def extract_origin(text: str) -> str:
    """원산지 추출. 긴 키워드 우선 매칭 + 별칭 정규화."""
    s = spec()
    known = s.get("extraction_rules", {}).get("origin", {}).get("known", [])
    # 긴 키워드부터 매칭 (콜롬비아 > 콜롬 > 콜)
    for o in sorted(known, key=len, reverse=True):
        if o in text:
            # 별칭이면 정규화된 이름 반환
            return ORIGIN_ALIASES.get(o, o)
    return ""


def extract_amount(text: str) -> str:
    m = AMOUNT_PATTERN.search(text)
    return m.group(1) if m else ""


# ─── 분류 엔진 ───

# 키워드 → (대분류, 중분류, 우선순위)
CLASSIFICATION_RULES = [
    # 불량/클레임 (최고 우선)
    (["전량클레임"], "DEFECT", "DEFECT_CLAIM", 100),
    (["클레임 불가", "클레임은 진행하기 어렵"], "DEFECT", "DEFECT_REJECT", 99),
    (["불량 차감", "차감"], "DEFECT", "DEFECT_DEDUCTION", 98),
    (["불량"], "DEFECT", "DEFECT_REPORT", 95),
    (["클레임"], "DEFECT", "DEFECT_CLAIM", 95),
    (["마름", "겉잎제거", "패킹 불량"], "DEFECT", "DEFECT_REPORT", 90),

    # 정산/회계
    (["세금계산서", "계산서 발행"], "FINANCE", "FIN_INVOICE", 85),
    (["현금수령", "현금 수령", "입금요청", "입금"], "FINANCE", "FIN_PAYMENT", 85),
    (["매출액", "판매등록", "판매내역"], "FINANCE", "FIN_SALES", 80),
    (["선발행"], "FINANCE", "FIN_INVOICE", 80),
    (["원가", "단가"], "FINANCE", "FIN_PRICING", 75),

    # 물류/수입
    (["스케줄 공유"], "LOGISTICS", "LOG_SCHEDULE", 85),
    (["적하", "편으로"], "LOGISTICS", "LOG_SCHEDULE", 80),
    (["소독건", "소독"], "LOGISTICS", "LOG_QUARANTINE", 80),
    (["세관검사", "검역"], "LOGISTICS", "LOG_QUARANTINE", 80),
    (["보관", "보온"], "LOGISTICS", "LOG_STORAGE", 70),

    # 전산/시스템
    (["품목생성", "품목 생성"], "SYSTEM", "SYS_REGISTER", 85),
    (["전산등록", "전산 등록"], "SYSTEM", "SYS_REGISTER", 85),
    (["업로드"], "SYSTEM", "SYS_UPLOAD", 75),
    (["수정했습니다"], "SYSTEM", "SYS_FIX", 75),

    # 주문/변경
    (["변경사항"], "ORDER", "ORDER_CHANGE_ADD", 80),
    (["전량 취소", "올 취소", "전부 취소", "다 취소"], "ORDER", "ORDER_CHANGE_CANCEL", 80),
    (["추가"], "ORDER", "ORDER_CHANGE_ADD", 70),
    (["감소"], "ORDER", "ORDER_CHANGE_REDUCE", 75),
    (["취소"], "ORDER", "ORDER_CHANGE_REDUCE", 75),
    (["폐기"], "INVENTORY", "STOCK_DISCARD", 75),
    (["발주 문의", "발주 가능"], "ORDER", "ORDER_INQUIRY", 75),
    (["고정발주"], "ORDER", "ORDER_NEW", 70),

    # 출고/배송
    (["출고 사진", "출고사진"], "SHIPMENT", "SHIP_COMPLETE", 80),
    (["출고 부탁", "출고해주세요", "출고요청"], "SHIPMENT", "SHIP_REQUEST", 75),
    (["출발합니다"], "SHIPMENT", "SHIP_COMPLETE", 75),
    (["배차"], "SHIPMENT", "SHIP_SCHEDULE", 70),

    # 재고
    (["출고후 잔량", "출고 후 잔량"], "INVENTORY", "STOCK_STATUS", 80),
    (["입고수량"], "INVENTORY", "STOCK_INCOMING", 80),
    (["재고"], "INVENTORY", "STOCK_CHECK", 70),
    (["수량 이상없습니다", "이상없습니다"], "INVENTORY", "STOCK_INCOMING", 70),

    # 커뮤니케이션
    (["확인 부탁", "전달 부탁", "부탁드립니다"], "COMMS", "COMM_REQUEST", 40),
    (["감사합니다", "네 확인", "네네", "넵"], "COMMS", "COMM_CONFIRM", 35),
    (["불가능", "어렵다", "어렵겠"], "COMMS", "COMM_REJECT", 40),
]

# 방 컨텍스트 가중치
ROOM_BOOST = {
    "수입방": {"ORDER": 10},
    "영업방팀 발주 및 추가 재고확인": {"ORDER": 15},
    "현장 추가취소방": {"ORDER": 15},
    "현장단체방": {"SHIPMENT": 10},
    "네노바 수입(불량 공유방)": {"DEFECT": 20},
    "견적방": {"FINANCE": 20},
    "한국방역": {"LOGISTICS": 20},
    "전산테스트팀": {"SYSTEM": 20},
    "백상": {"LOGISTICS": 10},
    "빌번호및 입고수량확인방": {"LOGISTICS": 15},
    "네노바&선율": {"LOGISTICS": 10},
}


def classify_message(msg: ParsedMessage) -> ParsedMessage:
    """메시지를 분류하고 구조 데이터를 추출."""
    text = msg.content

    # 1. 특수 메시지 처리
    if PHOTO_PATTERN.match(text.strip()):
        msg.is_photo = True
        msg.major = "COMMS"
        msg.minor = "COMM_PHOTO"
        msg.confidence = 1.0
        return msg

    if text.startswith("파일:"):
        msg.is_file = True
        msg.major = "COMMS"
        msg.minor = "COMM_PHOTO"
        msg.confidence = 1.0
        return msg

    if DATE_LINE.match(text.strip()):
        msg.is_date_separator = True
        return msg

    if text.strip() in ("메시지가 삭제되었습니다.",):
        return msg

    # 2. 구조 추출
    msg.sequence = extract_sequence(text)
    msg.product, msg.variety = extract_product(text)
    msg.quantity, msg.unit = extract_quantity(text)
    msg.supplier = extract_supplier(text)
    msg.origin = extract_origin(text)
    msg.amount = extract_amount(text)

    # 3. 키워드 매칭 + 방 컨텍스트
    best_major, best_minor, best_score = "COMMS", "COMM_INFO", 0

    for keywords, major, minor, base_score in CLASSIFICATION_RULES:
        for kw in keywords:
            if kw in text:
                score = base_score
                # 방 컨텍스트 가중치
                room_boosts = ROOM_BOOST.get(msg.room, {})
                score += room_boosts.get(major, 0)

                if score > best_score:
                    best_score = score
                    best_major = major
                    best_minor = minor
                break

    msg.major = best_major
    msg.minor = best_minor
    msg.confidence = min(best_score / 100.0, 1.0)

    # 4. 방향 추론 (화살표 패턴 + 키워드)
    if msg.major == "ORDER":
        arrow_m = ARROW_PATTERN.search(text)
        if arrow_m:
            left, right = arrow_m.group(1), arrow_m.group(2)
            # 양쪽 다 숫자면 수량 비교
            left_nums = re.findall(r"\d+", left)
            right_nums = re.findall(r"\d+", right)
            if left_nums and right_nums:
                lv, rv = int(left_nums[-1]), int(right_nums[-1])
                if rv > lv:
                    msg.direction = "+"
                    msg.minor = "ORDER_CHANGE_ADD"
                elif rv < lv:
                    msg.direction = "-"
                    msg.minor = "ORDER_CHANGE_REDUCE"
                else:
                    msg.direction = "~"
                    msg.minor = "ORDER_CHANGE_REPLACE"
            else:
                # 품종 교체 (텍스트 → 텍스트)
                msg.direction = "~"
                msg.minor = "ORDER_CHANGE_REPLACE"
        elif "전량 취소" in text or "올 취소" in text or "전부 취소" in text or "다 취소" in text:
            msg.direction = "-"
            msg.minor = "ORDER_CHANGE_CANCEL"
        elif re.search(r"\d+\s*추가|추가\s*\d+", text) or "추가" in text:
            msg.direction = "+"
            msg.minor = "ORDER_CHANGE_ADD"
        elif re.search(r"\d+\s*(?:단\s*)?취소|취소\s*\d+", text) or "감소" in text:
            msg.direction = "-"
            msg.minor = "ORDER_CHANGE_REDUCE"
        elif "취소" in text or "삭제" in text:
            msg.direction = "-"
            msg.minor = "ORDER_CHANGE_CANCEL"
        elif "변경" in text:
            msg.direction = "~"

    # 5. 스레드 ID 생성
    if msg.sequence and msg.product:
        msg.thread_id = f"{msg.sequence}_{msg.product}"
    elif msg.sequence:
        msg.thread_id = msg.sequence

    return msg


# ─── 컨텍스트 병합 ───

def merge_context(messages: list[ParsedMessage]) -> list[ParsedMessage]:
    """
    연속 메시지 병합:
    1. 사진 + 바로 다음 같은 발신자 텍스트 → 사진의 분류를 텍스트 기준으로
    2. 같은 발신자의 1분 이내 연속 메시지 → 하나로 병합
    """
    if not messages:
        return messages

    result = []
    i = 0
    while i < len(messages):
        msg = messages[i]

        # 사진 + 다음 텍스트 병합
        if msg.is_photo and i + 1 < len(messages):
            next_msg = messages[i + 1]
            if next_msg.sender == msg.sender and not next_msg.is_photo:
                # 다음 메시지의 분류를 사진에 적용
                next_msg.merged_with.append(f"사진: {msg.content}")
                next_msg.is_photo = True  # 사진 포함 표시
                # 사진 메시지는 스킵, 다음 메시지만 포함
                result.append(next_msg)
                i += 2
                continue

        # 같은 발신자 연속 메시지 병합 (1분 이내)
        if (msg.time_minutes >= 0 and i + 1 < len(messages)
                and not msg.is_photo and not msg.is_date_separator):
            merged_content = [msg.content]
            j = i + 1
            while j < len(messages):
                next_msg = messages[j]
                if (next_msg.sender == msg.sender
                        and next_msg.time_minutes >= 0
                        and abs(next_msg.time_minutes - msg.time_minutes) <= 1
                        and not next_msg.is_photo):
                    merged_content.append(next_msg.content)
                    msg.merged_with.append(next_msg.content)
                    j += 1
                else:
                    break

            if len(merged_content) > 1:
                msg.content = "\n".join(merged_content)
                # 병합된 내용으로 재분류
                msg = classify_message(msg)
                result.append(msg)
                i = j
                continue

        result.append(msg)
        i += 1

    return result


# ─── 메인 인터페이스 ───

def classify_delta(room_name: str, delta: str) -> list[ParsedMessage]:
    """
    델타 텍스트를 파싱 → 분류 → 컨텍스트 병합.

    멀티라인 메시지 지원: [발신자] [시각] 패턴이 없는 줄은
    이전 메시지의 연속(continuation)으로 인식하여 content에 병합.

    Returns:
        분류된 메시지 리스트
    """
    messages = []
    # 멀티라인 병합을 위한 pending 메시지 (아직 classify 안 된 상태)
    pending: ParsedMessage | None = None

    def _flush_pending():
        """pending 메시지를 분류하고 messages에 추가."""
        nonlocal pending
        if pending is not None:
            pending = classify_message(pending)
            messages.append(pending)
            pending = None

    for line in delta.strip().splitlines():
        line_stripped = line.strip()

        # 날짜 구분선
        if line_stripped and DATE_LINE.match(line_stripped):
            _flush_pending()
            msg = ParsedMessage(room=room_name, content=line_stripped, is_date_separator=True)
            messages.append(msg)
            continue

        # 카톡 메시지 패턴: [발신자] [시각] 내용
        m = MSG_PATTERN.match(line_stripped) if line_stripped else None
        if m:
            _flush_pending()
            sender, time_str, content = m.groups()
            pending = ParsedMessage(
                sender=sender.strip(),
                time_str=time_str.strip(),
                time_minutes=parse_time(time_str.strip()),
                content=content.strip(),
                room=room_name,
            )
            continue

        # 매칭되지 않는 줄: 이전 메시지의 연속 (빈 줄 포함)
        if pending is not None:
            if line_stripped:
                pending.content += "\n" + line_stripped
            else:
                # 빈 줄도 멀티라인 메시지의 일부로 보존
                pending.content += "\n"

    # 루프 종료 후 마지막 pending 처리
    _flush_pending()

    # 컨텍스트 병합
    messages = merge_context(messages)

    return messages


def classify_and_summarize(room_name: str, delta: str) -> dict:
    """분류 후 요약 통계 반환."""
    messages = classify_delta(room_name, delta)

    from collections import Counter
    major_counts = Counter(m.major for m in messages if m.major)
    minor_counts = Counter(m.minor for m in messages if m.minor)
    products = Counter(m.product for m in messages if m.product)
    suppliers = Counter(m.supplier for m in messages if m.supplier)
    threads = Counter(m.thread_id for m in messages if m.thread_id)

    classified = [m for m in messages if m.major and m.major != "COMMS"]
    total = len([m for m in messages if not m.is_date_separator])
    rate = len(classified) / total * 100 if total else 0

    return {
        "room": room_name,
        "total_messages": total,
        "classified": len(classified),
        "classification_rate": round(rate, 1),
        "major_distribution": dict(major_counts.most_common()),
        "minor_distribution": dict(minor_counts.most_common(20)),
        "products": dict(products.most_common(10)),
        "suppliers": dict(suppliers.most_common(10)),
        "threads": dict(threads.most_common(10)),
        "messages": [asdict(m) for m in messages if not m.is_date_separator],
    }
