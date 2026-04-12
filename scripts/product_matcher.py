#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
카카오톡 메시지에서 품명/거래처 전수 추출 → DB 매칭 사전 생성
v2: 자동 음역 사전 + 강화된 매칭
"""

import json
import os
import re
import sys
from collections import Counter, defaultdict
from difflib import SequenceMatcher

# Paths
KAKAO_DIR = r"C:\Users\USER\Downloads\카톡대화데이터"
PRODUCTS_PATH = r"C:\Users\USER\nenova_agent\data\master_products.json"
CUSTOMERS_PATH = r"C:\Users\USER\nenova_agent\data\master_customers.json"
OUTPUT_DIR = r"C:\Users\USER\nenova_agent\data"

# ─── 수동 한영 음역 사전 (자동 추출 보완용) ───
MANUAL_TRANSLITERATION = {
    # 카톡에서 자주 쓰이는데 DB에 괄호 안 한글로만 있는 것들
    "몬디알": "mondial", "노비아": "novia", "코랄리프": "coral reef",
    "비스위트": "be sweet", "폴림니아": "polimnia",
    "스테노카르푸스": "stenocarpus", "딥실버": "deep silver",
    "캔들라이트": "candlelight", "플라야블랑카": "playa blanca",
    "로다스크림": "rodas", "스노우플레이크": "snowflake",
    "에뮤그라스": "emu grass", "반커부쉬": "bankerbush",
    "유카리": "eucari",
    # 꽃 대분류
    "카네이션": "carnation", "장미": "rose", "튤립": "tulip",
    "수국": "hydrangea", "안시리움": "anthurium", "카라": "calla",
    "알스트로": "alstromeria", "스위트피": "sweet pea",
    "거베라": "gerbera", "작약": "peony",
    "히아신스": "hyacinthus", "덴드륨": "dendrobium",
    "아마릴리스": "amaryllis", "모카라": "mokara",
    "델피늄": "delphinium", "글라디오루스": "gladiolus",
    "유칼립투스": "eucalyptus", "리시안셔스": "lisianthus",
    "리시안서스": "lisianthus", "유스토마": "eustoma",
    "나르시스": "narcissus", "알륨": "allium",
    "미니카네이션": "minicarnation", "스프레이카네이션": "spray carnation",
    "스프레이장미": "spray rose", "헬레보루스": "helleborus",
    "에린지움": "eryngium", "클레마티스": "clematis",
    "핀쿠션": "pincushion", "프리티라리아": "fritillaria",
    "라넌큘러스": "ranunculus", "아네모네": "anemone",
    "루스커스": "ruscus", "루스쿠스": "ruscus",
    "스키미아": "skimmia", "스키미야": "skimmia",
    "온시디움": "oncidium", "덴파레": "dendrobium",
    "심비디움": "cymbidium", "백합": "lily",
    "레몬잎": "salal", "목화": "cotton",
    "호접란": "orchid", "피어니": "peony",
    "아가판서스": "agapanthus",
    # 국가
    "콜롬비아": "colombia", "에티오피아": "ethiopia",
    "에콰도르": "ecuador", "네덜란드": "netherlands",
    "뉴질랜드": "new zealand", "이스라엘": "israel",
    # 농장
    "꼴리브리": "colibri", "콜리브리": "colibri",
    "멜로디": "melody", "더글라스": "douglas",
    # 고빈도 미매칭 보강 (카톡 한글명 → DB 영문명)
    "유카리": "yukari", "유카리체리": "yukari cherry",
    "스노우플레이크": "snow flake", "몬디알화이트": "mondial white",
    "핑크몬디알": "pink mondial", "모멘텀": "momentum", "모멘툼": "momentum",
    "돈페드로": "don pedro", "로다스": "rodas",
    "비스윗": "be sweet", "비스위트": "be sweet",
    "엄브렐라펀": "umbrella fern", "스틸그라스": "steel grass",
    "애플티": "apple tea", "코알라펀": "koala fern",
    "샤넬": "chanel", "마리포사": "mariposa",
    "에뮤그라스": "emu grass",
    "캔들라이트": "candlelight", "플라야블랑카": "playa blanca",
    "엄브렐라펀": "umbrella", "헤르모사": "hermosa",
    "헤르메스": "hermes", "헤르메스오렌지": "hermes",
    "체리오": "cherrio", "카오리": "kaori",
    "브라이튼": "brighton", "하츠": "hearts",
    "이케바나": "ikebana", "릴로나": "rilona",
    "아마릴리스릴로나": "rilona", "휘슬러": "whistler",
    "라벤다": "lavender",
    "로다스크림": "rodas", "몬디알": "mondial",
    "프라우드": "proud", "프리덤": "freedom",
    "만달라": "mandala", "오션송": "ocean song",
    "돈셀": "doncel", "문라이트": "moonlight",
    "쉬머": "shimmer", "진그린": "esmeral",
    "글로잉": "glowing", "알프스": "alps",
    "매지컬": "magical", "팔레르모": "palermo",
    "에르메스": "hermes", "블랙잭": "black jack",
    "코만치": "comanche", "카펠로": "capello",
    "넬슨": "nelson", "히어로": "hero", "리갈": "regal",
    "스테노카르푸스": "stenocarpus",
    "몬디알 화이트": "mondial white",
    # 추가 품종/소재
    "아이비": "ivy", "스타티스": "statice",
    "유포르비아": "euphorbia", "미모사": "mimosa",
    "프로테아": "protea", "피토스": "pittosporum",
    "베로니카": "veronica", "부바르디아": "bouvardia",
    "파니쿰": "panicum", "젠티아나": "gentiana",
    "사포나리아": "saponaria", "샌더소니아": "sandersonia",
    "코치아": "kochia", "콘티누스": "continus",
    "팜파스": "cartadria", "코쿨루스": "cocculus",
    "리모니움": "limonium",
}


def auto_build_transliteration(products):
    """
    DB ProdName에서 '한글 (English)' 패턴을 자동 추출하여 음역 사전 구축.
    예: "Carnation CHINA / 문라이트 (Moonlight)" → {"문라이트": "moonlight"}
    예: "ROSE CHINA / 프리덤(Freedom)" → {"프리덤": "freedom"}
    """
    trans = dict(MANUAL_TRANSLITERATION)  # start with manual

    # Pattern 1: 한글 (English) or 한글(English)
    pat1 = re.compile(r"([가-힣]+)\s*\(([A-Za-z][A-Za-z\s]+?)\)")
    # Pattern 2: English (한글)
    pat2 = re.compile(r"([A-Za-z][A-Za-z\s]+?)\s*\(([가-힣]+)\)")
    # Pattern 3: "/ 한글명 (English)" in product names like "[MEL] Carnation CHINA / 한글 (Eng)"
    pat3 = re.compile(r"/\s*([가-힣][가-힣\s]+?)\s*\(([A-Za-z][A-Za-z\s]+?)\)")

    for p in products:
        name = p["ProdName"]

        for m in pat1.finditer(name):
            kr, en = m.group(1).strip(), m.group(2).strip()
            if len(kr) >= 2 and len(en) >= 2:
                trans.setdefault(kr, en.lower())

        for m in pat2.finditer(name):
            en, kr = m.group(1).strip(), m.group(2).strip()
            if len(kr) >= 2 and len(en) >= 2:
                trans.setdefault(kr, en.lower())

        for m in pat3.finditer(name):
            kr, en = m.group(1).strip(), m.group(2).strip()
            if len(kr) >= 2 and len(en) >= 2:
                trans.setdefault(kr, en.lower())

    # Build reverse map
    reverse = {}
    for kr, en in trans.items():
        reverse.setdefault(en.lower(), []).append(kr)

    return trans, reverse


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_kakao_messages(kakao_dir):
    """Load all kakaotalk txt files, return list of (filename, room_name, messages)"""
    all_messages = []
    msg_pattern = re.compile(r"^\[(.+?)\]\s*\[(.+?)\]\s*(.*)")

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
        for line in lines[2:]:  # Skip header lines
            line = line.rstrip("\n")
            m = msg_pattern.match(line)
            if m:
                if current_msg:
                    all_messages.append(current_msg)
                sender, time, text = m.group(1), m.group(2), m.group(3)
                current_msg = {
                    "room": room_name,
                    "sender": sender,
                    "time": time,
                    "text": text,
                    "file": fname,
                }
            elif line.startswith("---"):
                if current_msg:
                    all_messages.append(current_msg)
                    current_msg = None
            elif line == "메시지가 삭제되었습니다.":
                continue
            elif current_msg and line.strip():
                current_msg["text"] += "\n" + line

        if current_msg:
            all_messages.append(current_msg)

    return all_messages


def build_product_tokens(products):
    """Build token index from DB products for fast matching."""
    token_index = {}  # token -> list of product entries

    for p in products:
        prod_name = p["ProdName"].strip()
        prod_key = p["ProdKey"]
        flower = p["FlowerName"]
        country = p["CounName"]

        entry = {
            "ProdKey": prod_key,
            "ProdName": prod_name,
            "FlowerName": flower,
            "CounName": country,
        }

        # Full name as token
        token_index.setdefault(prod_name.lower(), []).append(entry)

        # Split by common delimiters
        # e.g. "CARNATION Polimnia" -> ["carnation", "polimnia"]
        # e.g. "Gerbera Germini spider fairytale" -> tokens
        # e.g. "[MEL] Carnation CHINA / 마스터 레드 (Master Red)" -> multiple tokens

        # Remove brackets content but keep it as token too
        bracket_content = re.findall(r"\[(.+?)\]", prod_name)
        paren_content = re.findall(r"\((.+?)\)", prod_name)

        # Clean name: remove [xxx], (xxx)
        clean = re.sub(r"\[.*?\]", "", prod_name)
        clean = re.sub(r"\(.*?\)", "", clean)

        # Split by / and spaces
        parts = re.split(r"[/\s]+", clean)
        parts = [p.strip().strip(",").strip() for p in parts if len(p.strip()) >= 2]

        for part in parts:
            key = part.lower()
            if key not in ("the", "and", "cm", "st", "10", "12", "단", "송이", "박스"):
                token_index.setdefault(key, []).append(entry)

        # Also add bracket and paren contents
        for bc in bracket_content + paren_content:
            for sub in re.split(r"[/\s,]+", bc):
                sub = sub.strip()
                if len(sub) >= 2:
                    token_index.setdefault(sub.lower(), []).append(entry)

    return token_index


def build_product_name_map(products):
    """Build maps for various matching strategies."""
    # Exact name map
    exact_map = {}
    for p in products:
        name = p["ProdName"].strip()
        exact_map[name.lower()] = p

    # Build Korean part -> products map
    korean_parts = {}
    english_parts = {}
    for p in products:
        name = p["ProdName"]
        # Extract Korean parts
        kr_tokens = re.findall(r"[가-힣]+", name)
        for t in kr_tokens:
            if len(t) >= 2:
                korean_parts.setdefault(t, []).append(p)
        # Extract English parts
        en_tokens = re.findall(r"[A-Za-z]+", name)
        for t in en_tokens:
            if len(t) >= 2:
                english_parts.setdefault(t.lower(), []).append(p)

    return exact_map, korean_parts, english_parts


# ─── Known farm names (from industry knowledge + kakaotalk patterns) ───
KNOWN_FARMS = {
    "꼴리브리", "콜리브리", "멜로디", "더글라스", "홀랙스",
    "CL", "클래식", "아시엔다", "에스메랄다팜",
    "마티즈", "엘리트", "선라이즈", "트레볼",
    "나리타", "엘도라도",
    # From kakaotalk patterns like "CL50", "CL1", "CL64"
}

# Known quantity patterns
QTY_PATTERN = re.compile(
    r"(\d+)\s*(단|송이|박스|스팀|스템|stem|box|bx|bunch|줄기|속|묶음|팩|세트|개)",
    re.IGNORECASE,
)

# Order/period pattern
PERIOD_PATTERN = re.compile(r"(\d{1,2})\s*[-~]\s*(\d)\s*(차|콜)?")

# Product candidate pattern: words before quantity
PROD_BEFORE_QTY = re.compile(
    r"([가-힣A-Za-z\s/]+?)\s*(\d+)\s*(단|송이|박스|스팀|스템|stem|box)",
    re.IGNORECASE,
)


def extract_product_candidates(messages, products, token_index, korean_parts, english_parts):
    """Extract all product name candidates from kakaotalk messages."""

    # Pre-build search terms from DB
    # All unique meaningful tokens from products
    search_tokens = set()
    for p in products:
        name = p["ProdName"]
        # Korean tokens (2+ chars)
        for t in re.findall(r"[가-힣]{2,}", name):
            search_tokens.add(t)
        # English tokens (3+ chars, skip common words)
        for t in re.findall(r"[A-Za-z]{3,}", name):
            if t.lower() not in {"the", "and", "per", "tak", "wit", "van", "spray", "double", "single", "mini", "dyed", "green", "white", "red", "pink", "blue", "yellow", "orange", "purple", "cream"}:
                search_tokens.add(t.lower())

    # FlowerName set for matching
    flower_names_kr = set()
    for p in products:
        fn = p["FlowerName"]
        if fn and len(fn) >= 2:
            flower_names_kr.add(fn)

    # Also add transliteration keys as search terms
    for kr in MANUAL_TRANSLITERATION:
        if len(kr) >= 2:
            search_tokens.add(kr)

    # Track occurrences
    product_mentions = Counter()  # "found_text" -> count
    product_contexts = defaultdict(list)  # "found_text" -> [context snippets]
    product_sources = defaultdict(set)  # "found_text" -> set of rooms

    # Strategy 1: Search for DB tokens in messages
    print("  Strategy 1: DB token search in messages...")
    for msg in messages:
        text = msg["text"]
        text_lower = text.lower()
        room = msg["room"]

        # Skip non-content messages
        if text.strip() in ("사진", "동영상", "이모티콘") or text.startswith("파일:"):
            continue
        if "삭제되었습니다" in text:
            continue

        # Check each flower name (high priority)
        for fn in flower_names_kr:
            if fn in text:
                product_mentions[fn] += 1
                product_sources[fn].add(room)
                if len(product_contexts[fn]) < 3:
                    product_contexts[fn].append(text[:200])

        # Check Korean tokens from DB
        for token in search_tokens:
            if len(token) < 2:
                continue
            if token in text or token in text_lower:
                product_mentions[token] += 1
                product_sources[token].add(room)
                if len(product_contexts[token]) < 3:
                    product_contexts[token].append(text[:200])

    # Strategy 2: Extract words before quantity patterns
    print("  Strategy 2: Words before quantity patterns...")
    for msg in messages:
        text = msg["text"]
        room = msg["room"]

        if text.strip() in ("사진", "동영상", "이모티콘"):
            continue

        matches = PROD_BEFORE_QTY.findall(text)
        for match_group in matches:
            candidate = match_group[0].strip()
            # Clean up
            candidate = re.sub(r"^\d+[-~]\d+\s*(차|콜)?\s*", "", candidate)
            candidate = re.sub(r"^(중국|콜|네덜란드|태국|에콰도르|에티오피아)\s*", "", candidate)
            candidate = candidate.strip()
            if len(candidate) >= 2 and candidate not in ("추가", "변경", "취소", "출고", "발주", "요청", "확인", "총"):
                product_mentions[f"[qty_prefix]{candidate}"] += 1
                product_sources[f"[qty_prefix]{candidate}"].add(room)
                if len(product_contexts[f"[qty_prefix]{candidate}"]) < 3:
                    product_contexts[f"[qty_prefix]{candidate}"].append(text[:200])

    # Strategy 3: Line-by-line product extraction (standalone product lines)
    print("  Strategy 3: Standalone product line detection...")
    standalone_pattern = re.compile(
        r"^[\s]*([가-힣A-Za-z][가-힣A-Za-z\s/()]+?)\s*(\d+)\s*(단|송이|박스|스팀|스템|stem|box)",
        re.IGNORECASE | re.MULTILINE,
    )
    for msg in messages:
        text = msg["text"]
        room = msg["room"]
        for line in text.split("\n"):
            line = line.strip()
            m = standalone_pattern.match(line)
            if m:
                candidate = m.group(1).strip()
                candidate = re.sub(r"^\d+[-~]\d+\s*(차|콜)?\s*", "", candidate)
                candidate = re.sub(r"^(중국|콜|네덜란드|태국|에콰도르|에티오피아|국내)\s*", "", candidate)
                candidate = candidate.strip()
                if len(candidate) >= 2 and candidate not in ("추가", "변경", "취소", "출고", "발주", "요청", "확인", "총"):
                    product_mentions[f"[standalone]{candidate}"] += 1
                    product_sources[f"[standalone]{candidate}"].add(room)

    # Strategy 4: Known English product patterns from import messages
    print("  Strategy 4: English product patterns ([MEL], CARNATION, etc.)...")
    eng_product_pattern = re.compile(
        r"(?:\[MEL\]|CARNATION|ROSE|SPRAY|HYDRANGEA|TULIP|LILY|CALLA|ALSTROMERIA|LISIANTHUS|EUSTOMA|DENDROBIUM|ANTHURIUM|GERBERA|PEONY)\s+([A-Za-z][A-Za-z\s]+?)(?:\s+\d|\s*$|\s*[/,])",
        re.IGNORECASE,
    )
    for msg in messages:
        text = msg["text"]
        room = msg["room"]
        for m in eng_product_pattern.finditer(text):
            candidate = m.group(1).strip()
            if len(candidate) >= 3:
                product_mentions[f"[eng]{candidate}"] += 1
                product_sources[f"[eng]{candidate}"].add(room)
                if len(product_contexts[f"[eng]{candidate}"]) < 3:
                    product_contexts[f"[eng]{candidate}"].append(text[:200])

    return product_mentions, product_contexts, product_sources


def match_to_db(candidate_text, products, exact_map, korean_parts, english_parts, token_index):
    """Try to match a candidate product name to DB products."""

    # Clean candidate
    clean = candidate_text
    for prefix in ("[qty_prefix]", "[standalone]", "[eng]"):
        clean = clean.replace(prefix, "")
    clean = clean.strip()
    clean_lower = clean.lower()

    # 1. Exact match
    if clean_lower in exact_map:
        p = exact_map[clean_lower]
        return {
            "ProdKey": p["ProdKey"],
            "ProdName": p["ProdName"],
            "FlowerName": p["FlowerName"],
            "CounName": p["CounName"],
            "confidence": 1.0,
            "match_type": "exact",
        }

    # 2. Exact match on FlowerName (대분류)
    for p in products:
        if p["FlowerName"].lower() == clean_lower:
            return {
                "ProdKey": p["ProdKey"],
                "ProdName": p["ProdName"],
                "FlowerName": p["FlowerName"],
                "CounName": p["CounName"],
                "confidence": 0.7,
                "match_type": "flower_category",
            }

    # 3. Partial match - candidate is substring of ProdName or vice versa
    best_partial = None
    best_partial_score = 0
    for p in products:
        pn = p["ProdName"].lower()
        if clean_lower in pn:
            score = len(clean_lower) / len(pn)
            if score > best_partial_score:
                best_partial_score = score
                best_partial = p
        elif pn in clean_lower:
            score = len(pn) / len(clean_lower)
            if score > best_partial_score:
                best_partial_score = score
                best_partial = p

    if best_partial and best_partial_score > 0.3:
        return {
            "ProdKey": best_partial["ProdKey"],
            "ProdName": best_partial["ProdName"],
            "FlowerName": best_partial["FlowerName"],
            "CounName": best_partial["CounName"],
            "confidence": round(min(0.9, 0.5 + best_partial_score * 0.4), 2),
            "match_type": "partial",
        }

    # 4. Korean part match
    kr_tokens = re.findall(r"[가-힣]{2,}", clean)
    for token in kr_tokens:
        if token in korean_parts:
            candidates = korean_parts[token]
            # If there's a unique match, use it
            if len(candidates) == 1:
                p = candidates[0]
                return {
                    "ProdKey": p["ProdKey"],
                    "ProdName": p["ProdName"],
                    "FlowerName": p["FlowerName"],
                    "CounName": p["CounName"],
                    "confidence": 0.7,
                    "match_type": "korean_token",
                }
            elif len(candidates) <= 10:
                # Pick the one with shortest name (most specific)
                p = min(candidates, key=lambda x: len(x["ProdName"]))
                return {
                    "ProdKey": p["ProdKey"],
                    "ProdName": p["ProdName"],
                    "FlowerName": p["FlowerName"],
                    "CounName": p["CounName"],
                    "confidence": 0.5,
                    "match_type": "korean_token_multi",
                }

    # 5. English part match
    en_tokens = re.findall(r"[A-Za-z]{3,}", clean)
    for token in en_tokens:
        tl = token.lower()
        if tl in english_parts:
            candidates = english_parts[tl]
            if len(candidates) == 1:
                p = candidates[0]
                return {
                    "ProdKey": p["ProdKey"],
                    "ProdName": p["ProdName"],
                    "FlowerName": p["FlowerName"],
                    "CounName": p["CounName"],
                    "confidence": 0.7,
                    "match_type": "english_token",
                }
            elif len(candidates) <= 10:
                p = min(candidates, key=lambda x: len(x["ProdName"]))
                return {
                    "ProdKey": p["ProdKey"],
                    "ProdName": p["ProdName"],
                    "FlowerName": p["FlowerName"],
                    "CounName": p["CounName"],
                    "confidence": 0.5,
                    "match_type": "english_token_multi",
                }

    # 6. Transliteration match (uses global TRANS_MAP / REVERSE_TRANS_MAP set in main)
    if clean_lower in TRANS_MAP:
        en_equiv = TRANS_MAP[clean_lower]
        # Search DB for english equivalent
        for p in products:
            if en_equiv.lower() in p["ProdName"].lower():
                return {
                    "ProdKey": p["ProdKey"],
                    "ProdName": p["ProdName"],
                    "FlowerName": p["FlowerName"],
                    "CounName": p["CounName"],
                    "confidence": 0.85,
                    "match_type": "transliteration",
                }

    # Try reverse: if candidate is English, check if Korean equivalent exists in DB
    if clean_lower in REVERSE_TRANS_MAP:
        kr_equivs = REVERSE_TRANS_MAP[clean_lower]
        for kr in kr_equivs:
            for p in products:
                if kr in p["ProdName"]:
                    return {
                        "ProdKey": p["ProdKey"],
                        "ProdName": p["ProdName"],
                        "FlowerName": p["FlowerName"],
                        "CounName": p["CounName"],
                        "confidence": 0.85,
                        "match_type": "transliteration_reverse",
                    }

    # 7. Fuzzy match (SequenceMatcher)
    best_fuzzy = None
    best_ratio = 0
    for p in products:
        pn = p["ProdName"].lower()
        # Only compare if at least one common character
        ratio = SequenceMatcher(None, clean_lower, pn).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_fuzzy = p

    if best_fuzzy and best_ratio >= 0.6:
        return {
            "ProdKey": best_fuzzy["ProdKey"],
            "ProdName": best_fuzzy["ProdName"],
            "FlowerName": best_fuzzy["FlowerName"],
            "CounName": best_fuzzy["CounName"],
            "confidence": round(best_ratio * 0.8, 2),
            "match_type": "fuzzy",
        }

    return None


def extract_customer_candidates(messages, customers):
    """Extract customer names from kakaotalk messages."""

    # Build customer name tokens
    cust_names = {}
    cust_short = {}
    for c in customers:
        name = c["CustName"].strip()
        cust_names[name.lower()] = c
        # Short name: remove (주), (재) etc
        short = re.sub(r"[\(（]주[\)）]|[\(（]재[\)）]|\(주식회사\)", "", name).strip()
        if short != name and len(short) >= 2:
            cust_short[short.lower()] = c
        # Also try without parenthetical
        no_paren = re.sub(r"\(.*?\)", "", name).strip()
        if no_paren and len(no_paren) >= 2:
            cust_short[no_paren.lower()] = c

    # Known kakaotalk sender → customer mapping patterns
    # Sender names like "네노바 정재훈님", "가브리엘", "Teresa", "아드리아나"
    # are internal staff, not customers

    customer_mentions = Counter()
    customer_contexts = defaultdict(list)
    customer_sources = defaultdict(set)

    # Search for customer names in messages
    for msg in messages:
        text = msg["text"]
        text_lower = text.lower()
        room = msg["room"]

        if text.strip() in ("사진", "동영상", "이모티콘"):
            continue

        # Check full customer names
        for name_lower, c in cust_names.items():
            if len(name_lower) >= 3 and name_lower in text_lower:
                customer_mentions[c["CustName"]] += 1
                customer_sources[c["CustName"]].add(room)
                if len(customer_contexts[c["CustName"]]) < 3:
                    customer_contexts[c["CustName"]].append(text[:200])

        # Check short names
        for short_lower, c in cust_short.items():
            if len(short_lower) >= 3 and short_lower in text_lower:
                customer_mentions[c["CustName"]] += 1
                customer_sources[c["CustName"]].add(room)

    # Also extract customer-like patterns from order messages
    # e.g. "유오디아", "소재2호", etc.
    customer_pattern = re.compile(
        r"(?:^|\n)\s*([가-힣A-Za-z0-9]+(?:\s*\d*호)?)\s*(?:\n|$)",
        re.MULTILINE,
    )

    for msg in messages:
        text = msg["text"]
        room = msg["room"]

        # Look for lines that appear between period/order info and product lists
        lines = text.split("\n")
        for i, line in enumerate(lines):
            line = line.strip()
            # Skip known non-customer patterns
            if not line or len(line) < 2 or len(line) > 20:
                continue
            if any(kw in line for kw in ["사진", "동영상", "이모티콘", "파일:", "확인", "감사", "부탁", "요청", "변경", "추가", "취소"]):
                continue
            if QTY_PATTERN.search(line):
                continue
            if PERIOD_PATTERN.match(line):
                continue

            # If previous line has period pattern and this line looks like a name
            if i > 0:
                prev = lines[i-1].strip()
                if PERIOD_PATTERN.search(prev) or any(kw in prev for kw in ["발주", "출고", "요청", "추가", "변경"]):
                    # This might be a customer name
                    if re.match(r"^[가-힣A-Za-z0-9\s]+$", line) and len(line) <= 15:
                        customer_mentions[f"[context]{line}"] += 1
                        customer_sources[f"[context]{line}"].add(room)
                        if len(customer_contexts[f"[context]{line}"]) < 3:
                            customer_contexts[f"[context]{line}"].append(text[:200])

    return customer_mentions, customer_contexts, customer_sources


def match_customer_to_db(candidate_text, customers, cust_name_map, cust_short_map):
    """Match a customer candidate to DB."""
    clean = candidate_text.replace("[context]", "").strip()
    clean_lower = clean.lower()

    # Exact match
    if clean_lower in cust_name_map:
        c = cust_name_map[clean_lower]
        return {
            "CustKey": c["CustKey"],
            "CustName": c["CustName"],
            "Group1": c["Group1"],
            "confidence": 1.0,
            "match_type": "exact",
        }

    # Short name match
    if clean_lower in cust_short_map:
        c = cust_short_map[clean_lower]
        return {
            "CustKey": c["CustKey"],
            "CustName": c["CustName"],
            "Group1": c["Group1"],
            "confidence": 0.9,
            "match_type": "short_name",
        }

    # Partial match
    best = None
    best_score = 0
    for c in customers:
        cn = c["CustName"].lower()
        if clean_lower in cn:
            score = len(clean_lower) / len(cn)
            if score > best_score:
                best_score = score
                best = c
        elif cn in clean_lower:
            score = len(cn) / len(clean_lower)
            if score > best_score:
                best_score = score
                best = c

    if best and best_score > 0.4:
        return {
            "CustKey": best["CustKey"],
            "CustName": best["CustName"],
            "Group1": best["Group1"],
            "confidence": round(0.5 + best_score * 0.4, 2),
            "match_type": "partial",
        }

    # Fuzzy
    best_fuzzy = None
    best_ratio = 0
    for c in customers:
        cn = c["CustName"].lower()
        ratio = SequenceMatcher(None, clean_lower, cn).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_fuzzy = c

    if best_fuzzy and best_ratio >= 0.6:
        return {
            "CustKey": best_fuzzy["CustKey"],
            "CustName": best_fuzzy["CustName"],
            "Group1": best_fuzzy["Group1"],
            "confidence": round(best_ratio * 0.8, 2),
            "match_type": "fuzzy",
        }

    return None


def is_farm_name(text):
    """Check if text looks like a farm name rather than a product."""
    clean = text.replace("[qty_prefix]", "").replace("[standalone]", "").replace("[eng]", "").strip()
    # CL prefix pattern (CL1, CL50, etc.)
    if re.match(r"^CL\d+$", clean, re.IGNORECASE):
        return True
    if clean in KNOWN_FARMS:
        return True
    return False


TRANS_MAP = {}
REVERSE_TRANS_MAP = {}


def main():
    global TRANS_MAP, REVERSE_TRANS_MAP

    print("=" * 70)
    print("카카오톡 메시지 → DB 품명/거래처 매칭 엔진 v2")
    print("=" * 70)

    # Load data
    print("\n[1/6] 데이터 로딩...")
    products = load_json(PRODUCTS_PATH)
    customers = load_json(CUSTOMERS_PATH)
    print(f"  DB 품목: {len(products)}건")
    print(f"  DB 거래처: {len(customers)}건")

    print("\n[2/6] 카카오톡 메시지 파싱...")
    messages = load_kakao_messages(KAKAO_DIR)
    print(f"  총 메시지: {len(messages)}건")

    # Unique rooms
    rooms = set(m["room"] for m in messages)
    print(f"  채팅방: {len(rooms)}개 - {', '.join(sorted(rooms))}")

    # Build indexes
    print("\n[3/6] DB 인덱스 + 자동 음역 사전 구축...")
    token_index = build_product_tokens(products)
    exact_map, korean_parts, english_parts = build_product_name_map(products)
    TRANS_MAP, REVERSE_TRANS_MAP = auto_build_transliteration(products)
    print(f"  토큰 인덱스: {len(token_index)}개 고유 토큰")
    print(f"  한글 파트: {len(korean_parts)}개")
    print(f"  영문 파트: {len(english_parts)}개")
    print(f"  자동 음역 사전: {len(TRANS_MAP)}개 한→영 매핑")

    # Extract product candidates
    print("\n[4/6] 카톡에서 품명 추출 중...")
    prod_mentions, prod_contexts, prod_sources = extract_product_candidates(
        messages, products, token_index, korean_parts, english_parts
    )

    # Filter: only keep candidates with 2+ mentions or from meaningful contexts
    filtered_mentions = {k: v for k, v in prod_mentions.items() if v >= 1}
    print(f"  원시 품명 후보: {len(prod_mentions)}개")
    print(f"  필터 후: {len(filtered_mentions)}개")

    # Match to DB
    print("\n[5/6] DB 매칭 중...")
    product_dictionary = {}
    farm_names = {}
    unmatched_products = {}

    for candidate, count in sorted(filtered_mentions.items(), key=lambda x: -x[1]):
        # Check if it's a farm name
        if is_farm_name(candidate):
            clean = candidate.replace("[qty_prefix]", "").replace("[standalone]", "").replace("[eng]", "").strip()
            farm_names[clean] = {
                "type": "farm_name",
                "occurrences": count,
                "rooms": list(prod_sources.get(candidate, set())),
            }
            continue

        # Skip very common non-product words
        clean = candidate.replace("[qty_prefix]", "").replace("[standalone]", "").replace("[eng]", "").strip()
        skip_words = {
            "확인", "부탁", "감사", "요청", "변경", "추가", "취소", "출고", "발주",
            "네", "아", "예", "좀", "건", "님", "시", "고", "해", "합니다", "드립니다",
            "가능", "불가", "진행", "완료", "수정", "삭제", "등록", "이번", "이번주",
            "다음", "오늘", "내일", "모레", "어제", "월요일", "화요일", "수요일",
            "목요일", "금요일", "토요일", "일요일", "사진", "동영상", "이모티콘",
            "총", "전부", "모두", "각", "당", "씩", "차", "콜", "단", "중국",
            "콜롬비아", "네덜란드", "태국", "에콰도르", "에티오피아", "국내",
            "최소", "최대", "가격", "단가", "원가", "비용", "입금", "정산",
        }
        if clean in skip_words or len(clean) < 2:
            continue

        match = match_to_db(candidate, products, exact_map, korean_parts, english_parts, token_index)

        if match:
            # Use the clean candidate name as key
            key = clean
            if key in product_dictionary:
                # Merge: keep higher confidence
                if match["confidence"] > product_dictionary[key]["confidence"]:
                    product_dictionary[key] = match
                    product_dictionary[key]["occurrences"] = count
                    product_dictionary[key]["rooms"] = list(prod_sources.get(candidate, set()))
                else:
                    product_dictionary[key]["occurrences"] += count
            else:
                match["occurrences"] = count
                match["rooms"] = list(prod_sources.get(candidate, set()))
                product_dictionary[key] = match
        else:
            if clean not in unmatched_products:
                unmatched_products[clean] = {
                    "occurrences": count,
                    "rooms": list(prod_sources.get(candidate, set())),
                    "sample_context": prod_contexts.get(candidate, [])[:2],
                }
            else:
                unmatched_products[clean]["occurrences"] += count

    # ─── Customer matching ───
    print("\n[5.5/6] 거래처 매칭 중...")
    cust_name_map = {c["CustName"].lower(): c for c in customers}
    cust_short_map = {}
    for c in customers:
        short = re.sub(r"[\(（]주[\)）]|[\(（]재[\)）]", "", c["CustName"]).strip()
        if short != c["CustName"]:
            cust_short_map[short.lower()] = c

    cust_mentions, cust_contexts, cust_sources = extract_customer_candidates(messages, customers)

    customer_dictionary = {}
    unmatched_customers = {}

    # Build a set of known product-related terms to filter from customer candidates
    product_terms = set()
    for p in products:
        for t in re.findall(r"[가-힣]{2,}", p["ProdName"]):
            product_terms.add(t)
        product_terms.add(p["FlowerName"])

    for candidate, count in sorted(cust_mentions.items(), key=lambda x: -x[1]):
        clean = candidate.replace("[context]", "").strip()
        if len(clean) < 2:
            continue

        # Skip if this looks like a product name (from [context] extraction)
        if candidate.startswith("[context]"):
            # Check if it matches a known product/flower term
            is_product = False
            for pt in product_terms:
                if pt in clean and len(pt) >= 2:
                    is_product = True
                    break
            # Also skip lines with quantities
            if re.search(r"\d+\s*(단|송이|박스|스팀|스템)", clean):
                is_product = True
            if is_product:
                continue

        match = match_customer_to_db(candidate, customers, cust_name_map, cust_short_map)
        if match:
            match["occurrences"] = count
            match["rooms"] = list(cust_sources.get(candidate, set()))
            customer_dictionary[clean] = match
        else:
            # Only keep context-extracted unmatched if they don't look like products
            if not candidate.startswith("[context]") or clean not in product_terms:
                unmatched_customers[clean] = {
                    "occurrences": count,
                    "rooms": list(cust_sources.get(candidate, set())),
                    "sample_context": cust_contexts.get(candidate, [])[:2],
                }

    # ─── Save results ───
    print("\n[6/6] 결과 저장...")

    result = {
        "meta": {
            "total_messages": len(messages),
            "total_rooms": len(rooms),
            "rooms": sorted(rooms),
            "db_products": len(products),
            "db_customers": len(customers),
        },
        "product_dictionary": product_dictionary,
        "farm_names": farm_names,
        "unmatched_products": unmatched_products,
        "customer_dictionary": customer_dictionary,
        "unmatched_customers": unmatched_customers,
    }

    out_path = os.path.join(OUTPUT_DIR, "product_dictionary.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"  저장: {out_path}")

    # ─── Statistics ───
    print("\n" + "=" * 70)
    print("통계 요약")
    print("=" * 70)

    print(f"\n■ 메시지 현황")
    print(f"  총 메시지: {len(messages):,}건")
    print(f"  채팅방: {len(rooms)}개")

    print(f"\n■ 품목 매칭 현황")
    print(f"  추출된 고유 품명: {len(filtered_mentions):,}개")
    print(f"  DB 매칭 성공: {len(product_dictionary)}개")
    print(f"  미매칭: {len(unmatched_products)}개")
    print(f"  농장명 분류: {len(farm_names)}개")

    # Match type distribution
    match_types = Counter(v["match_type"] for v in product_dictionary.values())
    print(f"\n  매칭 방법별 분포:")
    for mt, cnt in match_types.most_common():
        print(f"    {mt}: {cnt}건")

    # Confidence distribution
    conf_ranges = {"0.9-1.0": 0, "0.7-0.89": 0, "0.5-0.69": 0, "<0.5": 0}
    for v in product_dictionary.values():
        c = v["confidence"]
        if c >= 0.9:
            conf_ranges["0.9-1.0"] += 1
        elif c >= 0.7:
            conf_ranges["0.7-0.89"] += 1
        elif c >= 0.5:
            conf_ranges["0.5-0.69"] += 1
        else:
            conf_ranges["<0.5"] += 1
    print(f"\n  신뢰도 분포:")
    for r, cnt in conf_ranges.items():
        print(f"    {r}: {cnt}건")

    # Top matched products
    print(f"\n  출현 빈도 Top 30 매칭 품명:")
    sorted_prods = sorted(product_dictionary.items(), key=lambda x: -x[1]["occurrences"])
    for name, info in sorted_prods[:30]:
        print(f"    {name} ({info['occurrences']}회) → {info['ProdName']} [{info['match_type']}] conf={info['confidence']}")

    # Top unmatched
    print(f"\n  미매칭 Top 20 (DB 등록 후보):")
    sorted_unmatched = sorted(unmatched_products.items(), key=lambda x: -x[1]["occurrences"])
    for name, info in sorted_unmatched[:20]:
        print(f"    {name} ({info['occurrences']}회) rooms={info['rooms']}")

    # Farm names
    if farm_names:
        print(f"\n  농장명 목록:")
        for name, info in sorted(farm_names.items(), key=lambda x: -x[1]["occurrences"]):
            print(f"    {name} ({info['occurrences']}회)")

    print(f"\n■ 거래처 매칭 현황")
    print(f"  추출된 고유 거래처명: {len(cust_mentions)}개")
    print(f"  DB 매칭 성공: {len(customer_dictionary)}개")
    print(f"  미매칭: {len(unmatched_customers)}개")

    cust_match_types = Counter(v["match_type"] for v in customer_dictionary.values())
    print(f"\n  매칭 방법별 분포:")
    for mt, cnt in cust_match_types.most_common():
        print(f"    {mt}: {cnt}건")

    # Top matched customers
    print(f"\n  출현 빈도 Top 20 매칭 거래처:")
    sorted_custs = sorted(customer_dictionary.items(), key=lambda x: -x[1]["occurrences"])
    for name, info in sorted_custs[:20]:
        print(f"    {name} ({info['occurrences']}회) → {info['CustName']} [{info['match_type']}]")

    # Top unmatched customers
    if unmatched_customers:
        print(f"\n  미매칭 거래처 Top 20:")
        sorted_uc = sorted(unmatched_customers.items(), key=lambda x: -x[1]["occurrences"])
        for name, info in sorted_uc[:20]:
            print(f"    {name} ({info['occurrences']}회)")

    print(f"\n{'='*70}")
    print(f"완료! 결과 파일: {out_path}")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()
