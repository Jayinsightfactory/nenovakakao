"""
대화 흐름 분석 스크립트
- 발신자간 응답 패턴
- 대화 스레드 추출
- 방간 정보 전달
- 무응답 구간
"""
import json, re, sys
from collections import defaultdict
from datetime import datetime, timedelta

INPUT = r"C:\Users\USER\nenova_agent\data\analysis_dump.json"
OUTPUT = r"C:\Users\USER\nenova_agent\data\analysis_flow.json"

# ── 시각 파싱 ──
def parse_time(t: str):
    """'오전 10:19' / '오후 3:46' → datetime (날짜는 임의 고정)"""
    m = re.match(r"(오전|오후)\s*(\d{1,2}):(\d{2})", t.strip())
    if not m:
        return None
    ampm, h, mi = m.group(1), int(m.group(2)), int(m.group(3))
    if ampm == "오후" and h != 12:
        h += 12
    if ampm == "오전" and h == 12:
        h = 0
    return datetime(2026, 4, 11, h, mi)

def fmt_time(dt):
    if dt is None:
        return ""
    ampm = "오전" if dt.hour < 12 else "오후"
    h = dt.hour % 12
    if h == 0:
        h = 12
    return f"{ampm} {h}:{dt.minute:02d}"

def diff_minutes(t1, t2):
    """t2 - t1 in minutes, None if can't compute"""
    d1, d2 = parse_time(t1), parse_time(t2)
    if d1 is None or d2 is None:
        return None
    delta = (d2 - d1).total_seconds() / 60
    return round(delta, 1)

# ── 데이터 로드 ──
with open(INPUT, "r", encoding="utf-8") as f:
    data = json.load(f)

logs = data["logs"]
biz = data["biz"]
issues = data["issues"]

# ═══════════════════════════════════════════
# 1. 발신자간 응답 패턴
# ═══════════════════════════════════════════
room_logs = defaultdict(list)
for l in logs:
    room_logs[l["방이름"]].append(l)

response_patterns = []
room_stats = {}

for room, msgs in room_logs.items():
    turns = []  # (sender_from, sender_to, time_from, time_to, delta_min)
    for i in range(1, len(msgs)):
        prev, curr = msgs[i - 1], msgs[i]
        if prev["발신자"] != curr["발신자"]:
            delta = diff_minutes(prev["시각"], curr["시각"])
            if delta is not None and delta >= 0:
                turns.append({
                    "from": prev["발신자"],
                    "to": curr["발신자"],
                    "time_from": prev["시각"],
                    "time_to": curr["시각"],
                    "delta_min": delta,
                    "방이름": room,
                })
    if turns:
        deltas = [t["delta_min"] for t in turns]
        avg = round(sum(deltas) / len(deltas), 1)
        room_stats[room] = {
            "전환횟수": len(turns),
            "평균응답시간(분)": avg,
            "최소": min(deltas),
            "최대": max(deltas),
            "중앙값": round(sorted(deltas)[len(deltas)//2], 1),
        }
        response_patterns.extend(turns)

# 발신자 쌍별 평균 응답시간
pair_map = defaultdict(list)
for t in response_patterns:
    pair_map[(t["from"], t["to"])].append(t["delta_min"])
pair_stats = []
for (a, b), deltas in sorted(pair_map.items(), key=lambda x: -len(x[1])):
    pair_stats.append({
        "발신자A": a,
        "응답자B": b,
        "대화전환횟수": len(deltas),
        "평균응답시간(분)": round(sum(deltas)/len(deltas), 1),
        "최소(분)": min(deltas),
        "최대(분)": max(deltas),
    })

# ═══════════════════════════════════════════
# 2. 대화 스레드 추출
# ═══════════════════════════════════════════
# biz 이벤트를 (방이름, 차수, 품목) 기준으로 그룹
thread_key_fn = lambda b: (b["방이름"], b["차수"], b["품목"])
thread_map = defaultdict(list)
for b in biz:
    if b["차수"] or b["품목"]:
        thread_map[thread_key_fn(b)].append(b)

threads = []
for (room, cha, item), events in thread_map.items():
    if not cha and not item:
        continue
    participants = list(set(e["발신자"] for e in events))
    times = [e["시각"] for e in events]
    event_types = list(set(e["이벤트타입"] for e in events))

    # 해결 여부: 관련 이슈 확인
    related_issue_ids = set()
    for e in events:
        if e["연관이벤트ID"]:
            related_issue_ids.add(e["연관이벤트ID"])
        related_issue_ids.add(e["이벤트ID"])

    resolved = "정보없음"
    for iss in issues:
        if iss["연관이벤트ID"] in related_issue_ids:
            resolved = iss["결과"]
            break

    # 시간 범위
    parsed = [parse_time(t) for t in times]
    parsed_valid = [p for p in parsed if p]
    duration_min = None
    if len(parsed_valid) >= 2:
        duration_min = round((max(parsed_valid) - min(parsed_valid)).total_seconds() / 60, 1)

    threads.append({
        "방이름": room,
        "차수": cha,
        "품목": item,
        "메시지수": len(events),
        "참여자수": len(participants),
        "참여자": participants,
        "이벤트타입": event_types,
        "시작시각": times[0] if times else "",
        "종료시각": times[-1] if times else "",
        "지속시간(분)": duration_min,
        "해결여부": resolved,
    })

threads.sort(key=lambda x: -x["메시지수"])

# ═══════════════════════════════════════════
# 3. 방간 정보 전달
# ═══════════════════════════════════════════
# (차수, 품목) → [(방, 시각)] 으로 그룹
topic_rooms = defaultdict(list)
for b in biz:
    if b["차수"] and b["품목"]:
        topic_rooms[(b["차수"], b["품목"])].append({
            "방이름": b["방이름"],
            "시각": b["시각"],
            "발신자": b["발신자"],
            "이벤트타입": b["이벤트타입"],
        })

cross_room = []
for (cha, item), entries in topic_rooms.items():
    rooms_set = set(e["방이름"] for e in entries)
    if len(rooms_set) < 2:
        continue
    # 방별 최초 언급 시각
    room_first = {}
    for e in entries:
        r = e["방이름"]
        pt = parse_time(e["시각"])
        if pt and (r not in room_first or pt < room_first[r]["parsed"]):
            room_first[r] = {"시각": e["시각"], "parsed": pt, "발신자": e["발신자"]}

    sorted_rooms = sorted(room_first.items(), key=lambda x: x[1]["parsed"])
    origin_room = sorted_rooms[0][0]
    origin_time = sorted_rooms[0][1]

    transfers = []
    for rm, info in sorted_rooms[1:]:
        delta = round((info["parsed"] - origin_time["parsed"]).total_seconds() / 60, 1)
        transfers.append({
            "도착방": rm,
            "도착시각": info["시각"],
            "전달자": info["발신자"],
            "전달소요(분)": delta,
        })

    cross_room.append({
        "차수": cha,
        "품목": item,
        "출발방": origin_room,
        "출발시각": origin_time["시각"],
        "최초발신자": origin_time["발신자"],
        "전달경로": transfers,
        "관련방수": len(rooms_set),
    })

cross_room.sort(key=lambda x: -x["관련방수"])

# ═══════════════════════════════════════════
# 4. 무응답 구간
# ═══════════════════════════════════════════
# INQUIRY 타입 biz 이벤트 후 같은 방에서 5분 이상 응답 없는 경우
inquiry_events = [b for b in biz if b["이벤트타입"] == "INQUIRY"]
no_response = []

for inq in inquiry_events:
    room = inq["방이름"]
    sender = inq["발신자"]
    inq_time = parse_time(inq["시각"])
    if not inq_time:
        continue

    # 해당 방의 로그에서 이 시각 이후 다른 발신자의 첫 메시지 찾기
    room_msgs = room_logs.get(room, [])
    next_response = None
    for msg in room_msgs:
        mt = parse_time(msg["시각"])
        if mt and mt > inq_time and msg["발신자"] != sender:
            next_response = msg
            break

    if next_response is None:
        # 끝까지 응답 없음
        no_response.append({
            "방이름": room,
            "문의자": sender,
            "문의시각": inq["시각"],
            "원문요약": inq["원문요약"],
            "응답자": "",
            "응답시각": "",
            "대기시간(분)": "무응답",
            "차수": inq.get("차수", ""),
            "품목": inq.get("품목", ""),
        })
    else:
        rt = parse_time(next_response["시각"])
        delta = round((rt - inq_time).total_seconds() / 60, 1)
        if delta >= 5:
            no_response.append({
                "방이름": room,
                "문의자": sender,
                "문의시각": inq["시각"],
                "원문요약": inq["원문요약"],
                "응답자": next_response["발신자"],
                "응답시각": next_response["시각"],
                "대기시간(분)": delta,
                "차수": inq.get("차수", ""),
                "품목": inq.get("품목", ""),
            })

# ═══════════════════════════════════════════
# 요약 통계
# ═══════════════════════════════════════════
summary = {
    "총_로그수": len(logs),
    "총_비즈이벤트수": len(biz),
    "총_이슈수": len(issues),
    "방_수": len(set(l["방이름"] for l in logs)),
    "발신자_수": len(set(l["발신자"] for l in logs)),
    "대화전환_총횟수": len(response_patterns),
    "스레드_수": len(threads),
    "방간전달_수": len(cross_room),
    "무응답구간_수": len(no_response),
    "방별_응답통계": room_stats,
}

# ═══════════════════════════════════════════
# 출력
# ═══════════════════════════════════════════
result = {
    "분석일시": "2026-04-11",
    "요약": summary,
    "1_발신자간_응답패턴": {
        "방별통계": room_stats,
        "발신자쌍별통계": pair_stats[:30],  # top 30
        "전체전환_샘플": sorted(response_patterns, key=lambda x: -x["delta_min"])[:20],
    },
    "2_대화스레드": threads,
    "3_방간_정보전달": cross_room,
    "4_무응답구간": no_response,
}

with open(OUTPUT, "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False, indent=2)

print(f"분석 완료 → {OUTPUT}")
print(f"요약: {json.dumps(summary, ensure_ascii=False, indent=2)}")
