#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""인물별 업무 패턴 깊이 분석 스크립트"""

import json
import sys
import re
from collections import Counter, defaultdict
from itertools import combinations

sys.stdout.reconfigure(encoding='utf-8')

with open(r'C:\Users\USER\nenova_agent\data\analysis_dump.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

logs = data['logs']
biz = data['biz']
issues = data['issues']

# ===== TIME PARSING =====
def parse_time(t):
    """Parse '오전 10:19' or '오후 3:46' to 24h hour and minute"""
    if not t:
        return None, None
    m = re.match(r'(오전|오후)\s*(\d{1,2}):(\d{2})', t)
    if not m:
        return None, None
    period, h, mi = m.group(1), int(m.group(2)), int(m.group(3))
    if period == '오전':
        if h == 12:
            h = 0
    else:  # 오후
        if h != 12:
            h += 12
    return h, mi

def time_slot(h):
    if h is None:
        return '불명'
    if 6 <= h < 12:
        return '오전(6-12)'
    elif 12 <= h < 18:
        return '오후(12-18)'
    elif 18 <= h < 22:
        return '저녁(18-22)'
    else:
        return '심야/새벽'

def time_sort_key(t):
    """Sort key for time strings like '오전 10:19'"""
    h, mi = parse_time(t)
    if h is None:
        return 9999
    return h * 60 + (mi or 0)

# ===== 1. PERSON PROFILES =====
sender_counts = Counter(x['발신자'] for x in logs)
top15 = [s for s, _ in sender_counts.most_common(15)]

person_logs = defaultdict(list)
person_biz = defaultdict(list)
for x in logs:
    person_logs[x['발신자']].append(x)
for x in biz:
    person_biz[x['발신자']].append(x)

profiles = {}
for sender in top15:
    sl = person_logs[sender]
    sb = person_biz[sender]

    # Time distribution
    hours = [parse_time(x['시각'])[0] for x in sl]
    hours = [h for h in hours if h is not None]
    time_dist = Counter(time_slot(h) for h in hours)
    hour_dist = Counter(hours)

    # Peak hour
    peak_hour = hour_dist.most_common(1)[0] if hour_dist else (None, 0)

    # Room distribution
    room_counts = Counter(x['방이름'] for x in sl)
    total_msgs = len(sl)
    room_pct = {r: round(c / total_msgs * 100, 1) for r, c in room_counts.most_common()}

    # Event type distribution (from biz)
    etype_counts = Counter(x['이벤트타입'] for x in sb)

    # Items/차수 (from biz)
    items = Counter(x['품목'] for x in sb if x.get('품목'))
    chasu = Counter(x['차수'] for x in sb if x.get('차수'))
    varieties = Counter(x['품종'] for x in sb if x.get('품종'))
    traders = Counter(x['거래처'] for x in sb if x.get('거래처'))

    # Pipeline
    pipeline_counts = Counter(x['파이프라인'] for x in sl)

    profiles[sender] = {
        '총메시지수': total_msgs,
        '비즈이벤트수': len(sb),
        '시간대분포': dict(time_dist),
        '시간별분포(24h)': {str(k): v for k, v in sorted(hour_dist.items())},
        '피크시간': f"{peak_hour[0]}시 ({peak_hour[1]}건)" if peak_hour[0] is not None else '불명',
        '활동방_비중(%)': room_pct,
        '파이프라인': dict(pipeline_counts.most_common()),
        '이벤트타입': dict(etype_counts.most_common()),
        '주요품목': dict(items.most_common(10)),
        '주요품종': dict(varieties.most_common(10)),
        '주요차수': dict(chasu.most_common(10)),
        '주요거래처': dict(traders.most_common(5)),
    }

# ===== 2. ROLE INFERENCE =====
role_keywords = {
    '요청자': ['부탁', '확인 부탁', '해주세요', '가능할까', '해줄수', '요청', '주세요', '해주십시오'],
    '실행자': ['진행하겠', '완료', '처리했', '보냈습니다', '출고했', '했습니다', '하겠습니다', '배송완료', '출고완료'],
    '의사결정자': ['불가', '어렵', '안됩니다', '중단', '보류', '취소합니다', '안돼', '못합니다'],
    '현장보고자': ['사진'],
    '정보공유자': ['공유', '참고', '알려드립', '전달', '안내'],
    '지시자': ['하세요', '해라', '하십시오', '바랍니다', '지시'],
}

for sender in top15:
    sl = person_logs[sender]
    role_scores = defaultdict(int)
    role_examples = defaultdict(list)
    for x in sl:
        msg = str(x['원문']) if x.get('원문') else ''
        for role, keywords in role_keywords.items():
            for kw in keywords:
                if kw in msg:
                    role_scores[role] += 1
                    if len(role_examples[role]) < 3:
                        role_examples[role].append(str(msg)[:80])
                    break  # count once per role per message

    # Normalize
    total = sum(role_scores.values()) or 1
    role_pct = {r: round(v / total * 100, 1) for r, v in sorted(role_scores.items(), key=lambda x: -x[1])}

    # Primary role
    primary = max(role_scores, key=role_scores.get) if role_scores else '불명'

    # Secondary role
    sorted_roles = sorted(role_scores.items(), key=lambda x: -x[1])
    secondary = sorted_roles[1][0] if len(sorted_roles) > 1 else '없음'

    profiles[sender]['역할점수(%)'] = role_pct
    profiles[sender]['추정주역할'] = primary
    profiles[sender]['추정부역할'] = secondary
    profiles[sender]['역할근거_샘플'] = {r: ex for r, ex in role_examples.items()}

# ===== 3. CROSS-ROOM ACTIVITY =====
cross_room = {}
for sender in top15:
    sl = person_logs[sender]
    rooms = list(set(x['방이름'] for x in sl))
    if len(rooms) <= 1:
        cross_room[sender] = {
            '활동방수': len(rooms),
            '방목록': rooms,
            '정보중계횟수': 0,
            '주요중계경로': {},
            '중계샘플': [],
        }
        continue

    # Sort messages by time
    sorted_msgs = sorted(sl, key=lambda x: time_sort_key(x['시각']))
    relay_patterns = []
    for i in range(len(sorted_msgs) - 1):
        curr = sorted_msgs[i]
        nxt = sorted_msgs[i + 1]
        if curr['방이름'] != nxt['방이름']:
            relay_patterns.append({
                '시각1': curr['시각'],
                '방1': curr['방이름'],
                '내용1': str(curr['원문'])[:60],
                '시각2': nxt['시각'],
                '방2': nxt['방이름'],
                '내용2': str(nxt['원문'])[:60],
            })

    # Summarize relay directions
    relay_dirs = Counter(f"{p['방1']} -> {p['방2']}" for p in relay_patterns)

    cross_room[sender] = {
        '활동방수': len(rooms),
        '방목록': rooms,
        '정보중계횟수': len(relay_patterns),
        '주요중계경로': dict(relay_dirs.most_common(5)),
        '중계샘플': relay_patterns[:5],
    }

# ===== 4. COMMUNICATION NETWORK =====
# Build room-based conversation data
room_msgs = defaultdict(list)
for x in logs:
    room_msgs[x['방이름']].append((x['시각'], x['발신자']))

# Co-occurrence pairs (weighted by min activity in shared room)
pair_coactivity = Counter()
for room, msgs in room_msgs.items():
    senders_in_room = Counter(s for _, s in msgs)
    for s1, s2 in combinations(senders_in_room.keys(), 2):
        pair = tuple(sorted([s1, s2]))
        pair_coactivity[pair] += min(senders_in_room[s1], senders_in_room[s2])

# Response patterns: who talks right after whom in same room
response_pairs = Counter()
for room, msgs in room_msgs.items():
    sorted_msgs = sorted(msgs, key=lambda x: time_sort_key(x[0]))
    for i in range(len(sorted_msgs) - 1):
        t1, s1 = sorted_msgs[i]
        t2, s2 = sorted_msgs[i + 1]
        if s1 != s2:
            response_pairs[(s1, s2)] += 1

# Per-person network summary
person_network = {}
for sender in top15:
    # Who does this person interact with most?
    interactions = Counter()
    for (s1, s2), c in response_pairs.items():
        if s1 == sender:
            interactions[s2] += c
        elif s2 == sender:
            interactions[s1] += c

    # Who responds to this person most?
    responders = Counter()
    for (s1, s2), c in response_pairs.items():
        if s1 == sender:
            responders[s2] += c

    # Who does this person respond to most?
    responds_to = Counter()
    for (s1, s2), c in response_pairs.items():
        if s2 == sender:
            responds_to[s1] += c

    person_network[sender] = {
        '상호작용_TOP5': dict(interactions.most_common(5)),
        '내가_응답받는_TOP5': dict(responders.most_common(5)),
        '나에게_응답하는_TOP5': dict(responds_to.most_common(5)),
    }

network = {
    '자주_대화하는_쌍_TOP20': [
        {'인물1': p[0], '인물2': p[1], '공동활동수': c}
        for p, c in pair_coactivity.most_common(20)
    ],
    '응답패턴_TOP20(A발신후_B응답)': [
        {'발신': p[0], '응답': p[1], '횟수': c}
        for p, c in response_pairs.most_common(20)
    ],
    '인물별_네트워크': person_network,
}

# ===== 5. ISSUE ANALYSIS =====
# Issues by person involvement
issue_involvement = {}
for sender in top15:
    # Check if person is mentioned as 대응자
    responded = [x for x in issues if sender in x.get('대응자', '')]
    # Check related biz events
    issue_event_ids = set(x.get('연관이벤트ID', '') for x in issues if x.get('연관이벤트ID'))
    person_event_ids = set(x.get('이벤트ID', '') for x in person_biz[sender] if x.get('이벤트ID'))
    linked = issue_event_ids & person_event_ids

    issue_involvement[sender] = {
        '대응자로_참여': len(responded),
        '연관이벤트_매칭': len(linked),
    }

# ===== 6. PERSON SUMMARY (human-readable) =====
person_summaries = {}
for sender in top15:
    p = profiles[sender]
    cr = cross_room[sender]
    pn = person_network.get(sender, {})

    # Build readable summary
    top_room = list(p['활동방_비중(%)'].keys())[0] if p['활동방_비중(%)'] else '없음'
    top_room_pct = list(p['활동방_비중(%)'].values())[0] if p['활동방_비중(%)'] else 0
    top_etype = list(p['이벤트타입'].keys())[0] if p['이벤트타입'] else '없음'
    top_item = list(p['주요품목'].keys())[0] if p['주요품목'] else '없음'

    summary = (
        f"메시지 {p['총메시지수']}건(비즈 {p['비즈이벤트수']}건), "
        f"주역할: {p['추정주역할']}, 부역할: {p['추정부역할']}, "
        f"주활동방: {top_room}({top_room_pct}%), "
        f"주이벤트: {top_etype}, 주품목: {top_item}, "
        f"크로스방활동: {cr['활동방수']}개방, 중계 {cr['정보중계횟수']}회, "
        f"피크: {p['피크시간']}"
    )
    person_summaries[sender] = summary

# ===== COMPILE RESULT =====
result = {
    '분석개요': {
        '총로그수': len(logs),
        '총비즈이벤트수': len(biz),
        '총이슈수': len(issues),
        '분석대상인물수': len(top15),
        '분석대상인물': top15,
        '방수': len(set(x['방이름'] for x in logs)),
        '방목록': list(set(x['방이름'] for x in logs)),
    },
    '인물별_요약': person_summaries,
    '인물별_프로파일': profiles,
    '크로스방_활동패턴': cross_room,
    '커뮤니케이션_네트워크': network,
    '이슈_관여도': issue_involvement,
    '이슈_전체분석': {
        '총이슈수': len(issues),
        '미해결': len([x for x in issues if x.get('결과') == '미해결']),
        '해결': len([x for x in issues if x.get('결과') not in ['미해결', '']]),
        '이슈유형분포': dict(Counter(x.get('이슈내용', '') for x in issues).most_common()),
        '파이프라인별이슈': dict(Counter(x.get('파이프라인', '') for x in issues).most_common()),
    },
}

output_path = r'C:\Users\USER\nenova_agent\data\analysis_people.json'
with open(output_path, 'w', encoding='utf-8') as f:
    json.dump(result, f, ensure_ascii=False, indent=2)

print(f"Done. Output: {output_path}")
print(f"File size: {len(json.dumps(result, ensure_ascii=False)):,} chars")
print()
print("=== TOP 15 PERSON SUMMARIES ===")
for s in top15:
    print(f"  [{s}] {person_summaries[s]}")
print()
print("=== TOP 10 COMMUNICATION PAIRS ===")
for item in network['자주_대화하는_쌍_TOP20'][:10]:
    print(f"  {item['인물1']} <-> {item['인물2']}: {item['공동활동수']}")
print()
print("=== ISSUE OVERVIEW ===")
print(f"  Total: {len(issues)}, Unresolved: {result['이슈_전체분석']['미해결']}, Resolved: {result['이슈_전체분석']['해결']}")
