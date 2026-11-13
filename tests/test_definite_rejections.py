"""전산이 확실히 거절했거나 보내지 못한 저장은 '응답 불명'으로 묶지 않는다. 응답을 못 받은 저장은 그대로 묶는다."""
from copy import deepcopy
from unittest.mock import Mock

import pytest

from core import import_order as orders
from core import order_registration as reg
from core.atomic_json import read
from core.order_api import OrderAPI
from core.order_registration import OrderRejected


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


def products(current):
    return {'year': '2026', 'week': '41-01', 'products': [
        {'ProdKey': 20, 'ProdName': 'ROSE', 'CurrentQty': current, 'CurrentOrderRows': 1, 'OutUnit': '단'}]}


def api_with(status, body):
    api = OrderAPI('test'); api.auth = Mock()
    api.auth.session.request.return_value = Mock(status_code=status, json=lambda: body)
    return api


@pytest.mark.parametrize('status, body', [
    (400, {'success': False, 'code': 'SALES_MANAGER_INVALID', 'error': '선택한 담당자를 찾을 수 없습니다.'}),
    (404, {'success': False, 'error': '사용 가능한 업체가 아닙니다.'}),
    (409, {'success': False, 'code': 'ERP_EDIT_LOCKED', 'error': '다른 사용자가 편집 중'}),
    (409, {'success': False, 'code': 'STALE_CURRENT_QTY', 'error': '현재 수량이 바뀜'}),
])
def test_definite_server_rejection_is_reported_as_rejected(status, body):
    with pytest.raises(OrderRejected, match=body.get('code') or 'HTTP 404'):
        api_with(status, body)._call('POST', '/api/orders', json={})


@pytest.mark.parametrize('status, body', [
    (500, {'success': False, 'error': 'boom'}),
    (409, {'success': False, 'code': 'ORDER_POST_WRITE_MISMATCH', 'error': '저장 후 불일치'}),
    (502, None),
])
def test_ambiguous_failures_stay_unknown(status, body):
    api = api_with(status, body)
    with pytest.raises(RuntimeError) as caught:
        api._call('POST', '/api/orders', json={})
    assert not isinstance(caught.value, OrderRejected)
    assert api.auth.session.request.call_count == 1  # 다시 보내지 않는다


def test_failure_before_the_post_is_rejected_not_unknown(monkeypatch):
    monkeypatch.setattr('core.moyi_control.is_paused', lambda: False)
    checks = iter([None, ValueError('order_approval_required')])
    def validate(job):
        outcome = next(checks)
        if outcome: raise outcome
    monkeypatch.setattr('core.order_registration.validate_job', validate)
    api = OrderAPI('test'); api.auth = Mock()
    body = {'source': 'sales-paste', 'orderMode': 'ADD'}
    with pytest.raises(OrderRejected, match='저장 요청 전 중단'):
        api.request('ORD-12345678', 'POST', body=body)
    api.auth.session.request.assert_not_called()


def test_rejected_group_is_released_so_a_later_approval_can_save(row):
    calls = []
    current = [2]
    reject = [True]
    def request(rid, method, **kw):
        calls.append((method, deepcopy(kw)))
        if method == 'GET':
            return products(current[0])
        if reject[0]:
            raise OrderRejected('ERP_EDIT_LOCKED: 다른 사용자가 편집 중')
        current[0] += 3
        return {'verified': True, 'orderMasterKey': 30, 'verifiedCount': 1,
                'results': [{'prodKey': 20, 'finalQty': 5, 'unit': '단', 'status': 'ADDED'}]}
    with pytest.raises(OrderRejected):
        reg.register(row, request)
    assert read(reg.journal_path(row))['groups'] == {}  # 사전 기록 해제
    reject[0] = False
    assert reg.register(row, request)['verified']
    assert [m for m, _ in calls].count('POST') == 2  # 거절 1번 + 저장 1번, 저장은 한 번뿐


def test_lost_response_still_blocks_any_second_write(row):
    def request(rid, method, **kw):
        if method == 'GET':
            return products(2)
        raise TimeoutError('lost response')
    with pytest.raises(TimeoutError):
        reg.register(row, request)
    assert read(reg.journal_path(row))['groups']['10']['state'] == 'write_unknown'
    with pytest.raises(RuntimeError, match='응답 불명'):
        reg.register(row, request)


def poll_setup(row, tmp_path, monkeypatch, registrar, send_fails=False):
    state = tmp_path / 'order_state.json'
    monkeypatch.setattr(orders, 'STATE', state)
    monkeypatch.setattr(orders, 'LOG', tmp_path / 'order_log.jsonl')
    monkeypatch.setattr(orders, 'config', lambda: {'enabled': True, 'write_enabled': True, 'transport': 'direct-api',
                                                    'staff_rooms': {}, 'allowed_senders': []})
    row.update(status='waiting', request_event_id='q1',
               event={'event_id': 'e1', 'sender_name': '박성수', 'content': '[발주]\n차수: 41-1\n업체: 업체\nROSE / 3단',
                      'source_timestamp': '2026-10-02T12:00:00+09:00', 'timestamp': ''})
    row['confirmed_revision'] = None
    orders._save(state, {'e1': row})
    sent = []
    chat = [{'event_id': 'q1', 'sender_name': '나', 'content': '질문'},
            {'event_id': 'r1', 'sender_name': '박성수', 'content': '확인'}]
    def export(room):
        if send_fails and sent == [] and export.calls >= 1:
            raise RuntimeError('제목 불일치')
        export.calls += 1
        return chat + [{'event_id': 's%d' % i, 'sender_name': '나', 'content': t} for i, t in enumerate(sent)]
    export.calls = 0
    monkeypatch.setattr(orders, '_history', lambda export_fn, room: export_fn(room))
    monkeypatch.setattr(orders, 'validate', lambda draft: [])
    monkeypatch.setattr(orders, 'final_message', lambda r, result: '[등록 완료 %s]' % r['id'])
    monkeypatch.setattr(orders, 'review_message', lambda r: '[주문 확인 %s]' % r['id'])
    return state, sent, export


def test_rejection_after_approval_returns_to_waiting_and_tells_staff(row, tmp_path, monkeypatch):
    registrar = Mock(side_effect=OrderRejected('SALES_MANAGER_INVALID: 담당자 없음'))
    state, sent, export = poll_setup(row, tmp_path, monkeypatch, registrar)
    orders.poll(export, lambda room, text: sent.append(text), lambda: {}, registrar, lambda: False)
    saved = orders._read(state, {})['e1']
    assert saved['status'] == 'waiting'
    assert len(sent) == 1 and '등록되지 않았습니다' in sent[0] and 'SALES_MANAGER_INVALID' in sent[0]


def test_completed_registration_survives_a_failed_completion_notice(row, tmp_path, monkeypatch):
    registrar = Mock(return_value={'verified': True})
    state, sent, export = poll_setup(row, tmp_path, monkeypatch, registrar, send_fails=True)
    orders.poll(export, lambda room, text: sent.append(text), lambda: {}, registrar, lambda: False)
    saved = orders._read(state, {})['e1']
    assert saved['status'] == 'completed'  # 예전에는 'hold' 로 덮였다
    assert saved['pending_response'] == '[등록 완료 ORD-1234ABCD]'
    registrar.assert_called_once()


def test_defect_precheck_failure_returns_to_approved(tmp_path, monkeypatch):
    from core import defect_approval as d
    from core.defect_services import DefectAdapter
    path = tmp_path / 'req.json'
    row = {'id': 'R1', 'status': 'approved', 'revision': 1, 'approved_revision': 1, 'items': [{}], 'extracted': {}}
    d.save(path, row)
    monkeypatch.setattr(d, 'ready', lambda r: True)
    monkeypatch.setattr('core.defect_scope.upload_scope', lambda r: {'year': 2026, 'week': '44'})
    adapter = DefectAdapter.__new__(DefectAdapter)
    adapter.owner = {'managerId': 'x'}
    adapter.validate = Mock(); adapter.lookup = Mock(return_value=None)
    adapter.resolve_registration_period = Mock(side_effect=RuntimeError('불량 입력 API 실패: HTTP 502'))
    adapter.request = Mock(side_effect=AssertionError('저장 요청이 나가면 안 됨'))
    with pytest.raises(RuntimeError, match='502'):
        d.submit(path, adapter, lambda: False)
    saved = d.load(path)
    assert saved['status'] == 'approved' and '저장 전 확인 실패' in saved['last_error']


def test_defect_failure_after_the_post_stays_unknown(tmp_path, monkeypatch):
    from core import defect_approval as d
    from core.defect_services import DefectAdapter
    path = tmp_path / 'req.json'
    row = {'id': 'R1', 'status': 'approved', 'revision': 1, 'approved_revision': 1, 'items': [{}], 'extracted': {}}
    d.save(path, row)
    monkeypatch.setattr(d, 'ready', lambda r: True)
    monkeypatch.setattr('core.defect_scope.upload_scope', lambda r: {'year': 2026, 'week': '44'})
    adapter = DefectAdapter.__new__(DefectAdapter)
    adapter.owner = {'managerId': 'x'}
    adapter.validate = Mock(); adapter.lookup = Mock(return_value=None)
    adapter.resolve_registration_period = Mock()
    adapter.scope = lambda r: {'year': 2026, 'week': '44'}
    adapter.registration_listing = Mock(return_value={})
    adapter.ensure_period_open = Mock()
    adapter.payload = lambda r: {}
    def request(method, **kw):
        if method == 'POST': raise TimeoutError('lost')
        return {}
    adapter.request = request
    with pytest.raises(TimeoutError):
        d.submit(path, adapter, lambda: False)
    assert d.load(path)['status'] == 'write_unknown'
