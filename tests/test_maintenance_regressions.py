import json
from datetime import datetime
from unittest.mock import Mock
import pytest
from core import error_notifications as n, keyword_approval as a, keyword_forward as k


@pytest.mark.parametrize('status', ['sent', 'unknown'])
def test_hourly_report_never_reopens_after_send(monkeypatch, status):
    monkeypatch.setattr(n.time, 'time', lambda: 3601)
    n.report('inbound_processed'); n.collect_reports()
    rows = n._rows(); key = next(iter(rows))
    rows[key]['status'] = status
    n.save(n.STATE, rows)
    n.report('inbound_processed'); n.collect_reports()
    assert n._rows()[key]['status'] == status


def test_hourly_report_waits_until_bucket_closes(monkeypatch):
    monkeypatch.setattr(n.time, 'time', lambda: 3601)
    n.report('inbound_processed')
    export, send = Mock(), Mock()
    n.poll(export, send, lambda: False)
    export.assert_not_called(); send.assert_not_called()
    assert next(iter(n._rows().values()))['retry_at'] == 7200


def test_receipts_batch_one_request_and_partial_reply_later(monkeypatch):
    monkeypatch.setattr(a, 'has_pending_question', lambda: True)
    sent = []
    monkeypatch.setattr('core.moyi_inbound.parse_export', lambda *args: [
        {'event_id': str(i), 'content': text} for i, text in enumerate(sent)])
    export = Mock(return_value='임재용대리 님과 카카오톡 대화\n')
    for i, label in enumerate('가나다라마'):
        n.approval_result('ABC123', str(i), label, '승인거절')
    n.approval_result('DEF456', 'other', '가', '전송 성공')
    n.poll(export, lambda room, text: sent.append(text), lambda: False, receipts_only=True)
    assert len(sent) == 1 and sent[0].count('처리했습니다') == 5
    assert sent[0].count('[답변 처리 완료') == 1 and 'DEF456' not in sent[0]
    assert sum(r['status'] == 'sent' for r in n._rows().values()) == 5
    n.approval_result('ABC123', 'later', '바', '승인거절')
    n.poll(export, lambda room, text: sent.append(text), lambda: False, receipts_only=True)
    n.poll(export, lambda room, text: sent.append(text), lambda: False, receipts_only=True)
    assert len(sent) == 3 and '바:' in sent[-1] and '가:' not in sent[-1]


def test_old_requests_are_preserved_without_relabeling():
    old = {'event_id': 'one', 'timestamp': '2026년 9월 9일 오후 6:00'}
    today = {'event_id': 'two', 'timestamp': '2026년 9월 10일 오전 9:00'}
    rows = {'A': {'id': 'A', 'status': 'waiting', 'event': old, 'events': [old, today],
                  'request_event_id': 'boundary', 'item_answers': {'0': 'reject'}},
            'B': {'id': 'B', 'status': 'queued', 'event': today}}
    assert a.hold_old_requests(rows, {'approval_current_day_only': True}, datetime(2026, 9, 10))
    assert rows['A']['status'] == 'historical_review'
    assert rows['A']['events'] == [old, today] and rows['A']['item_answers'] == {'0': 'reject'}
    assert rows['B']['status'] == 'queued'
    assert a.pending_action(rows['A'], 999999) is None


def test_new_question_hours_do_not_allow_night_prompts():
    cfg = {'approval_active_hours': [8, 19]}
    assert a.question_hours_open(cfg, datetime(2026, 9, 10, 8))
    assert not a.question_hours_open(cfg, datetime(2026, 9, 10, 19))


def test_partial_answers_still_timeout_and_late_reply_processes(monkeypatch, tmp_path):
    monkeypatch.setattr(k, 'STATE', tmp_path / 'forward.json')
    monkeypatch.setattr(k, 'LOG', tmp_path / 'forward.jsonl')
    monkeypatch.setattr(k, 'config', lambda: {'enabled': True, 'source': '영업방', 'target': '현장방'})
    events = [dict(event_id=str(i), sender_name='직원', content='장미 추가') for i in range(2)]
    row = dict(id='ABC123', status='waiting', choice_format='per_item', event=events[0], events=events,
               request_event_id='prompt', sent_at=100, item_answers={'0': 'reject'})
    k.save_json(a.REQUESTS, {'ABC123': row})
    k.save_json(k.STATE, {'0': {'status': '승인거절'}, '1': {'status': '승인대기'}})
    history = [dict(event_id='prompt', sender_name='봇', content='[전달 승인 요청 ABC123]'),
               dict(event_id='answer', sender_name=a.APPROVER, content='가 안보내')]
    monkeypatch.setattr('core.moyi_inbound.parse_export', lambda *args: history)
    monkeypatch.setattr(a.time, 'time', lambda: 1001)
    export = Mock(return_value='임재용대리 님과 카카오톡 대화\n')
    send = Mock()
    a.poll(export, send, lambda: False, Mock())
    assert k.read_json(a.REQUESTS, {})['ABC123']['status'] == 'awaiting_late_reply'
    assert k.read_json(k.STATE, {})['0']['status'] == '승인거절'
    assert k.read_json(k.STATE, {})['1']['status'] == '미응답 보류'
    history.append(dict(event_id='late', sender_name=a.APPROVER, content='ABC123 나 안보내'))
    a.poll(export, send, lambda: False, Mock())
    assert k.read_json(k.STATE, {})['1']['status'] == '승인거절'
    assert k.read_json(a.REQUESTS, {})['ABC123']['status'] == 'resolved'
    send.assert_not_called()
