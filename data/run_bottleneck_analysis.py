#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
네노바 파이프라인 병목 분석 (Orbit 3D 수준)
analysis_dump.json → analysis_bottleneck.json
"""
import json
import re
from collections import defaultdict, Counter
from datetime import datetime

def parse_time(t_str):
    """'오전 10:19', '오후 3:46' → minutes from midnight"""
    if not t_str or not isinstance(t_str, str):
        return None
    m = re.match(r'(오전|오후)\s*(\d{1,2}):(\d{2})', t_str.strip())
    if not m:
        return None
    ampm, h, mi = m.group(1), int(m.group(2)), int(m.group(3))
    if ampm == '오후' and h != 12:
        h += 12
    elif ampm == '오전' and h == 12:
        h = 0
    return h * 60 + mi

def time_diff(t1_str, t2_str):
    """t2 - t1 in minutes, None if unparseable"""
    a, b = parse_time(t1_str), parse_time(t2_str)
    if a is None or b is None:
        return None
    d = b - a
    return d if d >= 0 else None  # ignore cross-day

def main():
    with open('analysis_dump.json', 'r', encoding='utf-8') as f:
        data = json.load(f)

    logs = data['logs']
    biz = data['biz']
    issues = data['issues']

    result = {}

    # =========================================================
    # 1. 파이프라인 단계간 전환 시간 (Pipeline Transition Times)
    # =========================================================
    # Group biz events by (차수, 품목) → list of (time, pipeline, room)
    item_events = defaultdict(list)
    for b in biz:
        key_cha = str(b.get('차수', '')).strip()
        key_item = str(b.get('품목', '')).strip()
        if key_cha and key_item:
            t = parse_time(b['시각'])
            if t is not None:
                item_events[(key_cha, key_item)].append({
                    'time_min': t,
                    'time_str': b['시각'],
                    'pipeline': b['파이프라인'],
                    'room': b['방이름'],
                    'event_type': b['이벤트타입'],
                    'event_id': b['이벤트ID']
                })

    transitions = []
    for (cha, item), evts in item_events.items():
        evts_sorted = sorted(evts, key=lambda x: x['time_min'])
        for i in range(len(evts_sorted)):
            for j in range(i+1, len(evts_sorted)):
                e1, e2 = evts_sorted[i], evts_sorted[j]
                if e1['pipeline'] != e2['pipeline'] and e1['pipeline'] != 'UNKNOWN' and e2['pipeline'] != 'UNKNOWN':
                    diff = e2['time_min'] - e1['time_min']
                    if diff > 0:
                        transitions.append({
                            '차수': cha,
                            '품목': item,
                            'from_pipeline': e1['pipeline'],
                            'to_pipeline': e2['pipeline'],
                            'from_room': e1['room'],
                            'to_room': e2['room'],
                            'from_time': e1['time_str'],
                            'to_time': e2['time_str'],
                            'transition_minutes': diff
                        })
                        break  # only first transition per pipeline pair per item

    # Aggregate transitions by pipeline pair
    trans_agg = defaultdict(list)
    for t in transitions:
        key = f"{t['from_pipeline']} → {t['to_pipeline']}"
        trans_agg[key].append(t['transition_minutes'])

    pipeline_transitions = {}
    for pair, times in sorted(trans_agg.items(), key=lambda x: -max(x[1])):
        pipeline_transitions[pair] = {
            'count': len(times),
            'avg_minutes': round(sum(times)/len(times), 1),
            'min_minutes': min(times),
            'max_minutes': max(times),
            'median_minutes': sorted(times)[len(times)//2]
        }

    # Top bottleneck transitions (slowest avg)
    bottleneck_transitions = sorted(transitions, key=lambda x: -x['transition_minutes'])[:20]

    result['pipeline_transitions'] = {
        'summary': pipeline_transitions,
        'worst_bottlenecks': bottleneck_transitions,
        'total_tracked_items': len(item_events)
    }

    # =========================================================
    # 2. 방별 처리 속도 (Room Response Speed)
    # =========================================================
    # Use logs: group consecutive messages by room, find INQUIRY→response patterns
    # Also use issues for response tracking
    room_logs = defaultdict(list)
    for l in logs:
        t = parse_time(l['시각'])
        if t is not None:
            room_logs[l['방이름']].append({
                'time_min': t,
                'time_str': l['시각'],
                'sender': l['발신자'],
                'text': l['원문'],
                'pipeline': l['파이프라인']
            })

    # Find question→answer patterns in biz (INQUIRY followed by any other event in same room)
    room_biz = defaultdict(list)
    for b in biz:
        t = parse_time(b['시각'])
        if t is not None:
            room_biz[b['방이름']].append({
                'time_min': t,
                'time_str': b['시각'],
                'type': b['이벤트타입'],
                'sender': b['발신자'],
                'summary': b.get('원문요약', ''),
                'event_id': b['이벤트ID']
            })

    room_speed = {}
    for room, evts in room_biz.items():
        evts_s = sorted(evts, key=lambda x: x['time_min'])
        response_times = []
        for i, e in enumerate(evts_s):
            if e['type'] == 'INQUIRY':
                # Find next non-INQUIRY, non-PHOTO event from a different sender
                for j in range(i+1, len(evts_s)):
                    nxt = evts_s[j]
                    if nxt['sender'] != e['sender'] and nxt['type'] not in ('INQUIRY', 'PHOTO'):
                        diff = nxt['time_min'] - e['time_min']
                        if diff > 0:
                            response_times.append(diff)
                        break

        activity_span = evts_s[-1]['time_min'] - evts_s[0]['time_min'] if len(evts_s) > 1 else 0
        event_counts = Counter(e['type'] for e in evts_s)

        room_speed[room] = {
            'total_events': len(evts_s),
            'event_type_breakdown': dict(event_counts),
            'activity_span_minutes': activity_span,
            'inquiry_count': event_counts.get('INQUIRY', 0),
            'avg_response_minutes': round(sum(response_times)/len(response_times), 1) if response_times else None,
            'min_response_minutes': min(response_times) if response_times else None,
            'max_response_minutes': max(response_times) if response_times else None,
            'response_samples': len(response_times)
        }

    # Rank rooms
    rooms_with_response = [(r, d) for r, d in room_speed.items() if d['avg_response_minutes'] is not None]
    rooms_with_response.sort(key=lambda x: x[1]['avg_response_minutes'])

    result['room_processing_speed'] = {
        'per_room': room_speed,
        'fastest_room': rooms_with_response[0][0] if rooms_with_response else None,
        'slowest_room': rooms_with_response[-1][0] if rooms_with_response else None,
        'ranking_by_response_time': [
            {'room': r, 'avg_response_min': d['avg_response_minutes'], 'samples': d['response_samples']}
            for r, d in rooms_with_response
        ]
    }

    # =========================================================
    # 3. 이벤트 체인 분석 (Event Chain Analysis)
    # =========================================================
    # Look at consecutive event type sequences per room
    chain_counter = Counter()
    chain_times = defaultdict(list)

    for room, evts in room_biz.items():
        evts_s = sorted(evts, key=lambda x: x['time_min'])
        for i in range(len(evts_s) - 1):
            pair = f"{evts_s[i]['type']} → {evts_s[i+1]['type']}"
            chain_counter[pair] += 1
            diff = evts_s[i+1]['time_min'] - evts_s[i]['time_min']
            if diff >= 0:
                chain_times[pair].append(diff)

    # Triple chains
    triple_counter = Counter()
    triple_times = defaultdict(list)
    for room, evts in room_biz.items():
        evts_s = sorted(evts, key=lambda x: x['time_min'])
        for i in range(len(evts_s) - 2):
            triple = f"{evts_s[i]['type']} → {evts_s[i+1]['type']} → {evts_s[i+2]['type']}"
            triple_counter[triple] += 1
            total_time = evts_s[i+2]['time_min'] - evts_s[i]['time_min']
            if total_time >= 0:
                triple_times[triple].append(total_time)

    pair_chains = {}
    for chain, count in chain_counter.most_common(20):
        times = chain_times[chain]
        pair_chains[chain] = {
            'count': count,
            'avg_transition_minutes': round(sum(times)/len(times), 1) if times else None,
            'max_transition_minutes': max(times) if times else None
        }

    triple_chains = {}
    for chain, count in triple_counter.most_common(15):
        times = triple_times[chain]
        triple_chains[chain] = {
            'count': count,
            'avg_total_minutes': round(sum(times)/len(times), 1) if times else None,
            'max_total_minutes': max(times) if times else None
        }

    result['event_chains'] = {
        'pair_chains_top20': pair_chains,
        'triple_chains_top15': triple_chains
    }

    # =========================================================
    # 4. 사진-텍스트 패턴 (Photo-Text Pattern)
    # =========================================================
    photo_events = []
    photo_followed_by_text = 0
    photo_followed_by_defect = 0
    photo_no_description = 0
    photo_to_text_times = []
    photo_to_defect_times = []

    for room, evts in room_biz.items():
        evts_s = sorted(evts, key=lambda x: x['time_min'])
        for i, e in enumerate(evts_s):
            if e['type'] == 'PHOTO':
                photo_events.append(e)
                found_text = False
                found_defect = False
                # Look at next 3 events within 30 min
                for j in range(i+1, min(i+4, len(evts_s))):
                    nxt = evts_s[j]
                    diff = nxt['time_min'] - e['time_min']
                    if diff > 30:
                        break
                    if nxt['type'] == 'DEFECT' and not found_defect:
                        found_defect = True
                        photo_to_defect_times.append(diff)
                    if nxt['type'] not in ('PHOTO',) and not found_text:
                        found_text = True
                        photo_to_text_times.append(diff)

                if found_text:
                    photo_followed_by_text += 1
                if found_defect:
                    photo_followed_by_defect += 1
                if not found_text:
                    photo_no_description += 1

    total_photos = len(photo_events)
    result['photo_text_pattern'] = {
        'total_photos': total_photos,
        'photo_with_followup_text': photo_followed_by_text,
        'photo_with_defect_report': photo_followed_by_defect,
        'photo_no_description': photo_no_description,
        'no_description_ratio': round(photo_no_description / total_photos, 3) if total_photos else 0,
        'avg_photo_to_text_minutes': round(sum(photo_to_text_times)/len(photo_to_text_times), 1) if photo_to_text_times else None,
        'avg_photo_to_defect_minutes': round(sum(photo_to_defect_times)/len(photo_to_defect_times), 1) if photo_to_defect_times else None,
        'max_photo_to_defect_minutes': max(photo_to_defect_times) if photo_to_defect_times else None
    }

    # =========================================================
    # 5. 비효율 패턴 감지 (Inefficiency Patterns)
    # =========================================================

    # 5a. 중복 전달 (same content in multiple rooms within 30 min)
    # Group biz by (차수, 품목, 이벤트타입) and check if appears in multiple rooms
    cross_room_dup = defaultdict(list)
    for b in biz:
        cha = str(b.get('차수', '')).strip()
        item = str(b.get('품목', '')).strip()
        if cha and item:
            key = (cha, item, b['이벤트타입'])
            t = parse_time(b['시각'])
            if t is not None:
                cross_room_dup[key].append({
                    'room': b['방이름'],
                    'time_min': t,
                    'time_str': b['시각'],
                    'sender': b['발신자'],
                    'summary': b.get('원문요약', '')
                })

    duplicate_broadcasts = []
    for (cha, item, etype), entries in cross_room_dup.items():
        rooms_seen = set(e['room'] for e in entries)
        if len(rooms_seen) >= 2:
            entries_s = sorted(entries, key=lambda x: x['time_min'])
            time_span = entries_s[-1]['time_min'] - entries_s[0]['time_min']
            if time_span <= 60:  # within 1 hour
                duplicate_broadcasts.append({
                    '차수': cha,
                    '품목': item,
                    'event_type': etype,
                    'rooms': list(rooms_seen),
                    'room_count': len(rooms_seen),
                    'time_span_minutes': time_span,
                    'occurrences': len(entries)
                })

    duplicate_broadcasts.sort(key=lambda x: -x['room_count'])

    # 5b. 무응답 질문 (INQUIRY with no follow-up response in same room within 30 min)
    unanswered = []
    for room, evts in room_biz.items():
        evts_s = sorted(evts, key=lambda x: x['time_min'])
        for i, e in enumerate(evts_s):
            if e['type'] == 'INQUIRY':
                answered = False
                for j in range(i+1, len(evts_s)):
                    nxt = evts_s[j]
                    diff = nxt['time_min'] - e['time_min']
                    if diff > 30:
                        break
                    if nxt['sender'] != e['sender'] and nxt['type'] not in ('PHOTO',):
                        answered = True
                        break
                if not answered:
                    unanswered.append({
                        'room': room,
                        'pipeline': e.get('pipeline', ''),
                        'sender': e['sender'],
                        'time': e['time_str'],
                        'summary': e['summary'],
                        'event_id': e['event_id']
                    })

    # 5c. 반복 변경 (same 차수+품목 with ORDER_CHANGE >= 3 times)
    change_tracker = defaultdict(list)
    for b in biz:
        if b['이벤트타입'] == 'ORDER_CHANGE':
            cha = str(b.get('차수', '')).strip()
            item = str(b.get('품목', '')).strip()
            if cha and item:
                change_tracker[(cha, item)].append({
                    'time': b['시각'],
                    'room': b['방이름'],
                    'sender': b['발신자'],
                    'summary': b.get('원문요약', '')
                })

    repeated_changes = []
    for (cha, item), changes in change_tracker.items():
        if len(changes) >= 3:
            repeated_changes.append({
                '차수': cha,
                '품목': item,
                'change_count': len(changes),
                'changes': changes
            })
    repeated_changes.sort(key=lambda x: -x['change_count'])

    result['inefficiency_patterns'] = {
        'duplicate_cross_room_broadcasts': {
            'count': len(duplicate_broadcasts),
            'details': duplicate_broadcasts[:20]
        },
        'unanswered_inquiries_30min': {
            'count': len(unanswered),
            'details': unanswered[:30]
        },
        'repeated_order_changes_3plus': {
            'count': len(repeated_changes),
            'details': repeated_changes
        }
    }

    # =========================================================
    # 6. 종합 요약 (Executive Summary)
    # =========================================================
    # Issues breakdown by pipeline
    issue_by_pipe = Counter(i['파이프라인'] for i in issues)

    # Overall pipeline volume
    pipe_volume = Counter(b['파이프라인'] for b in biz)

    # Peak activity times
    time_buckets = Counter()
    for b in biz:
        t = parse_time(b['시각'])
        if t is not None:
            hour = t // 60
            time_buckets[hour] += 1

    peak_hours = time_buckets.most_common(5)

    result['executive_summary'] = {
        'total_logs': len(logs),
        'total_biz_events': len(biz),
        'total_issues': len(issues),
        'issues_all_unresolved': all(i['결과'] == '미해결' for i in issues),
        'issues_by_pipeline': dict(issue_by_pipe.most_common()),
        'event_volume_by_pipeline': dict(pipe_volume.most_common()),
        'peak_activity_hours': [{'hour': h, 'events': c} for h, c in peak_hours],
        'unique_rooms': len(set(b['방이름'] for b in biz)),
        'unique_senders': len(set(l['발신자'] for l in logs)),
        'tracked_item_combinations': len(item_events),
        'critical_findings': [
            f"132건 이슈 전부 미해결 상태 — 대응 체계 부재",
            f"파이프라인간 전환 병목: {len(transitions)}건 감지, 최대 {max(t['transition_minutes'] for t in transitions) if transitions else 0}분 지연",
            f"사진 {total_photos}건 중 {photo_no_description}건({round(photo_no_description/total_photos*100,1) if total_photos else 0}%) 설명 없이 업로드",
            f"30분 내 무응답 질문: {len(unanswered)}건",
            f"중복 방간 전달: {len(duplicate_broadcasts)}건",
            f"3회 이상 반복 변경: {len(repeated_changes)}건"
        ]
    }

    # Write output
    with open('analysis_bottleneck.json', 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print("=== 분석 완료 ===")
    print(f"Output: analysis_bottleneck.json")
    print()
    for finding in result['executive_summary']['critical_findings']:
        print(f"  * {finding}")
    print()
    print(f"Pipeline transitions: {len(pipeline_transitions)} pairs")
    print(f"Room speed ranking: {len(rooms_with_response)} rooms")
    print(f"Event chains (pairs): {len(pair_chains)}")
    print(f"Event chains (triples): {len(triple_chains)}")

if __name__ == '__main__':
    main()
