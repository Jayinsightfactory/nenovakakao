"""
대화 스레드 인텔리전스 (CTI) 엔진

이벤트로그에서 Q&A 매칭, 가격 추출, 이슈 체인, 지시-이행, 품목 지식, 인물 역할을 추출.
"""
from __future__ import annotations

import re
import hashlib
from collections import defaultdict, Counter


# ─── 패턴 ───

# 가격 패턴
KRW_RE = re.compile(r'[₩]?\s*(\d{1,3}[,.]?\d{3}(?:\.\d+)?)\s*원')
USD_RE = re.compile(r'[\$]?\s*(\d+\.?\d*)\s*(달러|덜라|USD)')
CNY_RE = re.compile(r'(\d+\.?\d*)\s*유안')
ARRIVAL_COST_RE = re.compile(r'도착\s*원가[^\d]*(\d{1,3}[,.]?\d{3}(?:\.\d+)?)')

# 질문 패턴
Q_PATTERNS = [
    r'했나요\??', r'있나요\??', r'없나요\??', r'될까요\??',
    r'확인\s*(부탁|해주|좀)', r'답변\s*없', r'가능\s*(여부|한지|할)',
    r'어떻게\s*되', r'알려\s*주', r'보내\s*주',
]
Q_RE = re.compile('|'.join(Q_PATTERNS))

# 답변 패턴
A_PATTERNS = [
    r'입니다$', r'합니다$', r'했습니다', r'드립니다',
    r'없습니다', r'됩니다', r'으로\s*내렸', r'으로\s*올렸',
]
A_RE = re.compile('|'.join(A_PATTERNS))

# 지시 패턴
INSTR_PATTERNS = [
    r'해주세요', r'해주십시오', r'부탁드립니다', r'진행해',
    r'독촉', r'받아주세요', r'확인\s*요망',
]
INSTR_RE = re.compile('|'.join(INSTR_PATTERNS))

# 이슈 키워드
ISSUE_KW = ['불량', '클레임', '차감', '파손', '총체', '사육', '지연', '딜레이']

# 원산지 약어
ORIGIN_MAP = {
    '콜': '콜롬비아', '네덜': '네덜란드', '중국': '중국',
    '태국': '태국', '에콰': '에콰도르', '호주': '호주',
}


def _thread_id(room, ts, sender):
    return hashlib.md5(f"{room}|{ts}|{sender}".encode()).hexdigest()[:10]


# ─── Q&A 스레드 매칭 ───

def extract_qa_threads(messages: list[dict]) -> list[dict]:
    """
    같은 방에서 질문→답변 패턴 매칭.
    messages: [{"ts", "room", "sender", "content"}, ...]
    """
    threads = []
    room_msgs = defaultdict(list)

    for m in messages:
        room_msgs[m["room"]].append(m)

    for room, msgs in room_msgs.items():
        pending_q = None

        for m in msgs:
            content = m["content"]
            sender = m["sender"]

            if Q_RE.search(content):
                pending_q = m
            elif pending_q and sender != pending_q["sender"]:
                # 답변 감지
                if A_RE.search(content) or any(kw in content for kw in ["원가", "원", "달러", "가능", "완료", "확인"]):
                    # 품목 추출
                    product = ""
                    for kw in ["장미", "카네이션", "수국", "카라", "루스커스", "레몬잎", "모카라", "튤립", "안개"]:
                        if kw in pending_q["content"] or kw in content:
                            product = kw
                            break

                    # 가격 추출
                    extracted = ""
                    krw = KRW_RE.search(content)
                    usd = USD_RE.search(content)
                    ac = ARRIVAL_COST_RE.search(content)
                    if ac:
                        extracted = f"도착원가 ₩{ac.group(1)}"
                    elif krw:
                        extracted = f"₩{krw.group(1)}"
                    elif usd:
                        extracted = f"${usd.group(1)}"

                    threads.append({
                        "thread_id": _thread_id(room, pending_q["ts"], pending_q["sender"]),
                        "ts": pending_q["ts"],
                        "room": room,
                        "q_sender": pending_q["sender"],
                        "q_content": pending_q["content"][:200],
                        "a_sender": sender,
                        "a_content": content[:200],
                        "extracted": extracted,
                        "category": "가격" if extracted else "업무",
                        "product": product,
                        "sequence": "",
                    })
                    pending_q = None

    return threads


# ─── 가격 히스토리 추출 ───

def extract_prices(messages: list[dict]) -> list[dict]:
    """모든 메시지에서 가격 정보 추출"""
    prices = []

    for m in messages:
        content = m["content"]

        # 도착원가
        ac = ARRIVAL_COST_RE.search(content)
        if ac:
            # 품목 추출
            product = ""
            for kw in ["장미", "카네이션", "수국", "카라", "루스커스", "모카라", "안개", "튤립", "레몬잎"]:
                if kw in content:
                    product = kw
                    break

            prices.append({
                "date": m["ts"],
                "product": product,
                "variety": "",
                "origin": "",
                "currency": "KRW",
                "price": ac.group(1),
                "krw": ac.group(1),
                "unit": "",
                "source": m["sender"],
                "context": content[:100],
                "room": m["room"],
            })
            continue

        # 달러+원화 동시
        usd = USD_RE.search(content)
        krw = KRW_RE.search(content)
        if usd or krw:
            product = ""
            for kw in ["장미", "카네이션", "수국", "카라", "루스커스", "모카라", "안개", "목걸이", "트릭"]:
                if kw in content:
                    product = kw
                    break

            # 단위
            unit = ""
            for u in ["단", "박스", "송이", "스팀", "Kg"]:
                if u in content:
                    unit = u
                    break

            if usd:
                prices.append({
                    "date": m["ts"], "product": product, "variety": "",
                    "origin": "", "currency": "USD",
                    "price": usd.group(1),
                    "krw": krw.group(1) if krw else "",
                    "unit": unit, "source": m["sender"],
                    "context": content[:100], "room": m["room"],
                })
            elif krw:
                # 원화만 (단가/가격 맥락일 때만)
                if any(kw in content for kw in ["원가", "단가", "가격", "견적", "유안"]):
                    prices.append({
                        "date": m["ts"], "product": product, "variety": "",
                        "origin": "", "currency": "KRW",
                        "price": krw.group(1), "krw": krw.group(1),
                        "unit": unit, "source": m["sender"],
                        "context": content[:100], "room": m["room"],
                    })

    return prices


# ─── 이슈 체인 추출 ───

def extract_issue_chains(messages: list[dict]) -> list[dict]:
    """불량 보고 → 전파 → 해결 체인 추출"""
    chains = []
    active_issues = {}  # room → issue

    for m in messages:
        content = m["content"]
        room = m["room"]
        sender = m["sender"]

        # 이슈 발생
        if any(kw in content for kw in ISSUE_KW):
            # 차수 추출
            seq_m = re.search(r'(\d{1,3})[-/]?(\d{0,2})\s*차?', content)
            seq = f"{seq_m.group(1)}-{seq_m.group(2)}" if seq_m and seq_m.group(2) else (seq_m.group(1) if seq_m else "")

            # 품목
            product = ""
            for kw in ["장미", "카네이션", "수국", "카라", "루스커스", "레몬잎", "모카라", "튤립", "안개", "알스트로", "아마릴리스"]:
                if kw in content:
                    product = kw
                    break

            # 거래처
            supplier = ""
            from core.gsheet_sync import _load_known_suppliers
            for s in _load_known_suppliers():
                if s in content:
                    supplier = s
                    break

            issue = {
                "chain_id": _thread_id(room, m["ts"], sender),
                "start_ts": m["ts"],
                "start_room": room,
                "reporter": sender,
                "issue_content": content[:150],
                "spread_room": "", "spreader": "", "spread_ts": "",
                "resolver": "", "resolve_content": "", "resolve_ts": "",
                "duration": "",
                "product": product,
                "sequence": seq,
                "supplier": supplier,
            }
            active_issues[room] = issue

        # 해결 감지 (같은 방, 다른 사람, 해결 키워드)
        elif room in active_issues:
            iss = active_issues[room]
            if sender != iss["reporter"]:
                if any(kw in content for kw in ["확인", "알겠", "처리", "완료", "진행", "네 확인"]):
                    iss["resolver"] = sender
                    iss["resolve_content"] = content[:150]
                    iss["resolve_ts"] = m["ts"]
                    chains.append(iss)
                    del active_issues[room]

    # 미해결 이슈도 추가
    for iss in active_issues.values():
        chains.append(iss)

    return chains


# ─── 지시-이행 추출 ───

def extract_instructions(messages: list[dict]) -> list[dict]:
    """관리자 지시 → 이행 추적"""
    instructions = []
    MANAGERS = {"임재용", "네노바이사님"}

    for m in messages:
        if m["sender"] not in MANAGERS:
            continue
        if INSTR_RE.search(m["content"]):
            instructions.append({
                "instr_id": _thread_id(m["room"], m["ts"], m["sender"]),
                "ts": m["ts"],
                "room": m["room"],
                "instructor": m["sender"],
                "content": m["content"][:200],
                "target": "",  # @멘션 추출 가능
                "deadline": "",
                "done": "",
                "done_ts": "",
                "done_content": "",
            })

    return instructions


# ─── 품목 지식 추출 ───

def extract_product_knowledge(messages: list[dict], prices: list[dict]) -> list[dict]:
    """대화에서 품목 관련 지식 축적"""
    knowledge = []

    # 가격 → 지식
    for p in prices:
        if p["product"] and p["price"]:
            knowledge.append({
                "product": p["product"],
                "attribute": f"도착원가({p['currency']})",
                "value": p["price"],
                "source": p["source"],
                "date": p["date"],
                "room": p["room"],
                "confidence": "0.9",
            })

    # MoQ, 패킹 등
    for m in messages:
        content = m["content"]
        if "최소" in content and ("발주" in content or "MoQ" in content.upper()):
            product = ""
            for kw in ["장미", "카네이션", "수국", "카라", "모카라"]:
                if kw in content:
                    product = kw
                    break
            if product:
                knowledge.append({
                    "product": product,
                    "attribute": "최소발주수량(MoQ)",
                    "value": content[:100],
                    "source": m["sender"],
                    "date": m["ts"],
                    "room": m["room"],
                    "confidence": "0.7",
                })

    return knowledge


# ─── 인물 프로필 생성 ───

def build_person_profiles(messages: list[dict], threads: list[dict]) -> list[dict]:
    """직원별 프로필 자동 생성"""
    from core.gsheet_sync import _load_pipeline_config

    config = _load_pipeline_config()
    personnel = config.get("key_personnel", {})

    # 메시지 통계
    stats = defaultdict(lambda: {
        "total": 0, "rooms": Counter(), "products": Counter(),
        "suppliers": Counter(), "qa_answers": 0,
    })

    for m in messages:
        s = stats[m["sender"]]
        s["total"] += 1
        s["rooms"][m["room"]] += 1

    for t in threads:
        stats[t["a_sender"]]["qa_answers"] += 1

    # 전산 ID 매핑
    USER_MAP = {
        "네노바 정재훈님": "nenovaSD1", "네노바박성수친구": "nenovaSD7",
        "네노바 변진형 과장님": "nenovaSD2", "네노바조현욱": "nenovaSD3",
        "네노바연주": "nenovaSS2", "임재용": "nenovaSS3",
        "강현우": "nenovaSS1", "네노바김원영차장님": "nenova1",
        "김원빈": "nenovaIC4", "아드리아나": "nenovaIC2",
        "Teresa": "nenovaIC3", "가브리엘": "nenovaIC1",
    }

    profiles = []
    for name, s in sorted(stats.items(), key=lambda x: -x[1]["total"]):
        if s["total"] < 10:
            continue
        info = personnel.get(name, {})
        top_rooms = ", ".join(r for r, _ in s["rooms"].most_common(3))
        profiles.append({
            "name": name,
            "erp_id": USER_MAP.get(name, ""),
            "role": info.get("role", ""),
            "specialty": "",
            "main_rooms": top_rooms,
            "total": str(s["total"]),
            "avg_response": "",
            "main_products": "",
            "main_suppliers": "",
            "notes": f"Q&A 답변 {s['qa_answers']}회" if s["qa_answers"] else "",
        })

    return profiles


# ─── 메인: 전체 CTI 실행 ───

def run_cti(log_data: list[list]) -> dict:
    """
    이벤트로그 전체에 CTI 적용.
    log_data: [[시각, 방이름, 파이프라인, 발신자, 원문, 메시지ID], ...]
    """
    # 메시지 정규화
    messages = []
    for row in log_data:
        if len(row) < 5:
            continue
        messages.append({
            "ts": row[0],
            "room": row[1],
            "sender": row[3],
            "content": row[4],
        })

    print(f"  CTI 입력: {len(messages):,}건")

    # 1. Q&A 스레드
    threads = extract_qa_threads(messages)
    print(f"  Q&A 스레드: {len(threads)}건")

    # 2. 가격 히스토리
    prices = extract_prices(messages)
    print(f"  가격 추출: {len(prices)}건")

    # 3. 이슈 체인
    chains = extract_issue_chains(messages)
    print(f"  이슈 체인: {len(chains)}건")

    # 4. 지시-이행
    instructions = extract_instructions(messages)
    print(f"  지시 추출: {len(instructions)}건")

    # 5. 품목 지식
    knowledge = extract_product_knowledge(messages, prices)
    print(f"  품목 지식: {len(knowledge)}건")

    # 6. 인물 프로필
    profiles = build_person_profiles(messages, threads)
    print(f"  인물 프로필: {len(profiles)}명")

    return {
        "threads": threads,
        "prices": prices,
        "chains": chains,
        "instructions": instructions,
        "knowledge": knowledge,
        "profiles": profiles,
    }
