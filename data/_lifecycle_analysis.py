"""주문 라이프사이클 분석 스크립트"""
import json, re
from collections import Counter, defaultdict
from datetime import datetime

INPUT = r"C:\Users\USER\nenova_agent\data\analysis_dump.json"
OUTPUT = r"C:\Users\USER\nenova_agent\data\analysis_lifecycle.json"

with open(INPUT, "r", encoding="utf-8") as f:
    data = json.load(f)

logs = data["logs"]
biz = data["biz"]
issues = data["issues"]

# ── Helper: normalize 차수 to string ──
def norm_chasu(v):
    if v is None or v == "":
        return ""
    return str(v).strip()

# ── Helper: parse time for sorting (오전/오후 HH:MM) ──
def parse_time(t):
    if not t or not isinstance(t, str):
        return 0
    m = re.match(r"(오전|오후)\s*(\d{1,2}):(\d{2})", str(t))
    if not m:
        return 0
    period, h, mi = m.group(1), int(m.group(2)), int(m.group(3))
    if period == "오후" and h != 12:
        h += 12
    elif period == "오전" and h == 12:
        h = 0
    return h * 60 + mi

# ── Helper: extract 차수 from text ──
def extract_chasu_from_text(text):
    if not text or not isinstance(text, str):
        return []
    # Match patterns like 14-1, 15-1, 14차, 15-2차 etc
    found = re.findall(r'(\d{1,3}(?:-\d)?)\s*차?', text)
    return [f for f in found if f]

# ── Helper: extract 품목 from text ──
ITEMS = ["카네이션", "수국", "장미"]
def extract_pumok_from_text(text):
    if not text or not isinstance(text, str):
        return []
    return [it for it in ITEMS if it in text]

# ══════════════════════════════════════════════════════
# 1. 차수별 주문 라이프사이클
# ══════════════════════════════════════════════════════

# Group biz events by normalized 차수
chasu_events = defaultdict(list)
for b in biz:
    c = norm_chasu(b["차수"])
    if c:
        chasu_events[c].append(b)

# Also try to enrich from logs: find logs mentioning each 차수 and link them
# Build a message ID -> log mapping
msg_to_log = {}
for l in logs:
    mid = str(l.get("메시지ID", ""))
    if mid:
        msg_to_log[mid] = l

# Sort each 차수's events by time
for c in chasu_events:
    chasu_events[c].sort(key=lambda x: parse_time(x.get("시각", "")))

# Compute per-차수 stats
chasu_lifecycle = {}
for c, evts in sorted(chasu_events.items(), key=lambda x: -len(x[1])):
    type_counts = Counter(e["이벤트타입"] for e in evts)
    items_mentioned = Counter(e["품목"] for e in evts if e["품목"])

    # Build timeline
    timeline = []
    for e in evts:
        # Get original text from linked log if available
        trigger_id = str(e.get("트리거메시지ID", ""))
        orig_text = ""
        if trigger_id and trigger_id in msg_to_log:
            orig_text = msg_to_log[trigger_id].get("원문", "")

        timeline.append({
            "시각": e["시각"],
            "이벤트타입": e["이벤트타입"],
            "품목": e["품목"],
            "거래처": e["거래처"],
            "원문요약": e["원문요약"],
            "원문": orig_text if orig_text else e["원문요약"],
            "방이름": e["방이름"],
            "발신자": e["발신자"],
        })

    chasu_lifecycle[c] = {
        "총이벤트수": len(evts),
        "이벤트타입별건수": dict(type_counts),
        "품목별건수": dict(items_mentioned),
        "ORDER_CHANGE수": type_counts.get("ORDER_CHANGE", 0),
        "DEFECT수": type_counts.get("DEFECT", 0),
        "타임라인": timeline,
    }

# Find most change-heavy and defect-heavy 차수
most_changes = sorted(chasu_lifecycle.items(), key=lambda x: -x[1]["ORDER_CHANGE수"])[:5]
most_defects = sorted(chasu_lifecycle.items(), key=lambda x: -x[1]["DEFECT수"])[:5]

lifecycle_summary = {
    "차수별_라이프사이클": chasu_lifecycle,
    "변경_최다_차수_TOP5": [{"차수": c, "ORDER_CHANGE수": v["ORDER_CHANGE수"], "총이벤트수": v["총이벤트수"]} for c, v in most_changes],
    "불량_최다_차수_TOP5": [{"차수": c, "DEFECT수": v["DEFECT수"], "총이벤트수": v["총이벤트수"]} for c, v in most_defects],
}

# ══════════════════════════════════════════════════════
# 2. 품목별 이슈 추적
# ══════════════════════════════════════════════════════

item_analysis = {}
for item in ITEMS:
    # biz events mentioning this item
    item_biz = [b for b in biz if b["품목"] == item]
    # Also search in 원문요약 for items not in 품목 field
    item_biz_text = [b for b in biz if b["품목"] != item and item in str(b.get("원문요약", ""))]
    all_item_biz = item_biz + item_biz_text

    # 차수 appearances
    chasu_set = set()
    for b in all_item_biz:
        c = norm_chasu(b["차수"])
        if c:
            chasu_set.add(c)

    # Defects for this item
    defects = [b for b in all_item_biz if b["이벤트타입"] == "DEFECT"]
    total_mentions = len(all_item_biz)
    defect_count = len(defects)
    defect_rate = round(defect_count / total_mentions * 100, 1) if total_mentions > 0 else 0

    # 거래처 breakdown
    trader_defects = Counter()
    trader_total = Counter()
    for b in all_item_biz:
        t = b.get("거래처", "")
        if t:
            trader_total[t] += 1
            if b["이벤트타입"] == "DEFECT":
                trader_defects[t] += 1

    # 차수별 불량
    chasu_defects = Counter()
    for d in defects:
        c = norm_chasu(d["차수"])
        if c:
            chasu_defects[c] += 1

    # Enriched defect details from logs
    defect_details = []
    for d in defects:
        trigger_id = str(d.get("트리거메시지ID", ""))
        orig = ""
        if trigger_id and trigger_id in msg_to_log:
            orig = msg_to_log[trigger_id].get("원문", "")
        defect_details.append({
            "시각": d["시각"],
            "차수": norm_chasu(d["차수"]),
            "거래처": d["거래처"],
            "원문요약": d["원문요약"],
            "원문": orig if orig else d["원문요약"],
            "방이름": d["방이름"],
            "발신자": d["발신자"],
        })

    item_analysis[item] = {
        "총언급수": total_mentions,
        "등장_차수": sorted(list(chasu_set)),
        "등장_차수_수": len(chasu_set),
        "불량건수": defect_count,
        "불량률_퍼센트": defect_rate,
        "거래처별_총이벤트": dict(trader_total),
        "거래처별_불량": dict(trader_defects),
        "차수별_불량": dict(chasu_defects),
        "불량_상세": defect_details,
    }

# Also search logs for item mentions to capture non-biz references
for item in ITEMS:
    log_mentions = [l for l in logs if item in str(l.get("원문", ""))]
    item_analysis[item]["로그_원문_언급수"] = len(log_mentions)
    item_analysis[item]["로그_원문_샘플"] = [
        {"시각": l["시각"], "원문": l["원문"], "방이름": l["방이름"], "발신자": l["발신자"]}
        for l in log_mentions[:10]
    ]

# Find worst item+trader combo
worst_combos = []
for item in ITEMS:
    for trader, cnt in item_analysis[item]["거래처별_불량"].items():
        worst_combos.append({
            "품목": item,
            "거래처": trader,
            "불량건수": cnt,
            "총이벤트수": item_analysis[item]["거래처별_총이벤트"].get(trader, 0),
        })
worst_combos.sort(key=lambda x: -x["불량건수"])

pumok_summary = {
    "품목별_분석": item_analysis,
    "문제_최다_품목거래처_조합": worst_combos[:10],
}

# ══════════════════════════════════════════════════════
# 3. 주문변경→불량 전환율
# ══════════════════════════════════════════════════════

# For each ORDER_CHANGE, check if a DEFECT follows for the same 차수+품목
order_changes = [b for b in biz if b["이벤트타입"] == "ORDER_CHANGE"]
defect_events = [b for b in biz if b["이벤트타입"] == "DEFECT"]

# Build a set of (차수, 품목) pairs that have DEFECT
defect_pairs = set()
for d in defect_events:
    c = norm_chasu(d["차수"])
    p = d["품목"]
    if c:
        defect_pairs.add((c, p))
    # Also add (차수, "") to catch defects without item specified
    if c:
        defect_pairs.add((c, ""))

# Check each ORDER_CHANGE
change_to_defect_matches = []
change_total = 0
change_with_defect = 0

# Group ORDER_CHANGE by (차수, 품목) to avoid double-counting
change_pairs = defaultdict(list)
for oc in order_changes:
    c = norm_chasu(oc["차수"])
    p = oc["품목"]
    if c:
        change_pairs[(c, p)].append(oc)

for (c, p), changes in change_pairs.items():
    change_total += 1
    # Check if defect exists for same 차수 (and same 품목 or any 품목)
    has_defect = (c, p) in defect_pairs or (c, "") in defect_pairs
    if not has_defect and p:
        # Check if defect exists for this 차수 with any item
        has_defect = any((c, dp) in defect_pairs for dp in ["카네이션", "수국", "장미", ""])
    if has_defect:
        change_with_defect += 1
        # Get original texts
        sample_changes = []
        for oc in changes[:3]:
            tid = str(oc.get("트리거메시지ID", ""))
            orig = msg_to_log.get(tid, {}).get("원문", "") if tid else ""
            sample_changes.append({
                "시각": oc["시각"],
                "원문요약": oc["원문요약"],
                "원문": orig if orig else oc["원문요약"],
            })
        # Get the related defects
        related_defects = [d for d in defect_events
                          if norm_chasu(d["차수"]) == c and (d["품목"] == p or not d["품목"] or not p)]
        sample_defects = []
        for df in related_defects[:3]:
            tid = str(df.get("트리거메시지ID", ""))
            orig = msg_to_log.get(tid, {}).get("원문", "") if tid else ""
            sample_defects.append({
                "시각": df["시각"],
                "원문요약": df["원문요약"],
                "원문": orig if orig else df["원문요약"],
            })
        change_to_defect_matches.append({
            "차수": c,
            "품목": p,
            "변경건수": len(changes),
            "변경_샘플": sample_changes,
            "관련_불량_샘플": sample_defects,
        })

conversion_rate = round(change_with_defect / change_total * 100, 1) if change_total > 0 else 0

conversion_summary = {
    "ORDER_CHANGE_차수품목_고유조합수": change_total,
    "이후_DEFECT_발생_조합수": change_with_defect,
    "전환율_퍼센트": conversion_rate,
    "전환_상세": change_to_defect_matches,
}

# ══════════════════════════════════════════════════════
# 4. 미해결 이슈 체인 분석
# ══════════════════════════════════════════════════════

unresolved = [i for i in issues if i["결과"] == "미해결"]

# Enrich with original text from linked events/logs
for u in unresolved:
    # Try to find linked event
    linked_evt = u.get("연관이벤트ID", "")
    if linked_evt:
        matched = [b for b in biz if b["이벤트ID"] == linked_evt]
        if matched:
            u["_연관이벤트"] = matched[0]

# Categorize by 이슈내용 pattern
issue_categories = defaultdict(list)
for u in unresolved:
    content = u["이슈내용"]
    # Parse category from content
    if not content or content == "불량":
        cat = "불량_일반"
    elif re.search(r'\d+차.*불량', content):
        cat = "불량_차수지정"
    elif "수국" in content or "카네이션" in content or "장미" in content:
        cat = "불량_품목지정"
    elif "변경" in content or "추가" in content or "취소" in content:
        cat = "주문변경"
    elif "입고" in content or "출고" in content or "배송" in content:
        cat = "물류"
    elif "재고" in content or "확인" in content:
        cat = "재고확인"
    else:
        cat = "기타"
    issue_categories[cat].append(u)

# By room
room_issues = Counter(u["발생방"] for u in unresolved)

# Extract 차수 from issue content
issue_chasu = Counter()
for u in unresolved:
    found = re.findall(r'(\d{1,3}(?:-\d)?)\s*차', u["이슈내용"])
    for f in found:
        issue_chasu[f] += 1

# Build pipeline breakdown
pipeline_issues = Counter(u["파이프라인"] for u in unresolved)

# Detailed unresolved list
unresolved_detail = []
for u in unresolved:
    unresolved_detail.append({
        "이슈ID": u["이슈ID"],
        "발생시각": u["발생시각"],
        "발생방": u["발생방"],
        "파이프라인": u["파이프라인"],
        "이슈내용": u["이슈내용"],
        "대응자": u["대응자"],
        "대응내용": u["대응내용"],
    })

issue_summary = {
    "미해결_총건수": len(unresolved),
    "유형별_분류": {cat: len(items) for cat, items in sorted(issue_categories.items(), key=lambda x: -len(x[1]))},
    "유형별_상세": {
        cat: [{"이슈ID": i["이슈ID"], "발생시각": i["발생시각"], "발생방": i["발생방"],
               "이슈내용": i["이슈내용"], "파이프라인": i["파이프라인"]}
              for i in items]
        for cat, items in issue_categories.items()
    },
    "발생방별_건수": dict(room_issues.most_common()),
    "파이프라인별_건수": dict(pipeline_issues.most_common()),
    "차수별_미해결_건수": dict(issue_chasu.most_common()),
    "미해결_전체목록": unresolved_detail,
}

# ══════════════════════════════════════════════════════
# Final output
# ══════════════════════════════════════════════════════

result = {
    "분석일시": "2026-04-11",
    "데이터_요약": {
        "logs_총수": len(logs),
        "biz_총수": len(biz),
        "issues_총수": len(issues),
        "이벤트타입_분포": dict(Counter(b["이벤트타입"] for b in biz).most_common()),
        "차수_고유수": len(chasu_events),
        "품목_고유수": len(set(b["품목"] for b in biz if b["품목"])),
    },
    "1_차수별_주문_라이프사이클": lifecycle_summary,
    "2_품목별_이슈_추적": pumok_summary,
    "3_주문변경_불량_전환율": conversion_summary,
    "4_미해결_이슈_체인": issue_summary,
}

with open(OUTPUT, "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False, indent=2)

print("Analysis complete. Output:", OUTPUT)
print(f"데이터: logs={len(logs)}, biz={len(biz)}, issues={len(issues)}")
print(f"차수 수: {len(chasu_events)}")
print(f"변경 최다 차수: {most_changes[0][0]} ({most_changes[0][1]['ORDER_CHANGE수']}건)")
print(f"불량 최다 차수: {most_defects[0][0]} ({most_defects[0][1]['DEFECT수']}건)")
print(f"품목별 불량률: ", {it: f"{item_analysis[it]['불량률_퍼센트']}%" for it in ITEMS})
print(f"주문변경→불량 전환율: {conversion_rate}% ({change_with_defect}/{change_total})")
print(f"미해결 이슈: {len(unresolved)}건")
print(f"미해결 주요 발생방: {room_issues.most_common(3)}")
