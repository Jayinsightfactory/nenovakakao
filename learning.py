"""
학습 엔진 v2 — 로컬 regex 분석 + Gemini AI 심층 분석

다른 터미널에서 실행:
  python learning.py              # 1회 분석 (로컬 + AI)
  python learning.py local        # 로컬 regex만 (빠르게)
  python learning.py ai           # AI 분석만 (Gemini)
  python learning.py watch        # 지속 감시 (새 데이터 올 때마다 분석)
  python learning.py watch --ai   # 지속 감시 + AI 분석 포함

분석 결과는 data/learning/ 에 저장.
dashboard.py 에서 실시간 확인 가능.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import traceback
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

# ── AI API ──────────────────────────────────────────
try:
    import google.generativeai as genai
    HAS_GENAI = True
except ImportError:
    HAS_GENAI = False

try:
    import anthropic
    HAS_ANTHROPIC = True
except ImportError:
    HAS_ANTHROPIC = False

# .env 로드
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

ROOT = Path(__file__).parent
DATA_DIR = ROOT / "data"
LEARNING_DIR = DATA_DIR / "learning"
COLLECTED_DATA = DATA_DIR / "collected_data.jsonl"

# 로컬 분석 출력
ANALYSIS_LOG = LEARNING_DIR / "analysis_log.jsonl"
PATTERNS_FILE = LEARNING_DIR / "patterns.json"
ROOM_PROFILES = LEARNING_DIR / "room_profiles.json"
MESSAGE_STATS = LEARNING_DIR / "message_stats.json"

# AI 분석 출력
ROOM_ANALYSIS_FILE = LEARNING_DIR / "room_analysis.json"
CROSS_ROOM_MAP_FILE = LEARNING_DIR / "cross_room_map.json"
AUTOMATION_FILE = LEARNING_DIR / "automation_opportunities.json"

KAKAO_SAVE_DIR = Path(os.environ.get("KAKAO_SAVE_DIR", "C:/Users/USER/Downloads/카톡대화데이터"))

# Gemini 설정
GEMINI_MODEL = "gemini-2.0-flash"
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
SAMPLE_MESSAGES = 200  # 방당 최대 메시지 수


def _ensure_dirs():
    LEARNING_DIR.mkdir(parents=True, exist_ok=True)


def _log(event: str, detail: dict):
    """분석 이력 로그"""
    _ensure_dirs()
    entry = {
        "timestamp": datetime.now().isoformat(),
        "event": event,
        **detail,
    }
    with open(ANALYSIS_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


# ══════════════════════════════════════════════════════════
#  1. 카톡 데이터 로드
# ══════════════════════════════════════════════════════════

def _load_rooms() -> list[dict]:
    """카톡 저장 txt 파일에서 방별 데이터 로드"""
    if not KAKAO_SAVE_DIR.exists():
        print(f"[WARN] 카톡 저장 폴더 없음: {KAKAO_SAVE_DIR}")
        return []

    rooms = []
    for txt in sorted(KAKAO_SAVE_DIR.glob("*.txt")):
        content = txt.read_text(encoding="utf-8", errors="ignore")
        if not content.strip():
            continue

        # 방 이름 추출 (첫 줄: "방이름 임과 카카오톡 대화")
        first_line = content.strip().splitlines()[0]
        room_name = txt.stem
        if "카카오톡 대화" in first_line:
            parts = first_line.split("카카오톡 대화")[0].strip()
            for suffix in ["님과", "임과", "과"]:
                if parts.endswith(suffix):
                    parts = parts[:-len(suffix)].strip()
                    break
            if parts:
                room_name = parts

        rooms.append({
            "room_name": room_name,
            "content": content,
            "file": str(txt),
            "file_size": txt.stat().st_size,
        })

    return rooms


# ══════════════════════════════════════════════════════════
#  2. 로컬 regex 분석 (기존 — 빠른 1차 패스)
# ══════════════════════════════════════════════════════════

MSG_PATTERN = re.compile(r"^\[(.+?)\]\s*\[(.+?)\]\s*(.+)$")

ORDER_KEYWORDS = ["주문", "발주", "추가", "변경", "취소", "수정", "확인"]
PRODUCT_KEYWORDS = ["카네이션", "장미", "백합", "국화", "튤립", "수국", "리시안", "거베라",
                    "프리지아", "안개", "소국", "스프레이", "폼폼", "스탠다드"]
UNIT_KEYWORDS = ["박스", "단", "송이", "속", "개"]
SEQUENCE_PATTERN = re.compile(r"(\d{1,3}[-/]?\d{0,2})\s*차")


def parse_message_line(line: str) -> dict | None:
    """카톡 메시지 한 줄 파싱"""
    m = MSG_PATTERN.match(line.strip())
    if not m:
        return None
    sender, time_str, content = m.groups()
    return {"sender": sender, "time": time_str, "content": content}


def classify_content(text: str) -> list[str]:
    """메시지 내용에서 유형 태그 추출"""
    tags = []
    if "[사진]" in text or "[Photo]" in text:
        tags.append("photo")
    if any(kw in text for kw in ["파일", ".xlsx", ".pdf", ".jpg"]):
        tags.append("file")
    if any(kw in text for kw in ORDER_KEYWORDS):
        tags.append("order")
    if any(kw in text for kw in PRODUCT_KEYWORDS):
        tags.append("product")
    if SEQUENCE_PATTERN.search(text):
        tags.append("sequence")
    if any(kw in text for kw in ["단가", "가격", "원", "비용"]):
        tags.append("pricing")
    if any(kw in text for kw in ["입고", "출고", "재고", "배송"]):
        tags.append("logistics")
    if not tags:
        tags.append("general")
    return tags


def analyze_room_local(room_name: str, content: str) -> dict:
    """방 하나의 로컬 regex 분석"""
    lines = content.strip().splitlines()
    messages = []
    tag_counter = Counter()
    sender_counter = Counter()
    product_mentions = Counter()
    sequence_mentions = []

    for line in lines:
        parsed = parse_message_line(line)
        if not parsed:
            continue
        tags = classify_content(parsed["content"])
        messages.append({**parsed, "tags": tags})
        for t in tags:
            tag_counter[t] += 1
        sender_counter[parsed["sender"]] += 1
        for kw in PRODUCT_KEYWORDS:
            if kw in parsed["content"]:
                product_mentions[kw] += 1
        seq_match = SEQUENCE_PATTERN.search(parsed["content"])
        if seq_match:
            sequence_mentions.append(seq_match.group(1))

    return {
        "room": room_name,
        "total_lines": len(lines),
        "parsed_messages": len(messages),
        "tags": dict(tag_counter),
        "senders": dict(sender_counter),
        "products": dict(product_mentions),
        "sequences": list(set(sequence_mentions)),
        "analyzed_at": datetime.now().isoformat(),
    }


def run_local_analysis() -> dict:
    """전체 로컬 분석 실행"""
    rooms = _load_rooms()
    if not rooms:
        _log("local_analysis", {"status": "no_data"})
        return {"status": "no_data", "rooms": {}}

    _log("local_analyze_start", {"rooms": len(rooms)})

    room_results = {}
    global_tags = Counter()
    global_products = Counter()
    global_senders = Counter()

    for room_data in rooms:
        room = room_data["room_name"]
        result = analyze_room_local(room, room_data["content"])
        room_results[room] = result

        for t, c in result["tags"].items():
            global_tags[t] += c
        for p, c in result["products"].items():
            global_products[p] += c
        for s, c in result["senders"].items():
            global_senders[s] += c

    # 저장
    _ensure_dirs()
    with open(ROOM_PROFILES, "w", encoding="utf-8") as f:
        json.dump(room_results, f, ensure_ascii=False, indent=2)

    stats = {
        "total_records": len(rooms),
        "rooms_analyzed": len(room_results),
        "global_tags": dict(global_tags.most_common(20)),
        "global_products": dict(global_products.most_common(20)),
        "top_senders": dict(global_senders.most_common(20)),
        "updated_at": datetime.now().isoformat(),
    }
    with open(MESSAGE_STATS, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)

    patterns = discover_patterns(room_results, global_tags, global_products)
    with open(PATTERNS_FILE, "w", encoding="utf-8") as f:
        json.dump(patterns, f, ensure_ascii=False, indent=2)

    _log("local_analyze_complete", {
        "rooms": len(room_results),
        "total_messages": sum(r["parsed_messages"] for r in room_results.values()),
        "patterns_found": len(patterns.get("rules", [])),
    })

    return stats


def discover_patterns(room_results: dict, tags: Counter, products: Counter) -> dict:
    """로컬 분석에서 패턴 발견"""
    rules = []
    for room, result in room_results.items():
        room_tags = result.get("tags", {})
        if not room_tags:
            continue
        dominant = max(room_tags, key=room_tags.get)
        rules.append({
            "type": "room_category",
            "room": room,
            "dominant_type": dominant,
            "confidence": room_tags[dominant] / max(sum(room_tags.values()), 1),
            "evidence": room_tags,
        })

    if products:
        rules.append({
            "type": "frequent_products",
            "products": dict(products.most_common(10)),
        })

    return {
        "version": 2,
        "discovered_at": datetime.now().isoformat(),
        "rules": rules,
        "note": "로컬 regex 기반 패턴. AI 분석은 room_analysis.json 참조.",
    }


# ══════════════════════════════════════════════════════════
#  3. Gemini AI 심층 분석
# ══════════════════════════════════════════════════════════

def _extract_sample_messages(content: str, n: int = SAMPLE_MESSAGES) -> str:
    """방 텍스트에서 마지막 n개 메시지를 추출"""
    lines = content.strip().splitlines()
    msg_lines = []
    for line in lines:
        if MSG_PATTERN.match(line.strip()):
            msg_lines.append(line.strip())

    # 마지막 n개
    sample = msg_lines[-n:] if len(msg_lines) > n else msg_lines
    return "\n".join(sample)


class _AIModel:
    """Gemini 또는 Claude API 래퍼 — 동일한 generate_content() 인터페이스 제공"""

    def __init__(self, backend: str, model):
        self.backend = backend
        self._model = model

    def generate_content(self, prompt: str) -> object:
        if self.backend == "gemini":
            return self._model.generate_content(prompt)
        elif self.backend == "claude":
            resp = self._model.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=4096,
                messages=[{"role": "user", "content": prompt}],
            )

            class _R:
                text = resp.content[0].text
            return _R()
        raise RuntimeError(f"Unknown backend: {self.backend}")

    def __repr__(self):
        return f"_AIModel(backend={self.backend})"


def _init_gemini():
    """AI 모델 초기화 — Gemini 우선, 실패 시 Claude 폴백"""
    # 1. Gemini 시도
    if HAS_GENAI and GEMINI_API_KEY:
        try:
            genai.configure(api_key=GEMINI_API_KEY)
            model = genai.GenerativeModel(GEMINI_MODEL)
            # 빠른 테스트
            model.generate_content("test")
            print("  [AI] Gemini 연결 OK")
            return _AIModel("gemini", model)
        except Exception as e:
            print(f"  [AI] Gemini 실패: {str(e)[:80]}")

    # 2. Claude 폴백
    if HAS_ANTHROPIC:
        try:
            client = anthropic.Anthropic()
            print("  [AI] Claude API 폴백 사용")
            return _AIModel("claude", client)
        except Exception as e:
            print(f"  [AI] Claude 실패: {e}")

    raise RuntimeError("사용 가능한 AI API 없음 (Gemini/Claude 모두 실패)")


ROOM_ANALYSIS_PROMPT = """당신은 화훼 무역회사(네노바)의 카카오톡 업무 채팅방 분석 전문가입니다.

아래는 "{room_name}" 채팅방의 최근 메시지 샘플입니다.

### 메시지 샘플 (최근 {msg_count}개):
{messages}

### 이 방에 대한 로컬 regex 분석 결과:
- 총 메시지: {total_messages}개
- 태그 분포: {tags}
- 주요 발신자: {senders}
- 언급 품목: {products}

### 분석 요청:
다음 항목에 대해 한국어로 상세히 분석해주세요. 반드시 아래 JSON 형식으로 응답하세요.

```json
{{
  "방_목적": "이 방의 주요 목적과 역할 (2-3문장)",
  "업무_이슈": ["이 방에서 다뤄지는 주요 업무 이슈 목록"],
  "트리거_이벤트": [
    {{
      "트리거": "이벤트 설명",
      "빈도": "높음/보통/낮음",
      "예시": "실제 메시지 예시"
    }}
  ],
  "의사결정": ["이 방에서 이루어지는 주요 의사결정/판단 유형"],
  "자동화_기회": [
    {{
      "작업": "자동화 가능한 작업",
      "우선순위": "상/중/하",
      "방법": "자동화 구현 방법",
      "예상_효과": "시간 절약 또는 오류 감소 효과"
    }}
  ],
  "참조_방": ["이 방에서 언급되는 다른 방이나 외부 시스템"],
  "메시지_패턴": [
    {{
      "패턴_이름": "패턴 설명",
      "정규식_제안": "데이터 추출용 정규식 (선택)",
      "예시": "해당 패턴의 메시지 예시",
      "추출_가능_데이터": ["추출 가능한 필드 목록"]
    }}
  ],
  "방_중요도": "상/중/하",
  "요약": "이 방의 전체 특성을 3줄로 요약"
}}
```

JSON만 출력하고 다른 텍스트는 포함하지 마세요."""


CROSS_ROOM_PROMPT = """당신은 화훼 무역회사(네노바)의 카카오톡 업무 채팅방 분석 전문가입니다.

아래는 분석된 모든 채팅방의 요약입니다:

{room_summaries}

### 분석 요청:
모든 방 간의 관계와 업무 흐름을 분석해주세요. 반드시 아래 JSON 형식으로 응답하세요.

```json
{{
  "업무_흐름": [
    {{
      "흐름_이름": "흐름 설명 (예: 주문 접수→재고확인→출고)",
      "관련_방": ["방1", "방2"],
      "설명": "상세 설명"
    }}
  ],
  "방_관계_맵": [
    {{
      "출발_방": "방 이름",
      "도착_방": "방 이름",
      "관계": "정보 전달/승인 요청/지시 등",
      "빈도": "높음/보통/낮음"
    }}
  ],
  "핵심_허브_방": [
    {{
      "방": "방 이름",
      "역할": "허브 역할 설명",
      "연결_방_수": 3
    }}
  ],
  "병목_지점": ["업무 처리 병목이 되는 방이나 프로세스"],
  "자동화_우선순위": [
    {{
      "순위": 1,
      "작업": "작업 설명",
      "관련_방": ["방1"],
      "예상_효과": "효과 설명",
      "구현_난이도": "상/중/하"
    }}
  ],
  "전체_요약": "전체 채팅방 구조와 업무 흐름을 5줄로 요약"
}}
```

JSON만 출력하고 다른 텍스트는 포함하지 마세요."""


def _parse_gemini_json(text: str) -> dict:
    """Gemini 응답에서 JSON 추출"""
    # ```json ... ``` 블록 추출 시도
    json_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if json_match:
        text = json_match.group(1)

    # 직접 JSON 파싱
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # 앞뒤 비JSON 텍스트 제거 시도
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end])
            except json.JSONDecodeError:
                pass
    return {"error": "JSON 파싱 실패", "raw_response": text[:500]}


def analyze_room_ai(model, room_name: str, room_data: dict, local_result: dict) -> dict:
    """Gemini로 방 하나 심층 분석"""
    sample = _extract_sample_messages(room_data["content"])
    msg_count = len(sample.strip().splitlines())

    if msg_count < 5:
        return {"방_목적": "메시지 부족 (5개 미만)", "건너뜀": True}

    # 로컬 결과 요약
    top_senders = dict(sorted(local_result.get("senders", {}).items(),
                              key=lambda x: -x[1])[:5])
    top_products = dict(sorted(local_result.get("products", {}).items(),
                               key=lambda x: -x[1])[:5])

    prompt = ROOM_ANALYSIS_PROMPT.format(
        room_name=room_name,
        msg_count=msg_count,
        messages=sample,
        total_messages=local_result.get("parsed_messages", 0),
        tags=json.dumps(local_result.get("tags", {}), ensure_ascii=False),
        senders=json.dumps(top_senders, ensure_ascii=False),
        products=json.dumps(top_products, ensure_ascii=False),
    )

    try:
        response = model.generate_content(prompt)
        result = _parse_gemini_json(response.text)
        result["_ai_analyzed_at"] = datetime.now().isoformat()
        result["_sample_size"] = msg_count
        return result
    except Exception as e:
        error_msg = str(e)
        print(f"  [ERROR] {room_name}: {error_msg[:100]}")
        _log("ai_room_error", {"room": room_name, "error": error_msg[:200]})

        # Rate limit → 대기 후 재시도
        if "429" in error_msg or "quota" in error_msg.lower() or "rate" in error_msg.lower():
            print(f"  [RATE LIMIT] 60초 대기 후 재시도...")
            time.sleep(60)
            try:
                response = model.generate_content(prompt)
                result = _parse_gemini_json(response.text)
                result["_ai_analyzed_at"] = datetime.now().isoformat()
                result["_sample_size"] = msg_count
                result["_retried"] = True
                return result
            except Exception as e2:
                return {"error": str(e2)[:200], "건너뜀": True}

        return {"error": error_msg[:200], "건너뜀": True}


def analyze_cross_room(model, room_analyses: dict) -> dict:
    """Gemini로 방 간 관계 분석"""
    # 방별 요약 생성
    summaries = []
    for room_name, analysis in room_analyses.items():
        if analysis.get("건너뜀"):
            continue
        summary = f"- **{room_name}**: {analysis.get('방_목적', '알 수 없음')}"
        triggers = analysis.get("트리거_이벤트", [])
        if triggers:
            trigger_strs = [t.get("트리거", "") for t in triggers[:3] if isinstance(t, dict)]
            summary += f" | 트리거: {', '.join(trigger_strs)}"
        refs = analysis.get("참조_방", [])
        if refs:
            summary += f" | 참조: {', '.join(refs[:3])}"
        summaries.append(summary)

    if len(summaries) < 2:
        return {"전체_요약": "분석 가능한 방이 2개 미만"}

    prompt = CROSS_ROOM_PROMPT.format(room_summaries="\n".join(summaries))

    try:
        response = model.generate_content(prompt)
        result = _parse_gemini_json(response.text)
        result["_analyzed_at"] = datetime.now().isoformat()
        result["_room_count"] = len(summaries)
        return result
    except Exception as e:
        error_msg = str(e)
        print(f"  [ERROR] 방간 관계 분석: {error_msg[:100]}")
        _log("ai_cross_room_error", {"error": error_msg[:200]})
        return {"error": error_msg[:200]}


def _extract_automation_opportunities(room_analyses: dict, cross_room: dict) -> dict:
    """모든 분석에서 자동화 기회를 통합 추출"""
    opportunities = []
    seen = set()

    # 방별 자동화 기회
    for room_name, analysis in room_analyses.items():
        for opp in analysis.get("자동화_기회", []):
            if not isinstance(opp, dict):
                continue
            key = opp.get("작업", "")
            if key and key not in seen:
                seen.add(key)
                opportunities.append({
                    **opp,
                    "출처_방": room_name,
                })

    # 방간 분석의 자동화 우선순위
    for item in cross_room.get("자동화_우선순위", []):
        if not isinstance(item, dict):
            continue
        key = item.get("작업", "")
        if key and key not in seen:
            seen.add(key)
            opportunities.append({
                **item,
                "출처": "방간_분석",
            })

    # 우선순위 정렬
    priority_order = {"상": 0, "중": 1, "하": 2}
    opportunities.sort(key=lambda x: priority_order.get(x.get("우선순위", "하"), 2))

    return {
        "총_기회": len(opportunities),
        "기회_목록": opportunities,
        "분석_시각": datetime.now().isoformat(),
        "요약": f"총 {len(opportunities)}개 자동화 기회 발견. "
                f"상: {sum(1 for o in opportunities if o.get('우선순위')=='상')}개, "
                f"중: {sum(1 for o in opportunities if o.get('우선순위')=='중')}개, "
                f"하: {sum(1 for o in opportunities if o.get('우선순위')=='하')}개",
    }


def run_ai_analysis() -> dict:
    """Gemini AI 심층 분석 실행"""
    print("[AI] Gemini 심층 분석 시작...")

    # 로컬 분석 결과 먼저 확인/실행
    if not ROOM_PROFILES.exists():
        print("[AI] 로컬 분석 결과 없음 — 먼저 로컬 분석 실행")
        run_local_analysis()

    local_profiles = {}
    if ROOM_PROFILES.exists():
        local_profiles = json.loads(ROOM_PROFILES.read_text(encoding="utf-8"))

    # 방 데이터 로드
    rooms = _load_rooms()
    if not rooms:
        print("[AI] 분석할 방 데이터 없음")
        return {"status": "no_data"}

    room_map = {r["room_name"]: r for r in rooms}

    # Gemini 초기화
    try:
        model = _init_gemini()
    except RuntimeError as e:
        print(f"[AI] Gemini 초기화 실패: {e}")
        _log("ai_init_error", {"error": str(e)})
        return {"status": "error", "error": str(e)}

    _log("ai_analyze_start", {"rooms": len(rooms)})

    # ── 방별 분석 ──
    room_analyses = {}
    total = len(rooms)
    for i, room_data in enumerate(rooms, 1):
        room_name = room_data["room_name"]
        local_result = local_profiles.get(room_name, {})

        print(f"  [{i}/{total}] {room_name} ...", end=" ", flush=True)
        result = analyze_room_ai(model, room_name, room_data, local_result)

        if result.get("건너뜀"):
            print(f"건너뜀 ({result.get('error', result.get('방_목적', '?'))})")
        else:
            print("완료")

        room_analyses[room_name] = result

        # API rate limit 방지: 무료 티어 분당 5회 → 15초 간격
        if i < total:
            print("  [WAIT] 15초 대기 (API rate limit)...")
            time.sleep(15)

    # 저장: room_analysis.json
    _ensure_dirs()
    with open(ROOM_ANALYSIS_FILE, "w", encoding="utf-8") as f:
        json.dump(room_analyses, f, ensure_ascii=False, indent=2)
    print(f"[AI] 방별 분석 저장 → {ROOM_ANALYSIS_FILE}")

    # ── 방간 관계 분석 ──
    print("[AI] 방간 관계 분석 중...")
    cross_room = analyze_cross_room(model, room_analyses)
    with open(CROSS_ROOM_MAP_FILE, "w", encoding="utf-8") as f:
        json.dump(cross_room, f, ensure_ascii=False, indent=2)
    print(f"[AI] 방간 관계 저장 → {CROSS_ROOM_MAP_FILE}")

    # ── 자동화 기회 통합 ──
    automation = _extract_automation_opportunities(room_analyses, cross_room)
    with open(AUTOMATION_FILE, "w", encoding="utf-8") as f:
        json.dump(automation, f, ensure_ascii=False, indent=2)
    print(f"[AI] 자동화 기회 저장 → {AUTOMATION_FILE}")

    analyzed_count = sum(1 for v in room_analyses.values() if not v.get("건너뜀"))
    _log("ai_analyze_complete", {
        "rooms_analyzed": analyzed_count,
        "rooms_skipped": len(room_analyses) - analyzed_count,
        "automation_opportunities": automation["총_기회"],
        "cross_room_flows": len(cross_room.get("업무_흐름", [])),
    })

    print(f"\n[AI] 분석 완료: {analyzed_count}/{total}개 방, "
          f"자동화 기회 {automation['총_기회']}개")

    return {
        "status": "complete",
        "rooms_analyzed": analyzed_count,
        "automation_opportunities": automation["총_기회"],
    }


# ══════════════════════════════════════════════════════════
#  4. 전체 분석 (로컬 + AI)
# ══════════════════════════════════════════════════════════

def analyze_all(include_ai: bool = True) -> dict:
    """전체 분석: 로컬 regex + Gemini AI"""
    print("=" * 50)
    print("[LEARNING] 전체 분석 시작")
    print("=" * 50)

    # 1) 로컬 분석 (항상 실행)
    print("\n[1/2] 로컬 regex 분석...")
    local_stats = run_local_analysis()
    print(f"  방: {local_stats.get('rooms_analyzed', 0)}개")
    print(f"  태그: {local_stats.get('global_tags', {})}")

    # 2) AI 분석 (선택)
    ai_result = {"status": "skipped"}
    if include_ai:
        print(f"\n[2/2] Gemini AI 심층 분석...")
        ai_result = run_ai_analysis()
    else:
        print(f"\n[2/2] AI 분석 건너뜀 (--ai 옵션 없음)")

    return {
        "local": local_stats,
        "ai": ai_result,
    }


# ══════════════════════════════════════════════════════════
#  5. Watch 모드
# ══════════════════════════════════════════════════════════

def watch_mode(interval: int = 30, include_ai: bool = False):
    """지속 감시 모드"""
    print(f"[LEARNING] watch mode (interval: {interval}s, AI: {'ON' if include_ai else 'OFF'})")
    print(f"[LEARNING] source: {KAKAO_SAVE_DIR}")
    print(f"[LEARNING] output: {LEARNING_DIR}/")
    print()

    last_sizes = {}
    ai_run_count = 0

    while True:
        try:
            # 파일 크기 변화 감지
            current_sizes = {}
            if KAKAO_SAVE_DIR.exists():
                for txt in KAKAO_SAVE_DIR.glob("*.txt"):
                    current_sizes[str(txt)] = txt.stat().st_size

            if current_sizes != last_sizes:
                changed = set(current_sizes.keys()) - set(last_sizes.keys())
                modified = {k for k in current_sizes if k in last_sizes
                           and current_sizes[k] != last_sizes[k]}

                if changed or modified:
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] "
                          f"변경 감지: 새로운 {len(changed)}개, 수정 {len(modified)}개")

                    # 로컬 분석은 항상
                    stats = run_local_analysis()
                    print(f"  로컬 분석 완료: 방 {stats.get('rooms_analyzed', 0)}개")

                    # AI 분석: 10번째 변경마다 또는 include_ai일 때
                    ai_run_count += 1
                    if include_ai and ai_run_count % 10 == 1:  # 첫 번째 + 10번마다
                        print(f"  AI 분석 트리거 (매 10회)")
                        run_ai_analysis()

                    print()

                last_sizes = current_sizes
            else:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] 대기중...", end="\r")

            time.sleep(interval)

        except KeyboardInterrupt:
            print("\n[LEARNING] 중지됨.")
            break
        except Exception as e:
            print(f"\n[ERROR] {e}")
            traceback.print_exc()
            time.sleep(interval)


# ══════════════════════════════════════════════════════════
#  6. CLI 진입점
# ══════════════════════════════════════════════════════════

if __name__ == "__main__":
    args = sys.argv[1:]

    if "watch" in args:
        include_ai = "--ai" in args
        watch_mode(include_ai=include_ai)
    elif "ai" in args:
        result = run_ai_analysis()
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif "local" in args:
        stats = run_local_analysis()
        print(json.dumps(stats, ensure_ascii=False, indent=2))
    else:
        # 기본: 로컬 + AI
        result = analyze_all(include_ai=True)
        print("\n" + json.dumps(result, ensure_ascii=False, indent=2))
