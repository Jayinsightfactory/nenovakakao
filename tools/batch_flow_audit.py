"""C: 차수 생애주기 분석 (BLI).

각 차수가 어떤 방을 어떤 순서로 거쳤는지 추적.
data/learning/cross_room_map.json 의 선험적 업무_흐름과 실데이터 흐름을 대조.

출력: data/batch_flow_analysis.json + 콘솔 요약
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core.gsheet_sync import MSG_PATTERN, parse_message  # noqa: E402
from core.room_types import classify_room_type  # noqa: E402
from core.sender_aliases import normalize_sender  # noqa: E402

COLLECTED = ROOT / "data" / "collected_data.jsonl"
CROSS_MAP = ROOT / "data" / "learning" / "cross_room_map.json"
OUT = ROOT / "data" / "batch_flow_analysis.json"

DIVIDER = re.compile(r"^-{5,}\s*(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일[^-]*-{5,}$")
DELETED = "메시지가 삭제되었습니다."

# 차수 패턴: "15-1차", "14-2", "15차" 등. 메시지 시작부에서 찾음 (문맥 있을 때)
# 내부 숫자 오탐 방지 — 반드시 숫자로 시작하고 "차" 혹은 "-숫자"가 뒤따르는 형태
SEQ_IN_TEXT = re.compile(r"(?<![\d.])(\d{1,3})[-/](\d{1,2})(?!\d)(?:\s*차)?|\b(\d{1,3})\s*차\b")
TIME_RE = re.compile(r"오전\s*(\d{1,2}):(\d{2})|오후\s*(\d{1,2}):(\d{2})")


def parse_kakao_time(date_iso: str, time_str: str) -> str | None:
    """카톡 '오전/오후 H:MM' 을 ISO 'YYYY-MM-DDTHH:MM' 으로."""
    m = TIME_RE.search(time_str)
    if not m:
        return None
    if m.group(1):  # 오전
        h, mi = int(m.group(1)), int(m.group(2))
        if h == 12:
            h = 0
    else:
        h, mi = int(m.group(3)), int(m.group(4))
        if h < 12:
            h += 12
    return f"{date_iso}T{h:02d}:{mi:02d}"


def iter_messages_with_date(delta: str):
    """(date_iso, sender, time_str, content) 튜플 생성.
    날짜 구분선을 파싱해 같은 날짜를 후속 메시지에 붙여줌.
    """
    current: list | None = None
    current_date: str | None = None
    for line in delta.splitlines():
        m = DIVIDER.match(line.strip())
        if m:
            y, mo, d = m.group(1), int(m.group(2)), int(m.group(3))
            current_date = f"{y}-{mo:02d}-{d:02d}"
            if current is not None:
                yield current
                current = None
            continue
        m = MSG_PATTERN.match(line.strip())
        if m:
            if current is not None:
                yield current
            sender, time_str, first = m.groups()
            current = [current_date, sender, time_str, first]
            continue
        if current is None:
            continue
        stripped = line.rstrip()
        if not stripped:
            current[3] += "\n"
            continue
        if stripped == DELETED:
            continue
        current[3] += "\n" + stripped
    if current is not None:
        yield current


def extract_sequence(content: str) -> str | None:
    """메시지에서 차수(e.g., 15-1) 추출. 첫 줄 / 명시적 패턴만."""
    # 복수 줄 중 앞 2줄까지만 탐색 (본문 내 숫자 오탐 방지)
    candidate = "\n".join(content.splitlines()[:2])
    m = SEQ_IN_TEXT.search(candidate)
    if not m:
        return None
    if m.group(1) and m.group(2):
        main = int(m.group(1))
        sub = int(m.group(2))
        # 차수는 대체로 1~100. 1~100 x 1~9 허용
        if 1 <= main <= 100 and 1 <= sub <= 9:
            return f"{main}-{sub}"
    elif m.group(3):
        main = int(m.group(3))
        if 1 <= main <= 100:
            return f"{main}"
    return None


def main() -> None:
    cross = {}
    if CROSS_MAP.exists():
        cross = json.loads(CROSS_MAP.read_text(encoding="utf-8"))

    # batch -> events (list of dict)
    batches: dict[str, list[dict]] = defaultdict(list)
    # batch -> rooms visited (ordered)
    batch_rooms: dict[str, list[tuple[str, str]]] = defaultdict(list)
    # 전체 방-방 전이
    room_transitions: Counter = Counter()

    processed = 0
    total_msgs = 0

    with open(COLLECTED, encoding="utf-8") as f:
        for raw in f:
            raw = raw.strip()
            if not raw:
                continue
            processed += 1
            try:
                rec = json.loads(raw)
            except json.JSONDecodeError:
                continue
            room = rec.get("room_name", "") or "(unknown)"
            delta = rec.get("delta", "") or ""
            room_type = classify_room_type(room)

            for date_iso, sender, time_str, content in iter_messages_with_date(delta):
                if not date_iso:
                    continue
                total_msgs += 1
                seq = extract_sequence(content)
                if not seq:
                    continue
                iso_ts = parse_kakao_time(date_iso, time_str) or date_iso
                parsed = parse_message(content, room)
                batches[seq].append({
                    "ts": iso_ts,
                    "room": room,
                    "room_type": room_type,
                    "sender": normalize_sender(sender),
                    "event_type": parsed["event_type"],
                    "summary": parsed["summary"][:80],
                })

    # 차수별 정렬 및 방 전이 추출
    for seq, events in batches.items():
        events.sort(key=lambda e: e["ts"])
        prev_room = None
        for e in events:
            if prev_room and prev_room != e["room"]:
                room_transitions[(prev_room, e["room"])] += 1
            prev_room = e["room"]
        # 방 첫 등장 순서만 기록
        seen = {}
        for e in events:
            if e["room"] not in seen:
                seen[e["room"]] = e["ts"]
        batch_rooms[seq] = sorted(seen.items(), key=lambda x: x[1])

    # 상위 차수 분석 (이벤트 수 기준)
    top_batches = sorted(batches.items(), key=lambda x: -len(x[1]))[:30]

    # 선험적 업무 흐름 vs 실데이터 검증
    defined_flows = cross.get("업무_흐름", []) or []
    flow_validation = []
    for flow in defined_flows:
        flow_rooms = flow.get("관련_방", [])
        # 실데이터에서 이 방들이 같은 차수에 등장한 비율
        hits = 0
        sample_batches = []
        for seq, rooms in batch_rooms.items():
            room_names = [r[0] for r in rooms]
            if sum(1 for fr in flow_rooms if any(fr in rn for rn in room_names)) >= 2:
                hits += 1
                if len(sample_batches) < 3:
                    sample_batches.append(seq)
        flow_validation.append({
            "흐름_이름": flow.get("흐름_이름"),
            "관련_방": flow_rooms,
            "실제_적중_차수": hits,
            "샘플_차수": sample_batches,
        })

    # 실데이터 기반 "전형적 흐름" 추출
    top_transitions = room_transitions.most_common(15)

    report = {
        "_meta": {
            "분석_파일수": processed,
            "파싱_메시지": total_msgs,
            "차수_감지_메시지": sum(len(v) for v in batches.values()),
            "고유차수_수": len(batches),
        },
        "선험_흐름_검증": flow_validation,
        "실제_방_전이_top15": [
            {"from": f, "to": t, "count": c} for (f, t), c in top_transitions
        ],
        "차수별_상세_top30": [
            {
                "차수": seq,
                "이벤트수": len(events),
                "첫등장": events[0]["ts"],
                "마지막": events[-1]["ts"],
                "기간": str(_span(events[0]["ts"], events[-1]["ts"])),
                "방별_이벤트수": dict(Counter(e["room"] for e in events).most_common()),
                "방_방문_순서": [{"room": r, "첫등장": ts} for r, ts in batch_rooms[seq]],
                "이벤트_분포": dict(Counter(e["event_type"] for e in events).most_common()),
                "발신자_top5": Counter(e["sender"] for e in events).most_common(5),
            }
            for seq, events in top_batches
        ],
    }

    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    # ─── 콘솔 요약 ───
    print()
    print("=" * 72)
    print("[C: 차수 생애주기 분석 (BLI)]")
    print("=" * 72)
    print(f"고유 차수: {len(batches)}개  |  차수 감지 메시지: {sum(len(v) for v in batches.values()):,}건")

    print()
    print("[ 선험적 업무흐름 vs 실데이터 검증 ]")
    for fv in flow_validation:
        print(f"\n  ▪ {fv['흐름_이름'][:60]}")
        print(f"     관련 방: {', '.join(fv['관련_방'])}")
        print(f"     실제 적중 차수: {fv['실제_적중_차수']:3d}  샘플: {fv['샘플_차수']}")

    print()
    print("[ 실제 방-방 전이 Top 15 (데이터 기반 흐름) ]")
    for (f, t), c in top_transitions:
        print(f"  {f[:28]:28s}  →  {t[:28]:28s}  {c:>4d}")

    print()
    print("[ 이벤트 많은 차수 Top 10 ]")
    for seq, events in top_batches[:10]:
        rooms_order = batch_rooms[seq]
        chain = " → ".join(r[:10] for r, _ in rooms_order[:5])
        print(f"  {seq:>6s}  {len(events):>5d}건  {chain}")

    # 특정 차수 하나 상세
    if top_batches:
        seq, events = top_batches[0]
        print()
        print(f"[ 샘플 차수 '{seq}' 상세 흐름 ]")
        print(f"  기간: {events[0]['ts']} → {events[-1]['ts']}")
        print(f"  방문 방 순서:")
        for r, ts in batch_rooms[seq][:8]:
            print(f"    {ts}  {r}")

    print()
    print(f"[OK] 저장: {OUT.relative_to(ROOT)}")


def _span(ts_a: str, ts_b: str) -> str:
    try:
        a = datetime.fromisoformat(ts_a)
        b = datetime.fromisoformat(ts_b)
        delta = b - a
        days = delta.days
        hours = delta.seconds // 3600
        return f"{days}일 {hours}시간"
    except Exception:
        return ""


if __name__ == "__main__":
    main()
