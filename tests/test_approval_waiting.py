from unittest.mock import Mock
import pytest
from core import keyword_approval as a, keyword_forward as k, error_notifications as notices


@pytest.mark.parametrize('payload', ['줄\n' * 100, '🌸 장미\n' * 40, 'plain'])
def test_read_buffer_holds_rich_edit_newline_expansion(payload):
    actual = payload.replace('\n', '\r\n')
    assert k.paste_buffer_capacity(payload) >= len(actual.encode('utf-16-le')) // 2 + 1
    assert k.normalize(actual) == k.normalize(payload)


def test_unanswered_timer_only_starts_after_verified_delivery():
    row = {'status': 'waiting', 'created_at': 1}
    assert a.pending_action(row, 99999) is None
    row['sent_at'] = 100
    assert a.pending_action(row, 399) is None
    assert a.pending_action(row, 400) == 'remind'
    row['reminder_attempted_at'] = 400
    assert a.pending_action(row, 500) is None
    assert a.pending_action(row, 1000) == 'hold'
    row['status'] = 'awaiting_late_reply'
    assert a.pending_action(row, 1100) is None


@pytest.fixture
def pipeline(monkeypatch):
    monkeypatch.setattr(k, 'config', lambda: {'enabled': True, 'target': '현장방'})
    monkeypatch.setattr(a, 'route_status', Mock())
    messages = []
    monkeypatch.setattr('core.moyi_inbound.parse_export', lambda *args: list(messages))
    export = Mock(side_effect=lambda title: (
        a.APPROVER + ' 님과 카카오톡 대화\n' if title == a.APPROVER
        else title + ' 님과 카카오톡 대화\n'))
    def send(room, text):
        messages.append({'event_id': str(len(messages)), 'sender_name': '네노바', 'content': text})
    return messages, export, Mock(side_effect=send)


def row(key, status='queued'):
    return {'id': key, 'status': status, 'created_at': 1,
            'event': {'event_id': key, 'sender_name': '담당자', 'content': '장미 추가'}}


def test_legacy_generic_changes_are_removed_only_before_dispatch():
    generic = row('GENERIC'); generic['event']['content'] = '변경사항후 재고 현황 플라야 40'
    mixed = row('MIXED'); mixed['events'] = [generic['event'], mixed['event']]
    already_sent = row('SENT', 'waiting'); already_sent['event']['content'] = generic['event']['content']
    rows = {'GENERIC': generic, 'MIXED': mixed, 'SENT': already_sent}
    assert a.filter_queued_requests(rows) is True
    assert rows['GENERIC']['status'] == 'filtered_non_actionable'
    assert rows['MIXED']['events'] == [mixed['event']]
    assert rows['SENT']['status'] == 'waiting'


def test_only_one_question_is_sent(pipeline):
    messages, export, send = pipeline
    k.save_json(a.REQUESTS, {'AAA111': row('AAA111'), 'BBB222': row('BBB222')})
    a.poll(export, send, lambda: False, Mock())
    a.poll(export, send, lambda: False, Mock())
    assert send.call_count == 1
    saved = k.read_json(a.REQUESTS, {})
    assert saved['AAA111']['status'] == 'waiting'
    assert saved['BBB222']['status'] == 'queued'


def test_failed_question_does_not_cascade_to_next_queue_item(pipeline):
    _, export, send = pipeline
    send.side_effect = RuntimeError('unknown send result')
    first = row('AAA111'); first['created_at'] = 2
    second = row('BBB222'); second['created_at'] = 1
    k.save_json(a.REQUESTS, {'AAA111': first, 'BBB222': second})

    a.poll(export, send, lambda: False, Mock())

    assert send.call_count == 1
    saved = k.read_json(a.REQUESTS, {})
    assert saved['AAA111']['status'] == 'request_unknown'
    assert saved['BBB222']['status'] == 'queued'


def test_overdue_question_does_not_freeze_new_questions(pipeline):
    _, export, send = pipeline
    k.save_json(a.REQUESTS, {'AAA111': row('AAA111', 'awaiting_late_reply'), 'BBB222': row('BBB222')})
    a.poll(export, send, lambda: False, Mock())
    assert send.call_count == 1
    assert 'BBB222' in send.call_args.args[1]


def test_unknown_old_attempt_does_not_freeze_new_question(pipeline):
    messages, export, send = pipeline
    unknown = row('AAA111', 'request_unknown')
    unknown.update(request_payload='old question', baseline=[])
    fresh = row('BBB222'); fresh['created_at'] = 2
    k.save_json(a.REQUESTS, {'AAA111': unknown, 'BBB222': fresh})
    a.poll(export, send, lambda: False, Mock())
    assert send.call_count == 1
    assert 'BBB222' in send.call_args.args[1]
    saved = k.read_json(a.REQUESTS, {})
    assert saved['AAA111']['status'] == 'request_unknown'
    assert saved['BBB222']['status'] == 'waiting'


def test_newest_queued_request_is_dispatched_first(pipeline):
    _, export, send = pipeline
    old = row('AAA111'); old['created_at'] = 1
    new = row('BBB222'); new['created_at'] = 2
    k.save_json(a.REQUESTS, {'AAA111': old, 'BBB222': new})
    a.poll(export, send, lambda: False, Mock())
    assert 'BBB222' in send.call_args.args[1]


def test_existing_target_message_skips_approval_prompt(pipeline, monkeypatch):
    _, export, send = pipeline
    pending = row('AAA111')
    k.save_json(a.REQUESTS, {'AAA111': pending})
    monkeypatch.setattr('core.moyi_inbound.parse_export', lambda text, binding: (
        [{'event_id': 'target', 'sender_name': '누군가', 'content': '장미 추가'}]
        if binding == 'keyword-target-preapproval' else []))

    a.poll(export, send, lambda: False, Mock())

    send.assert_not_called()
    saved = k.read_json(a.REQUESTS, {})['AAA111']
    assert saved['status'] == 'resolved_existing'
    a.route_status.assert_called_once_with(
        pending['event'], '중복 생략', '승인 직전 대상 방 재확인: 동일 원문 존재')


def test_reminder_once_then_hold_without_auto_approval(pipeline, monkeypatch):
    monkeypatch.setattr(k, 'config', lambda: {'enabled': True, 'target': '현장방', 'approval_individual_reminders': True})
    messages, export, send = pipeline
    pending = row('AAA111', 'waiting')
    pending.update(request_event_id='prompt', sent_at=100)
    messages.append({'event_id': 'prompt', 'sender_name': '네노바', 'content': '질문'})
    k.save_json(a.REQUESTS, {'AAA111': pending})
    monkeypatch.setattr(a.time, 'time', lambda: 401)
    a.poll(export, send, lambda: False, Mock())
    a.poll(export, send, lambda: False, Mock())
    assert send.call_count == 1
    monkeypatch.setattr(a.time, 'time', lambda: 1001)
    a.poll(export, send, lambda: False, Mock())
    assert k.read_json(a.REQUESTS, {})['AAA111']['status'] == 'awaiting_late_reply'
    assert send.call_count == 1


def test_default_skips_five_minute_reminder_and_preserves_unanswered(pipeline, monkeypatch):
    messages, export, send = pipeline
    pending = row('AAA111', 'waiting')
    pending.update(request_event_id='prompt', sent_at=100)
    messages.append({'event_id': 'prompt', 'sender_name': '네노바', 'content': '질문'})
    k.save_json(a.REQUESTS, {'AAA111': pending})
    monkeypatch.setattr(a.time, 'time', lambda: 401)
    a.poll(export, send, lambda: False, Mock())
    send.assert_not_called()
    monkeypatch.setattr(a.time, 'time', lambda: 1001)
    a.poll(export, send, lambda: False, Mock())
    send.assert_not_called()
    assert k.read_json(a.REQUESTS, {})['AAA111']['status'] == 'awaiting_late_reply'


def test_pause_during_preflight_does_not_hold_or_notify(pipeline, monkeypatch):
    from core.moyi_control import OperationPaused
    _, export, send = pipeline
    k.save_json(a.REQUESTS, {'AAA111': row('AAA111')})
    export.side_effect = OperationPaused('일시정지')
    with pytest.raises(OperationPaused):
        a.poll(export, send, lambda: False, Mock())
    assert k.read_json(a.REQUESTS, {})['AAA111']['status'] == 'queued'
    assert not notices._rows()
    send.assert_not_called()


def test_historical_snapshot_tracks_authoritative_rows():
    old = row('AAA111', 'historical_review')
    old['events'] = [old['event'], dict(old['event'], event_id='second')]
    a.refresh_historical_review({'AAA111': old})
    path = a.REQUESTS.with_name('historical_approval_review.json')
    snapshot = k.read_json(path, [])
    assert len(snapshot) == 1 and snapshot[0]['items'] == 2
    old['status'] = 'resolved'
    a.refresh_historical_review({'AAA111': old})
    assert k.read_json(path, None) == []
def test_reports_wait_behind_question_but_errors_do_not(pipeline):
    _, export, send = pipeline
    k.save_json(a.REQUESTS, {'AAA111': row('AAA111')})
    notices.report('worker_started')
    notices.poll(export, send, lambda: False)
    send.assert_not_called()
    notices.enqueue('approval_error', 'AAA111')
    notices.poll(export, send, lambda: False)
    assert send.call_count == 1 and '오류 알림' in send.call_args.args[1]


def test_sent_question_recovers_after_export_failure_without_resend(pipeline):
    messages, export, send = pipeline
    k.save_json(a.REQUESTS, {'AAA111': row('AAA111')})
    export.side_effect = ['현장방 님과 카카오톡 대화\n',
                          a.APPROVER + ' 님과 카카오톡 대화\n',
                          RuntimeError('export failed')]
    a.poll(export, send, lambda: False, Mock())
    assert k.read_json(a.REQUESTS, {})['AAA111']['status'] == 'request_unknown'
    export.side_effect = lambda title: (
        a.APPROVER + ' 님과 카카오톡 대화\n' if title == a.APPROVER
        else title + ' 님과 카카오톡 대화\n')
    messages.append({'event_id': 'bare', 'sender_name': a.APPROVER, 'content': '보내'})
    a.poll(export, send, lambda: False, Mock())
    recovered = k.read_json(a.REQUESTS, {})['AAA111']
    assert recovered['status'] == 'awaiting_late_reply'
    assert recovered['request_event_id'] == '0'
    assert 'sent_at' not in recovered
    assert send.call_count == 1
    messages.append({'event_id': 'explicit', 'sender_name': a.APPROVER,
                     'content': 'AAA111 가 안보내'})
    a.poll(export, send, lambda: False, Mock())
    assert k.read_json(a.REQUESTS, {})['AAA111']['item_answers'] == {'0': 'reject'}
    assert send.call_count == 1


@pytest.mark.parametrize('case', ['missing', 'duplicate', 'old', 'approver_copy', 'legacy'])
def test_uncertain_evidence_never_requeues_or_resends(pipeline, case):
    messages, export, send = pipeline
    pending = row('AAA111', 'request_unknown')
    pending.update(request_payload=a.request_message(pending), baseline=[])
    candidate = {'event_id': 'prompt', 'sender_name': '네노바',
                 'content': pending['request_payload']}
    if case != 'missing': messages.append(candidate)
    if case == 'duplicate': messages.append(dict(candidate, event_id='duplicate'))
    if case == 'old': pending['baseline'] = ['prompt']
    if case == 'approver_copy': candidate['sender_name'] = a.APPROVER
    if case == 'legacy': pending.pop('request_payload')
    k.save_json(a.REQUESTS, {'AAA111': pending, 'BBB222': row('BBB222')})
    a.poll(export, send, lambda: False, Mock())
    assert k.read_json(a.REQUESTS, {})['AAA111']['status'] == 'request_unknown'
    # The uncertain request itself is never replayed. A distinct, explicitly
    # numbered queued request may proceed instead.
    assert send.call_count == 1
    assert 'BBB222' in send.call_args.args[1]
    assert 'AAA111' not in send.call_args.args[1]


def test_request_payload_is_persisted_before_sending(pipeline):
    messages, export, _ = pipeline
    k.save_json(a.REQUESTS, {'AAA111': row('AAA111')})
    observed = []
    def interrupted(room, payload):
        saved = k.read_json(a.REQUESTS, {})['AAA111']
        observed.append((saved, payload))
        raise RuntimeError('send interrupted')
    a.poll(export, interrupted, lambda: False, Mock())
    assert len(observed) == 1
    saved, payload = observed[0]
    assert saved['request_payload'] == payload
    assert saved['status'] == 'request_unknown'
    assert saved['baseline'] == []
    assert k.read_json(a.REQUESTS, {})['AAA111']['status'] == 'request_unknown'
