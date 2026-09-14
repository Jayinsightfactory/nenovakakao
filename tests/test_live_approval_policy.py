from unittest.mock import Mock
from core import keyword_approval as a, keyword_forward as k, error_notifications as notices
from core import workflow_settings
from core.atomic_json import save


def test_internal_errors_stay_local_while_rejection_receipts_are_sent(monkeypatch):
    save(workflow_settings.CONFIG, {'operator_reports_enabled': False})
    notices.enqueue('approval_error', 'ABC123', {'cause': 'exact room verification failed: 0 matches'})
    notices.report('worker_started')
    notices.approval_result('ABC123', 'item', '가', '승인거절')
    sent = []
    monkeypatch.setattr('core.moyi_control.audit', Mock())
    monkeypatch.setattr('core.moyi_inbound.parse_export', lambda *args: [
        {'event_id': str(i), 'content': x} for i, x in enumerate(sent)])
    notices.poll(lambda room: room + ' 님과 카카오톡 대화\n',
                 lambda room, payload: sent.append(payload), lambda: False)
    assert len(sent) == 1 and '처리했습니다' in sent[0]
    assert 'exact room' not in sent[0]
    assert all(r['status'] == 'local_only' for r in notices._rows().values() if r.get('kind') != 'receipt')


def test_stale_unsent_requests_are_preserved_without_affecting_sent_requests():
    event = {'timestamp': '2026년 9월 14일 오전 10:00'}
    rows = {'old': {'status': 'queued', 'event': event},
            'sent': {'status': 'waiting', 'event': event, 'request_event_id': 'prompt'}}
    now = k.timestamp('2026년 9월 14일 오전 11:00').timestamp()
    assert a.hold_stale_questions(rows, {'approval_max_source_age_sec': 600}, now)
    assert rows['old']['status'] == 'stale_review' and rows['old']['event'] == event
    assert rows['sent']['status'] == 'waiting'


def test_target_must_be_checked_before_operator_and_existing_content_is_skipped(monkeypatch):
    row = {'id': 'ABC123', 'status': 'queued', 'created_at': 1,
           'event': {'event_id': 'item', 'sender_name': '직원', 'content': '장미 추가'}}
    k.save_json(a.REQUESTS, {'ABC123': row})
    monkeypatch.setattr(k, 'config', lambda: {'enabled': True, 'target': '현장방', 'require_target_history': True})
    monkeypatch.setattr(a, 'route_status', Mock())
    monkeypatch.setattr('core.moyi_inbound.parse_export', lambda *args: [{'event_id': 'already', 'content': '직원 - 장미 추가'}])
    export = Mock(return_value='현장방 님과 카카오톡 대화\n')
    send = Mock()
    a.poll(export, send, lambda: False, Mock())
    export.assert_called_once_with('현장방')
    send.assert_not_called()
    assert k.read_json(a.REQUESTS, {})['ABC123']['status'] == 'resolved_existing'
