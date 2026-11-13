from copy import deepcopy
import pytest
from core import order_registration as reg, import_order as orders


@pytest.fixture
def row(tmp_path, monkeypatch):
    monkeypatch.setattr(reg, 'JOURNALS', tmp_path)
    monkeypatch.setattr('core.moyi_control.is_paused', lambda: False)
    r = {'id': 'ORD-1234ABCD', 'staff': '박성수', 'staff_room': '박성수', 'week': '41-1',
         'created_at': 1790900000, 'event': {'source_timestamp': '2026-10-02T12:00:00+09:00'},
         'items': [{'index': 1, 'customer_key': 10, 'customer': '업체', 'product_key': 20,
                    'product': 'ROSE', 'quantity': 3, 'unit': '단', 'matched': True}]}
    r['confirmed_revision'] = orders.revision(r)
    return r


def transport(fail=None):
    calls = []
    current = 2
    def request(rid, method, **kw):
        nonlocal current
        calls.append((method, deepcopy(kw)))
        if method == 'POST':
            current += 3
            if fail == 'post': raise TimeoutError('lost response')
            return {'verified': True, 'orderMasterKey': 30, 'verifiedCount': 1,
                    'results': [{'prodKey': 20, 'finalQty': 5, 'unit': '단', 'status': 'UPDATED'}]}
        if fail == 'readback' and current == 5: raise TimeoutError('lost read')
        return {'year': '2026', 'week': '41-01', 'products': [{'ProdKey': 20, 'ProdName': 'ROSE',
                'CurrentQty': current, 'CurrentOrderRows': 1, 'OutUnit': '단'}]}
    return request, calls


def test_registration_uses_add_current_quantity_and_readback(row):
    request, calls = transport()
    result = reg.register(row, request)
    assert result['verified'] and result['final_orders'][0]['items'][0]['quantity'] == 5
    payload = calls[1][1]['body']
    assert payload['source'] == 'sales-paste' and payload['orderMode'] == 'ADD'
    assert payload['items'][0]['expectedCurrentQty'] == 2
    assert payload['items'][0]['qty'] == 3 and payload['manager'] == '박성수'
    reg.register(row, request)
    assert [m for m, _ in calls].count('POST') == 1


def test_lost_post_is_never_replayed_even_when_quantity_matches(row):
    request, calls = transport('post')
    with pytest.raises(TimeoutError): reg.register(row, request)
    with pytest.raises(RuntimeError, match='응답 불명'): reg.register(row, request)
    assert [m for m, _ in calls].count('POST') == 1


def test_saved_receipt_recovers_failed_read_without_write(row):
    request, calls = transport('readback')
    with pytest.raises(TimeoutError): reg.register(row, request)
    recovered, new_calls = transport()
    def read_only(rid, method, **kw):
        assert method == 'GET'
        data = recovered(rid, method, **kw)
        data['products'][0]['CurrentQty'] = 5
        return data
    assert reg.register(row, read_only)['verified']


def test_receipt_cannot_confirm_another_product_or_unit(row):
    request, calls = transport()
    def bad(rid, method, **kw):
        data = request(rid, method, **kw)
        if method == 'POST': data['results'][0]['unit'] = '박스'
        return data
    with pytest.raises(ValueError, match='단위 불일치'): reg.register(row, bad)


def test_changed_revision_cannot_resume_old_write(row):
    request, _ = transport()
    reg.register(row, request)
    row['items'][0]['quantity'] = 6
    row['confirmed_revision'] = orders.revision(row)
    with pytest.raises(ValueError, match='내용 변경'): reg.register(row, request)


def test_major_week_without_subweek_is_not_guessed(row):
    row['week'] = '41차'
    with pytest.raises(ValueError): reg.scope(row)


def test_bridge_requires_exact_confirmed_payload_and_write_ahead(row, tmp_path, monkeypatch):
    from core.atomic_json import save
    monkeypatch.setattr(orders, 'STATE', tmp_path / 'state.json')
    monkeypatch.setattr(orders, 'config', lambda: {'enabled': True, 'write_enabled': True})
    row.update(status='registering', simulation=False)
    save(orders.STATE, {'event': row})
    def request(rid, method, **kw):
        job = {'request_id': rid, 'method': method, 'params': kw.get('params'), 'body': kw.get('body')}
        reg.validate_job(job)
        if method == 'POST':
            tampered = deepcopy(job)
            tampered['body']['items'][0]['qty'] = 100
            with pytest.raises(ValueError, match='write_ahead'): reg.validate_job(tampered)
        return underlying(rid, method, **kw)
    underlying, _ = transport()
    assert reg.register(row, request)['verified']


def test_order_confirmation_directly_approves_current_revision(row, monkeypatch):
    monkeypatch.setattr(orders, 'config', lambda: {'browser_order_registration': True, 'write_enabled': True})
    row['status'] = 'waiting'
    row['simulation'] = False
    assert orders._apply(row, ('confirm', None), {}) is None
    assert row['status'] == 'approved'
    assert row['confirmed_revision'] == orders.revision(row)


def test_old_waiting_draft_is_archived_without_sending(row, tmp_path, monkeypatch):
    from core.atomic_json import save, read
    monkeypatch.setattr(orders, 'STATE', tmp_path / 'orders.json')
    monkeypatch.setattr(orders, 'config', lambda: {'enabled': True, 'start_at': '2026-10-02T11:20:00+09:00'})
    row.update(status='waiting', simulation=True)
    row['event']['timestamp'] = '2026년 9월 14일 오전 10:10'
    save(orders.STATE, {'old': row})
    def forbidden(*args):
        raise AssertionError('old draft must not read, send, or register')
    orders.poll(forbidden, forbidden, forbidden, forbidden, lambda: False)
    assert read(orders.STATE)['old']['status'] == 'archived_previous_scope'


def test_current_scope_retains_new_events_after_large_old_backlog(monkeypatch):
    monkeypatch.setattr(orders, 'config', lambda: {'start_at': '2026-10-02T11:20:00+09:00'})
    events = [{'timestamp': '2026년 9월 14일 오전 10:10'}] * 1454 + [{'timestamp': '2026년 10월 2일 오후 1:00'}]
    assert len([e for e in events if orders.in_current_scope(e)]) == 1
