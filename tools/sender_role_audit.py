"""A: 발신자 역할 × 방타입 2D 분석.

pipeline_config.json 의 20명 key_personnel 역할 정보와
core.room_types.classify_room_type 을 결합해, 같은 키워드가
(역할, 방타입) 조합에 따라 얼마나 다르게 쓰이는지 검증한다.

출력: data/sender_role_audit.json + 콘솔 요약
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core.gsheet_sync import MSG_PATTERN, parse_message  # noqa: E402
from core.room_types import classify_room_type  # noqa: E402
from core.sender_aliases import normalize_sender  # noqa: E402

COLLECTED = ROOT / "data" / "collected_data.jsonl"
PIPELINE = ROOT / "data" / "pipeline_config.json"
OUT = ROOT / "data" / "sender_role_audit.json"

DIVIDER = re.compile(r"^-{5,}.*-{5,}$")
DELETED = "메시지가 삭제되었습니다."


def load_sender_roles() -> dict:
    """pipeline_config.key_personnel + 이름 변형 폴백 매핑."""
    cfg = json.loads(PIPELINE.read_text(encoding="utf-8"))
    base = cfg.get("key_personnel", {})
    table: dict[str, dict] = {}
    for name, info in base.items():
        table[name] = info
    # 짧은 형태도 매핑에 추가 (예: "임재용" 원본 vs "임재용대리" 변형)
    for name, info in list(base.items()):
        short = name.replace("네노바", "").replace("과장님", "").replace("차장님", "") \
                    .replace("대리님", "").replace("님", "").strip()
        if short and short not in table:
            table[short] = info
    return table


def get_role(sender: str, role_table: dict) -> tuple[str, str]:
    """발신자 → (stage, role_desc). 미등록은 ('UNKNOWN', '')."""
    if sender in role_table:
        info = role_table[sender]
        return info.get("stage", "UNKNOWN"), info.get("role", "")
    # 부분 문자열 매칭 (예: "네노바박성수친구" 등)
    for name, info in role_table.items():
        if name and (name in sender or sender in name):
            return info.get("stage", "UNKNOWN"), info.get("role", "")
    return "UNKNOWN", ""


def iter_messages(delta: str):
    current: list | None = None
    for line in delta.splitlines():
        m = MSG_PATTERN.match(line.strip())
        if m:
            if current is not None:
                yield tuple(current)
            sender, time_str, first = m.groups()
            current = [sender, time_str, first]
            continue
        if current is None:
            continue
        stripped = line.rstrip()
        if not stripped:
            current[2] += "\n"
            continue
        if DIVIDER.match(stripped):
            continue
        if stripped == DELETED:
            continue
        current[2] += "\n" + stripped
    if current is not None:
        yield tuple(current)


def main() -> None:
    role_table = load_sender_roles()
    print(f"[INIT] 역할 매핑 {len(role_table)}명 로드")

    # (stage, room_type) → Counter<event_type>
    matrix: dict[tuple, Counter] = defaultdict(Counter)
    matrix_totals = Counter()

    # 발신자별 프로파일
    sender_profile: dict[str, dict] = defaultdict(
        lambda: {"stage": "", "role": "", "total": 0, "events": Counter(), "rooms": Counter()}
    )
    unknown_senders = Counter()

    # 키워드 × (역할, 방타입) — 해석 차이 검증
    TRACE = ["불량", "추가", "취소", "확인", "부탁", "사진", "검역"]
    kw_matrix: dict[str, dict[tuple, Counter]] = {
        kw: defaultdict(Counter) for kw in TRACE
    }
    kw_samples: dict[str, dict[tuple, list]] = {
        kw: defaultdict(list) for kw in TRACE
    }

    with open(COLLECTED, encoding="utf-8") as f:
        for raw in f:
            raw = raw.strip()
            if not raw:
                continue
            try:
                rec = json.loads(raw)
            except json.JSONDecodeError:
                continue
            room = rec.get("room_name", "") or "(unknown)"
            delta = rec.get("delta", "") or ""
            room_type = classify_room_type(room)

            for sender, time_str, content in iter_messages(delta):
                sender = normalize_sender(sender)
                parsed = parse_message(content, room)
                et = parsed["event_type"]
                stage, role = get_role(sender, role_table)

                key = (stage, room_type)
                matrix[key][et] += 1
                matrix_totals[key] += 1

                prof = sender_profile[sender]
                prof["stage"] = stage
                prof["role"] = role
                prof["total"] += 1
                prof["events"][et] += 1
                prof["rooms"][room] += 1

                if stage == "UNKNOWN":
                    unknown_senders[sender] += 1

                for kw in TRACE:
                    if kw in content:
                        kw_matrix[kw][key][et] += 1
                        samples = kw_samples[kw][key]
                        if len(samples) < 5:
                            samples.append({
                                "sender": sender,
                                "room": room,
                                "event_type": et,
                                "excerpt": content.replace("\n", " | ")[:110],
                            })

    # ─── 리포트 빌드 ───
    report = {
        "_meta": {
            "역할매핑_인원": len(role_table),
            "발신자_수": len(sender_profile),
            "미매핑_발신자_top20": unknown_senders.most_common(20),
        },
        "역할x방타입_이벤트분포": {
            f"{stage}|{rtype}": {
                "총메시지": matrix_totals[(stage, rtype)],
                "이벤트분포": [
                    {"type": et, "count": n,
                     "pct": round(n / matrix_totals[(stage, rtype)] * 100, 1)}
                    for et, n in counter.most_common()
                ],
            }
            for (stage, rtype), counter in sorted(
                matrix.items(), key=lambda x: -matrix_totals[x[0]]
            )
        },
        "발신자_프로파일_top20": [
            {
                "name": sender,
                "stage": p["stage"],
                "role": p["role"],
                "총메시지": p["total"],
                "주요이벤트": p["events"].most_common(5),
                "주요방": p["rooms"].most_common(5),
            }
            for sender, p in sorted(
                sender_profile.items(), key=lambda x: -x[1]["total"]
            )[:20]
        ],
        "키워드_역할x방타입_샘플": {
            kw: {
                f"{stage}|{rtype}": samples
                for (stage, rtype), samples in keyed.items()
            }
            for kw, keyed in kw_samples.items()
        },
    }

    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    # ─── 콘솔 요약 ───
    print()
    print("=" * 72)
    print("[A: 발신자 역할 x 방타입 2D 분석]")
    print("=" * 72)

    print()
    print("[ 역할 x 방타입 조합별 총 메시지 (상위 15) ]")
    for (stage, rtype), total in matrix_totals.most_common(15):
        top3 = ", ".join(f"{e}:{c}" for e, c in matrix[(stage, rtype)].most_common(3))
        print(f"  {stage:<12s} x {rtype:<18s} {total:>6,}  → {top3}")

    print()
    print("[ 발신자 프로파일 Top 15 ]")
    for sender, p in sorted(
        sender_profile.items(), key=lambda x: -x[1]["total"]
    )[:15]:
        top_et = ", ".join(f"{e}:{c}" for e, c in p["events"].most_common(3))
        role_str = f"{p['stage']}({p['role'][:14]})" if p["role"] else p["stage"]
        print(f"  {sender[:24]:<24s} {role_str:<30s} {p['total']:>5d}건  {top_et}")

    print()
    print("[ 미매핑 발신자 Top 10 — 역할 테이블 보강 후보 ]")
    for sender, n in unknown_senders.most_common(10):
        print(f"  {sender[:40]:40s} {n:>5d}건")

    print()
    print("=" * 72)
    print("[ 같은 키워드, 다른 해석 — 역할x방타입 매트릭스 ]")
    print("=" * 72)

    for kw in ["불량", "부탁", "검역", "확인"]:
        print(f"\n▶ '{kw}' (역할 x 방타입 조합별)")
        per_key = []
        for (stage, rtype), counter in kw_matrix[kw].items():
            total = sum(counter.values())
            if total < 3:
                continue
            top_et = counter.most_common(1)[0]
            per_key.append((stage, rtype, total, counter))
        per_key.sort(key=lambda x: -x[2])
        for stage, rtype, total, counter in per_key[:8]:
            top3 = [f"{e}:{round(n/total*100)}%" for e, n in counter.most_common(3)]
            print(f"  {stage:<12s} x {rtype:<18s} ({total:>4d}건) {', '.join(top3)}")

    print()
    print(f"[OK] 저장: {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
