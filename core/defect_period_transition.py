"""Resolve new defect periods from estimate-registration audit evidence only."""
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

KST = ZoneInfo('Asia/Seoul')


class VerifiedPartitionHistory(list):
    """History assembled locally after complete partition coverage checks."""


def complete_support_history(period, request):
    listing = dict(request('GET', params={**period, 'view': 'support', 'history': '1', 'manager': ''}))
    history, rows = listing.get('history'), listing.get('rows')
    if not isinstance(history, list) or not isinstance(rows, list):
        raise ValueError('차수 전환 이력/원장 응답 누락')
    if len(history) < 1000:
        return listing
    if len({str(h['HistoryKey']) for h in history}) != len(history):
        raise ValueError('차수 이력키 중복; 대조 필요')
    target = (int(period['year']), int(period['week']))
    sources = {(int(r['orderYear']), int(r['orderWeek'])) for r in rows} | {target}
    eligible = {str(r['deductionKey']) for r in rows}
    # Archived deductions leave the active rows but their estimate-registration
    # history still determines period transitions. Include their original
    # partitions and full history; active-row membership cannot filter them out.
    history_deductions = {str(h['DeductionKey']) for h in history}
    for entry in history:
        if entry.get('OrderYear') is not None and entry.get('OrderWeek') is not None:
            year, week = int(entry['OrderYear']), int(entry['OrderWeek'])
            if not 2000 <= year <= 2100 or not 1 <= week <= 53:
                raise ValueError('이력 원차수 범위 확인 필요')
            sources.add((year, week))
    covered, merged, proof = set(), {}, []
    for year, week in sorted(sources):
        part = request('GET', params={'year': year, 'week': week, 'history': '1', 'manager': ''})
        if not isinstance(part.get('history'), list) or not isinstance(part.get('rows'), list) or len(part['history']) >= 1000:
            raise ValueError('원차수 이력도 조회 한도 초과/누락; 대조 필요')
        covered.update(str(r['deductionKey']) for r in part['rows'])
        proof.append({'year': year, 'week': week, 'history_count': len(part['history'])})
        for entry in part['history']:
            if (year, week) == target or str(entry['DeductionKey']) in eligible | history_deductions:
                key = str(entry['HistoryKey'])
                if key in merged and merged[key] != entry:
                    raise ValueError('분할 이력 조회 중 내용 변경; 재대조 필요')
                merged[key] = entry
    if not eligible <= covered or not {str(h['HistoryKey']) for h in history} <= set(merged):
        raise ValueError('분할 이력/원장 범위 불완전; 대조 필요')
    if any(merged[str(h['HistoryKey'])] != h for h in history):
        raise ValueError('분할 이력 조회 중 원장 범위 변경; 재대조 필요')
    listing['history'] = VerifiedPartitionHistory(merged.values())
    listing['history_partition_proof'] = proof
    return listing


def resolve(row, baseline, fetch, history_timestamp_mode=None):
    from core.keyword_forward import timestamp
    posted = timestamp(row['event']['timestamp'])
    if posted is None:
        raise ValueError('불량 원문 접수 시각 확인 필요')
    posted = posted.replace(tzinfo=KST) if posted.tzinfo is None else posted.astimezone(KST)
    period = {'year': int(baseline['year']), 'week': int(baseline['week'])}
    evidence, examined, partitions = [], [], []
    def result():
        return period, {'transitions': evidence, 'examined': examined,
                        'source_time': row['event']['timestamp'],
                        'history_timestamp_mode': history_timestamp_mode,
                        'history_partition_proof': partitions}
    for _ in range(54):
        examined.append(dict(period))
        listing = fetch(period)
        if listing.get('history_partition_proof'):
            partitions.append({'period': dict(period), 'partitions': listing['history_partition_proof']})
        history = listing.get('history')
        if not isinstance(history, list) or not isinstance(listing.get('rows'), list) or (len(history) >= 1000 and not isinstance(history, VerifiedPartitionHistory)):
            raise ValueError('차수 전환 이력 누락/조회 한도 초과; 대조 필요')
        registrations = []
        for entry in history:
            if entry.get('ActionType') != 'REGISTER_ESTIMATE':
                continue
            after = {str(k).lower(): v for k, v in json.loads(entry.get('AfterJson') or '{}').items()}
            if (str(after.get('appliedorderyear')) != str(period['year'])
                    or str(after.get('appliedorderweek')) != str(period['week'])
                    or after.get('deductiontype') != '불량차감' or not after.get('estimatekey')):
                continue
            stamp = datetime.fromisoformat(entry['ChangedAt'].replace('Z', '+00:00'))
            if stamp.tzinfo is None:
                raise ValueError('영업지원 이력 시간대 확인 필요')
            # SQL GETDATE serialized with Z can represent local wall time.
            # Until the server supplies explicit timezone semantics, accept
            # only outcomes identical under both interpretations.
            if history_timestamp_mode == 'database_kst_wall':
                bounds = [stamp.replace(tzinfo=KST)] * 2
            elif history_timestamp_mode is None:
                bounds = sorted((stamp.astimezone(KST), stamp.replace(tzinfo=KST)))
            else:
                raise ValueError('지원하지 않는 이력 시간대 정책')
            registrations.append((bounds, entry['HistoryKey']))
        if not registrations:
            if any(r.get('estimateKey') and str(r.get('appliedOrderYear')) == str(period['year'])
                   and str(r.get('appliedOrderWeek')) == str(period['week'])
                   and r.get('deductionType') == '불량차감' for r in listing['rows']):
                raise ValueError('견적 등록 행의 시각 이력 누락; 차수 대조 필요')
            return result()
        low = min(x[0][0] for x in registrations)
        high = min(x[0][1] for x in registrations)
        if posted + timedelta(minutes=1) <= low:
            return result()
        if posted <= high:
            raise ValueError('차수 전환 경계 시각/서버 시간대 대조 필요')
        evidence.append({'period': dict(period), 'history_keys': [x[1] for x in registrations],
                         'earliest_bound': low.isoformat(), 'latest_bound': high.isoformat()})
        if period['week'] >= 52:
            raise ValueError('연말 업무 차수 전환 규칙 확인 필요; 달력 자동 전환 금지')
        period['week'] += 1
    raise ValueError('차수 전환 조회 범위 초과')
