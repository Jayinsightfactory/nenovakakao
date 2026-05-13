"""
매핑 정정 추천 — verify_room_mapping_v2 결과를 읽고 mapping key 정정 후보 제시.

대상:
  1. 미검증 mapping key (카톡 리스트에서 어떤 변형으로도 못 찾음)
  2. fuzzy 매칭이지만 정확 일치 아닌 mapping key

각 key 마다 detected_rooms (Phase A 에서 찾은 43 개 방) 중에서
fuzzy 점수 상위 3 개를 후보로 제시.

출력: data/mapping_recommendations.json + 콘솔 사람용 표

사용자가 후보 중 하나를 선택하면 다음 단계에서 mapping 정정 도구로 적용 가능.
"""
from __future__ import annotations

import json
import re
import sys
from difflib import SequenceMatcher
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _norm(s: str) -> str:
    s = re.sub(r"[\s\[\]\(\)\.\-_\"'&+,]+", "", s or "")
    return s.lower()


def _score(key: str, candidate: str) -> float:
    nk, nc = _norm(key), _norm(candidate)
    if not nk or not nc:
        return 0.0
    if nk == nc:
        return 100.0
    base = SequenceMatcher(None, nk, nc).ratio()  # 0~1
    # 정확 부분 포함 보너스
    contain_bonus = 20.0 if (nk in nc or nc in nk) else 0.0
    # 길이 차 패널티
    len_penalty = abs(len(nk) - len(nc)) / max(len(nk), len(nc))
    return base * 60 + contain_bonus - len_penalty * 20


def topk_candidates(key: str, rooms: list[str], k: int = 3) -> list[tuple[str, float]]:
    scored = [(r, round(_score(key, r), 2)) for r in rooms]
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:k]


def main() -> int:
    report_path = ROOT / "data" / "mapping_verify_report_v2.json"
    if not report_path.exists():
        print(f"❌ {report_path.name} 없음. 먼저 verify_room_mapping_v2.py 실행하세요.")
        return 1

    report = json.loads(report_path.read_text(encoding="utf-8"))
    detected = report.get("phase_a_detected_rooms") or []
    final = report.get("final") or []
    extra = report.get("extra_rooms_in_chatlist_not_in_mapping") or []

    print(f"detected rooms (Phase A): {len(detected)}개")
    print(f"final 검증 항목: {len(final)}개")
    print(f"카톡에는 있지만 mapping 에 없는 방: {len(extra)}개")
    print()

    # 정정 대상: 미검증 OR (verified but not exact_match)
    targets = []
    for item in final:
        if not item.get("verified") or not item.get("exact_match"):
            targets.append(item)

    if not targets:
        print("✅ 정정 대상 없음 — 모든 mapping key 가 정확 일치")
        return 0

    print(f"=== 정정 대상 {len(targets)}개 ===\n")

    recommendations: dict[str, list[dict]] = {}
    for item in targets:
        key = item["mapping_key"]
        cid = item["conv_id"]
        status = "fuzzy" if item.get("verified") else "missing"
        print(f"[{status.upper()}] mapping={key!r}  conv_id={cid}")
        if status == "fuzzy":
            print(f"  현재 fuzzy 매칭: {item.get('phase_a_match')!r}")
        cands = topk_candidates(key, detected, k=4)
        recommendations[key] = [
            {"candidate": c, "score": sc, "conv_id": cid} for c, sc in cands
        ]
        print(f"  후보 (fuzzy 점수 순):")
        for i, (c, sc) in enumerate(cands, 1):
            tag = ""
            if sc >= 80:
                tag = " ← 거의 확실"
            elif sc >= 50:
                tag = " ← 검토 필요"
            print(f"    {i}. {c!r:50s}  (score {sc:>6.2f}){tag}")
        print()

    out_path = ROOT / "data" / "mapping_recommendations.json"
    out_path.write_text(
        json.dumps({
            "targets": [t["mapping_key"] for t in targets],
            "recommendations": recommendations,
            "all_detected_rooms": detected,
            "extra_rooms_in_chatlist": extra,
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"보고서: {out_path.name}")
    print()
    print("다음 단계 가이드:")
    print("  1) 위 후보 보고 각 mapping key 에 대해:")
    print("     - 후보 1 이 명확하면 → mapping key 를 그 이름으로 정정")
    print("     - 후보 다 어색하면 → 카톡에서 진짜 떠난 방. mapping 에서 제거")
    print("     - 새 이름이 있으면 → 카톡 화면에서 직접 확인 후 결정")
    print("  2) 결정한 정정을 적용할 도구는 다음 세션에서 추가 (apply_mapping_fixes.py)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
