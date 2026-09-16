from unittest.mock import Mock
import pytest
from core import keyword_approval as a, keyword_forward as k


@pytest.fixture
def case(monkeypatch, tmp_path):
    monkeypatch.setattr(k, 'STATE', tmp_path / 'forward.json')
    monkeypatch.setattr(k, 'config', lambda: {
        'enabled': True, 'target': '현장방', 'approval_missing_recheck': True})
    monkeypatch.setattr(a.time, 'time', lambda: 10000)
    row = dict(id='ABC', status='awaiting_late_reply', choice_format='per_item',
               item_labels=['가', '나'], request_event_id='original', sent_at=100,
               approver_name=a.operator_name(), baseline=[],
               event={'event_id': '1', 'sender_name': '영업', 'content': '장미 추가'})
    row['events'] = [row['event'], {'event_id': '2', 'sender_name': '영업', 'content': '국화 취소'}]
    k.save_json(k.STATE, {'1': {'status': '미응답 보류'}, '2': {'status': '미응답 보류'}})
    target = [{'event_id': 'existing', 'content': '다른 내용'}]
    monkeypatch.setattr('core.moyi_inbound.parse_export', lambda *args: target)
    messages = [dict(event_id='original', content='원 질문', sender_name='봇')]
    def send(room, payload):
        messages.append(dict(event_id='reminder', content=payload, sender_name='봇'))
    return row, target, messages, Mock(side_effect=send)


def run(row, messages, send):
    a.recheck_missing({'ABC': row}, Mock(return_value='현장방 님과 카카오톡 대화'),
                      send, lambda: False, lambda **kwargs: list(messages))


def test_recheck_only_missing_and_no_spam(case):
    row, target, messages, send = case
    target.append({'event_id': 'manual', 'content': '영업 - 장미 추가'})
    run(row, messages, send)
    assert '국화 취소' in send.call_args.args[1]
    assert '장미 추가' not in send.call_args.args[1]
    assert row['missing_recheck_status'] == 'sent'
    run(row, messages, send)
    send.assert_called_once()


def test_no_recheck_for_declined_or_unknown_delivery(case):
    row, target, messages, send = case
    row['item_answers'] = {'0': 'reject'}
    k.save_json(k.STATE, {'1': {'status': '승인거절'}, '2': {'status': '결과 불명'}})
    run(row, messages, send)
    send.assert_not_called()


def test_unknown_reminder_never_retried(case, monkeypatch):
    row, target, messages, send = case
    send.side_effect = RuntimeError('uncertain')
    with pytest.raises(RuntimeError):
        run(row, messages, send)
    assert k.read_json(a.REQUESTS, {})['ABC']['missing_recheck_status'] == 'unknown'
    monkeypatch.setattr(a.time, 'time', lambda: 20000)
    run(row, messages, send)
    send.assert_called_once()


def test_yes_no_only_after_verified_recheck_and_correct_sender(case):
    row, target, messages, send = case
    early = dict(event_id='early', sender_name=row['approver_name'], content='가 네')
    messages.append(early)
    assert a.decision(messages, row, True) is None
    run(row, messages, send)
    messages.append(dict(event_id='wrong', sender_name='다른사람', content='가 아니요'))
    messages.append(dict(event_id='answer', sender_name=row['approver_name'], content='가 네 나 아니요'))
    assert a.decision(messages, row, True) == {0: 'approve', 1: 'reject'}


def test_grouped_yes_no(case):
    row, target, messages, send = case
    run(row, messages, send)
    messages.append(dict(event_id='answer', sender_name=row['approver_name'], content='가,나 네'))
    assert a.decision(messages, row, True) == {0: 'approve', 1: 'approve'}


def test_empty_target_is_not_proof_of_missing(case):
    row, target, messages, send = case
    target.clear()
    run(row, messages, send)
    send.assert_not_called()
