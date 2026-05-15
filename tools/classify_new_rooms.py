"""
mapping 에 없지만 카톡 chat list 에 있는 방을 자동 분류 + 미러링 후보 추출.

분류 카테고리:
  - mirror_candidate : 거래처 단체방 (네노바 + 거래처, 또는 명확한 단체방)
  - personal_1to1    : 1:1 채팅 (단일 이름, 직책)
  - system           : 알림톡 / 뱅크 / 게시판 / 시스템
  - external_group   : 외부 단체방 (사적 모임, 알 수 없음)
  - ocr_artifact     : OCR 변형 (이미 다른 변형이 mirror_candidate 으로 들어감)

OCR 변형 흡수:
  카톡 OCR 이 같은 방 이름을 다르게 인식 (예: "네노바&수아래" vs "네노바&수야래").
  유사도 80%+ 끼리 묶어 대표 이름 1 개만 mirror_candidate, 나머지는 ocr_artifact.

출력: data/new_rooms_classification.json + 콘솔 표
"""
from __future__ import annotations

import json
import re
import sys
from difflib import SequenceMatcher
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parent.parent

REPORT_PATH = ROOT / "data" / "mapping_verify_report_v2.json"
OUT_PATH = ROOT / "data" / "new_rooms_classification.json"

# 시스템·알림 키워드
SYSTEM_KEYWORDS = [
    "알림톡", "뱅크", "은행", "카드", "결제",
    "게시판", "공지", "모아보기", "덧글", "댓글",
    "광고", "스팸",
]

# 직책 (1:1 추정 시그널)
JOB_TITLES = ["차장", "과장", "대리", "부장", "팀장", "이사", "사장", "대표", "님"]


def _norm(s: str) -> str:
    s = re.sub(r"[\s\[\]\(\)\.\-_\"'&+,]+", "", s or "")
    return s.lower()


def _is_system(name: str) -> bool:
    n = name
    return any(kw in n for kw in SYSTEM_KEYWORDS)


def _is_1to1(name: str) -> bool:
    """단일 한국 이름 또는 직책으로 끝나는 1:1 채팅 추정."""
    # 직책 포함
    for t in JOB_TITLES:
        if name.endswith(t):
            return True
    # 단일 단어 (공백·특수문자 없음) + 한글 2-4자 → 1:1 추정
    if not re.search(r"[\s+&,]", name):
        if re.fullmatch(r"[가-힣]{2,4}", name):
            return True
    return False


def _is_group(name: str) -> bool:
    """단체방 시그널: +, &, 쉼표 다수, '네노바' 포함 거래처 단체방."""
    if "+" in name or "&" in name:
        return True
    if name.count(",") >= 2:
        return True
    # "X + 네노바" / "네노바+ X" 패턴
    if "네노바" in name and len(name) > 3 and not _is_1to1(name):
        return True
    return False


def _similar(a: str, b: str) -> float:
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return 0.0
    return SequenceMatcher(None, na, nb).ratio()


def _absorb_ocr_variants(candidates: list[str], threshold: float = 0.78) -> tuple[list[str], dict[str, str]]:
    """OCR 변형 흡수: 유사도 threshold 이상은 한 그룹으로 묶고 대표 1 개만 남김.

    Returns:
        (representatives, variant_map: variant_name → representative_name)
    """
    cands = sorted(candidates, key=lambda x: (-len(x), x))  # 긴 이름 우선 (정보 더 많음)
    reps: list[str] = []
    variant_map: dict[str, str] = {}
    for name in cands:
        merged_with = None
        for r in reps:
            if _similar(name, r) >= threshold:
                merged_with = r
                break
        if merged_with is not None:
            variant_map[name] = merged_with
        else:
            reps.append(name)
            variant_map[name] = name
    return reps, variant_map


def classify(name: str, *, existing_mirror_names: set[str]) -> str:
    """단일 방 이름 분류."""
    if _is_system(name):
        return "system"
    if _is_group(name):
        # 단체방인데 이미 다른 거래처 이름과 매우 유사하면 ocr_artifact 는 후처리에서
        return "mirror_candidate"
    if _is_1to1(name):
        return "personal_1to1"
    return "external_group"


def main() -> int:
    if not REPORT_PATH.exists():
        print(f"❌ {REPORT_PATH.name} 없음. verify_room_mapping_v2.py 먼저 실행.")
        return 1
    report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
    extra: list[str] = report.get("extra_rooms_in_chatlist_not_in_mapping") or []
    mapping_path = ROOT / "data" / "room_mapping.json"
    existing = set(json.loads(mapping_path.read_text(encoding="utf-8")).keys()) if mapping_path.exists() else set()

    print(f"신규 방 (mapping 미등록): {len(extra)}개")
    print(f"기존 mapping: {len(existing)}개")
    print()

    # 1차 분류
    initial: dict[str, str] = {}
    for name in extra:
        initial[name] = classify(name, existing_mirror_names=existing)

    # 거래처 단체방 후보들 사이 OCR 변형 흡수
    group_cands = [n for n, c in initial.items() if c == "mirror_candidate"]
    reps, variant_map = _absorb_ocr_variants(group_cands)

    # 최종 카테고리: ocr_artifact 는 대표 이름과 다른 variant
    final: dict[str, dict] = {}
    for name in extra:
        cat = initial[name]
        entry = {"name": name, "category": cat}
        if cat == "mirror_candidate":
            rep = variant_map.get(name, name)
            if rep != name:
                entry["category"] = "ocr_artifact"
                entry["merged_into"] = rep
        final[name] = entry

    # 출력 (카테고리별 정렬)
    by_cat: dict[str, list[str]] = {}
    for name, entry in final.items():
        by_cat.setdefault(entry["category"], []).append(name)

    print("=" * 90)
    for cat, names in sorted(by_cat.items(), key=lambda x: -len(x[1])):
        mark = {
            "mirror_candidate": "🟢 미러링 후보",
            "personal_1to1": "👤 1:1 채팅 (스킵)",
            "system": "🔔 시스템/알림 (스킵)",
            "external_group": "❓ 외부 단체방 (검토)",
            "ocr_artifact": "🌀 OCR 변형 (이미 후보에 흡수)",
        }.get(cat, cat)
        print(f"\n[{mark}] {len(names)}개")
        for n in sorted(names):
            extra_info = ""
            if cat == "ocr_artifact":
                extra_info = f"  → {final[n].get('merged_into')!r}"
            print(f"  • {n!r}{extra_info}")

    OUT_PATH.write_text(
        json.dumps({
            "total_new_rooms": len(extra),
            "by_category": {k: sorted(v) for k, v in by_cat.items()},
            "details": list(final.values()),
            "mirror_candidates_unique": sorted(reps),
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print()
    print("=" * 90)
    print(f"보고서: {OUT_PATH.name}")
    print(f"\n🟢 미러링 후보 (OCR 변형 흡수 후 유니크): {len(reps)}개")
    for r in sorted(reps):
        print(f"   • {r}")
    print()
    print("다음 단계: 위 후보 중 실제 미러방 생성할 거 결정 후")
    print("    tools/create_mirrors_for_new_rooms.py (다음 작성) 실행")
    return 0


if __name__ == "__main__":
    sys.exit(main())
