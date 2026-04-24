"""
분류 진단 리포트 생성 — Phase 2 1단계.

data/collected_data.jsonl 의 모든 메시지를 core.gsheet_sync.parse_message 로
분류하고, 방별 × 이벤트타입 분포와 핵심 키워드의 방별 분류 결과를
data/classification_audit.json 에 저장. 2단계(방별 오버라이드 스키마)의
근거 데이터가 된다.

실행:
  python tools/classification_audit.py
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
OUT = ROOT / "data" / "classification_audit.json"

# 집계에서 확인할 핵심 키워드 — 방에 따라 의미가 달라질 수 있는 애매한 단어
# (room_specific_analysis_design.md 근거).
KEY_TRACE = ["불량", "차감", "추가", "취소", "확인", "대체", "출고", "입고"]

DIVIDER = re.compile(r"^-{5,}.*-{5,}$")
DELETED = "메시지가 삭제되었습니다."


def iter_messages(delta: str):
    """delta 텍스트에서 (sender, time_str, content) 튜플 생성.

    카톡 저장 포맷은 `[발신자] [시각] 첫줄` 뒤에 줄바꿈으로 이어지는 본문이
    따라올 수 있음. MSG_PATTERN 은 첫 줄만 인식하므로 이어지는 줄을 current
    message에 붙여 완전한 content 를 만든다.
    """
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
    if not COLLECTED.exists():
        print(f"[ERR] {COLLECTED} 없음", flush=True)
        return

    room_event: dict[str, Counter] = defaultdict(Counter)
    event_total = Counter()
    room_total = Counter()

    # keyword -> room -> [(event_type, excerpt), ...]
    keyword_trace: dict[str, dict[str, list[dict]]] = {
        kw: defaultdict(list) for kw in KEY_TRACE
    }

    samples: dict[str, list[dict]] = defaultdict(list)  # event_type -> samples
    info_samples: dict[str, list[dict]] = defaultdict(list)  # room -> INFO samples
    seq_missing: list[dict] = []  # 차수 미추출 (ORDER/DEFECT 에서)
    supplier_hits = Counter()
    product_hits = Counter()

    total_files = 0
    total_msgs = 0
    total_lines = 0

    with open(COLLECTED, encoding="utf-8") as f:
        for raw in f:
            raw = raw.strip()
            if not raw:
                continue
            total_files += 1
            try:
                rec = json.loads(raw)
            except json.JSONDecodeError:
                continue
            room = rec.get("room_name", "") or "(unknown)"
            delta = rec.get("delta", "") or ""
            total_lines += len(delta.splitlines())

            for sender, time_str, content in iter_messages(delta):
                total_msgs += 1
                parsed = parse_message(content, room)
                et = parsed["event_type"]

                room_event[room][et] += 1
                event_total[et] += 1
                room_total[room] += 1

                if parsed.get("supplier"):
                    supplier_hits[parsed["supplier"]] += 1
                if parsed.get("product"):
                    product_hits[parsed["product"]] += 1

                for kw in KEY_TRACE:
                    if kw in content:
                        keyword_trace[kw][room].append({
                            "event_type": et,
                            "sender": sender,
                            "time": time_str,
                            "excerpt": content.replace("\n", " ⏎ ")[:100],
                            "summary": parsed.get("summary", ""),
                        })

                if len(samples[et]) < 8:
                    samples[et].append({
                        "room": room,
                        "sender": sender,
                        "time": time_str,
                        "content": content.replace("\n", " ⏎ ")[:180],
                        "parsed": {k: v for k, v in parsed.items() if v},
                    })

                if et == "INFO" and len(info_samples[room]) < 5:
                    info_samples[room].append({
                        "sender": sender,
                        "time": time_str,
                        "content": content.replace("\n", " ⏎ ")[:150],
                    })

                if et in ("DEFECT", "ORDER_CHANGE") and not parsed.get("sequence"):
                    if len(seq_missing) < 30:
                        seq_missing.append({
                            "room": room,
                            "event_type": et,
                            "sender": sender,
                            "content": content.replace("\n", " ⏎ ")[:150],
                        })

    # keyword × room 분류 요약
    keyword_room_summary: dict[str, dict] = {}
    for kw, rooms in keyword_trace.items():
        per_room = {}
        for room, cases in rooms.items():
            type_counter = Counter(c["event_type"] for c in cases)
            per_room[room] = {
                "총건수": len(cases),
                "분류분포": dict(type_counter.most_common()),
                "샘플": cases[:5],
            }
        keyword_room_summary[kw] = dict(
            sorted(per_room.items(), key=lambda x: -x[1]["총건수"])
        )

    # 방별 이벤트 분포 → 가장 두드러진 분류 3개만
    room_event_summary = {}
    for room, counter in sorted(room_event.items(), key=lambda x: -sum(x[1].values())):
        total = sum(counter.values())
        top = counter.most_common()
        room_event_summary[room] = {
            "총메시지": total,
            "분포": [
                {"type": et, "count": n, "pct": round(n / total * 100, 1)}
                for et, n in top
            ],
        }

    report = {
        "_meta": {
            "source": str(COLLECTED.relative_to(ROOT)),
            "파일수": total_files,
            "원본_delta_라인": total_lines,
            "파싱_메시지수": total_msgs,
            "생성": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
        },
        "이벤트타입_전체분포": [
            {"type": et, "count": n, "pct": round(n / total_msgs * 100, 1)}
            for et, n in event_total.most_common()
        ],
        "방별_이벤트분포": room_event_summary,
        "키워드_방별_분류": keyword_room_summary,
        "이벤트타입_샘플": dict(samples),
        "INFO_샘플_방별": {
            r: s for r, s in sorted(info_samples.items(), key=lambda x: -len(x[1]))
        },
        "차수_미추출_샘플": seq_missing,
        "거래처_매칭_상위": supplier_hits.most_common(20),
        "품목_매칭_상위": product_hits.most_common(20),
    }

    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    # ─── 콘솔 요약 ───
    print("=" * 60)
    print(f"[분류 진단 리포트]")
    print("=" * 60)
    print(f"파일: {total_files}개 | 파싱된 메시지: {total_msgs}건")
    print()
    print("[ 이벤트타입 전체 분포 ]")
    for et, n in event_total.most_common():
        pct = n / total_msgs * 100
        bar = "#" * int(pct / 2)
        print(f"  {et:14s} {n:5d}  ({pct:5.1f}%) {bar}")

    print()
    print("[ 방별 총 메시지 (상위 15) ]")
    for room, n in room_total.most_common(15):
        top = room_event[room].most_common(3)
        top_str = " / ".join(f"{e}:{c}" for e, c in top)
        print(f"  {room[:30]:30s} {n:5d}   → {top_str}")

    print()
    print("[ '불량' 키워드의 방별 분류 (Top 10 방) ]")
    buljr = keyword_trace["불량"]
    sorted_rooms = sorted(buljr.items(), key=lambda x: -len(x[1]))[:10]
    for room, cases in sorted_rooms:
        type_counter = Counter(c["event_type"] for c in cases)
        total = len(cases)
        print(f"  {room[:30]:30s} ({total:3d}건): {dict(type_counter.most_common(3))}")

    print()
    print("[ '추가' 키워드의 방별 분류 (Top 10 방) ]")
    add = keyword_trace["추가"]
    for room, cases in sorted(add.items(), key=lambda x: -len(x[1]))[:10]:
        type_counter = Counter(c["event_type"] for c in cases)
        total = len(cases)
        print(f"  {room[:30]:30s} ({total:3d}건): {dict(type_counter.most_common(3))}")

    print()
    print("[ '취소' 키워드의 방별 분류 ]")
    cancel = keyword_trace["취소"]
    for room, cases in sorted(cancel.items(), key=lambda x: -len(x[1]))[:10]:
        type_counter = Counter(c["event_type"] for c in cases)
        total = len(cases)
        print(f"  {room[:30]:30s} ({total:3d}건): {dict(type_counter.most_common(3))}")

    print()
    print("[ INFO (분류 실패) 상위 방 ]")
    info_counts = [(r, room_event[r].get("INFO", 0)) for r in room_event]
    for room, n in sorted(info_counts, key=lambda x: -x[1])[:10]:
        if n == 0:
            break
        pct = n / room_total[room] * 100
        print(f"  {room[:30]:30s} {n:4d}건 ({pct:5.1f}% of 방)")

    print()
    print(f"[OK] 전체 리포트 저장: {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
