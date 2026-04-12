# -*- coding: utf-8 -*-
"""
네노바 불량/클레임 이미지 자동 분석 시스템 v1.0

카카오톡 "네노바 수입(불량 공유방)"에서 수집된
사진 + 텍스트 설명을 통합 분석하여 구조화된 불량 보고서를 생성한다.

흐름:
  1. 텍스트 파싱  — 차수/품목/품종/거래처/수량/불량유형 추출
  2. 이미지 분석  — Gemini Vision으로 꽃 종류/불량 추정
  3. 교차 검증    — 텍스트 vs 이미지 품목 일치율 계산
  4. 배치 처리    — 여러 건 일괄 처리
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# 상수 / 품목 사전
# ──────────────────────────────────────────────

KNOWN_DEFECT_TYPES: list[str] = [
    "마름",
    "습",
    "패킹 불량",
    "겉잎제거 필요",
    "겉잎제거",
    "전량클레임",
    "규격 미달",
    "수량부족",
    "수량 부족",
    "꺾임",
    "곰팡이",
    "벌레",
    "냉해",
    "과숙",
    "변색",
    "탈색",
]

# 품목 → 품종 매핑 (실제 데이터 기반)
PRODUCT_VARIETIES: dict[str, list[str]] = {
    "카네이션": [
        "문라이트", "돈셀", "헤르메스", "노비아", "지오지아", "폴림니아",
        "로맨스", "콤피", "리자", "아이린", "크림", "연핑크", "연그린",
        "체리", "마리포사", "핑크", "레드", "화이트",
    ],
    "장미": [
        "프라우드", "캔들라이트", "만달라", "블랙잭", "핫샷",
        "에스페란스", "프리덤", "아발란체", "비비안", "탈레아",
        "풀하우스", "핑크벨", "화이트오하라", "피치",
        "레드", "옐로", "핑크", "화이트",
    ],
    "수국": ["화이트", "블루", "연핑크", "핑크", "그린", "라벤더"],
    "루스커스": ["이스라엘", "콜롬비아", "에콰도르"],
    "레몬잎": [],
    "모카라": [],
    "알스트로": [],
    "안개": [],
    "리시안": [],
    "거베라": [],
    "백합": [],
    "국화": [],
}

# 품목명 별칭(축약) → 정규 품목명
PRODUCT_ALIASES: dict[str, str] = {
    "카네": "카네이션",
    "장미": "장미",
    "수국": "수국",
    "루스": "루스커스",
    "레몬": "레몬잎",
    "알스": "알스트로",
    "리시": "리시안",
    "거베": "거베라",
}

# 원산지 키워드
ORIGINS: list[str] = [
    "콜롬비아", "에콰도르", "케냐", "에티오피아", "이스라엘",
    "중국", "베트남", "말레이시아", "네덜란드", "인도",
    "콜", "에콰", "케냐",
]

# 차수 패턴
RE_SEQUENCE = re.compile(r"(\d{1,2})[-/](\d{1,2})\s*차?|(\d{1,2})\s*차")
# 수량 패턴
RE_QTY = re.compile(r"(\d+)\s*(단|송이|박스|속|개|스팀|대|파렛트|BOX|box|묶음)")
# 사진 N장 헤더
RE_PHOTO_HEADER = re.compile(r"^\[사진\s*(\d+)장?\]$")
# 규격 미달 (예: "40cm로 들어옴")
RE_SIZE_DEFECT = re.compile(r"(\d+)\s*cm\s*(로|으로)\s*(들어옴|왔|옴)")


# ──────────────────────────────────────────────
# 데이터 클래스
# ──────────────────────────────────────────────

@dataclass
class VarietyDetail:
    """한 품종의 수량/불량 상세."""
    variety: str = ""
    quantity: int = 0
    unit: str = ""
    defect_note: str = ""      # "마름", "습" 등


@dataclass
class DefectReport:
    """단일 불량 보고 건 (텍스트 + 이미지 통합)."""

    # ── 원본 입력 ──
    raw_text: str = ""
    image_paths: list[str] = field(default_factory=list)

    # ── 텍스트 파싱 결과 ──
    photo_count: int = 0          # [사진 N장]
    sequence: str = ""            # 차수: "15-1", "14"
    origin: str = ""              # 원산지
    product: str = ""             # 품목: "카네이션"
    header_defect: str = ""       # 헤더 줄의 불량 키워드
    customer: str = ""            # 거래처
    variety_details: list[VarietyDetail] = field(default_factory=list)

    # ── 이미지 AI 분석 결과 ──
    ai_product_guess: str = ""        # AI가 추정한 품목
    ai_defect_type: str = ""          # AI가 추정한 불량 유형
    ai_severity: int = 0              # 심각도 1-5
    ai_description: str = ""          # AI 설명
    ai_confidence: float = 0.0        # AI 확신도 0-1

    # ── 교차 검증 ──
    product_match: bool = False       # 텍스트 품목 == AI 품목?
    cross_validation_score: float = 0.0

    # ── 메타 ──
    report_id: str = ""
    timestamp: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        return d

    def summary_text(self) -> str:
        """카카오워크 전송용 요약 텍스트."""
        lines = []
        if self.sequence:
            lines.append(f"[{self.sequence}차]")
        if self.product:
            lines.append(f"품목: {self.product}")
        if self.origin:
            lines.append(f"원산지: {self.origin}")
        if self.customer:
            lines.append(f"거래처: {self.customer}")
        if self.header_defect:
            lines.append(f"불량: {self.header_defect}")

        for vd in self.variety_details:
            detail = f"  - {vd.variety}"
            if vd.quantity:
                detail += f" {vd.quantity}{vd.unit}"
            if vd.defect_note:
                detail += f" [{vd.defect_note}]"
            lines.append(detail)

        if self.ai_defect_type:
            lines.append(f"[AI] 불량유형: {self.ai_defect_type} (심각도 {self.ai_severity}/5)")
        if self.ai_description:
            lines.append(f"[AI] {self.ai_description}")
        if self.cross_validation_score > 0:
            lines.append(f"[검증] 품목 일치율: {self.cross_validation_score:.0%}")

        return "\n".join(lines)


# ──────────────────────────────────────────────
# 멀티라인 텍스트 파서
# ──────────────────────────────────────────────

def _identify_product(token: str) -> str:
    """토큰에서 품목명 추출. 없으면 빈 문자열."""
    for product in PRODUCT_VARIETIES:
        if product in token:
            return product
    for alias, product in PRODUCT_ALIASES.items():
        if alias in token:
            return product
    return ""


def _identify_variety(token: str, product: str) -> str:
    """토큰에서 품종명 추출."""
    if not product or product not in PRODUCT_VARIETIES:
        return ""
    for variety in PRODUCT_VARIETIES[product]:
        if variety in token:
            return variety
    return ""


def _identify_origin(token: str) -> str:
    for o in ORIGINS:
        if o in token:
            return o
    return ""


def _identify_defect(text: str) -> str:
    """텍스트에서 불량 유형 키워드 추출."""
    for d in KNOWN_DEFECT_TYPES:
        if d in text:
            return d
    m = RE_SIZE_DEFECT.search(text)
    if m:
        return f"{m.group(1)}cm 규격 미달"
    return ""


def _extract_sequence(text: str) -> str:
    m = RE_SEQUENCE.search(text)
    if not m:
        return ""
    if m.group(1) and m.group(2):
        return f"{m.group(1)}-{m.group(2)}"
    if m.group(3):
        return m.group(3)
    return ""


def parse_defect_text(text: str) -> DefectReport:
    """
    불량 공유방의 멀티라인 텍스트를 파싱한다.

    기대 구조:
        [사진 N장]
        {차수} {원산지?} {품목} {불량?}
        {빈줄}
        {거래처}
        {빈줄?}
        {품종} {수량}{단위} {불량?}
        {품종} {수량}{단위} {불량?}
        ...

    Returns:
        DefectReport (image_paths, ai_* 필드는 비어 있음)
    """
    report = DefectReport(raw_text=text)
    lines = text.strip().splitlines()
    if not lines:
        return report

    # --- Phase 1: 줄 분류 ---
    classified: list[tuple[str, str]] = []  # (type, line)
    # type: "photo", "blank", "content"

    for raw_line in lines:
        stripped = raw_line.strip()
        if not stripped:
            classified.append(("blank", ""))
        elif RE_PHOTO_HEADER.match(stripped):
            m = RE_PHOTO_HEADER.match(stripped)
            report.photo_count = int(m.group(1)) if m and m.group(1) else 0
            classified.append(("photo", stripped))
        else:
            classified.append(("content", stripped))

    # --- Phase 2: content 줄만 추출 ---
    content_lines: list[str] = [line for typ, line in classified if typ == "content"]
    if not content_lines:
        return report

    # --- Phase 3: 헤더 줄 (첫 content 줄) ---
    header = content_lines[0]
    report.sequence = _extract_sequence(header)
    report.product = _identify_product(header)
    report.origin = _identify_origin(header)
    report.header_defect = _identify_defect(header)

    # --- Phase 4: 빈줄 기준으로 블록 분리 ---
    # classified 리스트에서 photo/blank을 제거하고 블록화
    blocks: list[list[str]] = []
    current_block: list[str] = []

    # photo 줄 이후부터 시작
    started = False
    for typ, line in classified:
        if typ == "photo":
            started = True
            continue
        if not started:
            # photo 헤더가 없으면 첫 content부터 시작
            if typ == "content":
                started = True
            else:
                continue

        if typ == "blank":
            if current_block:
                blocks.append(current_block)
                current_block = []
        else:
            current_block.append(line)

    if current_block:
        blocks.append(current_block)

    if not blocks:
        return report

    # --- Phase 5: 블록 해석 ---
    # 블록 0: 헤더 (차수/품목/원산지) — 이미 파싱함
    # 그 이후: 거래처(단독 줄, 수량 없음) 또는 품종 상세(수량 있음)

    for block_idx, block in enumerate(blocks):
        if block_idx == 0:
            # 헤더 블록 — 이미 파싱됨, 다만 여러 줄이면 나머지도 품종일 수 있음
            for line in block[1:]:
                _parse_variety_or_customer(line, report)
            continue

        for line in block:
            _parse_variety_or_customer(line, report)

    return report


def _parse_variety_or_customer(line: str, report: DefectReport) -> None:
    """
    줄 하나를 분석하여 거래처이거나 품종 상세이면 report에 추가한다.

    판별 기준:
    - 수량 패턴이 있으면 → 품종 상세
    - 수량 없고, 짧은 단독 텍스트 → 거래처 후보
    """
    qty_match = RE_QTY.search(line)

    if qty_match:
        # 품종 상세 줄
        vd = VarietyDetail()
        vd.quantity = int(qty_match.group(1))
        vd.unit = qty_match.group(2)
        vd.defect_note = _identify_defect(line)

        # 수량 앞부분에서 품종명 추출
        before_qty = line[:qty_match.start()].strip()
        if report.product:
            vd.variety = _identify_variety(before_qty, report.product)
        if not vd.variety:
            # 품종 사전에 없으면 원문 그대로 사용
            vd.variety = before_qty if before_qty else ""

        report.variety_details.append(vd)
    else:
        # 불량 키워드가 있으면 품종 상세(수량 없는 불량 보고)
        defect = _identify_defect(line)
        if defect:
            # 품종명 추출 시도
            variety = ""
            if report.product:
                variety = _identify_variety(line, report.product)
            vd = VarietyDetail(variety=variety, defect_note=defect)
            report.variety_details.append(vd)
        elif not report.customer and len(line) < 20 and not RE_SEQUENCE.search(line):
            # 짧은 단독 줄 = 거래처
            report.customer = line.strip()
        else:
            # 분류 불가 줄 — 무시하지 않고 품종으로 최선 추정
            variety = ""
            if report.product:
                variety = _identify_variety(line, report.product)
            if variety:
                vd = VarietyDetail(variety=variety)
                report.variety_details.append(vd)


# ──────────────────────────────────────────────
# Gemini Vision 이미지 분석
# ──────────────────────────────────────────────

def _load_gemini_key() -> str:
    """GEMINI_API_KEY를 .env에서 로드."""
    key = os.environ.get("GEMINI_API_KEY", "")
    if key:
        return key
    env_path = Path(__file__).parent.parent / ".env"
    if env_path.exists():
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if line.startswith("GEMINI_API_KEY="):
                return line.split("=", 1)[1].strip()
    return ""


def _encode_image_base64(image_path: str) -> tuple[str, str]:
    """이미지 파일을 base64로 인코딩. (data, mime_type) 반환."""
    p = Path(image_path)
    suffix = p.suffix.lower()
    mime_map = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".bmp": "image/bmp",
    }
    mime = mime_map.get(suffix, "image/jpeg")
    data = base64.standard_b64encode(p.read_bytes()).decode("ascii")
    return data, mime


@dataclass
class ImageAnalysisResult:
    """이미지 분석 단건 결과."""
    flower_type: str = ""      # 꽃 종류 추정
    defect_type: str = ""      # 불량 유형 추정
    severity: int = 0          # 심각도 1-5
    description: str = ""      # 상세 설명
    confidence: float = 0.0    # 확신도 0-1
    raw_response: str = ""     # API 원문 (디버그)


def _build_vision_prompt() -> str:
    """Gemini Vision에 보낼 프롬프트."""
    return """\
당신은 화훼(꽃) 품질 검수 전문가입니다.
이 사진은 수입 화훼의 불량/클레임 보고 사진입니다.

다음을 JSON으로 답해주세요 (한국어):
{
  "flower_type": "꽃 종류 (카네이션, 장미, 수국, 루스커스, 레몬잎, 모카라, 알스트로 등)",
  "defect_type": "불량 유형 (마름, 습, 패킹 불량, 겉잎제거 필요, 곰팡이, 꺾임, 냉해, 과숙, 변색, 벌레, 규격 미달, 수량부족, 기타)",
  "severity": 3,
  "description": "불량 상태 간단 설명 (1~2문장)",
  "confidence": 0.8
}

severity 기준:
  1 = 경미 (판매 가능, 겉잎 제거 정도)
  2 = 경미-보통 (일부 손질 필요)
  3 = 보통 (B급 판매 가능)
  4 = 심각 (판매 어려움, 클레임 대상)
  5 = 전량 폐기 (전량클레임)

JSON만 출력하세요. 추가 설명 불필요."""


def analyze_defect_image(image_path: str) -> ImageAnalysisResult:
    """
    단일 이미지를 Gemini Vision으로 분석한다.

    API 키가 없거나 호출 실패 시 mock 결과를 반환한다.
    """
    api_key = _load_gemini_key()

    if not api_key:
        logger.warning("GEMINI_API_KEY 미설정 — mock 결과 반환")
        return _mock_analysis(image_path)

    if not Path(image_path).exists():
        logger.error("이미지 파일 없음: %s", image_path)
        return ImageAnalysisResult(description=f"파일 없음: {image_path}")

    try:
        return _call_gemini_vision(api_key, image_path)
    except Exception as e:
        logger.error("Gemini Vision 호출 실패: %s — mock 결과 반환", e)
        return _mock_analysis(image_path)


def _call_gemini_vision(api_key: str, image_path: str) -> ImageAnalysisResult:
    """Gemini 2.5 Flash API를 직접 호출한다 (requests 사용)."""
    import requests

    img_data, mime_type = _encode_image_base64(image_path)

    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        "gemini-2.5-flash:generateContent"
        f"?key={api_key}"
    )

    payload = {
        "contents": [
            {
                "parts": [
                    {"text": _build_vision_prompt()},
                    {
                        "inline_data": {
                            "mime_type": mime_type,
                            "data": img_data,
                        }
                    },
                ]
            }
        ],
        "generationConfig": {
            "temperature": 0.2,
            "maxOutputTokens": 512,
        },
    }

    resp = requests.post(url, json=payload, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    # Gemini 응답에서 텍스트 추출
    text = ""
    try:
        text = data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError):
        logger.error("Gemini 응답 파싱 실패: %s", json.dumps(data, ensure_ascii=False)[:500])
        return ImageAnalysisResult(
            description="API 응답 파싱 실패",
            raw_response=json.dumps(data, ensure_ascii=False)[:1000],
        )

    # JSON 추출 (코드 블록 안에 있을 수 있음)
    json_text = text.strip()
    if json_text.startswith("```"):
        # ```json ... ``` 형태 처리
        json_text = re.sub(r"^```(?:json)?\s*", "", json_text)
        json_text = re.sub(r"\s*```$", "", json_text)

    try:
        parsed = json.loads(json_text)
    except json.JSONDecodeError:
        logger.error("Gemini JSON 파싱 실패: %s", json_text[:300])
        return ImageAnalysisResult(
            description=text[:200],
            raw_response=text,
        )

    return ImageAnalysisResult(
        flower_type=parsed.get("flower_type", ""),
        defect_type=parsed.get("defect_type", ""),
        severity=int(parsed.get("severity", 0)),
        description=parsed.get("description", ""),
        confidence=float(parsed.get("confidence", 0)),
        raw_response=text,
    )


def _mock_analysis(image_path: str) -> ImageAnalysisResult:
    """API 없을 때 파일명 기반 mock 결과."""
    filename = Path(image_path).stem.lower()

    # 파일명에서 힌트 추출
    flower = "불명"
    for product in PRODUCT_VARIETIES:
        if product in filename:
            flower = product
            break

    return ImageAnalysisResult(
        flower_type=flower,
        defect_type="확인 필요",
        severity=3,
        description=f"[MOCK] 이미지 분석 미수행 ({Path(image_path).name})",
        confidence=0.0,
        raw_response="",
    )


# ──────────────────────────────────────────────
# 교차 검증
# ──────────────────────────────────────────────

def cross_validate(report: DefectReport) -> DefectReport:
    """
    텍스트 파싱 결과와 이미지 AI 결과를 교차 검증한다.

    비교 항목:
    - 품목 일치 (텍스트 product vs AI flower_type)
    - 불량 유형 일치 (텍스트 header_defect/variety defect_note vs AI defect_type)

    Returns:
        report (product_match, cross_validation_score 업데이트됨)
    """
    score = 0.0
    checks = 0

    # 1. 품목 일치 검사
    if report.product and report.ai_product_guess:
        text_product = report.product.strip()
        ai_product = report.ai_product_guess.strip()

        # 정확 일치
        if text_product == ai_product:
            report.product_match = True
            score += 1.0
        # 부분 일치 (AI가 "카네이션" 대신 "카네" 등)
        elif text_product in ai_product or ai_product in text_product:
            report.product_match = True
            score += 0.8
        # 별칭 일치
        else:
            normalized_ai = PRODUCT_ALIASES.get(ai_product, ai_product)
            if text_product == normalized_ai:
                report.product_match = True
                score += 0.9

        checks += 1

    # 2. 불량 유형 일치 검사
    text_defects: set[str] = set()
    if report.header_defect:
        text_defects.add(report.header_defect)
    for vd in report.variety_details:
        if vd.defect_note:
            text_defects.add(vd.defect_note)

    if text_defects and report.ai_defect_type:
        ai_defect = report.ai_defect_type.strip()
        if ai_defect in text_defects:
            score += 1.0
        elif any(ai_defect in td or td in ai_defect for td in text_defects):
            score += 0.6
        checks += 1

    # 최종 점수
    report.cross_validation_score = score / checks if checks > 0 else 0.0

    return report


# ──────────────────────────────────────────────
# 통합 분석 (텍스트 + 이미지)
# ──────────────────────────────────────────────

def analyze_defect(
    text: str,
    image_paths: Optional[list[str]] = None,
) -> DefectReport:
    """
    단일 불량 건을 텍스트+이미지 통합 분석한다.

    Args:
        text: 불량 공유방에서 수집된 멀티라인 텍스트
        image_paths: 관련 이미지 파일 경로 리스트 (없으면 텍스트만)

    Returns:
        DefectReport (모든 필드 채워진 상태)
    """
    # 1. 텍스트 파싱
    report = parse_defect_text(text)
    report.image_paths = image_paths or []

    # 2. 이미지 분석 (첫 번째 사진 기준, 나머지는 보조)
    if report.image_paths:
        primary_image = report.image_paths[0]
        result = analyze_defect_image(primary_image)

        report.ai_product_guess = result.flower_type
        report.ai_defect_type = result.defect_type
        report.ai_severity = result.severity
        report.ai_description = result.description
        report.ai_confidence = result.confidence

    # 3. 교차 검증
    if report.product and report.ai_product_guess:
        report = cross_validate(report)

    return report


# ──────────────────────────────────────────────
# 배치 처리
# ──────────────────────────────────────────────

@dataclass
class BatchResult:
    """배치 처리 결과."""
    reports: list[DefectReport] = field(default_factory=list)
    total: int = 0
    with_images: int = 0
    avg_severity: float = 0.0
    defect_type_counts: dict[str, int] = field(default_factory=dict)
    product_counts: dict[str, int] = field(default_factory=dict)
    match_rate: float = 0.0       # 교차 검증 평균 일치율

    def summary_text(self) -> str:
        lines = [
            f"=== 불량 보고 배치 분석 결과 ===",
            f"총 건수: {self.total}",
            f"이미지 포함: {self.with_images}",
            f"평균 심각도: {self.avg_severity:.1f}/5",
            f"교차검증 평균 일치율: {self.match_rate:.0%}",
        ]
        if self.defect_type_counts:
            lines.append("불량 유형 분포:")
            for dt, cnt in sorted(self.defect_type_counts.items(), key=lambda x: -x[1]):
                lines.append(f"  {dt}: {cnt}건")
        if self.product_counts:
            lines.append("품목 분포:")
            for p, cnt in sorted(self.product_counts.items(), key=lambda x: -x[1]):
                lines.append(f"  {p}: {cnt}건")
        return "\n".join(lines)


def process_defect_batch(
    items: list[dict],
) -> BatchResult:
    """
    불량 보고 일괄 처리.

    Args:
        items: [{"text": str, "images": list[str]}, ...]
               각 항목은 카톡 불량 공유방의 한 건 (텍스트 + 관련 사진들)

    Returns:
        BatchResult
    """
    result = BatchResult()
    severities: list[int] = []
    match_scores: list[float] = []

    for item in items:
        text = item.get("text", "")
        images = item.get("images", [])

        report = analyze_defect(text, images)
        result.reports.append(report)
        result.total += 1

        if images:
            result.with_images += 1

        if report.ai_severity > 0:
            severities.append(report.ai_severity)

        if report.cross_validation_score > 0:
            match_scores.append(report.cross_validation_score)

        # 불량 유형 집계
        all_defects: list[str] = []
        if report.header_defect:
            all_defects.append(report.header_defect)
        for vd in report.variety_details:
            if vd.defect_note:
                all_defects.append(vd.defect_note)
        if report.ai_defect_type and report.ai_defect_type not in all_defects:
            all_defects.append(report.ai_defect_type)

        for d in all_defects:
            result.defect_type_counts[d] = result.defect_type_counts.get(d, 0) + 1

        # 품목 집계
        if report.product:
            result.product_counts[report.product] = result.product_counts.get(report.product, 0) + 1

    # 평균 계산
    result.avg_severity = sum(severities) / len(severities) if severities else 0.0
    result.match_rate = sum(match_scores) / len(match_scores) if match_scores else 0.0

    return result


# ──────────────────────────────────────────────
# CLI 테스트
# ──────────────────────────────────────────────

if __name__ == "__main__":
    # 텍스트 파싱 테스트
    sample_text = """[사진 4장]
15-1차 콜롬비아 카네이션 마름

주광

문라이트 10단 마름
돈셀 5단
헤르메스 3단 겉잎제거 필요"""

    print("=== 텍스트 파싱 테스트 ===")
    report = parse_defect_text(sample_text)
    print(f"차수: {report.sequence}")
    print(f"원산지: {report.origin}")
    print(f"품목: {report.product}")
    print(f"헤더 불량: {report.header_defect}")
    print(f"거래처: {report.customer}")
    print(f"사진: {report.photo_count}장")
    print(f"품종 상세:")
    for vd in report.variety_details:
        print(f"  {vd.variety} {vd.quantity}{vd.unit} [{vd.defect_note}]")

    print()
    print("=== 요약 텍스트 ===")
    print(report.summary_text())

    # 배치 처리 테스트 (이미지 없음 — mock)
    print()
    print("=== 배치 처리 테스트 ===")
    batch = process_defect_batch([
        {"text": sample_text, "images": []},
        {
            "text": "[사진 2장]\n14차 장미 습\n\n이레\n\n프라우드 20송이 습",
            "images": [],
        },
    ])
    print(batch.summary_text())
