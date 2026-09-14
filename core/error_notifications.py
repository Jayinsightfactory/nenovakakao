"""Durable error notices to the administrator, separate from approvals."""
import hashlib
import json
import re
import time
import uuid
from pathlib import Path
from core.atomic_json import save

STATE = Path(__file__).resolve().parent.parent / 'data' / 'error_notifications.json'
OPERATOR_LOG = Path(__file__).resolve().parent.parent / 'logs' / 'operator_alerts.jsonl'
from core.operator_settings import operator_name
RECIPIENT = '임재용대리'  # Legacy compatibility; sending uses operator_name().
COOLDOWN = 1800
REPORT_LABELS = {
    'server_recovered': '서버 대기 목록 조회 정상 복구',
    'worker_started': '매크로 시작', 'pause_requested': '일시정지 요청',
    'resume_requested': '재개 요청', 'emergency_stopped': '긴급 정지 완료',
    'stop_failed': '정지 실패 — 관리자 확인 필요', 'worker_start_failed': '워커 시작 실패',
    'credential_saved': '담당자 계정 보안 저장', 'staff_review_enabled': '담당자 주문 확인 기능 설정',
    'manual_reconciliation': '수동처리 결과 반영', 'program_updated': '매크로 프로그램 최신화 완료',
    'order_received': '신규 주문 접수', 'order_review_sent': '담당자에게 주문 확인 요청 전송',
    'order_confirm': '담당자 주문 내용 확인', 'order_register': '주문 등록 요청 처리',
    'order_cancel': '주문 취소 처리', 'order_candidates': '품목 선택 반영',
    'order_quantity': '수량 수정 반영', 'order_product': '품목 수정 반영',
    'order_account_consent': '담당자 계정 사용 동의/거절 반영', 'order_hold': '주문 보류',
    'order_completed': '실제 주문 등록 결과 검증 완료',
    'order_completed_simulation': '주문 테스트 완료 — 실제 등록 없음',
    'order_changed': '주문 정보 수정 반영', 'approval_waiting': '승인 담당자 답변 대기',
    'approval_accepted': '전달 승인 반영', 'approval_rejected': '전달 거절/제외 반영',
    'forward_sent': '메시지 전달 확인', 'forward_skipped': '중복 메시지 전달 생략',
    'outbound_sent': '서버 요청 메시지 전송 처리', 'inbound_processed': '새 대화 수집 처리',
    'archive_completed': '자료 보관 처리 완료',
}
NOISY_REPORTS = {'inbound_processed', 'archive_completed', 'forward_skipped'}

ERROR_GUIDANCE = {
    'order_error': ('발주 답변 처리', '담당자의 품목·수량 수정 또는 등록 요청을 정상 반영하지 못했습니다.',
                    '해당 주문은 완료 처리하지 않았고 자동 등록도 중지했습니다.',
                    '같은 주문을 다시 등록하지 말고 아래 요청번호로 현재 주문 확인 메시지를 기다려주세요.'),
    'approval_error': ('추가취소 승인 처리', '승인 질문 전송 또는 승인 답변 확인 결과를 확정하지 못했습니다.',
                       '승인되지 않은 내용은 현장 추가취소방에 보내지 않았습니다.',
                       '중복 전송하지 말고 요청번호가 포함된 다음 안내를 확인해주세요.'),
    'approval_input_mismatch': ('추가취소 승인 질문 전송', '카카오톡 입력란의 내용이 보낼 승인 질문과 달라 Enter 전 전송 차단했습니다.',
                                '승인 질문과 추가취소 내용 모두 전송하지 않았습니다.',
                                '프로그램이 입력란을 비운 뒤 새 승인 질문으로 다시 처리합니다.'),
    'approval_unanswered': ('추가취소 승인 대기', '승인 질문에 15분 동안 답변이 없어 해당 요청을 보류했습니다.',
                            '답변 없이 현장 추가취소방으로 자동 전달하지 않습니다.',
                            '처리하려면 요청번호와 항목을 함께 답해주세요. 예: 요청번호 가 보내 / 요청번호 나 안보내.'),
    'forward_error': ('영업방 추가취소 전달', '현장 추가취소방의 기존 내용 확인 또는 전달 결과 검증에 실패했습니다.',
                      '해당 내용은 완료 처리하지 않았으며 불명확한 전송을 자동 반복하지 않습니다.',
                      '다음 승인 질문을 기다려주세요. 이미 수동 처리했다면 보내지마로 답해주세요.'),
    'unknown_result': ('카카오톡 메시지 전송', 'Enter 입력 뒤 대상 방에서 동일 메시지를 확인하지 못했습니다.',
                       '전송 여부가 불명확하여 같은 메시지의 자동 재전송을 차단했습니다.',
                       '대상 방에 메시지가 있는지 확인한 뒤 중복되지 않게 처리해주세요.'),
    'approval_check_failed': ('승인 담당자 답변 확인', '승인 담당자 대화방을 읽지 못해 승인 답변을 확인하지 못했습니다.',
                              '기존 승인 상태를 유지하며 추가취소 전달은 진행하지 않았습니다.',
                              '프로그램이 다음 회차에 답변을 다시 확인합니다.'),
    'import_order_check_failed': ('발주 확인 답변 처리', '담당자 대화방을 읽지 못해 발주 확인·수정 답변을 처리하지 못했습니다.',
                                  '주문 등록은 실행하지 않았고 현재 발주 상태를 유지했습니다.',
                                  '프로그램이 다음 회차에 같은 답변을 다시 확인합니다.'),
    'order_capture_failed': ('수입방 신규 발주 분석', '수입방 메시지에서 차수·업체·품목 정보를 안전하게 추출하지 못했습니다.',
                             '해당 메시지는 주문 초안이나 실제 주문으로 등록하지 않았습니다.',
                             '원문을 보존했으며 프로그램 검증 후 다시 분석합니다.'),
    'inbound_room_failed': ('카카오톡 대화방 수집', '대화 저장 완료창 또는 방 포커스 문제로 해당 방 내용을 읽지 못했습니다.',
                            '해당 회차의 신규 메시지는 전송·등록하지 않았습니다.',
                            '프로그램이 창을 정리하고 다음 회차에 해당 방을 다시 확인합니다.'),
    'inbound_scan_failed': ('카카오톡 감시방 목록 확인', '감시 대상 대화방 목록을 불러오지 못했습니다.',
                            '새 메시지 수집만 보류하고 이미 확인된 작업은 변경하지 않았습니다.',
                            '프로그램이 연결 상태를 확인한 뒤 다시 조회합니다.'),
    'failed_not_sent': ('카카오톡 메시지 전송', '대상 방을 정확히 확인하지 못해 전송 전에 작업을 차단했습니다.',
                        '메시지는 전송되지 않았습니다.', '프로그램이 대상 방을 다시 확인하기 전에는 전송하지 않습니다.'),
    'pending_unavailable': ('서버 대기 작업 조회', '서버 연결 문제로 새 전송 요청을 가져오지 못했습니다.',
                            '기존 작업을 중복 실행하지 않고 새 요청 수신만 지연됩니다.',
                            '프로그램이 간격을 늘려 자동 재접속합니다.'),
    'archive_deferred': ('대화 자료 보관', '수집된 원문을 보관 서버에 저장하지 못했습니다.',
                         '카카오 전송과 주문 상태에는 영향을 주지 않으며 원문은 로컬 대기열에 남아 있습니다.',
                         '프로그램이 다음 보관 회차에 다시 저장합니다.'),
}


def report(category, request_id=''):
    """Independent spool files avoid console/worker read-modify-write races."""
    if category not in REPORT_LABELS: return
    key = uuid.uuid4().hex
    request_id = str(request_id)
    if not re.fullmatch(r'(?:ORD-)?[A-Fa-f0-9-]{6,64}', request_id): request_id = ''
    value = {'id': key, 'category': category, 'request_id': request_id,
             'kind': 'report', 'status': 'queued', 'created_at': time.time(), 'updated_at': time.time()}
    try:
        save(STATE.parent / 'operation_reports' / (key + '.json'), value)
    except OSError:
        # Reporting must not turn an already successful business write into
        # a retryable operation. Keep its business state unchanged.
        from core.moyi_control import audit
        audit('report_queue_failed', '상황 보고 저장 실패 · 프로그램 확인 필요')


def collect_reports():
    # This directory also holds previews and diagnostic artifacts, not spools.
    paths = []
    for path in sorted((STATE.parent / 'operation_reports').glob('*.json')):
        try:
            row = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue  # Diagnostic files are not necessarily finished spools.
        if (isinstance(row, dict) and row.get('kind') == 'report'
                and row.get('category') in REPORT_LABELS
                and all(field in row for field in ('id', 'status', 'created_at', 'updated_at'))):
            paths.append(path)
        if len(paths) == 100:
            break
    if not paths: return
    rows = _rows()
    for path in paths:
        row = json.loads(path.read_text(encoding='utf-8'))
        if row.get('category') in NOISY_REPORTS:
            bucket = int(row['created_at'] // 3600)
            key = hashlib.sha256(
                f"report:{row['category']}:{row.get('request_id', '')}:{bucket}".encode()
            ).hexdigest()
            old = rows.get(key)
            if old:
                if old.get('status') == 'queued':
                    old['count'] = int(old.get('count', 1)) + 1
                    old['updated_at'] = max(old.get('updated_at', 0), row['updated_at'])
                    old['retry_at'] = (bucket + 1) * 3600
                # Never turn a sent or uncertain hourly report back into queued.
            else:
                row.update(id=key, count=1, retry_at=(bucket + 1) * 3600)
                rows[key] = row
        else:
            rows.setdefault(row['id'], row)
    save(STATE, rows)
    for path in paths: path.unlink()


def approval_result(request_id, event_id, label, result):
    """Durable per-item receipts; repeated polls or restarts never resend one."""
    descriptions = {
        '승인거절': '안보내 — 전달하지 않도록 처리했습니다.',
        '전송 성공': '보내 — 전달 완료했습니다.',
        '중복 생략': '보내 — 이미 같은 내용이 있어 중복 전달하지 않도록 처리했습니다.',
    }
    if result not in descriptions:
        return
    key = hashlib.sha256(f'approval-result:{request_id}:{event_id}'.encode()).hexdigest()
    rows = _rows()
    if key in rows:
        return
    rows[key] = {'id': key, 'category': 'approval_result', 'kind': 'receipt',
                 'request_id': request_id, 'status': 'queued',
                 'created_at': time.time(), 'updated_at': time.time(),
                 'result_text': f'{label}: {descriptions[result]}'}
    save(STATE, rows)


def _rows():
    return json.loads(STATE.read_text(encoding='utf-8')) if STATE.exists() else {}


CONTEXT_FIELDS = ('source_room', 'target_room', 'sender', 'preview', 'stage', 'cause', 'automatic_action')


def _safe_context(context):
    """Keep only bounded, operator-facing facts; strip likely secrets and links."""
    clean = {}
    for field in CONTEXT_FIELDS:
        value = str((context or {}).get(field) or '').strip()
        if not value:
            continue
        value = re.sub(r'https?://\S+', '[링크 생략]', value, flags=re.I)
        value = re.sub(r'(?i)(password|passwd|비밀번호|token|secret)\s*[:=]\s*\S+', r'\1=[보안정보 생략]', value)
        value = re.sub(r'\s+', ' ', value)[:240]
        clean[field] = value
    return clean


def _operator_event(event, row):
    """Durable local evidence independent from Kakao notification delivery."""
    try:
        OPERATOR_LOG.parent.mkdir(parents=True, exist_ok=True)
        record = {
            'at': time.time(), 'event': event, 'id': row.get('id', ''),
            'category': row.get('category', ''), 'request_id': row.get('request_id', ''),
            'status': row.get('status', ''), 'context': _safe_context(row.get('context')),
        }
        with OPERATOR_LOG.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + '\n')
    except OSError:
        # The business operation remains fail-closed even if diagnostics fail.
        pass


def enqueue(category, request_id='', context=None):
    category = re.sub(r'[^a-z_]', '', category)[:64]
    request_id = str(request_id)
    if not re.fullmatch(r'(?:ORD-)?[A-Fa-f0-9-]{6,64}', request_id):
        request_id = ''
    key = hashlib.sha256(f'{category}:{request_id}'.encode()).hexdigest()[:12]
    rows = _rows(); old = rows.get(key); now = time.time()
    if old and (old['status'] != 'sent' or now - old['updated_at'] < COOLDOWN):
        new_context = _safe_context(context)
        if new_context and old.get('status') == 'queued':
            old['context'] = new_context
            old['last_seen_at'] = now
            old['occurrences'] = int(old.get('occurrences', 1)) + 1
            save(STATE, rows)
            _operator_event('incident_updated', old)
        # Suppressed repeats do not rewrite the file on every poll.
        return key
    rows[key] = {'id': key, 'category': category, 'request_id': request_id,
                 'status': 'queued', 'created_at': now, 'updated_at': now,
                 'context': _safe_context(context), 'occurrences': 1}
    save(STATE, rows)
    _operator_event('incident_opened', rows[key])
    return key


def notify(category, request_id='', context=None):
    try:
        enqueue(category, request_id, context)
    except Exception:
        from core.moyi_control import audit
        audit('error_notice_queue_failed', '오류 알림 저장 실패 · 프로그램 확인 필요')


def resolve(category, request_id=''):
    """Suppress a queued notice when positive recovery evidence now exists."""
    category = re.sub(r'[^a-z_]', '', category)[:64]
    request_id = str(request_id)
    key = hashlib.sha256(f'{category}:{request_id}'.encode()).hexdigest()[:12]
    rows = _rows()
    row = rows.get(key)
    if row and row.get('status') == 'queued':
        row.update(status='resolved', updated_at=time.time())
        save(STATE, rows)
        _operator_event('incident_resolved', row)
        return True
    return False


def resolve_all(category):
    """Close queued incidents after a later successful run proves recovery."""
    rows = _rows(); recovered = []; now = time.time()
    for row in rows.values():
        if row.get('category') == category and row.get('status') == 'queued':
            row.update(status='resolved', updated_at=now, recovery='후속 실행 성공으로 자동 복구 확인')
            recovered.append(row)
    if recovered: save(STATE, rows)
    for row in recovered:
        _operator_event('incident_recovered', row)
    return bool(recovered)


def message(row):
    if row.get('kind') == 'receipt':
        return f"[답변 처리 완료 {row['request_id']}]\n{row['result_text']}"
    if row.get('kind') == 'report':
        count = int(row.get('count', 1))
        count_text = f' · {count}건 집계' if count > 1 else ''
        return (f"[매크로 상황 보고 {row['id'][:12]}]\n"
                f"발생: {time.strftime('%m-%d %H:%M:%S', time.localtime(row['created_at']))}\n"
                f"상황: {REPORT_LABELS[row['category']]}{count_text}\n"
                f"요청번호: {row['request_id'] or '없음 (전체 운영 상황)'}\n"
                '처리 결과를 알리는 상황 보고이며 승인 요청이 아닙니다.')
    work, problem, impact, action = ERROR_GUIDANCE.get(
        row['category'], ('운영 프로그램', '정상 처리 여부를 확정하지 못했습니다.',
                          '관련 작업을 완료 처리하지 않았습니다.', '프로그램이 안전 상태에서 다시 확인합니다.'))
    delayed = ('지연 알림: 아래 발생 시각의 기존 오류입니다. 현재 재발·복구 여부는 별도 확인이 필요합니다.\n'
               if time.time() - row['created_at'] >= 300 else '')
    context = row.get('context') or {}
    labels = {'source_room': '원본 방', 'target_room': '처리 대상 방', 'sender': '원문 작성자',
              'preview': '처리할 원문', 'stage': '실패 단계', 'cause': '정확한 실패 사유',
              'automatic_action': '자동 조치'}
    context_text = ''.join(f"{labels[k]}: {context[k]}\n" for k in CONTEXT_FIELDS if context.get(k))
    heading = '담당자 답변 대기' if row['category'] == 'approval_unanswered' else '매크로 오류 알림'
    return (f"[{heading} {row['id']}]\n"
            f"{delayed}"
            f"발생: {time.strftime('%m-%d %H:%M:%S', time.localtime(row['created_at']))}\n"
            f"발생 작업: {work}\n"
            f"{context_text}"
            f"문제: {problem}\n"
            f"현재 상태: {impact}\n"
            f"처리 안내: {action}\n"
            f"요청번호: {row['request_id'] or '없음 (대화방 또는 프로그램 단위 오류)'}\n"
            '이 메시지는 오류 알림이며 승인 요청이 아닙니다.')


def poll(export, send, paused, receipts_only=False):
    if paused(): return
    recipient = operator_name()
    from core.moyi_inbound import parse_export
    from core.moyi_control import audit
    if not receipts_only:
        collect_reports()
    rows = _rows()
    from core.keyword_approval import REQUESTS, question_hours_open
    from core import keyword_forward as k
    requests = k.read_json(REQUESTS, {})
    changed = False
    for entry in rows.values():
        if entry.get('category') == 'approval_unanswered' and entry['status'] == 'queued':
            request = requests.get(entry.get('request_id'), {})
            if request.get('status') not in ('waiting', 'awaiting_late_reply'):
                entry.update(status='resolved', updated_at=time.time())
                changed = True
    if changed:
        save(STATE, rows)
    last_reminder = max((r.get('reminder_attempted_at', 0) for r in rows.values()
                         if r.get('category') == 'approval_unanswered'), default=0)
    reminder_ready = question_hours_open(k.config()) and time.time() - last_reminder >= 3600
    ordered = sorted(rows.values(), key=lambda r: (
        0 if r.get('kind') == 'receipt' else 3 if r.get('kind') == 'report'
        else 2 if r.get('category') == 'approval_unanswered' else 1,
        r['created_at']))
    row = next((r for r in ordered if r['status'] == 'queued'
                and (not receipts_only or r.get('kind') == 'receipt')
                and (r.get('category') != 'approval_unanswered' or reminder_ready)
                and r.get('retry_at', 0) <= time.time()), None)
    if not row: return
    if row.get('kind') == 'report':
        from core.keyword_approval import has_pending_question
        if has_pending_question(): return
    def history():
        text = export(recipient)
        if not text.splitlines() or text.splitlines()[0].strip() not in (
                recipient + ' 님과 카카오톡 대화', recipient + ' 임과 카카오톡 대화'):
            raise RuntimeError('wrong room')
        return parse_export(text, 'error-notice')
    try:
        before = {e['event_id'] for e in history()}
    except Exception as exc:
        from core.moyi_control import OperationPaused
        if isinstance(exc, OperationPaused):
            raise
        row['retry_at'] = time.time() + 60
        save(STATE, rows)
        audit('error_notice_preflight_failed', '보고 담당자 알림 대기 · 60초 후 조회 재시도')
        return
    if paused() or operator_name() != recipient: return
    batch = [row]
    if row.get('kind') == 'receipt':
        batch = [r for r in ordered if r.get('kind') == 'receipt'
                 and r.get('request_id') == row['request_id'] and r['status'] == 'queued'
                 and r.get('retry_at', 0) <= time.time()][:14]
    elif row.get('kind') == 'report':
        batch = [r for r in ordered if r.get('kind') == 'report' and r['status'] == 'queued'
                 and r.get('retry_at', 0) <= time.time()][:5]
    elif row.get('category') == 'approval_unanswered':
        batch = [r for r in ordered if r.get('category') == 'approval_unanswered'
                 and r['status'] == 'queued' and r.get('retry_at', 0) <= time.time()][:14]
    payload = (f"[답변 처리 완료 {row['request_id']}]\n" + '\n'.join(r['result_text'] for r in batch)
               if row.get('kind') == 'receipt' else '\n\n'.join(message(r) for r in batch))
    if row.get('category') == 'approval_unanswered':
        lines = []
        for entry in batch:
            request = requests[entry['request_id']]
            events = request.get('events') or [request['event']]
            unanswered = len(events) - len(request.get('item_answers', {}))
            lines.append(f"{entry['request_id']}: 미응답 {max(0, unanswered)}건")
        payload = ('[승인 답변 대기 모음]\n' + '\n'.join(lines) + '\n\n'
                   '각 요청번호와 항목을 함께 답해주세요.\n'
                   f"예: {batch[0]['request_id']} 가 보내 / {batch[0]['request_id']} 가 안보내\n"
                   '답하지 않은 항목은 계속 대기하며 자동 전달하지 않습니다.')
    for entry in batch:
        entry.update(status='unknown', recipient=recipient, updated_at=time.time())
        if entry.get('category') == 'approval_unanswered':
            entry['reminder_attempted_at'] = time.time()
    save(STATE, rows)  # crash or uncertain send must never replay
    try:
        if operator_name() != recipient:
            from core.moyi_control import OperationPaused
            raise OperationPaused('보고 담당자 변경으로 전송 보류')
        send(recipient, payload)
        after = history()
        normalized = lambda value: re.sub(r'\s+', ' ', value).strip()
        matches = [e for e in after if e['event_id'] not in before
                   and normalized(e['content']) == normalized(payload)]
        if len(matches) != 1: raise RuntimeError('unverified notice')
        for entry in batch:
            entry.update(status='sent', updated_at=time.time(), sent_event_id=matches[0]['event_id'])
        save(STATE, rows)
        audit('error_notice_sent', f"{recipient} · 알림 {row['id']}")
    except Exception as exc:
        from core.moyi_control import OperationPaused
        if isinstance(exc, OperationPaused):
            raise
        audit('error_notice_unknown', f"알림 {row['id']} 결과 미확인 · 자동 재전송 금지")
