"""Numbered Kakao approval requests; no ambiguous or old reply is accepted."""
import json
import re
import time
from . import keyword_forward as k

REQUESTS = k.ROOT / 'data' / 'keyword_approval_requests.json'
APPROVER = '임재용대리'


def enqueue(event, batch_id=None):
    rows = k.read_json(REQUESTS, {})
    # Recovery must not enqueue a second request for the same source event.
    for old in rows.values():
        if any(e['event_id'] == event['event_id'] for e in old.get('events', [old['event']])):
            return old['id']
    rid = batch_id or event['event_id'].removeprefix('kakao_')[:12].upper()
    if rid not in rows:
        rows[rid] = {'id': rid, 'event': event, 'status': 'queued', 'created_at': time.time()}
        if batch_id:
            rows[rid]['events'] = []
    if batch_id:
        if rows[rid]['status'] != 'queued':
            raise RuntimeError('Cannot append to a dispatched approval batch')
        rows[rid]['events'].append(event)
    k.save_json(REQUESTS, rows)
    return rid


def batch_selection(content, row):
    """Require the request ID so delayed replies cannot approve another batch."""
    match = re.fullmatch(r'([A-Za-z0-9]+)\s+([0-9,\s]+)', content.strip())
    if not match or match[1].upper() != row['id']:
        return None
    choice = match[2].strip()
    if not re.fullmatch(r'\d+(?:\s*,\s*\d+)*', choice):
        return None
    numbers = [int(n.strip()) for n in choice.split(',')]
    count = len(row['events'])
    if numbers == [count + 1]:
        return list(range(count))
    if numbers == [count + 2]:
        return []
    if len(set(numbers)) != len(numbers) or any(n < 1 or n > count for n in numbers):
        return None
    return [i for i in range(count) if i + 1 not in numbers]


def request_message(row):
    rid = row['id']
    if 'events' in row:
        events = row['events']
        numbered = '\n\n'.join(f"{i}. {e['sender_name']} - {e['content']}" for i, e in enumerate(events, 1))
        n = len(events)
        return (f"[전달 승인 요청 {rid}]\n대상: {k.config()['target']}\n"
                f"추가·취소·변경 메시지 {n}건입니다. 몇 번을 빼고 보낼까요?\n\n{numbered}\n\n"
                f"{n+1}. 모두 보낸다\n{n+2}. 모두 안 보낸다\n"
                f"제외할 번호: {rid} 1 (여러 개는 쉼표로 구분)\n"
                f"모두 전달: {rid} {n+1}\n모두 생략: {rid} {n+2}\n"
                "다른 묶음과 혼동되지 않도록 요청번호도 함께 답해주세요.")
    event = row['event']
    # Kakao's RichEdit control can transform or truncate long multiline text.
    # Keep approval prompts compact; the authoritative original stays in 영업방.
    summary = re.sub(r'메시지가 삭제되었습니다\.?', '', event['content'])
    summary = re.sub(r'\s+', ' ', summary).strip()
    if len(summary) > 70:
        summary = summary[:67].rstrip() + '...'
    return (f"[전달 승인 {rid}]\n"
            f"{event['sender_name']} - {summary}\n"
            f"대상: {k.config()['target']}\n"
            f"보내 {rid} / 보내지마 {rid}")


def route_status(event, status, detail):
    rows = k.read_json(k.STATE, {})
    row = rows.get(event['event_id'])
    if row:
        row.update(status=status, detail=detail, at=time.time())
        k.save_json(k.STATE, rows)
        with k.LOG.open('a', encoding='utf-8') as f:
            f.write(json.dumps(row, ensure_ascii=False) + '\n')


def decision(replies, row):
    known = set(row.get('baseline', []))
    boundary = row.get('request_event_id')
    if not boundary:
        return None
    index = next((i for i, e in enumerate(replies) if e['event_id'] == boundary), None)
    if index is None:
        return None
    for event in replies[index + 1:]:
        if event['event_id'] in known or event.get('sender_name') != APPROVER:
            continue
        content = event['content'].strip()
        if 'events' in row:
            selected = batch_selection(content, row)
            if selected is not None:
                return selected
            continue
        match = re.fullmatch(r'(보내|보내지마)\s+([A-Za-z0-9]+)', content)
        if match and match[2].upper() == row['id']:
            return match[1]
    return None


def poll(export, send, paused, mark_rescan):
    if paused() or not k.config().get('enabled'):
        return
    rows = k.read_json(REQUESTS, {})
    active = [r for r in rows.values() if r['status'] in ('queued', 'waiting', 'approved')]
    if not active:
        return
    from core.moyi_inbound import parse_export

    def history():
        text = export(APPROVER)
        if not text.splitlines() or text.splitlines()[0].strip() not in (APPROVER + ' 님과 카카오톡 대화', APPROVER + ' 임과 카카오톡 대화'):
            raise RuntimeError('승인자 대화방 제목 불일치')
        return parse_export(text, 'keyword-approval')

    for row in active:
        if paused() or not k.config().get('enabled'):
            return
        event, rid = row['event'], row['id']
        events = row.get('events', [event])
        def report(status, detail):
            for item in events:
                route_status(item, status, detail)
        if row['status'] == 'queued':
            try:
                before = history()
            except Exception as exc:
                row['status'] = 'hold'
                k.save_json(REQUESTS, rows)
                report('확인 필요', f'승인요청 방 확인 실패: {exc}')
                continue
            row.update(status='request_unknown', baseline=[e['event_id'] for e in before])
            k.save_json(REQUESTS, rows)  # persist before any Enter; never auto resend
            message = request_message(row)
            try:
                if paused() or not k.config().get('enabled'):
                    raise RuntimeError('일시정지')
                send(APPROVER, message)
                after = history()
                new = [e for e in after if e['event_id'] not in row['baseline'] and k.normalize(e['content']) == k.normalize(message)]
                if len(new) != 1:
                    raise RuntimeError('승인요청 전송 결과 확인 불가')
                row.update(status='waiting', request_event_id=new[0]['event_id'])
                k.save_json(REQUESTS, rows)
                report('승인대기', f'요청 {rid} 전송 확인 · 임재용대리 답변 대기')
            except Exception as exc:
                report('확인 필요', f'요청 {rid} 결과 불명: {exc}; 자동 재전송 금지')
                continue
        if row['status'] == 'waiting':
            reply = decision(history(), row)
            if 'events' in row:
                if reply is None:
                    continue
                row.update(status='approved', selected=reply)
                k.save_json(REQUESTS, rows)
            else:
                if reply == '보내지마':
                    row['status'] = 'rejected'
                    k.save_json(REQUESTS, rows)
                    route_status(event, '승인거절', f'요청 {rid}: 임재용대리 보내지마')
                    continue
                if reply != '보내':
                    continue
                row['status'] = 'approved'
                k.save_json(REQUESTS, rows)
                route_status(event, '승인됨', f'요청 {rid}: 임재용대리 보내')
        if row['status'] == 'approved':
            if 'events' in row:
                for i, item in enumerate(events):
                    if paused() or not k.config().get('enabled'):
                        return
                    current = k.read_json(k.STATE, {}).get(item['event_id'], {}).get('status')
                    if current not in ('승인대기', '승인됨'):
                        continue  # Never replay sent, failed, or uncertain items.
                    if i not in row['selected']:
                        route_status(item, '승인거절', f'요청 {rid}: 제외 선택')
                        continue
                    route_status(item, '승인됨', f'요청 {rid}: 전달 선택')
                    cfg = k.config()
                    mark_rescan(cfg['target'])
                    k.process_source(cfg['source'], [item], export, send, paused)
                remaining = k.read_json(k.STATE, {})
                if all(remaining.get(e['event_id'], {}).get('status') not in ('승인대기', '승인됨') for e in events):
                    row['status'] = 'resolved'
                    k.save_json(REQUESTS, rows)
                continue
            current = k.read_json(k.STATE, {}).get(event['event_id'], {}).get('status')
            if current == '승인대기':
                route_status(event, '승인됨', f'요청 {rid}: 저장된 승인 복구')
            cfg = k.config()
            mark_rescan(cfg['target'])
            k.process_source(cfg['source'], [event], export, send, paused)
            result = k.read_json(k.STATE, {}).get(event['event_id'], {}).get('status')
            if result != '승인됨':
                row['status'] = 'resolved'
                k.save_json(REQUESTS, rows)
