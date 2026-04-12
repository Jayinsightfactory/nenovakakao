#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
구글시트 "품목매칭" / "거래처매칭" 탭 업데이트 스크립트 v1.0
- 카톡 대화에서 품목/거래처별 출현 빈도 재집계
- 원산지(콜롬비아/중국/멜로디 등) 구분하여 DB 매칭
- build_matching_table.py의 find_product_match_v2() 활용
- classifier.py의 SUPPLIER_ALIASES 적용
"""
import json
import os
import re
import sys
from collections import Counter, defaultdict
from difflib import SequenceMatcher

import gspread
from google.oauth2.service_account import Credentials

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 기본 설정
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CREDS_FILE = "C:/Users/USER/nenova_agent/data/gsheet_credentials.json"
SHEET_URL = "https://docs.google.com/spreadsheets/d/1pXLVZqiMwWt6Vh0IhWwASBvgLtZqLnbHXMWqOLNwAXU/edit"
DATA_DIR = r"C:\Users\USER\nenova_agent\data"
KAKAO_DIR = r"C:\Users\USER\Downloads\카톡대화데이터"

PRODUCTS_PATH = os.path.join(DATA_DIR, "master_products.json")
CUSTOMERS_PATH = os.path.join(DATA_DIR, "master_customers.json")

# 구글시트 스코프
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 거래처 별칭 (classifier.py에서 가져옴)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SUPPLIER_ALIASES = {
    "주광농원": "주광",
    "소재장터": "소재2호",
    "레바논": "레바논꽃방",
    "참좋은": "참좋은원예",
    "일신": "일신원예",
    "대지": "대지원예",
    "상희": "상희원예",
    "광주천사": "광주천사",
    "친구": "친구플라워",
}

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 원산지 힌트 → DB 국가 매핑
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ORIGIN_HINTS = {
    # 콜롬비아
    "콜": "콜롬비아",
    "콜롬": "콜롬비아",
    "콜롬비아": "콜롬비아",
    "colombia": "콜롬비아",
    "CO": "콜롬비아",
    # 중국
    "중국": "중국",
    "차이나": "중국",
    "china": "중국",
    "CHI": "중국",
    # 멜로디 (중국산 브랜드)
    "멜로디": "중국",
    "멜": "중국",
    "MEL": "중국",
    "[MEL]": "중국",
    # 에콰도르
    "에콰": "에콰도르",
    "에콰도르": "에콰도르",
    "ecuador": "에콰도르",
    # 네덜란드
    "네덜란드": "네덜란드",
    "holland": "네덜란드",
    "netherlands": "네덜란드",
    # 호주
    "호주": "호주",
    "australia": "호주",
    # 태국
    "태국": "태국",
    "thailand": "태국",
    # 베트남
    "베트남": "베트남",
    "vietnam": "베트남",
    # 국내
    "국내": "국내",
    "국산": "국내",
}

# ProdCode 접두사 → 원산지 매핑
PRODCODE_COUNTRY = {
    "CO": "콜롬비아",
    "CHI": "중국",
    "EC": "에콰도르",
    "NE": "네덜란드",
    "AU": "호주",
    "TH": "태국",
    "VN": "베트남",
    "KR": "국내",
}

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 영문↔한글 음역 사전 (build_matching_table.py 참조)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
EN_KO_TRANSLITERATION = {
    "moonlight": "문라이트", "doncel": "돈셀", "hermes": "헤르메스",
    "hermes orange": "헤르메스오렌지", "novia": "노비아", "georgia": "지오지아",
    "polymnia": "폴림니아", "cherrio": "체리오", "yukari cherry": "유카리체리",
    "yukari": "유카리", "brut": "브루트", "ness": "네스",
    "mariposa": "마리포사", "electric purple": "일렉트릭퍼플",
    "colibri": "콜리브리", "farida": "파리다", "minuetto": "미뉴에또",
    "spray white": "스프레이화이트", "spray light pink": "스프레이연핑크",
    "gladiator": "글래디에터", "symphony": "심포니",
    "proud": "프라우드", "candlelight": "캔들라이트", "mandala": "만달라",
    "coral reef": "코랄리프", "pink floyd": "핑크플로이드",
    "star platinum": "스타플레티넘", "laura": "로라",
    "red panther": "레드팬서", "pink mondial": "핑크몬디알",
    "blackjack": "블랙잭", "black jack": "블랙잭",
    "pink expression": "핑크익스프레션", "jumilia": "주밀리아",
    "pink snowberg": "핑크스노우버그", "sweet avalanche": "스윗아발란체",
    "julring": "줄링", "martina": "마티나", "esperance": "에스페란스",
    "freedom": "프리덤", "avalanche": "아발란체", "viviane": "비비안",
    "talea": "탈레아", "full house": "풀하우스", "pink bell": "핑크벨",
    "white o'hara": "화이트오하라", "peach": "피치", "hot shot": "핫샷",
    "carola": "카롤라",
    "white": "화이트", "blue": "블루", "pink": "핑크", "red": "레드",
    "orange": "오렌지", "light pink": "연핑크", "dark pink": "진핑크",
    "light green": "연그린", "green": "그린", "yellow": "옐로",
    "cream": "크림", "lavender": "라벤더", "purple": "퍼플", "coral": "코랄",
    "salix": "살릭스", "salicis": "살릭스",
    "lisianthus": "리시안셔스", "hydrangea": "수국", "carnation": "카네이션",
    "rose": "장미", "tulip": "튤립", "allium": "알륨",
    "amaryllis": "아마릴리스", "alstroemeria": "알스트로",
    "eucalyptus": "유칼립투스",
}
KO_EN_TRANSLITERATION = {v: k for k, v in EN_KO_TRANSLITERATION.items()}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 유틸리티
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def extract_searchable_names(prod_name):
    """ProdName에서 한글명, 영문명, 괄호 안 내용 등 모든 검색 키워드 추출"""
    names = set()
    original = prod_name.strip()
    names.add(original)

    for m in re.finditer(r'[\(（]([^)）]+)[\)）]', original):
        inner = m.group(1).strip()
        names.add(inner)
        names.add(inner.lower())

    parts = original.split('/')
    for part in parts:
        part = part.strip()
        part = re.sub(r'^\[.*?\]\s*', '', part)
        names.add(part)

    cleaned = re.sub(
        r'\b(CHINA|COLOMBIA|ECUADOR|NETHERLANDS|THAILAND|VIETNAM|AUSTRALIA)\b',
        '', original, flags=re.IGNORECASE
    ).strip()
    cleaned = re.sub(r'\s+', ' ', cleaned).strip(' /')
    names.add(cleaned)

    en_only = re.sub(r'\d+\s*cm', '', original, flags=re.IGNORECASE)
    en_only = re.sub(r'[\(（][^)）]*[\)）]', '', en_only)
    en_only = re.sub(r'[/]', ' ', en_only)
    en_words = re.findall(r'[A-Za-z]+(?:\s+[A-Za-z]+)*', en_only)
    for w in en_words:
        w = w.strip()
        if len(w) > 2 and w.upper() not in (
            'CHINA', 'COLOMBIA', 'ECUADOR', 'NETHERLANDS',
            'ROSE', 'CARNATION', 'SPRAY', 'MEL', 'BOX', 'MIX', 'THE', 'AND'
        ):
            names.add(w)
            names.add(w.lower())

    ko_parts = re.findall(r'[가-힣]+(?:\s*[가-힣]+)*', original)
    for kp in ko_parts:
        kp = kp.strip()
        if len(kp) >= 2:
            names.add(kp)

    for en, ko in EN_KO_TRANSLITERATION.items():
        if en.lower() in original.lower():
            names.add(ko)

    return names


def get_country_from_prodcode(prod_code):
    """ProdCode에서 원산지 추출 (예: CAR01-CO0001 → 콜롬비아)"""
    if not prod_code:
        return None
    m = re.search(r'-([A-Z]+)\d', prod_code)
    if m:
        prefix = m.group(1)
        return PRODCODE_COUNTRY.get(prefix)
    return None


def detect_origin_hint(text):
    """카톡 품명 텍스트에서 원산지 힌트 추출"""
    text_lower = text.lower().strip()

    # 우선순위 높은 힌트부터 (긴 문자열 우선)
    sorted_hints = sorted(ORIGIN_HINTS.keys(), key=len, reverse=True)
    for hint in sorted_hints:
        if hint.lower() in text_lower:
            return ORIGIN_HINTS[hint]

    # [MEL] 접두사 체크
    if re.search(r'\[MEL\]', text, re.IGNORECASE):
        return "중국"

    # CHINA / COLOMBIA 등 영문 국가명
    if re.search(r'\bCHINA\b', text, re.IGNORECASE):
        return "중국"
    if re.search(r'\bCOLOMBIA\b', text, re.IGNORECASE):
        return "콜롬비아"

    return None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# DB 로드 및 인덱스 구축
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def load_db():
    """마스터 DB 로드"""
    products = load_json(PRODUCTS_PATH)
    customers = load_json(CUSTOMERS_PATH)
    return products, customers


def build_product_index(products):
    """원산지별 분리된 검색 인덱스 구축"""
    active = [p for p in products if not p.get("isDeleted", False)]

    # 전체 인덱스: token → [entries]
    full_index = {}
    # 원산지별 인덱스: country → token → [entries]
    country_index = defaultdict(lambda: defaultdict(list))

    for p in active:
        pname = p["ProdName"]
        pkey = p["ProdKey"]
        fn = p.get("FlowerName", "")
        cn = p.get("CounName", "")
        pc = p.get("ProdCode", "")

        entry = {
            "ProdKey": pkey,
            "ProdName": pname,
            "FlowerName": fn,
            "CounName": cn,
            "ProdCode": pc,
        }

        # 원산지 판별: CounName 우선, 없으면 ProdCode에서 추출
        country = cn
        if not country:
            country = get_country_from_prodcode(pc) or ""

        # ProdName 내 CHINA / [MEL] 등으로도 원산지 판별
        if not country:
            if re.search(r'\bCHINA\b', pname, re.IGNORECASE) or '[MEL]' in pname:
                country = "중국"
            elif re.search(r'\bCOLOMBIA\b', pname, re.IGNORECASE):
                country = "콜롬비아"

        entry["resolved_country"] = country

        searchable = extract_searchable_names(pname)
        for token in searchable:
            token_lower = token.lower().strip()
            if len(token_lower) < 2:
                continue
            full_index.setdefault(token_lower, []).append(entry)
            if country:
                country_index[country][token_lower].append(entry)

    return full_index, country_index, active


def build_customer_index(customers):
    """거래처 인덱스 구축"""
    active = [c for c in customers if not c.get("isDeleted", False)]
    index = {}
    for c in active:
        name = c["CustName"].strip()
        entry = {
            "CustKey": c["CustKey"],
            "CustCode": c.get("CustCode", ""),
            "CustName": name,
            "Group1": c.get("Group1", ""),
        }
        index.setdefault(name, []).append(entry)
    return index, active


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 매칭 함수 (원산지 인식 강화)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def find_product_match_v3(query, full_index, country_index,
                          flower_hint=None, origin_hint=None):
    """
    원산지 구분 강화 매칭.
    origin_hint가 있으면 해당 원산지 인덱스에서만 우선 검색.
    """
    query = query.strip()
    result = {
        "query": query,
        "flower_hint": flower_hint,
        "origin_hint": origin_hint,
        "matches": [],
        "best_match": None,
        "match_type": "NONE",
        "confidence": 0.0,
    }

    query_lower = query.lower()

    # 한글↔영문 변환 변형
    query_variants = [query_lower]
    if query_lower in KO_EN_TRANSLITERATION:
        query_variants.append(KO_EN_TRANSLITERATION[query_lower])
    if query_lower in EN_KO_TRANSLITERATION:
        query_variants.append(EN_KO_TRANSLITERATION[query_lower])

    # 원산지 힌트가 있으면 해당 국가 인덱스 우선 사용
    search_indices = []
    if origin_hint and origin_hint in country_index:
        search_indices.append(("origin_preferred", country_index[origin_hint]))
    search_indices.append(("full", full_index))

    candidates = {}  # ProdKey → {info, score, match_type}

    for idx_label, search_idx in search_indices:
        # 이미 origin_preferred에서 충분한 결과가 있으면 full 스킵
        if idx_label == "full" and candidates:
            exact_in_candidates = any(
                c["match_type"] == "EXACT" for c in candidates.values()
            )
            if exact_in_candidates:
                break

        for q in query_variants:
            # 1) 인덱스 완전 일치
            if q in search_idx:
                for entry in search_idx[q]:
                    pk = entry["ProdKey"]
                    score = 1.0
                    # 원산지 보너스/페널티
                    if origin_hint:
                        rc = entry.get("resolved_country", entry.get("CounName", ""))
                        if rc == origin_hint:
                            score += 0.2  # 원산지 일치 보너스
                        elif rc and rc != origin_hint:
                            if idx_label == "full":
                                score -= 0.4  # 원산지 불일치 페널티
                    if pk not in candidates or candidates[pk]["score"] < score:
                        candidates[pk] = {**entry, "score": score, "match_type": "EXACT"}

            # 2) 인덱스 부분 일치
            for token, entries in search_idx.items():
                if q in token or token in q:
                    ratio = len(q) / max(len(token), len(q))
                    if ratio >= 0.3:
                        for entry in entries:
                            pk = entry["ProdKey"]
                            adj_score = ratio
                            if origin_hint:
                                rc = entry.get("resolved_country", entry.get("CounName", ""))
                                if rc == origin_hint:
                                    adj_score += 0.2
                                elif rc and rc != origin_hint and idx_label == "full":
                                    adj_score -= 0.3
                            if pk not in candidates or candidates[pk]["score"] < adj_score:
                                candidates[pk] = {
                                    **entry,
                                    "score": round(adj_score, 3),
                                    "match_type": "PARTIAL",
                                }

    # 3) 유사도 매칭 (인덱스에서 못 찾은 경우)
    if not candidates:
        target_idx = full_index
        if origin_hint and origin_hint in country_index:
            target_idx = country_index[origin_hint]
        for q in query_variants:
            for token, entries in target_idx.items():
                ratio = SequenceMatcher(None, q, token).ratio()
                if ratio >= 0.6:
                    for entry in entries:
                        pk = entry["ProdKey"]
                        if pk not in candidates or candidates[pk]["score"] < ratio:
                            candidates[pk] = {
                                **entry,
                                "score": round(ratio, 3),
                                "match_type": "FUZZY",
                            }

    # flower_hint 보너스 + 필터
    if flower_hint and candidates:
        filtered = {}
        for pk, c in candidates.items():
            if c["FlowerName"] == flower_hint:
                c["score"] += 0.3
                filtered[pk] = c
        if filtered:
            candidates = filtered

    # Mix Box 페널티
    for pk, c in candidates.items():
        pname = c.get("ProdName", "")
        if "Mix Box" in pname or "MIX" in pname.upper().split():
            c["score"] -= 0.3

    # 정렬
    sorted_matches = sorted(candidates.values(), key=lambda c: -c["score"])[:10]
    result["matches"] = sorted_matches

    if sorted_matches:
        best = sorted_matches[0]
        result["best_match"] = best
        result["match_type"] = best["match_type"]
        result["confidence"] = round(min(1.0, max(0.0, best["score"])), 2)

    return result


def find_customer_match(query, cust_index):
    """거래처 매칭 (별칭 정규화 포함)"""
    query = query.strip()
    result = {
        "query": query,
        "matches": [],
        "best_match": None,
        "match_type": "NONE",
        "confidence": 0.0,
    }

    # 별칭 정규화
    normalized = SUPPLIER_ALIASES.get(query, query)

    # 1) 완전 일치
    if normalized in cust_index:
        entries = cust_index[normalized]
        result["matches"] = entries
        result["best_match"] = entries[0]
        result["match_type"] = "EXACT"
        result["confidence"] = 1.0
        return result

    # 원본명으로도 시도
    if query != normalized and query in cust_index:
        entries = cust_index[query]
        result["matches"] = entries
        result["best_match"] = entries[0]
        result["match_type"] = "EXACT"
        result["confidence"] = 1.0
        return result

    # 2) 포함 매칭
    contains = []
    search_terms = [normalized, query] if query != normalized else [query]
    for st in search_terms:
        for name, entries in cust_index.items():
            if st in name:
                score = len(st) / len(name)
                for e in entries:
                    contains.append({**e, "score": round(score, 3), "match_type": "CONTAINS"})
            elif name in st:
                score = len(name) / len(st)
                for e in entries:
                    contains.append({**e, "score": round(score, 3), "match_type": "CONTAINS"})

    # 중복 제거 (CustKey 기준)
    seen_keys = set()
    deduped = []
    for c in sorted(contains, key=lambda x: -x["score"]):
        if c["CustKey"] not in seen_keys:
            seen_keys.add(c["CustKey"])
            deduped.append(c)
    contains = deduped

    if contains:
        result["matches"] = contains[:5]
        result["best_match"] = contains[0]
        result["match_type"] = "CONTAINS"
        result["confidence"] = round(contains[0]["score"], 2)
        return result

    # 3) 유사도
    fuzzy = []
    for name, entries in cust_index.items():
        clean_name = re.sub(r'[\(\)\（\）\[\]\（\）]', '', name).strip()
        clean_name = re.sub(r'\(주\)|\(주식회사\)', '', clean_name).strip()
        for st in search_terms:
            ratio = SequenceMatcher(None, st, clean_name).ratio()
            if ratio >= 0.4:
                for e in entries:
                    fuzzy.append({**e, "score": round(ratio, 3), "match_type": "FUZZY"})

    seen_keys = set()
    deduped = []
    for c in sorted(fuzzy, key=lambda x: -x["score"]):
        if c["CustKey"] not in seen_keys:
            seen_keys.add(c["CustKey"])
            deduped.append(c)
    fuzzy = deduped

    if fuzzy:
        result["matches"] = fuzzy[:5]
        result["best_match"] = fuzzy[0]
        result["match_type"] = "FUZZY"
        result["confidence"] = round(fuzzy[0]["score"], 2)

    return result


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 카톡 대화 로드 및 빈도 집계
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def load_kakao_messages(kakao_dir):
    """카톡 대화 파일 전체 로드"""
    all_messages = []
    msg_pattern = re.compile(r"^\[(.+?)\]\s*\[(.+?)\]\s*(.*)")

    if not os.path.isdir(kakao_dir):
        print(f"  [WARN] 카톡 대화 디렉토리 없음: {kakao_dir}")
        return all_messages

    for fname in sorted(os.listdir(kakao_dir)):
        if not fname.endswith(".txt"):
            continue
        filepath = os.path.join(kakao_dir, fname)
        with open(filepath, "r", encoding="utf-8") as f:
            lines = f.readlines()

        if not lines:
            continue
        room_name = lines[0].strip().replace(" 님과 카카오톡 대화", "")

        current_msg = None
        for line in lines[2:]:
            line = line.rstrip("\n")
            m = msg_pattern.match(line)
            if m:
                if current_msg:
                    all_messages.append(current_msg)
                sender, time_str, text = m.group(1), m.group(2), m.group(3)
                current_msg = {
                    "room": room_name,
                    "sender": sender,
                    "time": time_str,
                    "text": text,
                    "file": fname,
                }
            elif line.startswith("---"):
                if current_msg:
                    all_messages.append(current_msg)
                    current_msg = None
            elif current_msg and line.strip():
                current_msg["text"] += "\n" + line

        if current_msg:
            all_messages.append(current_msg)

    return all_messages


def count_product_occurrences(messages, products):
    """
    카톡 메시지에서 품목명(품종+원산지) 출현 빈도 집계.
    반환: {(정규화된_품명, 원산지_힌트): count}
    """
    # DB에서 검색 토큰 준비
    flower_names = set()
    variety_tokens = set()
    for p in products:
        fn = p.get("FlowerName", "")
        if fn and len(fn) >= 2:
            flower_names.add(fn)
        # ProdName에서 한글 토큰
        for kr in re.findall(r'[가-힣]{2,}', p["ProdName"]):
            variety_tokens.add(kr)

    # 음역 사전 키도 추가
    for kr in KO_EN_TRANSLITERATION:
        if len(kr) >= 2:
            variety_tokens.add(kr)

    # 수량 패턴 앞의 품명 추출
    qty_pattern = re.compile(
        r"([가-힣A-Za-z][가-힣A-Za-z\s/()]+?)\s*(\d+)\s*(단|송이|박스|스팀|스템|stem|box|bx|bunch)",
        re.IGNORECASE,
    )

    product_counter = Counter()  # (품명, 원산지) → count
    raw_counter = Counter()  # 원본 품명 → count

    skip_words = {
        "추가", "변경", "취소", "출고", "발주", "요청", "확인", "총",
        "수량", "단가", "금액", "합계", "소계", "입금", "미입금",
        "사진", "동영상", "이모티콘", "검역", "차감",
    }

    for msg in messages:
        text = msg["text"]
        if text.strip() in ("사진", "동영상", "이모티콘") or text.startswith("파일:"):
            continue
        if "삭제되었습니다" in text:
            continue

        # 메시지 전체에서 원산지 힌트 추출
        msg_origin = detect_origin_hint(text)

        for line in text.split("\n"):
            line = line.strip()
            if not line:
                continue

            # 수량 패턴 앞의 품명 추출
            for m in qty_pattern.finditer(line):
                candidate = m.group(1).strip()
                # 차수 제거: "15-1차 카네이션" → "카네이션"
                candidate = re.sub(r"^\d+[-~]\d+\s*(차|콜)?\s*", "", candidate)
                # 원산지 키워드 제거 (별도 추적)
                line_origin = detect_origin_hint(candidate) or msg_origin
                candidate = re.sub(
                    r"^(중국|콜|콜롬비아|네덜란드|태국|에콰도르|호주|베트남|국내|멜로디)\s*",
                    "", candidate
                ).strip()

                if len(candidate) >= 2 and candidate not in skip_words:
                    product_counter[(candidate, line_origin or "")] += 1
                    raw_counter[candidate] += 1

            # FlowerName 직접 매칭
            for fn in flower_names:
                if fn in line:
                    line_origin = detect_origin_hint(line) or msg_origin
                    product_counter[(fn, line_origin or "")] += 1

    return product_counter, raw_counter


def count_supplier_occurrences(messages):
    """카톡 메시지에서 거래처명 출현 빈도 집계"""
    supplier_counter = Counter()

    # 알려진 거래처명 리스트 (classifier.py 참조)
    known_suppliers = [
        "주광", "주광농원", "대한", "신라", "그린", "미우", "태림",
        "소재2호", "소재장터", "수연", "꽃길", "알파", "원협가빈",
        "남대문청화", "광주천사", "참좋은원예", "참좋은", "미카엘",
        "꽃동산", "친구플라워", "친구", "일신원예", "일신",
        "성남", "시흥", "경향", "대지원예", "대지", "꿀벌",
        "레바논꽃방", "레바논", "경부청화", "상희원예", "상희",
        "영남소재",
    ]
    # 긴 이름 우선 매칭
    known_suppliers.sort(key=len, reverse=True)

    for msg in messages:
        text = msg["text"]
        if text.strip() in ("사진", "동영상", "이모티콘"):
            continue

        for sup in known_suppliers:
            if sup in text:
                # 별칭 정규화
                normalized = SUPPLIER_ALIASES.get(sup, sup)
                supplier_counter[normalized] += 1
                break  # 한 메시지당 하나만

    return supplier_counter


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 품목매칭 테이블 생성
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def build_product_matching_rows(product_counter, full_index, country_index):
    """
    품목매칭 행 생성.
    출력: [[카톡품명, 출현수, ProdKey, ProdName, 꽃종류, 국가, 매칭방법, 신뢰도, 상태], ...]
    """
    rows = []

    # (품명, 원산지) 쌍을 출현수 내림차순 정렬
    sorted_items = sorted(product_counter.items(), key=lambda x: -x[1])

    for (product_name, origin_hint), count in sorted_items:
        if count < 1:
            continue

        # 꽃 대분류 힌트 추출
        flower_hint = None
        for fn in ["카네이션", "장미", "수국", "알스트로", "튤립", "모카라",
                    "아마릴리스", "리시안셔스", "알륨", "루스커스", "레몬잎"]:
            if fn in product_name or fn == product_name:
                flower_hint = fn
                break

        # 표시용 카톡품명 (원산지 포함)
        display_name = product_name
        if origin_hint:
            display_name = f"{product_name} ({origin_hint})"

        match = find_product_match_v3(
            product_name, full_index, country_index,
            flower_hint=flower_hint,
            origin_hint=origin_hint if origin_hint else None,
        )

        best = match["best_match"]
        if best:
            status = "확인필요" if match["confidence"] < 0.7 else "자동매칭"
            if match["match_type"] == "EXACT" and match["confidence"] >= 0.9:
                status = "확정"

            rows.append([
                display_name,
                count,
                best["ProdKey"],
                best["ProdName"],
                best.get("FlowerName", ""),
                best.get("CounName", "") or best.get("resolved_country", ""),
                match["match_type"],
                match["confidence"],
                status,
            ])
        else:
            rows.append([
                display_name,
                count,
                "",
                "",
                "",
                "",
                "NONE",
                0.0,
                "미매칭",
            ])

    return rows


def build_customer_matching_rows(supplier_counter, cust_index):
    """
    거래처매칭 행 생성.
    출력: [[카톡거래처명, 출현수, CustKey, CustName, 그룹, 매칭방법, 신뢰도, 상태], ...]
    """
    rows = []

    sorted_items = sorted(supplier_counter.items(), key=lambda x: -x[1])

    for supplier_name, count in sorted_items:
        if count < 1:
            continue

        match = find_customer_match(supplier_name, cust_index)

        best = match["best_match"]
        if best:
            status = "확인필요" if match["confidence"] < 0.7 else "자동매칭"
            if match["match_type"] == "EXACT":
                status = "확정"

            rows.append([
                supplier_name,
                count,
                best["CustKey"],
                best["CustName"],
                best.get("Group1", ""),
                match["match_type"],
                match["confidence"],
                status,
            ])
        else:
            rows.append([
                supplier_name,
                count,
                "",
                "",
                "",
                "NONE",
                0.0,
                "미매칭",
            ])

    return rows


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 구글시트 업데이트
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def connect_gsheet():
    """구글시트 연결"""
    creds = Credentials.from_service_account_file(CREDS_FILE, scopes=SCOPES)
    gc = gspread.authorize(creds)
    sh = gc.open_by_url(SHEET_URL)
    return sh


def update_product_sheet(sh, rows, dry_run=True):
    """품목매칭 탭 업데이트"""
    HEADER = ["카톡품명", "출현수", "DB ProdKey", "DB ProdName",
              "꽃종류", "국가", "매칭방법", "신뢰도", "상태"]

    try:
        ws = sh.worksheet("품목매칭")
    except gspread.exceptions.WorksheetNotFound:
        ws = sh.add_worksheet(title="품목매칭", rows=len(rows) + 10, cols=len(HEADER))

    all_data = [HEADER] + rows

    print(f"\n{'='*60}")
    print(f"품목매칭 탭: {len(rows)}행 준비됨")
    print(f"  확정: {sum(1 for r in rows if r[8] == '확정')}건")
    print(f"  자동매칭: {sum(1 for r in rows if r[8] == '자동매칭')}건")
    print(f"  확인필요: {sum(1 for r in rows if r[8] == '확인필요')}건")
    print(f"  미매칭: {sum(1 for r in rows if r[8] == '미매칭')}건")

    # 원산지별 통계
    origin_stats = Counter()
    for r in rows:
        origin = r[5] if r[5] else "(없음)"
        origin_stats[origin] += 1
    print(f"  원산지별: {dict(origin_stats.most_common())}")

    if dry_run:
        print("\n  [DRY RUN] 시트 쓰기 생략 (dry_run=True)")
        print("  실행하려면: python update_product_matching.py --write")
        return

    ws.clear()
    ws.update(range_name="A1", values=all_data)
    print(f"  [OK] 시트에 {len(all_data)}행 기록 완료")


def update_customer_sheet(sh, rows, dry_run=True):
    """거래처매칭 탭 업데이트"""
    HEADER = ["카톡거래처명", "출현수", "DB CustKey", "DB CustName",
              "그룹", "매칭방법", "신뢰도", "상태"]

    try:
        ws = sh.worksheet("거래처매칭")
    except gspread.exceptions.WorksheetNotFound:
        ws = sh.add_worksheet(title="거래처매칭", rows=len(rows) + 10, cols=len(HEADER))

    all_data = [HEADER] + rows

    print(f"\n{'='*60}")
    print(f"거래처매칭 탭: {len(rows)}행 준비됨")
    print(f"  확정: {sum(1 for r in rows if r[7] == '확정')}건")
    print(f"  자동매칭: {sum(1 for r in rows if r[7] == '자동매칭')}건")
    print(f"  확인필요: {sum(1 for r in rows if r[7] == '확인필요')}건")
    print(f"  미매칭: {sum(1 for r in rows if r[7] == '미매칭')}건")

    if dry_run:
        print("\n  [DRY RUN] 시트 쓰기 생략 (dry_run=True)")
        return

    ws.clear()
    ws.update(range_name="A1", values=all_data)
    print(f"  [OK] 시트에 {len(all_data)}행 기록 완료")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 기존 시트 데이터 읽기 (현재 상태 확인용)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def read_current_sheet(sh, tab_name):
    """기존 시트 데이터 읽기"""
    try:
        ws = sh.worksheet(tab_name)
        data = ws.get_all_values()
        print(f"  [{tab_name}] 현재 {len(data)}행 (헤더 포함)")
        return data
    except gspread.exceptions.WorksheetNotFound:
        print(f"  [{tab_name}] 탭 없음 — 새로 생성 예정")
        return []


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 메인 실행
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def main():
    dry_run = "--write" not in sys.argv

    print("=" * 60)
    print("구글시트 품목/거래처 매칭 업데이트 스크립트 v1.0")
    print("=" * 60)

    # 1) DB 로드
    print("\n[1/5] DB 로드...")
    products, customers = load_db()
    print(f"  품목: {len(products)}건, 거래처: {len(customers)}건")

    # 2) 인덱스 구축
    print("\n[2/5] 검색 인덱스 구축...")
    full_index, country_index, active_products = build_product_index(products)
    cust_index, active_customers = build_customer_index(customers)
    print(f"  전체 인덱스: {len(full_index)} 토큰")
    print(f"  원산지별 인덱스:")
    for country, idx in sorted(country_index.items()):
        # 해당 국가의 고유 ProdKey 수 계산
        prod_keys = set()
        for entries in idx.values():
            for e in entries:
                prod_keys.add(e["ProdKey"])
        print(f"    {country}: {len(idx)} 토큰, {len(prod_keys)} 품목")
    print(f"  거래처 인덱스: {len(cust_index)} 이름")

    # 3) 카톡 대화 로드 및 빈도 집계
    print("\n[3/5] 카톡 대화 로드 및 빈도 집계...")
    messages = load_kakao_messages(KAKAO_DIR)
    print(f"  메시지: {len(messages)}건")

    if messages:
        product_counter, raw_counter = count_product_occurrences(messages, products)
        supplier_counter = count_supplier_occurrences(messages)
        print(f"  품목 후보: {len(product_counter)}건 (원산지 구분)")
        print(f"  품목 후보 (원산지 무관): {len(raw_counter)}건")
        print(f"  거래처 후보: {len(supplier_counter)}건")

        # 상위 20개 출력
        print("\n  [품목 상위 20]")
        for (name, origin), cnt in product_counter.most_common(20):
            origin_label = f" ({origin})" if origin else ""
            print(f"    {name}{origin_label}: {cnt}건")

        print("\n  [거래처 상위 20]")
        for name, cnt in supplier_counter.most_common(20):
            print(f"    {name}: {cnt}건")
    else:
        # 카톡 대화 없으면 reclassification_result.json에서 가져옴
        print("  카톡 대화 없음 → reclassification_result.json 사용")
        reclass_path = os.path.join(DATA_DIR, "reclassification_result.json")
        if os.path.exists(reclass_path):
            reclass = load_json(reclass_path)
            # products + varieties를 합산
            product_counter = Counter()
            raw_counter = Counter()
            for name, cnt in reclass.get("products", {}).items():
                product_counter[(name, "")] = cnt
                raw_counter[name] = cnt
            for key, cnt in reclass.get("varieties", {}).items():
                parts = key.split("/")
                variety = parts[-1] if len(parts) > 1 else key
                flower = parts[0] if len(parts) > 1 else ""
                product_counter[(variety, "")] += cnt
                raw_counter[variety] += cnt

            supplier_counter = Counter()
            for name, cnt in reclass.get("suppliers", {}).items():
                normalized = SUPPLIER_ALIASES.get(name, name)
                supplier_counter[normalized] += cnt
        else:
            print("  [ERROR] 데이터 소스 없음!")
            return

    # 4) 매칭 테이블 생성
    print("\n[4/5] 매칭 테이블 생성...")
    product_rows = build_product_matching_rows(product_counter, full_index, country_index)
    customer_rows = build_customer_matching_rows(supplier_counter, cust_index)

    # 5) 구글시트 업데이트
    print("\n[5/5] 구글시트 연결...")
    sh = connect_gsheet()

    # 현재 상태 확인
    print("\n  현재 시트 상태:")
    read_current_sheet(sh, "품목매칭")
    read_current_sheet(sh, "거래처매칭")

    # 업데이트
    update_product_sheet(sh, product_rows, dry_run=dry_run)
    update_customer_sheet(sh, customer_rows, dry_run=dry_run)

    # 결과 요약 저장 (로컬)
    summary = {
        "product_rows": len(product_rows),
        "customer_rows": len(customer_rows),
        "product_stats": {
            "확정": sum(1 for r in product_rows if r[8] == "확정"),
            "자동매칭": sum(1 for r in product_rows if r[8] == "자동매칭"),
            "확인필요": sum(1 for r in product_rows if r[8] == "확인필요"),
            "미매칭": sum(1 for r in product_rows if r[8] == "미매칭"),
        },
        "customer_stats": {
            "확정": sum(1 for r in customer_rows if r[7] == "확정"),
            "자동매칭": sum(1 for r in customer_rows if r[7] == "자동매칭"),
            "확인필요": sum(1 for r in customer_rows if r[7] == "확인필요"),
            "미매칭": sum(1 for r in customer_rows if r[7] == "미매칭"),
        },
        "dry_run": dry_run,
    }

    summary_path = os.path.join(DATA_DIR, "matching_update_summary.json")
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(f"\n  요약 저장: {summary_path}")

    # 카네이션 원산지 구분 검증
    print("\n" + "=" * 60)
    print("카네이션 원산지 구분 검증:")
    print("=" * 60)
    carnation_rows = [r for r in product_rows if "카네이션" in r[0]]
    for r in carnation_rows[:20]:
        print(f"  {r[0]:30s} | {r[1]:>5} | {r[3]:40s} | {r[5]:10s} | {r[8]}")

    print(f"\n{'='*60}")
    if dry_run:
        print("DRY RUN 완료. 시트에 쓰려면: python update_product_matching.py --write")
    else:
        print("시트 업데이트 완료!")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
