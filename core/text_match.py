"""짧은 한글 토큰의 substring 오탐 방지를 위한 공유 유틸.

한국어에는 정규식 \\b 같은 단어 경계가 없고, 짧은 토큰(예: '콜', '그린')이
다른 단어 내부에 부분일치 ('콜라', '연그린')하면 분류가 깨진다.

룰:
  - 4자 이상 토큰  → 부분일치 허용 (오탐 가능성 낮음)
  - 3자 이하 토큰  → 좌측이 한글로 이어지면 거절. 우측은 조사 결합이 자연
                     스러우므로 허용 ('꽃동산에서' / '꽃동산입니다' OK)

Korean Hangul syllables: 가(U+AC00) ~ 힣(U+D7A3)
"""
from __future__ import annotations


def is_hangul(ch: str) -> bool:
    if not ch:
        return False
    return "가" <= ch <= "힣"


def find_token(text: str, token: str) -> int:
    """경계 검사를 통과한 토큰의 위치를 반환. 없으면 -1.

    경계 정책:
        길이 ≥4    → 부분일치 허용 (오탐 가능성 낮음)
        길이 3      → 좌측 한글 거절. 우측은 조사 결합 자연스러움 → 허용
                     ('꽃동산에서' / '꽃동산입니다' OK, '연꽃동산' OK 안 함)
        길이 ≤2     → 양쪽 한글 모두 거절 (예: '콜' inside '콜라', '연그린')
                     1~2자 토큰은 약어/원산지 코드. 한글 글자에 묻히면 거의 항상 오탐.

    예:
        find_token("연그린", "그린")     → -1   (좌측 한글)
        find_token("그린 5박스", "그린")  → 0
        find_token("꽃동산에서", "꽃동산")  → 0
        find_token("콜 카네이션", "콜")   → 0
        find_token("콜라 마셨음", "콜")   → -1  (우측 한글, 토큰 길이 1)
    """
    if not text or not token:
        return -1
    idx = text.find(token)
    if idx < 0:
        return -1
    if len(token) >= 4:
        return idx
    left_ok = idx == 0 or not is_hangul(text[idx - 1])
    if not left_ok:
        return -1
    if len(token) <= 2:
        right_idx = idx + len(token)
        right_ok = right_idx >= len(text) or not is_hangul(text[right_idx])
        if not right_ok:
            return -1
    return idx


def contains_token(text: str, token: str) -> bool:
    return find_token(text, token) >= 0
