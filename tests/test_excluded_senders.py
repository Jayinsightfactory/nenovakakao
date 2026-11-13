"""발주 확인 메시지 발송 제외 대상: 원문은 기록만 남기고 아무것도 보내지 않는다."""
from unittest.mock import Mock

import pytest

from core import import_order as orders


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setattr(orders, 'STATE', tmp_path / 'order_state.json')
    monkeypatch.setattr(orders, 'LOG', tmp_path / 'order_log.jsonl')
    monkeypatch.setattr(orders, 'CONFIG', tmp_path / 'order_config.json')
    return tmp_path


def configure(tmp_path, **extra):
    import json
    (tmp_path / 'order_config.json').write_text(json.dumps(
        {'enabled': True, 'write_enabled': False, 'staff_rooms': {}, 'allowed_senders': [], **extra},
        ensure_ascii=False), encoding='utf-8')


def waiting_row(sender, status='draft'):
    return {'id': 'ORD-1234ABCD', 'status': status, 'staff': sender, 'staff_room': sender, 'week': '41-1',
            'created_at': 1790900000, 'request_event_id': 'q1',
            'event': {'event_id': 'e1', 'sender_name': sender, 'content': '[발주]', 'timestamp': ''},
            'items': [{'index': 1, 'customer_key': 10, 'customer': '업체', 'product_key': 20,
                       'product': 'ROSE', 'quantity': 3, 'unit': '단', 'matched': True}]}


def test_default_list_holds_the_owner_instruction(state):
    configure(state)
    assert orders.config()['excluded_senders'] == ['김인석']
    assert orders.sender_excluded({'sender_name': '김인석'})
    assert not orders.sender_excluded({'sender_name': '김지니'})


def test_local_config_replaces_the_default_and_an_empty_list_lifts_it(state):
    configure(state, excluded_senders=[])
    assert not orders.sender_excluded({'sender_name': '김인석'})
    configure(state, excluded_senders=['김인석', '홍길동'])
    assert orders.sender_excluded({'sender_name': '홍길동'})


@pytest.mark.parametrize('status', ['draft', 'waiting', 'gate_waiting', 'gate_request_unknown'])
def test_nothing_is_sent_to_an_excluded_sender(state, status):
    configure(state)
    orders._save(orders.STATE, {'e1': waiting_row('김인석', status)})
    send = Mock(side_effect=AssertionError('보내면 안 됨'))
    export = Mock(side_effect=AssertionError('방을 열 필요도 없음'))
    orders.poll(export, send, lambda: {}, Mock(side_effect=AssertionError('등록 금지')), lambda: False)
    saved = orders._read(orders.STATE, {})['e1']
    assert saved['status'] == 'sender_excluded' and saved['previous_status'] == status
    send.assert_not_called()


@pytest.mark.parametrize('status', ['registering', 'registration_unknown', 'completed', 'request_unknown'])
def test_rows_past_the_point_of_writing_are_left_alone(state, monkeypatch, status):
    configure(state)
    row = waiting_row('김인석', status)
    row['next_reconcile_at'] = 9e12
    orders._save(orders.STATE, {'e1': row})
    monkeypatch.setattr(orders, '_history', lambda export, room: [])
    orders.poll(Mock(return_value=''), Mock(), lambda: {}, Mock(), lambda: False)
    assert orders._read(orders.STATE, {})['e1']['status'] == status


def test_new_message_from_excluded_sender_is_recorded_without_notice(state, monkeypatch):
    configure(state)
    monkeypatch.setattr(orders, 'build_draft', lambda event, parsed, master: waiting_row('김인석'))
    report = Mock()
    monkeypatch.setattr('core.error_notifications.report', report)
    event = {'event_id': 'e1', 'sender_name': '김인석', 'content': '[발주]', 'timestamp': ''}
    orders.capture(event, lambda text: {'items': [{}]}, lambda: {})
    saved = orders._read(orders.STATE, {})['e1']
    assert saved['status'] == 'sender_excluded' and saved['items']  # 원문·분석은 기록에 남는다
    report.assert_not_called()


def test_other_senders_are_unaffected(state, monkeypatch):
    configure(state)
    monkeypatch.setattr(orders, 'build_draft', lambda event, parsed, master: waiting_row('김지니'))
    monkeypatch.setattr('core.error_notifications.report', Mock())
    event = {'event_id': 'e1', 'sender_name': '김지니', 'content': '[발주]', 'timestamp': ''}
    orders.capture(event, lambda text: {'items': [{}]}, lambda: {})
    assert orders._read(orders.STATE, {})['e1']['status'] == 'draft'
