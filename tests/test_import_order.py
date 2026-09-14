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


@pytest.mark.parametrize('after_send', [False, True])
def test_pause_keeps_draft_or_uncertain_write_without_error_notice(isolated, monkeypatch, after_send):
    from core.moyi_control import OperationPaused
    order.capture(event(), lambda _: parsed(), master)
    sent = Mock()
    history = Mock(side_effect=[[], OperationPaused('일시정지')] if after_send
                   else OperationPaused('일시정지'))
    monkeypatch.setattr(order, '_history', history)
    notify = Mock()
    monkeypatch.setattr('core.error_notifications.notify', notify)
    order.poll(Mock(), sent, master, Mock(), lambda: False)
    saved = order._read(order.STATE, {})[event()['event_id']]
    assert saved['status'] == ('request_unknown' if after_send else 'draft')
    assert sent.call_count == int(after_send)
    assert 'error' not in saved
    notify.assert_not_called()
    assert json.loads(order.LOG.read_text(encoding='utf-8').splitlines()[-1])['action'] == 'paused'


def test_draft_uses_human_product_names_and_internal_keys(isolated):
    rid = order.capture(event(), lambda _: parsed(), master)
    row = order._read(order.STATE, {})[event()['event_id']]
    assert rid.startswith('ORD-')
    assert row['items'][0]['product'] == 'CARNATION NOVIA'
    assert row['items'][0]['product_key'] == 2515
    message = order.review_message(row)
    assert '1. CARNATION NOVIA 2박스' in message
    assert '주문하시겠습니까?' in message
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
    assert order.parse_command('확인', rid) == ('confirm', None)
    assert order.parse_command('등록', rid) is None
    assert order.parse_command('진행', rid) is None
    assert order.parse_command('3번 노비아', rid) == ('product', (3, '노비아'))
    assert order.parse_command('3번 2박스', rid) == ('quantity', (3, 2.0, '박스'))
    assert order.parse_command('2. 2박스', rid) == ('quantity', (2, 2.0, '박스'))
    assert order.parse_command('1, pink mondial 50, 10단', rid) == (
        'product_quantity', (1, 'pink mondial 50', 10.0, '단'))
    assert order.parse_command('3번 거래처 CL10', rid) == ('item_customer', (3, 'CL10'))
    assert order.parse_command('1A 2b', rid) == ('candidates', [(1, 0), (2, 1)])
    assert order.parse_command('차수 35-2', rid) == ('차수', '35-2')
    assert order.parse_command('확인', rid, allow_short=False) is None


def test_only_one_mobile_review_can_wait_per_room(isolated):
    rows = {
        'one': {'staff_room': '정재훈대리', 'status': 'waiting'},
        'two': {'staff_room': '정재훈대리', 'status': 'draft'},
        'other': {'staff_room': '임재용대리', 'status': 'waiting'},
    }
    assert [event_id for event_id, _ in order._waiting_in_room(rows, '정재훈대리')] == ['one']


def test_gate_waiting_rows_share_one_room_export_per_poll(isolated, monkeypatch):
    rows = {}
    for number in range(3):
        row = order.build_draft(event(f'event-{number}'), parsed(), master())
        row.update(status='gate_waiting', gate_room='임재용대리',
                   gate_request_event_id=f'gate-{number}')
        rows[f'event-{number}'] = row
    order._save(order.STATE, rows)
    history = [{'event_id': f'gate-{number}', 'sender_name': '네노바', 'content': '질문'}
               for number in range(3)]
    read = Mock(return_value=history)
    monkeypatch.setattr(order, '_history', read)
    order.poll(Mock(), Mock(), master, Mock(), lambda: False)
    assert read.call_count == 1


def test_review_gate_names_payload_and_staff_before_staff_send(isolated, monkeypatch):
    cfg = order.config(); cfg['review_gate_room'] = '임재용대리'; order._save(order.CONFIG, cfg)
    row = order.build_draft(event(), parsed(), master())
    row.update(staff='정재훈', staff_room='정재훈')
    order._save(order.STATE, {event()['event_id']: row})
    histories = {'임재용대리': [], row['staff_room']: []}
    monkeypatch.setattr(order, '_history', lambda export, room: list(histories[room]))
    sent = []
    def send(room, text):
        sent.append((room, text))
        histories[room].append({'event_id': f'sent-{len(sent)}',
                                'sender_name': '네노바', 'content': text})

    order.poll(Mock(), send, master, Mock(), lambda: False)
    gated = order._read(order.STATE, {})[event()['event_id']]
    assert gated['status'] == 'gate_waiting'
    assert sent[0][0] == '임재용대리'
    assert event()['content'] in sent[0][1]
    assert '업체 = 주광' in sent[0][1]
    assert '품목 = CARNATION NOVIA' in sent[0][1]
    assert '단위 = 박스' in sent[0][1]
    assert '정재훈 담당자에게 보낼까요?' in sent[0][1]

    histories['임재용대리'].append({'event_id': 'decision', 'sender_name': '임재용대리',
                                     'content': f"보내 {gated['id']}"})
    order.poll(Mock(), send, master, Mock(), lambda: False)
    forwarded = order._read(order.STATE, {})[event()['event_id']]
    assert forwarded['status'] == 'waiting'
    assert sent[1][0] == row['staff_room']
    assert sent[1][1] == order.review_message(row)


def test_corrections_rematch_existing_master_only(isolated):
    row = order.build_draft(event(), parsed(), master())
    message = order._apply(row, ('product', (1, '로다스')), master())
    assert row['items'][0]['product'] == 'CARNATION RODAS CREAM'
    assert row['items'][0]['product_key'] == 2308
    assert '2308' not in message
    order._apply(row, ('quantity', (1, 3, '박스')), master())
    assert row['items'][0]['quantity'] == 3


def test_combined_product_quantity_reply_rematches_and_updates_unit(isolated):
    row = order.build_draft(event(), parsed(), master())
    command = order.parse_command('1, 로다스, 10단', row['id'])
    assert command == ('product_quantity', (1, '로다스', 10.0, '단'))
    message = order._apply(row, command, master())
    item = row['items'][0]
    assert item['product'] == 'CARNATION RODAS CREAM'
    assert item['product_key'] == 2308
    assert item['quantity'] == 10 and item['unit'] == '단'
    assert '1. CARNATION RODAS CREAM 10단' in message


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
    assert '1. 주광 / CARNATION NOVIA 2박스' in message
    assert '2. CL10 / CARNATION RODAS CREAM 1박스' in message


def test_mobile_candidate_letters_select_exact_internal_product(isolated):
    values = master()
    values['products']['data'].append({
        'name': '[EZ] NOVIA', 'name_en': '[EZ] NOVIA', 'category': 'carnation',
        'nenova_key': 999, 'name_alias': ['novia']})
    row = order.build_draft(event(), parsed(), values)
    assert not row['items'][0]['matched']
    order._apply(row, ('candidates', [(1, 1)]), values)
    assert row['items'][0]['matched']
    assert row['items'][0]['product_key'] in {2515, 999}


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


def test_nenova_master_rows_include_cl_code_and_korean_product_aliases():
    customer = order_services._customer_row({
        'CustKey': 659, 'CustName': '친구플라워', 'OrderCode': 'CL73',
        'CustCode': '2409300274', 'Descr': '친구5125/카-월/CL73'})
    assert customer['nenova_key'] == 659
    assert 'CL73' in customer['name_alias']
    product = order_services._product_row({
        'ProdKey': 2829, 'ProdName': '[EZ] Astilbe / Washington White',
        'FlowerName': '아스틸베', 'CounName': '네덜란드'})
    assert product['nenova_key'] == 2829
    assert '워싱턴 화이트' in product['name_alias']


def test_llm_missing_key_returns_question(monkeypatch):
    monkeypatch.delenv('ANTHROPIC_API_KEY', raising=False)
    result = order_llm.parse('주광 노비아 2박스')
    assert result['items'] == []
    assert result['questions']


def history_master():
    values = master()
    for key in (991, 992, 993):
        values['products']['data'].append({'name': f'NOVIA {key}', 'nenova_key': key,
                                           'name_alias': ['novia']})
    values['customer_histories'] = {'10': {'status': 'available', 'scope': '상위 10개', 'products': [
        {'ProdKey': 993, 'orderFrequency': 41, 'totalBox': 57, 'totalBunch': 855},
        {'ProdKey': 2308, 'orderFrequency': 100, 'totalBox': 100, 'totalBunch': 1000}]}}
    return values


def test_history_ranks_before_truncation_without_accepting_ambiguity(isolated):
    row = order.build_draft(event(), parsed(), history_master())
    item = row['items'][0]
    assert not item['matched']
    assert item['candidate_options'][0]['product_key'] == 993
    assert 2308 not in [o['product_key'] for o in item['candidate_options']]
    message = order.review_message(row)
    assert '후보 1A CARNATION NOVIA 993' in message
    assert '1. CARNATION CARNATION NOVIA 2박스' in message
    order._apply(row, ('candidates', [(1, 0)]), history_master())
    assert item['product_key'] == 993
    assert item['unit'] == '박스'
    assert item['order_history']['orderFrequency'] == 41


def test_corrections_refresh_candidates_and_customer_history(isolated):
    values = history_master()
    values['customers']['data'].append({'name': 'CL10', 'nenova_key': 11})
    row = order.build_draft(event(), parsed(), values)
    order._apply(row, ('product', (1, '로다스')), values)
    assert [o['product_key'] for o in row['items'][0]['candidate_options']] == [2308]
    order._apply(row, ('item_customer', (1, 'CL10')), values)
    assert row['items'][0]['order_history'] is None
    assert row['items'][0]['history_status'] == 'unavailable'
    order._apply(row, ('거래처', '주광농원'), values)
    assert row['items'][0]['customer_key'] == 10
    assert row['items'][0]['order_history']['orderFrequency'] == 100


def test_history_failure_keeps_review_and_requested_unit(isolated):
    values = master()
    values['customer_history_loader'] = Mock(side_effect=RuntimeError('offline'))
    row = order.build_draft(event(), parsed(), values)
    assert row['items'][0]['unit'] == '박스'
    assert '1. CARNATION NOVIA 2박스' in order.review_message(row)
    command = order.parse_command('1번 20송이', row['id'])
    assert command == ('quantity', (1, 20.0, '송이'))
    order._apply(row, command, values)
    assert row['items'][0]['unit'] == '송이'


def test_history_api_checks_customer_and_caches_read_only(monkeypatch):
    monkeypatch.setattr(order_services, '_customer_history_cache', {})
    response = Mock()
    response.json.return_value = {'ok': True, 'customer': {'CustKey': 659},
                                 'topProducts': [{'ProdKey': 456, 'orderFrequency': 41}]}
    get = Mock(return_value=response)
    monkeypatch.setattr(order_services.requests, 'get', get)
    assert order_services.customer_history(659)['products'][0]['ProdKey'] == 456
    order_services.customer_history('659')
    assert get.call_count == 1
    assert get.call_args.args[0].endswith('/api/nenova/customers/659')
    response.json.return_value['customer']['CustKey'] = 123
    with pytest.raises(RuntimeError, match='불일치'):
        order_services.customer_history(660)


@pytest.mark.parametrize('matched, expected', [(True, 'waiting'), (False, 'waiting')])
def test_disabled_write_never_calls_registrar_and_checks_items(isolated, monkeypatch, matched, expected):
    row = order.build_draft(event(), parsed(), master())
    row.update(status='waiting', request_event_id='request')
    row['items'][0]['matched'] = matched
    order._save(order.STATE, {event()['event_id']: row})
    monkeypatch.setattr(order, '_history', lambda *args: [
        {'event_id': 'request'}, {'event_id': 'reply', 'sender_name': row['staff_room'], 'content': '확인'}])
    monkeypatch.setattr(order, '_verified_send', lambda *args: None)
    registrar = Mock()
    order.poll(Mock(), Mock(), master, registrar, lambda: False)
    registrar.assert_not_called()
    assert order._read(order.STATE, {})[event()['event_id']]['status'] == expected


def test_poll_does_not_process_other_staff_pending_rows(isolated):
    cfg = order.config(); cfg['allowed_senders'] = ['정재훈']; order._save(order.CONFIG, cfg)
    row = order.build_draft(event(), parsed(), master())
    order._save(order.STATE, {event()['event_id']: row})
    export, send = Mock(), Mock()
    order.poll(export, send, master, Mock(), lambda: False)
    export.assert_not_called()
    send.assert_not_called()


def test_separate_lowercase_candidate_replies_are_processed_once(isolated, monkeypatch):
    values = history_master()
    data = parsed(); data['items'] *= 2
    row = order.build_draft(event(), data, values)
    message = order.review_message(row)
    assert '후보 선택: 1A 2B (한 품목씩 따로 답변 가능)' in message
    row.update(status='waiting', request_event_id='request', simulation=True)
    order._save(order.STATE, {event()['event_id']: row})
    replies = [{'event_id': 'request'},
               {'event_id': 'reply1', 'sender_name': row['staff_room'], 'content': '1a'},
               {'event_id': 'reply2', 'sender_name': row['staff_room'], 'content': '2b'}]
    monkeypatch.setattr(order, '_history', lambda *args: replies)
    sent = Mock()
    monkeypatch.setattr(order, '_verified_send', sent)
    registrar = Mock()
    for _ in range(3):
        order.poll(Mock(), Mock(), lambda: values, registrar, lambda: False)
    updated = order._read(order.STATE, {})[event()['event_id']]
    assert [i['product_key'] for i in updated['items']] == [
        row['items'][0]['candidate_options'][0]['product_key'],
        row['items'][1]['candidate_options'][1]['product_key']]
    assert updated['request_event_id'] == 'reply2'
    assert updated['status'] == 'waiting'
    assert sent.call_count == 2
    registrar.assert_not_called()
    message = order.review_message(updated)
    assert '1. CARNATION NOVIA 993 2박스' in message and '맞으면: 확인' in message
    assert '3번' not in message


def test_inactive_orders_do_not_rewrite_state(isolated, monkeypatch):
    row = order.build_draft(event(), parsed(), master())
    row['status'] = 'reply_pending'
    order._save(order.STATE, {event()['event_id']: row})
    save = Mock(); monkeypatch.setattr(order, '_save', save)
    order.poll(Mock(), Mock(), Mock(), Mock(), lambda: False)
    save.assert_not_called()


def test_registration_requires_confirmation_of_current_revision(isolated):
    row = order.build_draft(event(), parsed(), master())
    row['status'] = 'waiting'
    assert '먼저 확인' in order._apply(row, ('register', None), master())
    assert row['status'] == 'waiting'
    assert '아직 주문등록하지 않았습니다' in order._apply(row, ('confirm', None), master())
    order._apply(row, ('quantity', (1, 3, '단')), master())
    assert 'confirmed_revision' not in row
    assert '먼저 확인' in order._apply(row, ('register', None), master())
    order._apply(row, ('confirm', None), master())
    order._apply(row, ('register', None), master())
    assert row['status'] == 'approved'


@pytest.mark.parametrize('quantity', [float('nan'), float('inf'), True, 0, -1])
def test_invalid_quantities_cannot_be_confirmed(isolated, quantity):
    row = order.build_draft(event(), parsed(), master())
    row['items'][0]['quantity'] = quantity
    assert '확인이 필요' in order._apply(row, ('confirm', None), master())
    assert 'confirmed_revision' not in row


def receipt(row):
    return {'verified': True, 'request_id': row['id'], 'revision': order.revision(row),
            'outcome': 'already_registered', 'final_orders': [
                {'customer_key': 10, 'customer': '주광', 'week': row['week'], 'complete': True,
                 'items': [{'product': 'NOVIA', 'quantity': 2, 'unit': '박스'},
                           {'product': '기존 다른 품목', 'quantity': 5, 'unit': '단'}]}]}


def test_final_receipt_includes_existing_items_and_no_duplicate_claim(isolated):
    row = order.build_draft(event(), parsed(), master())
    text = order.final_message(row, receipt(row))
    assert '기등록 확인·추가 등록 없음' in text
    assert '기존 다른 품목 5단' in text
    assert '가 이미 등록되어 있습니다.' in text
    result = receipt(row); result['final_orders'][0]['complete'] = False
    with pytest.raises(RuntimeError, match='전체 조회'):
        order.final_message(row, result)
    result = receipt(row); result['revision'] = 'stale'
    with pytest.raises(RuntimeError, match='불일치'):
        order.final_message(row, result)


def test_confirmation_prompt_blocks_prequeued_registration(isolated, monkeypatch):
    row = order.build_draft(event(), parsed(), master())
    row.update(status='waiting', request_event_id='request', simulation=True)
    order._save(order.STATE, {event()['event_id']: row})
    replies = [{'event_id': 'request'},
               {'event_id': 'confirm', 'sender_name': row['staff_room'], 'content': '확인'},
               {'event_id': 'old-register', 'sender_name': row['staff_room'], 'content': row['id']+' 등록'}]
    monkeypatch.setattr(order, '_history', lambda *args: replies)
    def send_prompt(draft, *args):
        replies.append({'event_id': 'new-prompt'})
        draft['request_event_id'] = 'new-prompt'
    monkeypatch.setattr(order, '_verified_send', send_prompt)
    registrar = Mock()
    for _ in range(2): order.poll(Mock(), Mock(), master, registrar, lambda: False)
    assert order._read(order.STATE, {})[event()['event_id']]['status'] == 'waiting'
    replies.append({'event_id': 'new-register', 'sender_name': row['staff_room'], 'content': row['id']+' 등록'})
    order.poll(Mock(), Mock(), master, registrar, lambda: False)
    assert order._read(order.STATE, {})[event()['event_id']]['status'] == 'completed_simulation'
    registrar.assert_not_called()


def test_unverified_receipt_never_completes_or_retries_registration(isolated, monkeypatch):
    cfg = order.config(); cfg['write_enabled'] = True; order._save(order.CONFIG, cfg)
    row = order.build_draft(event(), parsed(), master())
    row.update(status='waiting', request_event_id='request', confirmed_revision=order.revision(row), account_consent=True)
    order._save(order.STATE, {event()['event_id']: row})
    monkeypatch.setattr(order, '_history', lambda *args: [
        {'event_id': 'request'}, {'event_id': 'reply', 'sender_name': row['staff_room'], 'content': row['id']+' 등록'}])
    registrar = Mock(return_value={'ok': True})
    for _ in range(2): order.poll(Mock(), Mock(), master, registrar, lambda: False)
    assert registrar.call_count == 1
    assert order._read(order.STATE, {})[event()['event_id']]['status'] == 'registration_unknown'


def test_unverified_production_api_is_never_called(monkeypatch):
    monkeypatch.setenv('NENOVA_ORDER_WRITE_ENABLED', '1')
    monkeypatch.setattr('core.credential_store.load', lambda _: {'username': 'test', 'password': 'test'})
    session = Mock(); monkeypatch.setattr(order_services.requests, 'Session', session)
    with pytest.raises(RuntimeError, match='규격 검증 필요'):
        order_services.register_bulk({'staff_room': '정재훈'})
    session.assert_not_called()


def test_staff_setup_preserves_other_staff_and_write_guard(isolated, monkeypatch):
    monkeypatch.setattr('core.moyi_control.audit', Mock())
    cfg = order.config()
    cfg.update(allowed_senders=['임재용대리'], write_enabled=False)
    order._save(order.CONFIG, cfg)
    order.configure_staff('정재훈대리', '정재훈')
    updated = order.config()
    assert updated['allowed_senders'] == ['임재용대리', '정재훈대리']
    assert updated['staff_rooms'] == {'임재용대리': '임재용대리', '정재훈대리': '정재훈'}
    assert updated['write_enabled'] is False
    with pytest.raises(ValueError, match='기존 담당자 방 변경'):
        order.configure_staff('정재훈대리', '다른 방')
    assert order.config() == updated


def test_account_consent_is_explicit_and_separate_from_registration(isolated):
    row = order.build_draft(event(), parsed(), master())
    row['status'] = 'waiting'
    cfg = order.config(); cfg['write_enabled'] = True; order._save(order.CONFIG, cfg)
    assert '주문하시겠습니까?' in order.review_message(row)
    assert order.parse_command('계정사용 동의', row['id']) is None
    order._apply(row, ('confirm', None), master())
    assert '계정 사용 동의가 먼저' in order._apply(row, ('register', None), master())
    command = order.parse_command(row['id'] + ' 계정사용 동의', row['id'])
    assert '아직 주문등록하지 않았습니다' in order._apply(row, command, master())
    assert row['account_consent'] is True and row['status'] == 'waiting'
    order._apply(row, ('register', None), master())
    assert row['status'] == 'approved'
    row['status'] = 'waiting'
    order._apply(row, order.parse_command(row['id'] + ' 계정사용 거절', row['id']), master())
    assert row['account_consent'] is False and 'confirmed_revision' not in row
