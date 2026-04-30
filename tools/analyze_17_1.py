"""17-1차 차수 생애주기 전용 분석."""
from __future__ import annotations
import json, re, sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(r"C:\Users\USER\nenova_agent")
sys.path.insert(0, str(ROOT))

from core.gsheet_sync import parse_message  # noqa: E402
from tools.classification_audit import iter_messages  # noqa: E402

COLLECTED = ROOT / "data" / "collected_data.jsonl"

# 17-1차 매칭 패턴 — 17-1, 17-1차, 17 - 1, 17.1 등 변형 허용
SEQ_RE = re.compile(r"(?:^|[^0-9])17\s*[-.–]\s*1(?!\d)")

# 시각 파싱 ([오전 9:30] / [오후 2:15])
TIME_RE = re.compile(r"\[(오전|오후)\s+(\d+):(\d+)\]")


def parse_time(time_str: str, base_date: str | None) -> str:
    """[오전 9:30] + base_date(YYYY-MM-DD) → 'YYYY-MM-DD HH:MM' or just time."""
    m = TIME_RE.match(f"[{time_str}]" if not time_str.startswith("[") else time_str)
    if not m:
        m = TIME_RE.search(time_str)
    if not m:
        return time_str
    ampm, h, mm = m.group(1), int(m.group(2)), int(m.group(3))
    if ampm == "오후" and h != 12:
        h += 12
    if ampm == "오전" and h == 12:
        h = 0
    base = base_date or "????-??-??"
    return f"{base} {h:02d}:{mm:02d}"


def extract_record_date(record: dict) -> str | None:
    """timestamp(2026-04-22T...) 또는 delta 첫 줄(--- 2026년 4월 22일 ---)에서 날짜."""
    ts = record.get("timestamp", "")
    if ts and len(ts) >= 10 and ts[4] == "-":
        return ts[:10]
    delta = record.get("delta", "")
    m = re.search(r"(20\d{2})\s*[년-]\s*(\d{1,2})\s*[월-]\s*(\d{1,2})", delta)
    if m:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    return None


def main() -> None:
    hits = []  # (timestamp_iso, room, sender, time_str, content, parsed)

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
            base_date = extract_record_date(rec)

            # delta 안의 날짜 구분선 따라가기
            current_date = base_date
            for line in delta.splitlines():
                m = re.match(r"^-+\s*(20\d{2})[년\s.-]+(\d{1,2})[월\s.-]+(\d{1,2}).*-+$", line.strip())
                if m:
                    current_date = f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"

            # iter_messages 로 메시지 단위 추출
            for sender, time_str, content in iter_messages(delta):
                if not SEQ_RE.search(content):
                    continue
                # content 안의 날짜 구분선이 직전에 있었는지 추적이 어려우므로
                # 우선 base_date 사용 (보수적)
                ts = parse_time(time_str, base_date)
                parsed = parse_message(content, room)
                hits.append({
                    "ts": ts,
                    "room": room,
                    "sender": sender,
                    "time_str": time_str,
                    "content": content.replace("\n", " ⏎ ").strip(),
                    "event_type": parsed["event_type"],
                    "supplier": parsed.get("supplier", ""),
                    "product": parsed.get("product", ""),
                    "summary": parsed.get("summary", "")[:80],
                })

    print(f"[17-1차] 총 매칭 메시지: {len(hits)}건")

    # 정렬 (timestamp 문자열 정렬 → 부정확하지만 OK)
    hits.sort(key=lambda h: (h["ts"], h["room"]))

    if not hits:
        print("매칭 없음.")
        return

    first = hits[0]
    last = hits[-1]
    print()
    print("=" * 70)
    print("[1] 시작과 끝")
    print("=" * 70)
    print(f"  첫 언급: {first['ts']}  방={first['room']}  발신자={first['sender']}")
    print(f"           event={first['event_type']}")
    print(f"           내용: {first['content'][:160]}")
    print()
    print(f"  마지막:  {last['ts']}  방={last['room']}  발신자={last['sender']}")
    print(f"           event={last['event_type']}")
    print(f"           내용: {last['content'][:160]}")

    # 기간 계산
    try:
        d1 = datetime.fromisoformat(first["ts"].replace(" ", "T"))
        d2 = datetime.fromisoformat(last["ts"].replace(" ", "T"))
        span_days = (d2 - d1).days
        print(f"  span: {span_days}일 ({span_days/7:.1f}주)")
    except Exception:
        print(f"  span 계산 실패")

    # [3] 방별 카운트
    print()
    print("=" * 70)
    print("[3] 방별 카운트")
    print("=" * 70)
    room_cnt = Counter(h["room"] for h in hits)
    for room, n in room_cnt.most_common():
        print(f"  {room[:38]:38s} {n:5d}")

    # 방 흐름 (최초 등장 순)
    print()
    print("[3-2] 방별 최초 등장 순서 (TOP 10)")
    first_seen = {}
    for h in hits:
        if h["room"] not in first_seen:
            first_seen[h["room"]] = h["ts"]
    for room, ts in sorted(first_seen.items(), key=lambda x: x[1])[:10]:
        print(f"  {ts}  {room}")

    # [4] 이벤트 분포
    print()
    print("=" * 70)
    print("[4] event_type 분포")
    print("=" * 70)
    et_cnt = Counter(h["event_type"] for h in hits)
    for et, n in et_cnt.most_common():
        pct = n / len(hits) * 100
        print(f"  {et:16s} {n:5d} ({pct:5.1f}%)")

    # DEFECT 발췌
    defects = [h for h in hits if h["event_type"] == "DEFECT"]
    print()
    print(f"[4-2] DEFECT 메시지 (전체 {len(defects)}건, 최대 8개 발췌)")
    for h in defects[:8]:
        print(f"  [{h['ts']}] {h['room']} / {h['sender']}: {h['content'][:140]}")

    # [5] 발신자 TOP
    print()
    print("=" * 70)
    print("[5] 발신자 TOP")
    print("=" * 70)
    sender_cnt = Counter(h["sender"] for h in hits)
    for s, n in sender_cnt.most_common(10):
        print(f"  {s[:30]:30s} {n:5d}")

    # [6] 거래처 / 품목
    print()
    print("=" * 70)
    print("[6] 거래처 / 품목 매칭 상위")
    print("=" * 70)
    sup = Counter(h["supplier"] for h in hits if h["supplier"])
    prd = Counter(h["product"] for h in hits if h["product"])
    print("거래처:")
    for s, n in sup.most_common(10):
        print(f"  {s[:30]:30s} {n:5d}")
    print("품목:")
    for p, n in prd.most_common(10):
        print(f"  {p[:30]:30s} {n:5d}")

    # 본문에서 직접 단어 빈도 (품종/거래처 보조)
    print()
    print("[6-2] 본문 단어 빈도 (간이 품종 추출)")
    flower_kw = ["카네이션", "장미", "거베라", "리시안서스", "수국", "라넌", "튤립",
                 "프리지아", "데이지", "스토크", "백합", "국화", "안개", "스프레이",
                 "알스트로", "라일락", "작약", "달리아", "히야신스", "아이리스"]
    fc = Counter()
    for h in hits:
        for kw in flower_kw:
            if kw in h["content"]:
                fc[kw] += 1
    for kw, n in fc.most_common(10):
        print(f"  {kw:14s} {n:5d}")

    # 시각순 의미 발췌 — 방 다양성 우선
    print()
    print("=" * 70)
    print("[발췌] 의미 있는 메시지 시각순 (방 다양성 우선)")
    print("=" * 70)
    seen_rooms = set()
    picks = []
    for h in hits:
        if h["event_type"] == "INFO":
            continue
        if h["room"] in seen_rooms and len(picks) >= 5:
            continue
        seen_rooms.add(h["room"])
        picks.append(h)
        if len(picks) >= 10:
            break
    for h in picks:
        print(f"  [{h['ts']}] [{h['event_type']:14s}] {h['room'][:18]} / {h['sender']}")
        print(f"      {h['content'][:160]}")
        print()


if __name__ == "__main__":
    main()
