import json
from pathlib import Path
from unittest.mock import Mock
import pytest
from core import import_order as order
from core import order_services, order_llm


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(order, 'CONFIG', tmp_path / 'config.json')
    monkeypatch.setattr(order, 'STATE', tmp_path / 'state.json')
    monkeypatch.setattr(order, 'LOG', tmp_path / 'log.jsonl')
    order._save(order.CONFIG, {'enabled': True, 'source': '수입방',
                               'staff_rooms': {'임재용대리': '임재용대리'}})
    return tmp_path


def master():
    return {
        'products': {'data': [
            {'name': '노비아', 'name_en': 'NOVIA', 'category': 'carnation', 'nenova_key': 2515,
             'name_alias': ['novia']},
            {'name': '로다스크림', 'name_en': 'RODAS CREAM', 'category': 'carnation', 'nenova_key': 2308,
             'name_alias': ['로다스']},
        ]},
        'customers': {'data': [
            {'name': '주광', 'nenova_key': 10, 'staff': '임재용대리', 'name_alias': ['주광농원']}
        ]},
    }


def parsed():
    return {'staff': '임재용대리', 'customer': '주광농원', 'week': '35-1',
            'items': [{'customer': '주광농원', 'category': 'carnation', 'product': 'novia', 'quantity': 2, 'unit': '박스'}],
            'questions': []}


def event(eid='kakao-one'):
    return {'event_id': eid, 'sender_name': '직원', 'timestamp': '2026년 8월 27일 오전 10:00',
            'content': '35-1 주광 노비아 2박스'}


def test_draft_uses_human_product_names_and_internal_keys(isolated):
    rid = order.capture(event(), lambda _: parsed(), master)
    row = order._read(order.STATE, {})[event()['event_id']]
    assert rid.startswith('ORD-')
    assert row['items'][0]['product'] == 'CARNATION NOVIA'
    assert row['items'][0]['product_key'] == 2515
    message = order.review_message(row)
    assert 'CARNATION NOVIA / 2박스' in message
    assert '2515' not in message


def test_capture_is_idempotent_and_non_order_is_ignored(isolated):
    parse = Mock(return_value=parsed())
    assert order.capture(event(), parse, master) == order.capture(event(), parse, master)
    assert parse.call_count == 1
    empty = dict(parsed(), items=[])
    order.capture(event('kakao-empty'), lambda _: empty, master)
    assert order._read(order.STATE, {})['kakao-empty']['status'] == 'ignored'


def test_capture_respects_operational_cutoff(isolated):
    cfg = order.config(); cfg['start_at'] = '2026-08-27T10:00:30+09:00'; order._save(order.CONFIG, cfg)
    assert order.capture(event(), lambda _: parsed(), master) is None
    newer = event('new'); newer['timestamp'] = '2026년 8월 27일 오전 10:01'
    assert order.capture(newer, lambda _: parsed(), master)


def test_capture_can_limit_import_test_to_named_staff(isolated):
    cfg = order.config()
    cfg['allowed_senders'] = ['정재훈', '정재훈대리']
    cfg['staff_rooms'].update({'정재훈': '정재훈대리', '정재훈대리': '정재훈대리'})
    order._save(order.CONFIG, cfg)
    blocked = event('blocked'); blocked['sender_name'] = '다른담당자'
    assert order.capture(blocked, lambda _: parsed(), master) is None
    accepted = event('accepted'); accepted['sender_name'] = '정재훈'
    assert order.capture(accepted, lambda _: parsed(), master)
    row = order._read(order.STATE, {})['accepted']
    assert row['staff'] == '정재훈'
    assert row['staff_room'] == '정재훈대리'


def test_commands_are_request_scoped_and_unambiguous():
    rid = 'ORD-ABC12345'
    assert order.parse_command(f'{rid} 3=CARNATION NOVIA', rid) == ('product', (3, 'CARNATION NOVIA'))
    assert order.parse_command(f'{rid} 3수량=2박스', rid) == ('quantity', (3, 2.0, '박스'))
    assert order.parse_command(f'{rid} 3거래처=CL10', rid) == ('item_customer', (3, 'CL10'))
    assert order.parse_command(f'{rid} 거래처=주광', rid) == ('거래처', '주광')
    assert order.parse_command(f'{rid} 차수=35-1', rid) == ('차수', '35-1')
    assert order.parse_command(f'{rid} 등록', rid) == ('register', None)
    assert order.parse_command('ORD-WRONG 등록', rid) is None
    assert order.parse_command('확인', rid) == ('register', None)
    assert order.parse_command('3번 노비아', rid) == ('product', (3, '노비아'))
    assert order.parse_command('3번 2박스', rid) == ('quantity', (3, 2.0, '박스'))
    assert order.parse_command('3번 거래처 CL10', rid) == ('item_customer', (3, 'CL10'))
    assert order.parse_command('차수 35-2', rid) == ('차수', '35-2')
    assert order.parse_command('확인', rid, allow_short=False) is None


def test_only_one_mobile_review_can_wait_per_room(isolated):
    rows = {
        'one': {'staff_room': '정재훈대리', 'status': 'waiting'},
        'two': {'staff_room': '정재훈대리', 'status': 'draft'},
        'other': {'staff_room': '임재용대리', 'status': 'waiting'},
    }
    assert [event_id for event_id, _ in order._waiting_in_room(rows, '정재훈대리')] == ['one']


def test_corrections_rematch_existing_master_only(isolated):
    row = order.build_draft(event(), parsed(), master())
    message = order._apply(row, ('product', (1, '로다스')), master())
    assert row['items'][0]['product'] == 'CARNATION RODAS CREAM'
    assert row['items'][0]['product_key'] == 2308
    assert '2308' not in message
    order._apply(row, ('quantity', (1, 3, '박스')), master())
    assert row['items'][0]['quantity'] == 3


def test_validation_blocks_unmatched_or_missing_values(isolated):
    invalid = dict(parsed(), week='', customer='없음', items=[
        {'customer': '없음', 'product': 'novia', 'quantity': 2, 'unit': '박스'}])
    row = order.build_draft(event(), invalid, master())
    row['items'][0].update(matched=False, quantity=None, unit='')
    issues = order.validate(row)
    assert any('거래처' in issue for issue in issues)
    assert any('차수' in issue for issue in issues)
    assert any('품목' in issue for issue in issues)
    assert any('수량' in issue for issue in issues)


def test_multiple_customer_sections_are_kept_per_item(isolated):
    values = master()
    values['customers']['data'].append({'name': 'CL10', 'nenova_key': 11})
    multi = dict(parsed(), customer='', items=[
        {'customer': '주광농원', 'product': 'novia', 'quantity': 2, 'unit': '박스'},
        {'customer': 'CL10', 'product': '로다스', 'quantity': 1, 'unit': '박스'},
    ])
    row = order.build_draft(event(), multi, values)
    assert [item['customer_key'] for item in row['items']] == [10, 11]
    message = order.review_message(row)
    assert '주광 / CARNATION NOVIA / 2박스' in message
    assert 'CL10 / CARNATION RODAS CREAM / 1박스' in message


def test_registration_is_disabled_without_explicit_switch(monkeypatch):
    monkeypatch.delenv('NENOVA_ORDER_WRITE_ENABLED', raising=False)
    with pytest.raises(RuntimeError, match='쓰기 비활성'):
        order_services.register_bulk({})


def test_registration_uses_exact_staff_room_credential(monkeypatch):
    monkeypatch.setenv('NENOVA_ORDER_WRITE_ENABLED', '1')
    seen = []
    monkeypatch.setattr('core.credential_store.load', lambda profile: seen.append(profile) or None)
    with pytest.raises(RuntimeError, match='임재용대리'):
        order_services.register_bulk({'staff': '임재용', 'staff_room': '임재용대리'})
    assert seen == ['임재용대리']


def test_llm_missing_key_returns_question(monkeypatch):
    monkeypatch.delenv('ANTHROPIC_API_KEY', raising=False)
    result = order_llm.parse('주광 노비아 2박스')
    assert result['items'] == []
    assert result['questions']
