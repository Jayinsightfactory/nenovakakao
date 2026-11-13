"""발주 담당자 이름 다듬기와 연말·연초 차수의 연도 판단."""
from datetime import datetime

import pytest

from core import import_order as orders
from core import order_registration as reg


@pytest.fixture(autouse=True)
def order_config(monkeypatch):
    value = {'enabled': True}
    monkeypatch.setattr(orders, 'config', lambda: value)
    return value


@pytest.mark.parametrize('kakao, erp', [
    ('김도준(j)', '김도준'),        # 11/13 수입방 실제 이름 — 예전 방식으로는 전산 거절
    ('김원영차장', '김원영'),
    ('임재용대리', '임재용'),
    ('박성수', '박성수'),
    ('김선희 과장님', '김선희'),
    ('설연주 [영업지원]', '설연주'),
    ('강현우（네노바）', '강현우'),
])
def test_kakao_sender_becomes_erp_user_name(kakao, erp):
    assert reg.manager_name(kakao) == erp


def test_short_names_that_merely_end_like_a_title_are_kept():
    assert reg.manager_name('이사') == '이사'      # 직함만 있는 이름을 빈 값으로 만들지 않는다
    assert reg.manager_name('한대리') == '한대리'  # 떼면 한 글자만 남는 경우는 그대로 둔다


def test_unknown_person_is_not_guessed_but_can_be_mapped(order_config):
    assert reg.manager_name('김인석') == '김인석'  # 전산에 없으면 서버가 거절하고 담당자에게 사유가 간다
    order_config['manager_aliases'] = {'김인석': 'nenova1'}
    assert reg.manager_name('김인석') == 'nenova1'


def row(week, kakao_time=None, created=None, source=None):
    event = {}
    if kakao_time: event['timestamp'] = kakao_time
    if source: event['source_timestamp'] = source
    return {'week': week, 'event': event,
            'created_at': (created or datetime(2026, 6, 1)).timestamp()}


@pytest.mark.parametrize('kakao_time, week, expected', [
    ('2026년 12월 29일 오후 3:10', '1-1', ('2027', '01-01')),   # 12월 말의 1-1차 = 다음 해 물량
    ('2026년 12월 14일 오전 9:00', '2-1', ('2027', '02-01')),
    ('2026년 12월 29일 오후 3:10', '53-1', ('2026', '53-01')),  # 같은 해 마지막 차수
    ('2027년 1월 2일 오전 10:00', '1-1', ('2027', '01-01')),    # 1월 2일은 달력상 전년도 53주차
    ('2027년 1월 5일 오전 10:00', '52-2', ('2026', '52-02')),   # 연초에 정리하는 지난해 차수
    ('2026년 11월 13일 오후 4:00', '46-1', ('2026', '46-01')),
    ('2026년 11월 13일 오후 4:00', '47-1', ('2026', '47-01')),
    ('2026년 6월 10일 오후 4:00', '30-1', ('2026', '30-01')),   # 몇 주 앞 차수는 같은 해
])
def test_year_follows_the_message_date(kakao_time, week, expected):
    scope = reg.scope(row(week, kakao_time))
    assert (scope['year'], scope['week']) == expected


def test_message_date_wins_over_late_analysis():
    # 12월 29일 원문을 1월 3일에 수집·분석해도 다음 해 1차로 본다(같은 결과여야 한다)
    late = row('1-1', '2026년 12월 29일 오후 3:10', created=datetime(2027, 1, 3))
    assert reg.scope(late)['year'] == '2027'
    # 12월 29일 원문의 52-1차는 수집이 1월로 넘어가도 2026년
    assert reg.scope(row('52-1', '2026년 12월 29일 오후 3:10', created=datetime(2027, 1, 3)))['year'] == '2026'


def test_explicit_year_in_the_week_text_is_respected():
    assert reg.scope(row('2026-1-1', '2026년 12월 29일 오후 3:10'))['year'] == '2026'


def test_falls_back_to_capture_time_without_a_message_date():
    assert reg.scope(row('41-1', created=datetime(2026, 10, 2)))['year'] == '2026'
    assert reg.scope(row('41-1', source='2026-10-02T12:00:00+09:00'))['year'] == '2026'


# ── 주문 담당자는 업체 기준. 카톡에 올린 사람은 기록에만 남는다 ──
from copy import deepcopy
from core.atomic_json import read
from core.order_registration import OrderRejected


@pytest.fixture
def order(tmp_path, monkeypatch):
    monkeypatch.setattr(reg, 'JOURNALS', tmp_path)
    monkeypatch.setattr('core.moyi_control.is_paused', lambda: False)
    r = {'id': 'ORD-1234ABCD', 'staff': '김인석', 'staff_room': '김인석', 'week': '41-1',
         'created_at': 1790900000, 'event': {'source_timestamp': '2026-10-02T12:00:00+09:00'},
         'items': [{'index': 1, 'customer_key': 10, 'customer': '업체', 'product_key': 20,
                    'product': 'ROSE', 'quantity': 3, 'unit': '단', 'matched': True}]}
    r['confirmed_revision'] = orders.revision(r)
    return r


def erp(reject_managers=()):
    posts = []
    current = [2]
    def request(rid, method, **kw):
        if method == 'GET':
            return {'year': '2026', 'week': '41-01', 'products': [
                {'ProdKey': 20, 'ProdName': 'ROSE', 'CurrentQty': current[0], 'CurrentOrderRows': 1, 'OutUnit': '단'}]}
        posts.append(deepcopy(kw['body']))
        if kw['body']['manager'] in reject_managers:
            raise OrderRejected('SALES_MANAGER_INVALID: 선택한 담당자를 UserInfo에서 찾을 수 없습니다.')
        current[0] += 3
        return {'verified': True, 'orderMasterKey': 30, 'verifiedCount': 1,
                'results': [{'prodKey': 20, 'finalQty': 5, 'unit': '단', 'status': 'ADDED'}]}
    return request, posts


def test_order_is_registered_under_the_customers_manager_not_the_sender(order):
    request, posts = erp()
    assert reg.register(order, request, managers={'10': 'nenovaSD7'})['verified']
    assert [p['manager'] for p in posts] == ['nenovaSD7']
    assert order['staff'] == '김인석'  # 올린 사람은 기록에 그대로


def test_customer_without_a_manager_registers_under_the_login_account(order):
    request, posts = erp()
    assert reg.register(order, request, managers={})['verified']
    assert [p['manager'] for p in posts] == ['']  # 서버가 로그인 계정을 담당자로 쓴다


def test_manager_unknown_to_the_erp_does_not_block_the_order(order):
    request, posts = erp(reject_managers=('퇴사자',))
    assert reg.register(order, request, managers={'10': '퇴사자'})['verified']
    assert [p['manager'] for p in posts] == ['퇴사자', '']  # 거절(저장 없음) 뒤 담당자만 비워 한 번 더
    group = read(reg.journal_path(order))['groups']['10']
    assert group['state'] == 'verified' and group['manager_fallback'] == '퇴사자'
    assert group['payload']['manager'] == ''  # 사전 기록도 실제 보낸 내용과 같다


def test_other_rejections_are_not_retried(order):
    def request(rid, method, **kw):
        if method == 'GET':
            return {'year': '2026', 'week': '41-01', 'products': [
                {'ProdKey': 20, 'ProdName': 'ROSE', 'CurrentQty': 2, 'CurrentOrderRows': 1, 'OutUnit': '단'}]}
        request.posts += 1
        raise OrderRejected('ERP_EDIT_LOCKED: 다른 사용자가 편집 중')
    request.posts = 0
    with pytest.raises(OrderRejected):
        reg.register(order, request, managers={'10': 'nenovaSD7'})
    assert request.posts == 1


def test_customer_manager_table_comes_from_the_master(monkeypatch):
    from core import order_services
    monkeypatch.setattr(order_services, 'master', lambda: {'customers': {'data': [
        {'nenova_key': 10, 'staff': 'nenovaSD7'}, {'nenova_key': 11, 'staff': ''}, {'name': '키 없음'}]}})
    assert order_services.customer_managers() == {'10': 'nenovaSD7', '11': ''}
    monkeypatch.setattr(order_services, 'master', lambda: (_ for _ in ()).throw(RuntimeError('down')))
    assert order_services.customer_managers() == {}
