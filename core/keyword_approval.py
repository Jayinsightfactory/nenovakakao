"""Numbered Kakao approval requests; no ambiguous or old reply is accepted."""
import json
import re
import time
from datetime import datetime
from . import keyword_forward as k
from core.moyi_control import OperationPaused

REQUESTS = k.ROOT / 'data' / 'keyword_approval_requests.json'
from core.operator_settings import operator_name, LEGACY_NAME
APPROVER = LEGACY_NAME  # Legacy request ownership and compatibility.
REMINDER_SEC = 300
HOLD_SEC = 900
PRECHECK_RETRY_SEC = 15
NEW_QUESTION_GAP_SEC = 60
# A request with an unknown send result must never be resent, but it must not
# freeze every later question forever. Replies require the request ID, so a
# newer verified prompt remains unambiguous.
BLOCKING = {'approved'}
LABELS = '가나다라마바사아자차카타파하'


def choice_label(index):
    return LABELS[index] if index < len(LABELS) else choice_label(index // len(LABELS) - 1) + LABELS[index % len(LABELS)]


def has_pending_question():
    return any(r['status'] in ('waiting', 'approved', 'queued')
               for r in k.read_json(REQUESTS, {}).values())


def blocks_new_question(row, now):
    if row.get('status') == 'approved':
        return True
    if row.get('status') != 'waiting':
        return False
    if row.get('choice_format') == 'per_item':
        return True
    sent_at = row.get('sent_at')
    return sent_at is None or now - sent_at < NEW_QUESTION_GAP_SEC


def pending_action(row, now):
    """Only verified sent requests start the unanswered timer."""
    if row.get('status') == 'historical_review' or not row.get('sent_at'): return None
    age = now - row['sent_at']
    if age >= HOLD_SEC: return 'hold' if row['status'] == 'waiting' else None
    if age >= REMINDER_SEC and not row.get('reminder_attempted_at'): return 'remind'
    return None


def is_batch(row):
    return len(row.get('events', [])) > 1


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


def filter_queued_requests(rows):
    """Remove legacy false positives only before a question has ever been sent."""
    changed = False
    for row in rows.values():
        if row.get('status') != 'queued':
            continue
        events = row.get('events') or [row.get('event', {})]
        actionable = [event for event in events if k.requires_approval(event.get('content', ''))]
        if len(actionable) == len(events):
            continue
        changed = True
        if not actionable:
            row.update(status='filtered_non_actionable', filtered_at=time.time())
            continue
        row['event'] = actionable[0]
        if 'events' in row:
            row['events'] = actionable
    return changed


def hold_old_requests(rows, cfg, now=None):
    """Keep old originals and IDs, but stop automatic prompts/reminders."""
    if not cfg.get('approval_current_day_only'):
        return False
    today = (now or datetime.now(k.KST)).date()
    changed = False
    for row in rows.values():
        if row.get('status') not in ('queued', 'waiting', 'awaiting_late_reply'):
            continue
        events = row.get('events') or [row['event']]
        dates = [k.timestamp(e.get('timestamp', '')) for e in events]
        if any(stamp is None or stamp.date() != today for stamp in dates):
            row.update(previous_status=row['status'], status='historical_review',
                       held_at=time.time(), hold_reason='오늘 이전 원문 또는 날짜 확인 필요; 별도 검토')
            changed = True
    return changed


def question_hours_open(cfg, now=None):
    hours = cfg.get('approval_active_hours')
    if not hours:
        return True
    hour = (now or datetime.now(k.KST)).hour
    start, end = hours
    return start <= hour < end if start <= end else hour >= start or hour < end


def refresh_historical_review(rows):
    """Derived counts only; approval requests remain the authoritative record."""
    path = REQUESTS.with_name('historical_approval_review.json')
    snapshot = [{'id': row['id'], 'status': row['status'],
                 'previous_status': row.get('previous_status', ''),
                 'items': len(row.get('events') or [row['event']])}
                for row in rows.values() if row.get('status') == 'historical_review']
    if k.read_json(path, None) != snapshot:
        k.save_json(path, snapshot)


def batch_selection(content, row):
    """Require the request ID so delayed replies cannot approve another batch."""
    if row.get('choice_format') == 'hangul':
        parts = content.strip().split(maxsplit=1)
        if len(parts) != 2 or parts[0].upper() != row['id']:
            return None
        count = len(row['events'])
        labels = [choice_label(i) for i in range(count + 2)]
        choices = [s for s in re.split(r'[,\s]+', parts[1].strip()) if s]
        if choices == [labels[count]]:
            return list(range(count))
        if choices == [labels[count + 1]]:
            return []
        if not choices or len(set(choices)) != len(choices) or any(c not in labels[:count] for c in choices):
            return None
        return [i for i in range(count) if labels[i] not in choices]
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
    row['choice_format'] = 'per_item'
    events = row.get('events') or [row['event']]
    originals = '\n\n'.join(
        f"{choice_label(i)} — {e['sender_name']}\n{e['content']}"
        for i, e in enumerate(events))
    return (f"[전달 승인 요청 {row['id']}]\n대상: {k.config()['target']}\n\n{originals}\n\n"
            "답변 예시: 가 보내" + (" 나 안보내" if len(events) > 1 else " / 가 안보내") + "\n"
            "한 건씩 따로 답해도 됩니다. 답하지 않은 건은 대기합니다.\n"
            f"이전 요청에는 요청번호도 붙여주세요: {row['id']} 가 보내")


def route_status(event, status, detail):
    from core.error_notifications import report
    request_match = re.search(r'요청\s+([A-Fa-f0-9]{12})', detail)
    request_id = request_match[1] if request_match else ''
    reports = {'승인대기': 'approval_waiting', '승인됨': 'approval_accepted', '승인거절': 'approval_rejected'}
    if status in reports:
        match = re.search(r'요청\s+([A-Fa-f0-9]{12})', detail)
        report(reports[status], match[1] if match else '')
    if status in ('확인 필요', '결과 불명'):
        from core.error_notifications import notify
        category = ('approval_input_mismatch' if '입력란 원문 검증 실패' in detail
                    else 'approval_error')
        notify(category, request_id, {
            'source_room': k.config().get('source', '영업방'),
            'target_room': k.config().get('target', '현장 추가취소방'),
            'sender': event.get('sender_name', ''), 'preview': event.get('content', ''),
            'stage': status, 'cause': detail,
            'automatic_action': '승인 질문과 현장 전달을 중단하고 중복 여부가 확인될 때까지 보류',
        })
    rows = k.read_json(k.STATE, {})
    row = rows.get(event['event_id'])
    if row:
        row.update(status=status, detail=detail, at=time.time())
        k.save_json(k.STATE, rows)
        with k.LOG.open('a', encoding='utf-8') as f:
            f.write(json.dumps(row, ensure_ascii=False) + '\n')


def decision(replies, row, allow_short=False):
    approver = row.get('approver_name', LEGACY_NAME)
    known = set(row.get('baseline', []))
    boundary = row.get('request_event_id')
    if not boundary:
        return None
    index = next((i for i, e in enumerate(replies) if e['event_id'] == boundary), None)
    if index is None:
        return None
    short_context = allow_short
    item_answers = {}
    for event in replies[index + 1:]:
        # An intervening approval prompt makes bare replies refer to that
        # newer prompt, never an older pending request.
        if event.get('sender_name') != approver and re.match(r'\[전달 승인', event.get('content', '')):
            short_context = False
        if event['event_id'] in known or event.get('sender_name') != approver:
            continue
        content = event['content'].strip()
        if row.get('choice_format') == 'per_item':
            parts = content.split(maxsplit=1)
            if parts and parts[0].upper() == row['id']:
                content = parts[1] if len(parts) == 2 else ''
            elif not short_context:
                continue
            matches = list(re.finditer(r'([가-하]+)\s+(안\s*보내|보내)', content))
            residue = re.sub(r'([가-하]+)\s+(안\s*보내|보내)', '', content)
            labels = {choice_label(i): i for i in range(len(row.get('events') or [row['event']]))}
            if (not matches or residue.strip(' ,\n\t') or
                    any(m[1] not in labels for m in matches) or
                    len({m[1] for m in matches}) != len(matches)):
                continue
            for m in matches:
                # The first explicit decision is immutable after processing.
                item_answers.setdefault(labels[m[1]], 'approve' if m[2] == '보내' else 'reject')
            continue
        if is_batch(row):
            selected = batch_selection(content, row)
            if selected is None and short_context:
                selected = batch_selection(row['id'] + ' ' + content, row)
            if selected is not None:
                return selected
            continue
        if row.get('choice_format') == 'hangul':
            for label, answer in (('가', '보내'), ('나', '보내지마')):
                if content == row['id'] + ' ' + label or (short_context and content == label):
                    return answer
        match = re.fullmatch(r'(보내|보내지마)\s+([A-Za-z0-9]+)', content)
        if match and match[2].upper() == row['id']:
            return match[1]
        if short_context and content in ('보내', '보내지마'):
            return content
    return item_answers or None


def verified_request(replies, row):
    """Positive evidence only; absence never authorizes a resend."""
    approver = row.get('approver_name', LEGACY_NAME)
    message = row.get('request_payload')
    if not message or 'baseline' not in row:
        return None  # Legacy attempts need manual reconciliation.
    baseline = set(row['baseline'])
    matches = [e for e in replies
               if e['event_id'] not in baseline
               and e.get('sender_name') != approver
               and k.normalize(e['content']) == k.normalize(message)]
    return matches[0] if len(matches) == 1 else None


def notify_results(row):
    from core.error_notifications import approval_result
    state = k.read_json(k.STATE, {})
    for index, event in enumerate(row.get('events') or [row['event']]):
        approval_result(row['id'], event['event_id'], choice_label(index),
                        state.get(event['event_id'], {}).get('status'))


def hold_previous_operator_requests(rows, current):
    changed = False
    for row in rows.values():
        if row.get('status') in ('waiting', 'awaiting_late_reply', 'approved', 'request_unknown', 'historical_review') and row.get('approver_name', LEGACY_NAME) != current:
            row.update(approver_name=row.get('approver_name', LEGACY_NAME),
                       previous_operator_status=row['status'], status='operator_changed_review',
                       operator_changed_at=time.time())
            changed = True
    return changed


def poll(export, send, paused, mark_rescan):
    if paused() or not k.config().get('enabled'):
        return
    approver = operator_name()
    rows = k.read_json(REQUESTS, {})
    changed = hold_previous_operator_requests(rows, approver)
    changed = filter_queued_requests(rows) or changed
    changed = hold_old_requests(rows, k.config()) or changed
    if changed:
        k.save_json(REQUESTS, rows)
    refresh_historical_review(rows)
    active = [r for r in rows.values() if r['status'] in ('queued', 'waiting', 'awaiting_late_reply', 'approved', 'request_unknown')
              or (r['status'] == 'historical_review' and r.get('request_event_id'))]
    rank = {'approved': 0, 'waiting': 1, 'awaiting_late_reply': 1,
            'historical_review': 1,
            'queued': 2, 'request_unknown': 3}
    # Operational additions/cancellations are time-sensitive. When no prompt
    # is currently waiting, dispatch the newest queued request first. Older
    # rows remain durable and are not silently approved or discarded.
    active.sort(key=lambda r: (rank[r['status']],
        -r.get('created_at', 0) if r['status'] == 'queued' else r.get('created_at', 0)))
    if not active:
        return
    from core.moyi_inbound import parse_export

    cached_history = None
    def history(refresh=False):
        nonlocal cached_history
        if cached_history is not None and not refresh:
            return cached_history
        text = export(approver)
        if not text.splitlines() or text.splitlines()[0].strip() not in (approver + ' 님과 카카오톡 대화', approver + ' 임과 카카오톡 대화'):
            raise RuntimeError('승인자 대화방 제목 불일치')
        cached_history = parse_export(text, 'keyword-approval')
        return cached_history

    for row in active:
        if paused() or operator_name() != approver or not k.config().get('enabled'):
            return
        event, rid = row['event'], row['id']
        events = row.get('events', [event])
        def report(status, detail):
            for item in events:
                current = k.read_json(k.STATE, {}).get(item['event_id'], {}).get('status')
                if status == '미응답 보류' and current in ('전송 성공', '중복 생략', '승인거절'):
                    continue
                route_status(item, status, detail)
        if row['status'] == 'request_unknown':
            if not row.get('request_payload'):
                continue
            verified = verified_request(history(), row)
            if verified is None:
                continue
            # Do not guess the original send time or accept an ambiguous bare
            # reply after recovery. An explicit request ID is required.
            row.update(status='awaiting_late_reply', request_event_id=verified['event_id'],
                       recovered_at=time.time())
            k.save_json(REQUESTS, rows)
            from core.error_notifications import resolve
            resolve('approval_error', rid)
            report('승인대기', f'요청 {rid} 대화 기록에서 전송 확인 복구; 요청번호 포함 답변 필요')
        if row['status'] == 'queued':
            if not question_hours_open(k.config()):
                continue
            if any(other['id'] != rid and blocks_new_question(other, time.time())
                   for other in rows.values()):
                continue
            if row.get('precheck_retry_at', 0) > time.time():
                continue
            # The target may have received the message manually after source
            # detection but before this approval prompt is dispatched. Recheck
            # at the last responsible moment so we do not ask for approval for
            # work that is already present.
            try:
                cfg = k.config()
                target = cfg['target']
                target_text = export(target)
                if target not in '\n'.join(target_text.splitlines()[:3]):
                    raise RuntimeError('대상 방 내보내기 제목 불일치')
                target_events = parse_export(target_text, 'keyword-target-preapproval')
                remaining = [item for item in events
                             if not k.duplicate(item['content'], target_events)]
                for item in events:
                    if item not in remaining:
                        route_status(item, '중복 생략', '승인 직전 대상 방 재확인: 동일 원문 존재')
                if not remaining:
                    row['status'] = 'resolved_existing'
                    k.save_json(REQUESTS, rows)
                    continue
                if len(remaining) != len(events):
                    row['events'] = remaining
                    row['event'] = remaining[0]
                    event, events = remaining[0], remaining
                    k.save_json(REQUESTS, rows)
                from core.error_notifications import resolve
                resolve('approval_error', rid)
            except OperationPaused:
                raise
            except Exception as exc:
                row['precheck_retry_at'] = time.time() + PRECHECK_RETRY_SEC
                k.save_json(REQUESTS, rows)
                report('확인 필요', f'요청 {rid}: 승인 전 대상 방 중복 확인 실패: {exc}')
                return
            try:
                before = history()
            except OperationPaused:
                raise
            except Exception as exc:
                row['status'] = 'hold'
                k.save_json(REQUESTS, rows)
                report('확인 필요', f'승인요청 방 확인 실패: {exc}')
                continue
            message = request_message(row)
            row.update(status='request_unknown', approver_name=approver, baseline=[e['event_id'] for e in before],
                       request_payload=message, request_attempted_at=time.time())
            k.save_json(REQUESTS, rows)  # persist before any Enter; never auto resend
            try:
                if paused() or operator_name() != approver or not k.config().get('enabled'):
                    raise OperationPaused('일시정지')
                send(approver, message)
                after = history(refresh=True)
                verified = verified_request(after, row)
                if verified is None:
                    raise RuntimeError('승인요청 전송 결과 확인 불가')
                row.update(status='waiting', request_event_id=verified['event_id'], sent_at=time.time())
                k.save_json(REQUESTS, rows)
                report('승인대기', f'요청 {rid} 전송 확인 · 승인 담당자 답변 대기')
            except OperationPaused:
                raise
            except Exception as exc:
                report('확인 필요', f'요청 {rid} 결과 불명: {exc}; 자동 재전송 금지')
            # Never cascade into another queued approval in the same poll.
            # A fresh poll gets a fresh UI/history preflight.
            return
        if row['status'] in ('waiting', 'awaiting_late_reply', 'historical_review'):
            reply = decision(history(), row, allow_short=row['status'] == 'waiting')
            if row.get('choice_format') == 'per_item':
                saved = row.get('item_answers', {})
                fresh = {i: value for i, value in (reply or {}).items() if str(i) not in saved}
                state = k.read_json(k.STATE, {})
                for i, item in enumerate(events):
                    if str(i) in saved and state.get(item['event_id'], {}).get('status') in (
                            '승인요청 전송대기', '승인대기', '미응답 보류', '승인됨'):
                        fresh[i] = saved[str(i)]  # Resume a saved decision before advancing timers.
                reply = fresh or None
            if reply is None:
                action = pending_action(row, time.time())
                if action == 'hold':
                    row['status'] = 'awaiting_late_reply'
                    k.save_json(REQUESTS, rows)
                    from core.error_notifications import notify
                    notify('approval_unanswered', rid, {
                        'source_room': k.config().get('source', '영업방'),
                        'target_room': k.config().get('target', '현장 추가취소방'),
                        'sender': event.get('sender_name', ''), 'preview': event.get('content', ''),
                        'stage': '승인 담당자 답변 대기',
                        'cause': '승인 질문 전송 확인 후 15분 동안 답변 없음',
                        'automatic_action': '현장방으로 보내지 않고 늦은 답변 대기 상태로 보류',
                    })
                    report('미응답 보류', f'요청 {rid}: 15분 미응답; 자동 승인 없음; 요청번호 포함 답변 필요')
                elif action == 'remind' and k.config().get('approval_individual_reminders', False):
                    row['reminder_attempted_at'] = time.time()
                    k.save_json(REQUESTS, rows)
                    count = sum(r['status'] == 'queued' for r in rows.values())
                    payload = (f'[승인 답변 재안내 {rid}]\n답변 대기 중입니다. 뒤에 대기 {count}묶음.\n'
                               f'원래 질문에 요청번호 {rid}를 포함하여 답해주세요.\n'
                               '답변 없이 자동 전달하지 않습니다.')
                    try:
                        if paused(): return
                        before = {e['event_id'] for e in history()}
                        send(approver, payload)
                        matched = [e for e in history(refresh=True) if e['event_id'] not in before
                                   and k.normalize(e['content']) == k.normalize(payload)]
                        row['reminder_status'] = 'sent' if len(matched) == 1 else 'unknown'
                    except OperationPaused:
                        raise
                    except Exception:
                        row['reminder_status'] = 'unknown'
                    k.save_json(REQUESTS, rows)
                    if row['reminder_status'] == 'unknown':
                        from core.error_notifications import notify
                        notify('approval_error', rid)
                continue
            from core.error_notifications import resolve
            resolve('approval_unanswered', rid)
            if row.get('choice_format') == 'per_item':
                answers = row.setdefault('item_answers', {})
                for index, answer in reply.items():
                    answers.setdefault(str(index), answer)
                k.save_json(REQUESTS, rows)
                for index, item in enumerate(events):
                    answer = answers.get(str(index))
                    if answer is None:
                        continue
                    if paused() or not k.config().get('enabled'):
                        return
                    current = k.read_json(k.STATE, {}).get(item['event_id'], {}).get('status')
                    if current not in ('승인요청 전송대기', '승인대기', '미응답 보류', '승인됨'):
                        continue
                    if answer == 'reject':
                        route_status(item, '승인거절', f'요청 {rid}: 항목별 안보내')
                        continue
                    route_status(item, '승인됨', f'요청 {rid}: 항목별 보내')
                    cfg = k.config()
                    mark_rescan(cfg['target'])
                    k.process_source(cfg['source'], [item], export, send, paused)
                state = k.read_json(k.STATE, {})
                notify_results(row)
                if len(answers) == len(events) and all(
                        state.get(item['event_id'], {}).get('status') not in
                        ('승인요청 전송대기', '승인대기', '미응답 보류', '승인됨') for item in events):
                    row['status'] = 'resolved'
                k.save_json(REQUESTS, rows)
                continue
            if is_batch(row):
                if reply is None:
                    continue
                row.update(status='approved', selected=reply)
                k.save_json(REQUESTS, rows)
            else:
                if reply == '보내지마':
                    row['status'] = 'rejected'
                    k.save_json(REQUESTS, rows)
                    route_status(event, '승인거절', f'요청 {rid}: 승인 담당자 보내지마')
                    notify_results(row)
                    continue
                if reply != '보내':
                    continue
                row['status'] = 'approved'
                k.save_json(REQUESTS, rows)
                route_status(event, '승인됨', f'요청 {rid}: 승인 담당자 보내')
        if row['status'] == 'approved':
            if is_batch(row):
                for i, item in enumerate(events):
                    if paused() or not k.config().get('enabled'):
                        return
                    current = k.read_json(k.STATE, {}).get(item['event_id'], {}).get('status')
                    if current not in ('승인요청 전송대기', '승인대기', '미응답 보류', '승인됨'):
                        continue  # Never replay sent, failed, or uncertain items.
                    if i not in row['selected']:
                        route_status(item, '승인거절', f'요청 {rid}: 제외 선택')
                        continue
                    route_status(item, '승인됨', f'요청 {rid}: 전달 선택')
                    cfg = k.config()
                    mark_rescan(cfg['target'])
                    k.process_source(cfg['source'], [item], export, send, paused)
                remaining = k.read_json(k.STATE, {})
                notify_results(row)
                if all(remaining.get(e['event_id'], {}).get('status') not in ('승인요청 전송대기', '승인대기', '미응답 보류', '승인됨') for e in events):
                    row['status'] = 'resolved'
                    k.save_json(REQUESTS, rows)
                continue
            current = k.read_json(k.STATE, {}).get(event['event_id'], {}).get('status')
            if current in ('승인요청 전송대기', '승인대기', '미응답 보류'):
                route_status(event, '승인됨', f'요청 {rid}: 저장된 승인 복구')
            cfg = k.config()
            mark_rescan(cfg['target'])
            k.process_source(cfg['source'], [event], export, send, paused)
            result = k.read_json(k.STATE, {}).get(event['event_id'], {}).get('status')
            notify_results(row)
            if result != '승인됨':
                row['status'] = 'resolved'
                k.save_json(REQUESTS, rows)
