"""
data/mapping_recommendations.json 의 추천을 점수 기반 default action 으로 변환해
data/room_mapping.json 에 적용하는 도구.

기본은 dry-run (변경 계획만 출력). --apply 줘야 실제 갱신 + 백업 생성.

자동 결정 규칙 (default):
  score >= 70:  remap to candidate[0]
  25 <= score < 70:  review (변경 안 함, 사람이 결정)
  score < 25:   delete (mapping 에서 제거)

사용자 오버라이드:
  --override "수입방=delete"
  --override "네노바&선울=keep"        # 그대로 유지
  --override "네노바현장팀=remap:네노바 영업/현장"

옵션:
  --apply               실제 갱신 (백업 자동 생성)
  --dry-run             기본 (변경 계획만, 안전)
  --auto-delete-below N 점수 N 미만 모두 delete (default 25)
  --auto-remap-above N  점수 N 이상 모두 remap (default 70)
  --override KEY=ACTION 개별 mapping key 결정 강제

ACTION 종류:
  keep                  현재 mapping 그대로 유지
  delete                mapping 에서 제거
  remap                 점수 1위 후보로 정정
  remap:<candidate>     특정 후보로 정정
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parent.parent

MAPPING_PATH = ROOT / "data" / "room_mapping.json"
REC_PATH = ROOT / "data" / "mapping_recommendations.json"
REPORT_PATH = ROOT / "data" / "mapping_verify_report_v2.json"


def _decide(key: str, cands: list[dict], cfg: argparse.Namespace) -> dict:
    """점수 기반 default action 결정."""
    overrides: dict[str, str] = cfg.override_map
    if key in overrides:
        ov = overrides[key]
        if ov == "keep":
            return {"action": "keep", "source": "override"}
        if ov == "delete":
            return {"action": "delete", "source": "override"}
        if ov == "remap":
            top = cands[0]["candidate"] if cands else ""
            return {"action": "remap", "to": top, "source": "override", "score": cands[0]["score"] if cands else 0}
        if ov.startswith("remap:"):
            target = ov[len("remap:"):].strip()
            return {"action": "remap", "to": target, "source": "override-explicit"}
        return {"action": "review", "source": f"override:invalid({ov})"}

    if not cands:
        return {"action": "delete", "source": "auto:no-candidates"}
    top = cands[0]
    score = top.get("score", 0)
    if score >= cfg.auto_remap_above:
        return {"action": "remap", "to": top["candidate"], "score": score, "source": "auto:high-score"}
    if score < cfg.auto_delete_below:
        return {"action": "delete", "score": score, "source": "auto:low-score"}
    return {"action": "review", "score": score, "top_candidate": top["candidate"],
            "source": "auto:mid-score"}


def main() -> int:
    p = argparse.ArgumentParser(description="매핑 정정 적용 도구")
    p.add_argument("--apply", action="store_true",
                   help="실제 room_mapping.json 갱신 (백업 자동 생성)")
    p.add_argument("--dry-run", action="store_true",
                   help="변경 계획만 출력 (default)")
    p.add_argument("--auto-delete-below", type=float, default=25.0,
                   help="이 점수 미만 mapping key 자동 삭제 (default 25)")
    p.add_argument("--auto-remap-above", type=float, default=70.0,
                   help="이 점수 이상 top 후보로 자동 정정 (default 70)")
    p.add_argument("--override", action="append", default=[],
                   help='개별 결정 (예: "수입방=delete", "네노바&선울=keep")')
    args = p.parse_args()

    # 오버라이드 파싱
    args.override_map = {}
    for ov in args.override:
        if "=" not in ov:
            print(f"⚠️ override 형식 오류: {ov!r}", flush=True)
            continue
        k, v = ov.split("=", 1)
        args.override_map[k.strip()] = v.strip()

    # 안전: --apply 안 주면 dry-run 으로 간주
    is_apply = bool(args.apply)
    if not is_apply:
        print("ℹ️ dry-run 모드 (--apply 없음) — 실제 파일 변경 안 함.\n")

    # 데이터 로드
    if not REC_PATH.exists():
        print(f"❌ {REC_PATH.name} 없음. 먼저 recommend_mapping_fixes.py 실행하세요.")
        return 1
    if not MAPPING_PATH.exists():
        print(f"❌ {MAPPING_PATH.name} 없음.")
        return 1

    rec = json.loads(REC_PATH.read_text(encoding="utf-8"))
    recommendations: dict[str, list[dict]] = rec.get("recommendations") or {}
    mapping: dict[str, str] = json.loads(MAPPING_PATH.read_text(encoding="utf-8"))

    print(f"현재 mapping: {len(mapping)} keys")
    print(f"추천 대상: {len(recommendations)} keys (= verify_v2 미검증/fuzzy)")
    print(f"규칙: score≥{args.auto_remap_above}=remap, score<{args.auto_delete_below}=delete, 그 외=review")
    if args.override_map:
        print(f"오버라이드: {args.override_map}")
    print()

    plan: list[dict] = []
    for key, cands in recommendations.items():
        decision = _decide(key, cands, args)
        decision["mapping_key"] = key
        decision["current_conv_id"] = mapping.get(key, "")
        plan.append(decision)

    # 계획 출력
    counts = {"remap": 0, "delete": 0, "review": 0, "keep": 0}
    print(f"{'Action':<8} {'Score':>6}  {'Mapping key':<35} {'→':<2} Detail")
    print("─" * 100)
    for d in plan:
        a = d["action"]
        counts[a] = counts.get(a, 0) + 1
        sc = d.get("score", 0)
        sc_str = f"{sc:5.1f}" if sc else "  -  "
        key = d["mapping_key"][:33]
        if a == "remap":
            detail = f"→ {d['to']!r}"
        elif a == "delete":
            detail = f"(remove from mapping) [{d.get('source', '')}]"
        elif a == "keep":
            detail = "(no change)"
        else:
            top = d.get("top_candidate", "")
            detail = f"(중점수 — 사람 결정 필요. top={top!r})"
        mark = {"remap": "🔄", "delete": "🗑️", "review": "❓", "keep": "✓"}.get(a, " ")
        print(f"{mark} {a:<6} {sc_str}  {key:<35} {detail}")

    print()
    print(f"=== 요약 ===")
    print(f"  🔄 remap   : {counts.get('remap', 0)}개")
    print(f"  🗑️ delete  : {counts.get('delete', 0)}개")
    print(f"  ❓ review  : {counts.get('review', 0)}개 (자동 적용 안 함)")
    print(f"  ✓  keep    : {counts.get('keep', 0)}개")
    print()

    # 적용 단계
    if not is_apply:
        print("ℹ️ --apply 로 다시 실행하면 실제 mapping 정정 + 백업 생성.")
        # 계획 자체는 저장 (재사용 용)
        plan_path = ROOT / "data" / "mapping_fix_plan.json"
        plan_path.write_text(
            json.dumps({
                "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "config": {
                    "auto_delete_below": args.auto_delete_below,
                    "auto_remap_above": args.auto_remap_above,
                    "overrides": args.override_map,
                },
                "plan": plan,
                "counts": counts,
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"계획 저장: {plan_path.name}")
        return 0

    # 실제 적용
    new_mapping = dict(mapping)
    applied: list[dict] = []
    for d in plan:
        key = d["mapping_key"]
        a = d["action"]
        if a == "delete":
            if key in new_mapping:
                applied.append({"action": "delete", "key": key, "old_conv_id": new_mapping[key]})
                del new_mapping[key]
        elif a == "remap":
            new_key = d["to"]
            if not new_key:
                continue
            if key in new_mapping:
                conv_id = new_mapping[key]
                applied.append({"action": "remap", "from": key, "to": new_key, "conv_id": conv_id})
                del new_mapping[key]
                # 이미 같은 이름 키가 있으면 충돌 — 새 키는 추가 안 함
                if new_key not in new_mapping:
                    new_mapping[new_key] = conv_id
                else:
                    applied[-1]["note"] = "target_key_already_exists"
        # keep / review 는 변경 없음

    if not applied:
        print("적용할 변경 없음.")
        return 0

    # 백업
    ts = time.strftime("%Y%m%d_%H%M%S")
    bak = MAPPING_PATH.with_suffix(f".json.bak.{ts}")
    shutil.copy(MAPPING_PATH, bak)
    print(f"백업: {bak.name}")

    MAPPING_PATH.write_text(json.dumps(new_mapping, ensure_ascii=False, indent=2),
                            encoding="utf-8")
    print(f"갱신: {MAPPING_PATH.name}  ({len(mapping)} → {len(new_mapping)} keys)")

    # 적용 로그
    log_path = ROOT / "data" / "mapping_fix_log.jsonl"
    with open(log_path, "a", encoding="utf-8") as f:
        for a in applied:
            f.write(json.dumps({"ts": ts, **a}, ensure_ascii=False) + "\n")
    print(f"로그: {log_path.name}  ({len(applied)}건 추가)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
