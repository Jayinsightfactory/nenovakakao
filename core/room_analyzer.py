"""
방별 직접 전용 분석기.

각 방마다 data/room_analysis_config.json 에 정의된 고유 설정으로:
1. focus_keywords — 핵심 주제 출현 빈도 집계
2. extract_fields — 메시지에서 구조화된 필드 추출
3. intelligence_weights — 인텔리전스 엔진 가중치 적용 (기존 20개 엔진 결과 필터)
4. alert_keywords — 즉시 알림 대상 키워드 탐지

결과는 data/per_room_analysis.json 에 방별로 저장.
이 JSON은 nenovaweb.com ERP 챗봇이 읽어 활용.
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).parent.parent
CONFIG_PATH = ROOT / "data" / "room_analysis_config.json"
OUTPUT_PATH = ROOT / "data" / "per_room_analysis.json"


def load_config() -> dict:
    """방별 설정 로드. 파일 없으면 빈 dict."""
    if not CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[room_analyzer] 설정 로드 실패: {e}", flush=True)
        return {}


def get_room_config(config: dict, room_name: str) -> dict:
    """방 이름 → 설정. 미정의 방은 _default 사용. 띄어쓰기 정규화."""
    if room_name in config:
        return config[room_name]
    # 띄어쓰기 정규화 fallback
    normalized = room_name.replace(" ", "")
    for key, cfg in config.items():
        if key.startswith("_"):
            continue
        if key.replace(" ", "") == normalized:
            return cfg
    return config.get("_default", {
        "focus_keywords": [],
        "extract_fields": [],
        "intelligence_weights": {},
        "alert_keywords": [],
        "chatbot_role": "미정의 방",
    })


# ─── 필드 추출 패턴 ───
FIELD_PATTERNS = {
    "차수": re.compile(r"(\d{1,3}-?\d?)\s*차"),
    "수량": re.compile(r"(\d+)\s*(?:박스|단|스팀|송이|B)"),
    "박스수": re.compile(r"(\d+)\s*박스"),
    "빌번호": re.compile(r"(KE?\d{8,}|BL-?\d+)", re.IGNORECASE),
    "거래처": re.compile(r"([가-힣]{2,10}(?:원예|화훼|꽃집|상사))"),
    "품목": re.compile(r"(카네이션|장미|수국|아마릴리스|레몬잎|알륨|튤립|해바라기|국화|리시안|거베라|카라)"),
    "원산지": re.compile(r"(네덜란드|콜롬비아|에콰도르|중국|일본|대한민국|한국)"),
    "농장": re.compile(r"(늘봄|일신|주광|스타일|미우|소재|상희|문라이트|나르시스)"),
}


def extract_fields(text: str, field_names: list[str]) -> dict[str, list[str]]:
    """텍스트에서 지정된 필드 값 추출."""
    result = {}
    for field in field_names:
        pattern = FIELD_PATTERNS.get(field)
        if pattern:
            matches = pattern.findall(text)
            if matches:
                result[field] = list(dict.fromkeys(matches))  # 중복 제거 (순서 유지)
    return result


def count_keywords(text: str, keywords: list[str]) -> dict[str, int]:
    """키워드별 등장 횟수."""
    counts = {}
    for kw in keywords:
        n = text.count(kw)
        if n > 0:
            counts[kw] = n
    return counts


def analyze_room(room_name: str, messages: list[str], config: dict) -> dict:
    """
    단일 방의 메시지들을 방별 설정에 따라 분석.

    messages: [msg_text, msg_text, ...] (해당 방의 시간순 메시지)
    config: 전체 설정 dict
    """
    room_cfg = get_room_config(config, room_name)

    # 전체 텍스트 합본
    joined = "\n".join(messages)

    # 1. focus_keywords 출현 빈도
    focus_counts = count_keywords(joined, room_cfg.get("focus_keywords", []))

    # 2. extract_fields 구조화
    extracted = extract_fields(joined, room_cfg.get("extract_fields", []))

    # 3. alert_keywords 탐지 — 어느 메시지에서 나왔는지
    alerts = []
    alert_kws = room_cfg.get("alert_keywords", [])
    for idx, msg in enumerate(messages):
        for kw in alert_kws:
            if kw in msg:
                alerts.append({
                    "keyword": kw,
                    "message_index": idx,
                    "excerpt": msg[:80],
                })
                break  # 한 메시지당 1개만

    return {
        "room_name": room_name,
        "message_count": len(messages),
        "chatbot_role": room_cfg.get("chatbot_role", ""),
        "focus_counts": focus_counts,
        "extracted_fields": extracted,
        "alerts": alerts[:10],  # 최근 10개만
        "intelligence_weights": room_cfg.get("intelligence_weights", {}),
    }


def analyze_all(messages_by_room: dict[str, list[str]]) -> dict[str, Any]:
    """
    모든 방을 방별 설정으로 분석.

    messages_by_room: {"수입방": [msg1, msg2, ...], ...}

    반환: {
      "rooms": {방이름: 분석결과},
      "summary": {전체 요약},
      "alerts": [모든 방의 알림 합본]
    }
    """
    config = load_config()
    room_results = {}
    all_alerts = []

    for room, msgs in messages_by_room.items():
        if not msgs:
            continue
        result = analyze_room(room, msgs, config)
        room_results[room] = result
        for alert in result["alerts"]:
            all_alerts.append({"room": room, **alert})

    # 전체 요약
    summary = {
        "total_rooms": len(room_results),
        "total_messages": sum(r["message_count"] for r in room_results.values()),
        "total_alerts": len(all_alerts),
        "rooms_with_alerts": sum(1 for r in room_results.values() if r["alerts"]),
    }

    return {
        "rooms": room_results,
        "summary": summary,
        "all_alerts": all_alerts[:50],  # 상위 50개
    }


def save_analysis(result: dict) -> Path:
    """분석 결과 저장 → ERP 챗봇이 읽을 JSON."""
    OUTPUT_PATH.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return OUTPUT_PATH


def run_from_sheet_data(sheet_rows: list[list[str]], room_col: int = 1, msg_col: int = 4) -> dict:
    """
    구글시트 이벤트로그 행들로부터 방별 메시지 그룹 구성 후 분석.

    sheet_rows: [[시각, 방이름, 파이프라인, 발신자, 원문, 메시지ID], ...]
    room_col: 방이름 컬럼 인덱스 (기본 1)
    msg_col: 원문 컬럼 인덱스 (기본 4)
    """
    by_room: dict[str, list[str]] = defaultdict(list)
    for row in sheet_rows:
        if len(row) <= max(room_col, msg_col):
            continue
        room = (row[room_col] or "").strip()
        msg = (row[msg_col] or "").strip()
        if room and msg:
            by_room[room].append(msg)

    return analyze_all(dict(by_room))


if __name__ == "__main__":
    # 간단 테스트
    test_msgs = {
        "수입방": [
            "47-1차 네덜란드 검역차감\n주광\n카라 블랙 -10스팀",
            "15-1차 카네이션 문라이트 불량대체 요청",
        ],
        "영업방팀 발주 및 추가 재고확인": [
            "15-1차 콜 카네이션 추가 상희 폴림니아 1박스",
            "취소: 14-2차 수국 2단",
        ],
    }
    result = analyze_all(test_msgs)
    print(json.dumps(result, ensure_ascii=False, indent=2))
