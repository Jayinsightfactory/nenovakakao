"""
카카오톡 자연어 품명 <-> nenovaweb DB 매칭 엔진 v2
- 한글/영문 혼합 매칭
- ProdName에서 한글명, 영문명, 괄호 안 내용 모두 추출하여 인덱싱
- 영문→한글 음역 사전 (Candlelight→캔들라이트 등)
"""
import json
import os
import re
from difflib import SequenceMatcher

DATA_DIR = r"C:\Users\USER\nenova_agent\data"

def load_json(filename):
    with open(os.path.join(DATA_DIR, filename), "r", encoding="utf-8") as f:
        return json.load(f)

def save_json(filename, data):
    with open(os.path.join(DATA_DIR, filename), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# ─── 데이터 로드 ───
products_db = load_json("master_products.json")
customers_db = load_json("master_customers.json")
reclass = load_json("reclassification_result.json")

print(f"DB 품목: {len(products_db)}건, DB 거래처: {len(customers_db)}건")

# ─── 영문→한글 음역 사전 (카톡에서 자주 쓰는 한글 표기) ───
EN_KO_TRANSLITERATION = {
    # ─── 카네이션 품종 ───
    "moonlight": "문라이트",
    "doncel": "돈셀",
    "hermes": "헤르메스",
    "hermes orange": "헤르메스오렌지",
    "novia": "노비아",
    "georgia": "지오지아",
    "polymnia": "폴림니아",
    "cherrio": "체리오",
    "yukari cherry": "유카리체리",
    "yukari": "유카리",
    "brut": "브루트",
    "ness": "네스",
    "mariposa": "마리포사",
    "electric purple": "일렉트릭퍼플",
    "colibri": "콜리브리",
    "farida": "파리다",
    "minuetto": "미뉴에또",
    "spray white": "스프레이화이트",
    "spray light pink": "스프레이연핑크",
    "gladiator": "글래디에터",
    "symphony": "심포니",

    # ─── 장미 품종 ───
    "proud": "프라우드",
    "candlelight": "캔들라이트",
    "mandala": "만달라",
    "coral reef": "코랄리프",
    "pink floyd": "핑크플로이드",
    "star platinum": "스타플레티넘",
    "laura": "로라",
    "red panther": "레드팬서",
    "pink mondial": "핑크몬디알",
    "blackjack": "블랙잭",
    "black jack": "블랙잭",
    "pink expression": "핑크익스프레션",
    "jumilia": "주밀리아",
    "pink snowberg": "핑크스노우버그",
    "sweet avalanche": "스윗아발란체",
    "julring": "줄링",
    "guitana orange flame": "가이타나오렌지플레임",
    "martina": "마티나",
    "esperance": "에스페란스",
    "freedom": "프리덤",
    "avalanche": "아발란체",
    "viviane": "비비안",
    "talea": "탈레아",
    "full house": "풀하우스",
    "pink bell": "핑크벨",
    "white o'hara": "화이트오하라",
    "peach": "피치",
    "hot shot": "핫샷",
    "carola": "카롤라",

    # ─── 색상 공통 ───
    "white": "화이트",
    "blue": "블루",
    "pink": "핑크",
    "red": "레드",
    "orange": "오렌지",
    "light pink": "연핑크",
    "dark pink": "진핑크",
    "light green": "연그린",
    "green": "그린",
    "yellow": "옐로",
    "cream": "크림",
    "lavender": "라벤더",
    "purple": "퍼플",
    "coral": "코랄",

    # ─── 기타 ───
    "salix": "살릭스",
    "salicis": "살릭스",

    # ─── 꽃 대분류 ───
    "lisianthus": "리시안셔스",
    "hydrangea": "수국",
    "carnation": "카네이션",
    "rose": "장미",
    "tulip": "튤립",
    "allium": "알륨",
    "amaryllis": "아마릴리스",
    "alstroemeria": "알스트로",
    "eucalyptus": "유칼립투스",
}

# 한글→영문 역방향도 생성
KO_EN_TRANSLITERATION = {v: k for k, v in EN_KO_TRANSLITERATION.items()}

# ─── ProdName에서 검색 가능한 이름들 추출 ───
def extract_searchable_names(prod_name):
    """ProdName에서 한글명, 영문명, 괄호 안 내용 등 모든 검색 키워드 추출"""
    names = set()
    original = prod_name.strip()
    names.add(original)

    # 괄호 안 내용 추출: (Moonlight), (연핑크), （블루）
    for m in re.finditer(r'[\(（]([^)）]+)[\)）]', original):
        inner = m.group(1).strip()
        names.add(inner)
        names.add(inner.lower())

    # '/' 기준 분리
    parts = original.split('/')
    for part in parts:
        part = part.strip()
        # [MEL], [오경] 등 대괄호 접두사 제거
        part = re.sub(r'^\[.*?\]\s*', '', part)
        names.add(part)

    # CHINA, COLOMBIA 등 국가명 제거 후 남은 부분
    cleaned = re.sub(r'\b(CHINA|COLOMBIA|ECUADOR|NETHERLANDS|THAILAND|VIETNAM|AUSTRALIA)\b', '', original, flags=re.IGNORECASE).strip()
    cleaned = re.sub(r'\s+', ' ', cleaned).strip(' /')
    names.add(cleaned)

    # 영문명만 추출 (cm 크기 제거)
    en_only = re.sub(r'\d+\s*cm', '', original, flags=re.IGNORECASE)
    en_only = re.sub(r'[\(（][^)）]*[\)）]', '', en_only)  # 괄호 제거
    en_only = re.sub(r'[/]', ' ', en_only)
    en_words = re.findall(r'[A-Za-z]+(?:\s+[A-Za-z]+)*', en_only)
    for w in en_words:
        w = w.strip()
        if len(w) > 2 and w.upper() not in ('CHINA', 'COLOMBIA', 'ECUADOR', 'NETHERLANDS', 'ROSE', 'CARNATION', 'SPRAY', 'MEL', 'BOX', 'MIX', 'THE', 'AND'):
            names.add(w)
            names.add(w.lower())

    # 한글명만 추출
    ko_parts = re.findall(r'[가-힣]+(?:\s*[가-힣]+)*', original)
    for kp in ko_parts:
        kp = kp.strip()
        if len(kp) >= 2:
            names.add(kp)

    # 영문→한글 음역 추가
    for en, ko in EN_KO_TRANSLITERATION.items():
        if en.lower() in original.lower():
            names.add(ko)

    return names


# ─── 품목 인덱스 구축 ───
active_products = [p for p in products_db if not p.get("isDeleted", False)]

# name_token → [(ProdKey, ProdName, FlowerName, CounName, score_weight)]
product_search_index = {}
for p in active_products:
    pname = p["ProdName"]
    pkey = p["ProdKey"]
    fn = p.get("FlowerName", "")
    cn = p.get("CounName", "")

    searchable = extract_searchable_names(pname)
    for token in searchable:
        token_lower = token.lower().strip()
        if len(token_lower) < 2:
            continue
        if token_lower not in product_search_index:
            product_search_index[token_lower] = []
        product_search_index[token_lower].append({
            "ProdKey": pkey,
            "ProdName": pname,
            "FlowerName": fn,
            "CounName": cn,
            "ProdCode": p.get("ProdCode", "")
        })

print(f"검색 인덱스: {len(product_search_index)} 토큰")

# ─── 꽃 대분류별 그룹핑 ───
flower_groups = {}
for p in products_db:
    fn = p.get("FlowerName", "미분류")
    if fn not in flower_groups:
        flower_groups[fn] = []
    flower_groups[fn].append({
        "ProdKey": p["ProdKey"],
        "ProdName": p["ProdName"],
        "CounName": p.get("CounName", ""),
        "isDeleted": p.get("isDeleted", False)
    })

# ─── 거래처 인덱스 ───
active_customers = [c for c in customers_db if not c.get("isDeleted", False)]
custname_index = {}
for c in active_customers:
    name = c["CustName"].strip()
    if name not in custname_index:
        custname_index[name] = []
    custname_index[name].append({
        "CustKey": c["CustKey"],
        "CustCode": c.get("CustCode", ""),
        "Group1": c.get("Group1", ""),
        "CustName": name
    })


# ─── 매칭 함수 ───

def find_product_match_v2(query, flower_hint=None):
    """고급 매칭: 한글/영문 혼합, 음역 사전 활용"""
    query = query.strip()
    result = {
        "query": query,
        "flower_hint": flower_hint,
        "matches": [],
        "best_match": None,
        "match_type": "NONE"
    }

    query_lower = query.lower()

    # 한글→영문 변환도 시도
    query_variants = [query_lower]
    if query_lower in KO_EN_TRANSLITERATION:
        query_variants.append(KO_EN_TRANSLITERATION[query_lower])
    # 영문→한글도
    if query_lower in EN_KO_TRANSLITERATION:
        query_variants.append(EN_KO_TRANSLITERATION[query_lower])

    candidates = {}  # ProdKey → {info, score, match_type}

    for q in query_variants:
        # 1) 인덱스 완전 일치
        if q in product_search_index:
            for entry in product_search_index[q]:
                pk = entry["ProdKey"]
                if pk not in candidates or candidates[pk]["score"] < 1.0:
                    candidates[pk] = {**entry, "score": 1.0, "match_type": "EXACT"}

        # 2) 인덱스 부분 일치
        for token, entries in product_search_index.items():
            if q in token or token in q:
                ratio = len(q) / max(len(token), len(q))
                if ratio >= 0.3:
                    for entry in entries:
                        pk = entry["ProdKey"]
                        if pk not in candidates or candidates[pk]["score"] < ratio:
                            candidates[pk] = {**entry, "score": round(ratio, 3), "match_type": "PARTIAL"}

    # 3) 유사도 매칭 (인덱스에서 못 찾은 경우)
    if not candidates:
        for q in query_variants:
            for token, entries in product_search_index.items():
                ratio = SequenceMatcher(None, q, token).ratio()
                if ratio >= 0.6:
                    for entry in entries:
                        pk = entry["ProdKey"]
                        if pk not in candidates or candidates[pk]["score"] < ratio:
                            candidates[pk] = {**entry, "score": round(ratio, 3), "match_type": "FUZZY"}

    # flower_hint 부스트 + 필터
    if flower_hint and candidates:
        for pk, c in candidates.items():
            if c["FlowerName"] == flower_hint:
                c["flower_bonus"] = 0.5  # 꽃 분류 일치 보너스
            else:
                c["flower_bonus"] = 0.0
        # 완전 필터는 하지 않고, 보너스로 우선순위 조정
        filtered = {pk: c for pk, c in candidates.items() if c["FlowerName"] == flower_hint}
        if filtered:
            candidates = filtered

    # Mix Box 페널티: 단품 우선
    for pk, c in candidates.items():
        pname = c.get("ProdName", "")
        if "Mix Box" in pname or "MIx Box" in pname or "MIX" in pname.upper().split():
            c["mix_penalty"] = -0.3
        else:
            c["mix_penalty"] = 0.0

    # 정렬: score + flower_bonus + mix_penalty
    def sort_key(c):
        return -(c["score"] + c.get("flower_bonus", 0) + c.get("mix_penalty", 0))
    sorted_matches = sorted(candidates.values(), key=sort_key)[:10]
    result["matches"] = sorted_matches

    if sorted_matches:
        result["best_match"] = sorted_matches[0]
        result["match_type"] = sorted_matches[0]["match_type"]

    return result


def find_customer_match(query):
    """거래처 매칭"""
    query = query.strip()
    result = {
        "query": query,
        "matches": [],
        "best_match": None,
        "match_type": "NONE"
    }

    # 1) 완전 일치
    if query in custname_index:
        entries = custname_index[query]
        result["matches"] = entries
        result["best_match"] = entries[0]
        result["match_type"] = "EXACT"
        return result

    # 2) 포함 매칭
    contains = []
    for name, entries in custname_index.items():
        if query in name:
            score = len(query) / len(name)
            for e in entries:
                contains.append({**e, "score": round(score, 3), "match_type": "CONTAINS"})
        elif name in query:
            score = len(name) / len(query)
            for e in entries:
                contains.append({**e, "score": round(score, 3), "match_type": "CONTAINS"})

    contains.sort(key=lambda x: -x["score"])
    if contains:
        result["matches"] = contains[:5]
        result["best_match"] = contains[0]
        result["match_type"] = "CONTAINS"
        return result

    # 3) 유사도
    fuzzy = []
    for name, entries in custname_index.items():
        clean_name = re.sub(r'[\(\)\（\）\[\]\（\）(주)(주식회사)]', '', name).strip()
        ratio = SequenceMatcher(None, query, clean_name).ratio()
        if ratio >= 0.4:
            for e in entries:
                fuzzy.append({**e, "score": round(ratio, 3), "match_type": "FUZZY"})

    fuzzy.sort(key=lambda x: -x["score"])
    if fuzzy:
        result["matches"] = fuzzy[:5]
        result["best_match"] = fuzzy[0]
        result["match_type"] = "FUZZY"

    return result


# ─── 카톡 품목 대분류 매칭 ───
kakao_products = reclass.get("products", {})
product_match_results = {}

print("\n━━━ 카톡 품목(대분류) -> DB FlowerName 매칭 ━━━")
for name, count in kakao_products.items():
    matched_fn = None
    match_type = "NONE"

    # 완전 일치
    if name in flower_groups:
        matched_fn = name
        match_type = "EXACT"
    else:
        # 부분 일치
        for fn in flower_groups:
            if name in fn or fn in name:
                matched_fn = fn
                match_type = "PARTIAL"
                break
        # 음역 사전 체크
        if not matched_fn:
            name_lower = name.lower() if name.isascii() else name
            if name_lower in KO_EN_TRANSLITERATION:
                en = KO_EN_TRANSLITERATION[name_lower]
                for fn in flower_groups:
                    if en in fn.lower():
                        matched_fn = fn
                        match_type = "TRANSLITERATION"
                        break
        # 유사도
        if not matched_fn:
            best_fn, best_score = None, 0
            for fn in flower_groups:
                ratio = SequenceMatcher(None, name, fn).ratio()
                if ratio > best_score:
                    best_fn, best_score = fn, ratio
            if best_score >= 0.5:
                matched_fn = best_fn
                match_type = "FUZZY"

    active_count = len([i for i in flower_groups.get(matched_fn, []) if not i["isDeleted"]]) if matched_fn else 0
    symbol = {"EXACT": "[O]", "PARTIAL": "[~]", "FUZZY": "[?]", "TRANSLITERATION": "[T]", "NONE": "[X]"}[match_type]
    print(f"  {symbol} {name} (카톡 {count}건) -> '{matched_fn or '?'}' ({match_type}, {active_count}품종)")

    product_match_results[name] = {
        "kakao_count": count,
        "match_type": match_type,
        "db_flower_name": matched_fn,
        "db_variety_count": active_count
    }


# ─── 카톡 품종 매칭 ───
kakao_varieties = reclass.get("varieties", {})
variety_match_results = {}

print("\n━━━ 카톡 품종 -> DB ProdName 매칭 ━━━")
for variety_key, count in kakao_varieties.items():
    parts = variety_key.split("/")
    flower_hint = parts[0] if len(parts) > 1 else None
    variety_name = parts[-1]

    match = find_product_match_v2(variety_name, flower_hint)

    symbol = {"EXACT": "[O]", "PARTIAL": "[~]", "FUZZY": "[?]", "NONE": "[X]"}[match["match_type"]]
    best = match["best_match"]
    if best:
        best_info = f"ProdKey={best['ProdKey']}, '{best['ProdName']}' ({best['FlowerName']}/{best['CounName']}) score={best.get('score', 'N/A')}"
    else:
        best_info = "매칭 없음"

    print(f"  {symbol} {variety_key} (카톡 {count}건) -> {best_info}")

    variety_match_results[variety_key] = {
        "kakao_count": count,
        "variety_name": variety_name,
        "flower_hint": flower_hint,
        "match_type": match["match_type"],
        "best_match": {
            "ProdKey": best["ProdKey"],
            "ProdName": best["ProdName"],
            "FlowerName": best["FlowerName"],
            "CounName": best["CounName"],
            "score": best.get("score", 0)
        } if best else None,
        "all_matches_count": len(match["matches"]),
        "top3_matches": [
            {"ProdKey": m["ProdKey"], "ProdName": m["ProdName"], "FlowerName": m["FlowerName"], "score": m.get("score", 0)}
            for m in match["matches"][:3]
        ]
    }


# ─── 카톡 거래처 매칭 ───
kakao_suppliers = reclass.get("suppliers", {})
supplier_match_results = {}

print("\n━━━ 카톡 거래처 -> DB CustName 매칭 ━━━")
for name, count in kakao_suppliers.items():
    match = find_customer_match(name)

    symbol = {"EXACT": "[O]", "CONTAINS": "[C]", "FUZZY": "[?]", "NONE": "[X]"}[match["match_type"]]
    best = match["best_match"]
    if best:
        best_info = f"CustKey={best['CustKey']}, '{best['CustName']}' ({best.get('Group1', '')})"
    else:
        best_info = "매칭 없음"

    print(f"  {symbol} {name} (카톡 {count}건) -> {best_info}")

    supplier_match_results[name] = {
        "kakao_count": count,
        "match_type": match["match_type"],
        "best_match": {
            "CustKey": best["CustKey"],
            "CustName": best["CustName"],
            "Group1": best.get("Group1", "")
        } if best else None,
        "all_matches": [
            {"CustKey": m["CustKey"], "CustName": m["CustName"], "Group1": m.get("Group1", ""), "score": m.get("score", 0)}
            for m in match["matches"][:5]
        ]
    }


# ─── 매칭율 ───
def calc_rate(results, high_confidence_types=("EXACT", "CONTAINS")):
    total = len(results)
    matched = sum(1 for r in results.values() if r["match_type"] != "NONE")
    high_conf = sum(1 for r in results.values() if r["match_type"] in high_confidence_types)
    return {
        "total": total,
        "matched": matched,
        "high_confidence": high_conf,
        "low_confidence": matched - high_conf,
        "unmatched": total - matched,
        "match_rate_pct": round(matched / total * 100, 1) if total else 0,
        "high_confidence_pct": round(high_conf / total * 100, 1) if total else 0
    }

product_rate = calc_rate(product_match_results, ("EXACT",))
variety_rate = calc_rate(variety_match_results, ("EXACT",))
supplier_rate = calc_rate(supplier_match_results, ("EXACT", "CONTAINS"))

print("\n━━━ 매칭율 요약 ━━━")
print(f"  품목(대분류) {product_rate['total']}개: 전체 {product_rate['match_rate_pct']}% | 고신뢰 {product_rate['high_confidence_pct']}%")
print(f"  품종(세부)   {variety_rate['total']}개: 전체 {variety_rate['match_rate_pct']}% | 고신뢰 {variety_rate['high_confidence_pct']}%")
print(f"  거래처       {supplier_rate['total']}개: 전체 {supplier_rate['match_rate_pct']}% | 고신뢰 {supplier_rate['high_confidence_pct']}%")


# ─── 미매칭 분석 ───
unmatched_analysis = {"products": {}, "varieties": {}, "suppliers": {}}

for name, r in product_match_results.items():
    if r["match_type"] == "NONE":
        unmatched_analysis["products"][name] = {
            "reason": "DB FlowerName에 해당 대분류 없음",
            "suggestion": f"FlowerName '{name}'을 DB에 추가하거나 별칭 매핑 필요"
        }

for key, r in variety_match_results.items():
    if r["match_type"] == "NONE":
        unmatched_analysis["varieties"][key] = {
            "reason": "DB ProdName에서 한글명/영문명/음역 모두 매칭 실패",
            "suggestion": f"'{r['variety_name']}' 품종을 DB에 등록하거나 별칭 테이블에 추가",
            "top_fuzzy": r.get("top3_matches", [])
        }

for name, r in supplier_match_results.items():
    if r["match_type"] == "NONE":
        unmatched_analysis["suppliers"][name] = {
            "reason": "DB CustName에 해당 거래처명 없음",
            "suggestion": f"'{name}' 거래처를 DB에 등록하거나 별칭 매핑 필요"
        }

# 낮은 신뢰도 매칭 경고
low_confidence = {"varieties": {}, "suppliers": {}}
for key, r in variety_match_results.items():
    best = r.get("best_match")
    if not best:
        continue
    needs_review = False
    warnings = []

    # 1) 낮은 유사도 점수
    if r["match_type"] in ("PARTIAL", "FUZZY") and best["score"] < 0.8:
        needs_review = True
        warnings.append(f"매칭 신뢰도 낮음 (score={best['score']})")

    # 2) FlowerName 불일치 (카톡 flower_hint와 DB FlowerName이 다름)
    if r.get("flower_hint") and best.get("FlowerName") and r["flower_hint"] != best["FlowerName"]:
        needs_review = True
        warnings.append(f"꽃분류 불일치: 카톡='{r['flower_hint']}' vs DB='{best['FlowerName']}' -- DB에 해당 품종 미등록 가능성")

    if needs_review:
        low_confidence["varieties"][key] = {
            "warnings": warnings,
            "matched_to": best["ProdName"],
            "matched_flower": best.get("FlowerName", ""),
            "expected_flower": r.get("flower_hint", ""),
            "needs_review": True,
            "action": "DB에 해당 품종 신규 등록 필요" if r.get("flower_hint") and r["flower_hint"] != best.get("FlowerName") else "수동 매칭 확인 필요"
        }
for name, r in supplier_match_results.items():
    if r["match_type"] == "FUZZY":
        low_confidence["suppliers"][name] = {
            "warning": "유사도 기반 매칭 - 수동 확인 필요",
            "matched_to": r["best_match"]["CustName"] if r["best_match"] else None,
            "needs_review": True
        }


# ─── FlowerName별 품종 요약 ───
flower_summary = {}
for fn, items in sorted(flower_groups.items(), key=lambda x: -len(x[1])):
    active = [i for i in items if not i["isDeleted"]]
    if not active:
        continue
    countries = list(set(i["CounName"] for i in active if i["CounName"]))
    flower_summary[fn] = {
        "total_count": len(items),
        "active_count": len(active),
        "countries": countries,
        "sample_varieties": [i["ProdName"] for i in active[:15]]
    }


# ─── 별칭 테이블 (수동 확인용 제안) ───
alias_suggestions = []
for key, r in variety_match_results.items():
    if r["match_type"] in ("EXACT", "PARTIAL") and r["best_match"]:
        alias_suggestions.append({
            "kakao_term": r["variety_name"],
            "kakao_full": key,
            "db_prodkey": r["best_match"]["ProdKey"],
            "db_prodname": r["best_match"]["ProdName"],
            "match_type": r["match_type"],
            "confidence": r["best_match"]["score"],
            "auto_approve": r["best_match"]["score"] >= 0.8
        })


# ─── 결과 조립 ───
output = {
    "_meta": {
        "description": "카카오톡 자연어 품명 <-> nenovaweb DB 매칭 테이블 v2",
        "generated": "2026-04-11",
        "engine_version": "2.0",
        "db_products_total": len(products_db),
        "db_products_active": len(active_products),
        "db_customers_total": len(customers_db),
        "db_customers_active": len(active_customers),
        "flower_types": len(flower_groups),
        "search_index_tokens": len(product_search_index)
    },
    "matching_rates": {
        "products_major": product_rate,
        "varieties_detail": variety_rate,
        "suppliers": supplier_rate
    },
    "flower_db_summary": flower_summary,
    "product_matches": product_match_results,
    "variety_matches": variety_match_results,
    "supplier_matches": supplier_match_results,
    "unmatched_analysis": unmatched_analysis,
    "low_confidence_warnings": low_confidence,
    "alias_suggestions": alias_suggestions,
    "transliteration_dict": EN_KO_TRANSLITERATION,
    "matching_rules": {
        "priority_order": [
            "1. 검색 인덱스 완전 일치 (ProdName 파싱 토큰 == 카톡 품명)",
            "2. 영문<->한글 음역 사전 변환 후 재매칭",
            "3. 인덱스 부분 일치 (포함 관계, score >= 0.3)",
            "4. SequenceMatcher 유사도 >= 0.6",
            "5. FlowerName 힌트로 동명이인 필터링"
        ],
        "index_extraction": [
            "ProdName 원본",
            "괄호 안 내용: (Moonlight) -> Moonlight, (연핑크) -> 연핑크",
            "/ 구분자 분리: 'Carnation CHINA / 문라이트' -> '문라이트'",
            "한글만 추출, 영문만 추출",
            "국가명(CHINA, COLOMBIA 등) 제거",
            "cm 크기 제거"
        ]
    }
}

save_json("matching_table.json", output)

# 최종 요약
print(f"\n{'='*60}")
print(f"결과 저장: {os.path.join(DATA_DIR, 'matching_table.json')}")
print(f"{'='*60}")
print(f"  품목 대분류 {product_rate['total']}개: {product_rate['match_rate_pct']}% 매칭")
print(f"  품종 세부   {variety_rate['total']}개: {variety_rate['match_rate_pct']}% 매칭 (고신뢰 {variety_rate['high_confidence_pct']}%)")
print(f"  거래처      {supplier_rate['total']}개: {supplier_rate['match_rate_pct']}% 매칭 (고신뢰 {supplier_rate['high_confidence_pct']}%)")
print(f"  미매칭 품목: {list(unmatched_analysis['products'].keys())}")
print(f"  미매칭 품종: {list(unmatched_analysis['varieties'].keys())}")
print(f"  미매칭 거래처: {list(unmatched_analysis['suppliers'].keys())}")
print(f"  낮은 신뢰도 거래처: {list(low_confidence['suppliers'].keys())}")
print(f"  별칭 제안: {len(alias_suggestions)}건")
