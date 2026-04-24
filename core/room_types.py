"""방 성격 분류 — 거래처 채널 vs 내부 백본 vs 협력사 등.

Phase 2 분류 1단계 진단 (tools/room_type_audit.py) 결과, 같은 키워드도
방의 성격에 따라 실제 의미가 다름을 확인. 예:
- 내부 백본 '불량'  = 검수팀 사실 보고
- 거래처 채널 '불량' = 거래처의 사과/품질 협상
- 파트너 채널 '검역' = 협력사의 정상 업무 흐름
- 내부 '검역'        = 검역차감 문제 보고

parse_message 가 이 타입을 참조해 YAML의 type_overrides 를 적용한다.
"""
from __future__ import annotations

INTERNAL_BACKBONE_KEYS = [
    "수입방", "영업방", "불량 공유방", "물량 공유방",
    "현장단체", "현장 추가취소", "현장추가취소",
    "빌번호", "발번호",
    "견적방", "전산테스트", "네노바현장팀",
    "네노바 영업", "네노바 수입/영업/현장",
    "영업지원팀", "영업방팀",
]
PARTNER_KEYS = ["선율", "선울", "방역"]
SUPPLIER_ONLY_KEYS = ["란스 발주방", "백상", "경부 중앙화훼", "미우신라"]


def classify_room_type(name: str | None) -> str:
    """방 이름 → 타입.

    Returns:
        INTERNAL_BACKBONE  네노바 팀 내부 업무 방
        SUPPLIER_CHANNEL   거래처 1개 ↔ 네노바 직통
        PARTNER_CHANNEL    외부 협력사 (검역/방역)
        INTERNAL_PRIVATE   개인명 나열 사적 단톡
        MISC               기타
    """
    if not name:
        return "MISC"
    n = name.strip()
    if n.count(",") >= 2:
        return "INTERNAL_PRIVATE"
    if any(k in n for k in PARTNER_KEYS):
        return "PARTNER_CHANNEL"
    if any(k in n for k in INTERNAL_BACKBONE_KEYS):
        return "INTERNAL_BACKBONE"
    if "네노바" in n and any(c in n for c in "+&"):
        return "SUPPLIER_CHANNEL"
    if any(k in n for k in SUPPLIER_ONLY_KEYS):
        return "SUPPLIER_CHANNEL"
    if any(k in n for k in ("원예", "화훼", "플라워", "꽃")) and "네노바" in n:
        return "SUPPLIER_CHANNEL"
    return "MISC"
