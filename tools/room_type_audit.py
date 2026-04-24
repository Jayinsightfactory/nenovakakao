"""
방 성격(거래처 vs 내부) 기준 분류 재진단 — 1단계 심화.

방을 자동으로 타입 분류하고, 타입별로 이벤트 분류 분포와 핵심 키워드의
해석 차이를 정량화한다. 예를 들어 거래처 채널에서의 '불량' 은 외부
컴플레인(DEFECT)인지, 내부 백본 방에서의 '불량'은 검수 보고인지가
실데이터에서 실제로 구분되는지 검증.

출력: data/room_type_audit.json + 콘솔 요약
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

COLLECTED = ROOT / "data" / "collected_data.jsonl"
OUT = ROOT / "data" / "room_type_audit.json"

DIVIDER = re.compile(r"^-{5,}.*-{5,}$")
DELETED = "메시지가 삭제되었습니다."

# ─── 방 타입 자동 분류 규칙 ───

INTERNAL_BACKBONE_KEYS = [
    "수입방", "영업방", "불량 공유방", "물량 공유방",
    "현장단체", "현장 추가취소", "현장추가취소",
    "빌번호", "발번호",
    "견적방", "전산테스트", "네노바현장팀",
    "네노바 영업", "네노바 수입/영업/현장",
    "영업지원팀", "영업방팀",
]
PARTNER_KEYS = ["선율", "선울", "방역"]  # 검역협력사(선율), 방역업체
SUPPLIER_ONLY_KEYS = ["란스 발주방", "백상", "경부 중앙화훼", "미우신라"]
PRIVATE_MARKERS = [","]  # 쉼표 다수는 개인명 나열 단톡


def classify_room_type(name: str) -> str:
    """방 이름 → 타입 분류.

    INTERNAL_BACKBONE  — 네노바 팀원만 있는 업무 백본 방 (수입/영업/현장/QC/전산)
    SUPPLIER_CHANNEL   — 특정 거래처 1개 ↔ 네노바 직통 채널
    PARTNER_CHANNEL    — 외부 협력사 (검역/방역 등)
    INTERNAL_PRIVATE   — 개인명 나열 사적 단톡
    MISC               — 기타/미분류 (예: 주님방)
    """
    n = name.strip()
    # 사적 단톡 (이름이 쉼표로 3명+ 나열)
    if n.count(",") >= 2:
        return "INTERNAL_PRIVATE"
    # 파트너 (외부 협력사)
    if any(k in n for k in PARTNER_KEYS):
        return "PARTNER_CHANNEL"
    # 내부 백본
    if any(k in n for k in INTERNAL_BACKBONE_KEYS):
        return "INTERNAL_BACKBONE"
    # 거래처 + 네노바 패턴 (거래처방)
    if "네노바" in n and any(c in n for c in "+&"):
        return "SUPPLIER_CHANNEL"
    # 거래처 이름만 (발주방, 백상 등)
    if any(k in n for k in SUPPLIER_ONLY_KEYS):
        return "SUPPLIER_CHANNEL"
    # "원예/화훼/플라워" 등 거래처 naming 패턴
    if any(k in n for k in ("원예", "화훼", "플라워", "꽃")) and "네노바" in n:
        return "SUPPLIER_CHANNEL"
    return "MISC"


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
    # 타입별 집계
    type_event = defaultdict(Counter)       # type -> event_type -> count
    type_total = Counter()                   # type -> total msg
    type_rooms: dict[str, set] = defaultdict(set)  # type -> {room, ...}
    type_msg_counts_by_room: dict[str, Counter] = defaultdict(Counter)  # type -> room -> count

    # 키워드 × 타입 분류 분포
    TRACE = ["불량", "차감", "추가", "취소", "확인", "부탁", "사진", "불가능", "검역"]
    keyword_type: dict[str, dict[str, Counter]] = {
        kw: defaultdict(Counter) for kw in TRACE
    }
    keyword_type_samples: dict[str, dict[str, list]] = {
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
            rtype = classify_room_type(room)
            type_rooms[rtype].add(room)

            for sender, time_str, content in iter_messages(delta):
                parsed = parse_message(content, room)
                et = parsed["event_type"]
                type_event[rtype][et] += 1
                type_total[rtype] += 1
                type_msg_counts_by_room[rtype][room] += 1

                for kw in TRACE:
                    if kw in content:
                        keyword_type[kw][rtype][et] += 1
                        samples = keyword_type_samples[kw][rtype]
                        if len(samples) < 6:
                            samples.append({
                                "room": room,
                                "sender": sender,
                                "event_type": et,
                                "excerpt": content.replace("\n", " | ")[:120],
                            })

    # 보고서 빌드
    report = {
        "_meta": {
            "총타입": len(type_rooms),
            "타입별_방수": {t: len(rs) for t, rs in type_rooms.items()},
            "타입별_방리스트": {t: sorted(rs) for t, rs in type_rooms.items()},
        },
        "타입별_총메시지": dict(type_total.most_common()),
        "타입별_이벤트분포": {
            t: [
                {"type": et, "count": n, "pct": round(n / type_total[t] * 100, 1)}
                for et, n in c.most_common()
            ]
            for t, c in type_event.items()
        },
        "키워드_타입별_분류": {},
        "키워드_타입별_샘플": {},
    }

    for kw in TRACE:
        per_type = {}
        for rtype, counter in keyword_type[kw].items():
            total = sum(counter.values())
            per_type[rtype] = {
                "총건수": total,
                "분포": [
                    {"type": et, "count": n, "pct": round(n / total * 100, 1)}
                    for et, n in counter.most_common()
                ],
            }
        report["키워드_타입별_분류"][kw] = per_type
        report["키워드_타입별_샘플"][kw] = {
            rtype: samples for rtype, samples in keyword_type_samples[kw].items()
        }

    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    # ─── 콘솔 요약 ───
    print("=" * 70)
    print("[방 성격 기준 분류 재진단]")
    print("=" * 70)

    print()
    print("[ 타입별 방 구성 ]")
    for t, rs in sorted(type_rooms.items(), key=lambda x: -type_total[x[0]]):
        print(f"\n  ■ {t} ({len(rs)}방, {type_total[t]:,}건)")
        for r in sorted(rs, key=lambda x: -type_msg_counts_by_room[t][x])[:15]:
            n = type_msg_counts_by_room[t][r]
            print(f"     {r[:35]:35s} {n:>6,}건")

    print()
    print("=" * 70)
    print("[ 타입별 이벤트 분포 (%) ]")
    print("=" * 70)
    all_events = sorted({et for c in type_event.values() for et in c})
    header = f"{'타입':<22s}" + "".join(f"{et[:8]:>9s}" for et in all_events)
    print(header)
    for t in sorted(type_event.keys(), key=lambda x: -type_total[x]):
        row = f"{t:<22s}"
        for et in all_events:
            n = type_event[t].get(et, 0)
            pct = n / type_total[t] * 100 if type_total[t] else 0
            row += f"{pct:>8.1f}%"
        print(row)

    print()
    print("=" * 70)
    print("[ 핵심 키워드가 타입별로 다르게 분류되는가? ]")
    print("=" * 70)
    for kw in ["불량", "차감", "추가", "취소", "부탁", "사진", "검역"]:
        print(f"\n▶ '{kw}'")
        for rtype, info in sorted(
            report["키워드_타입별_분류"][kw].items(), key=lambda x: -x[1]["총건수"]
        ):
            if info["총건수"] == 0:
                continue
            top3 = info["분포"][:3]
            top_str = ", ".join(f"{d['type']}:{d['pct']}%" for d in top3)
            print(f"  {rtype:<22s} ({info['총건수']:>5,}건)  {top_str}")

    print()
    print("=" * 70)
    print("[ 타입별 키워드 샘플 — 해석 차이 검증 ]")
    print("=" * 70)
    # '불량' 을 타입별로 비교
    print("\n▶ '불량' 키워드 타입별 실제 샘플 (해석이 다른가?)")
    for rtype, samples in report["키워드_타입별_샘플"]["불량"].items():
        print(f"\n  ▪ {rtype}")
        for s in samples[:3]:
            print(f"    [{s['event_type']}] [{s['room'][:20]}] {s['excerpt']}")

    print("\n▶ '사진' 키워드 타입별 실제 샘플")
    for rtype, samples in report["키워드_타입별_샘플"]["사진"].items():
        print(f"\n  ▪ {rtype}")
        for s in samples[:3]:
            print(f"    [{s['event_type']}] [{s['room'][:20]}] {s['excerpt']}")

    print()
    print(f"[OK] 저장: {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
