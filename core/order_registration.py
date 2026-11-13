"""ERP sales-paste ADD contract with durable per-customer write receipts.

An unanswered POST is never replayed. A saved server receipt permits read-only
reconciliation; equal quantities alone cannot prove that this request committed.
"""
import math
import re
import time
from datetime import datetime
from pathlib import Path
from core.atomic_json import read, save

JOURNALS = Path(__file__).resolve().parents[1] / 'data' / 'order_registration'


class OrderRejected(RuntimeError):
    """저장 요청을 보내지 못했거나 전산이 명확히 거절했다 — 전산에 쓰인 것이 없다.

    응답을 못 받은 경우(시간 초과·연결 끊김·5xx)는 여기에 해당하지 않으며 계속 '응답 불명'이다.
    """


TITLES = ('차장', '대리', '과장', '부장', '팀장', '실장', '이사', '주임', '사원', '님')


def manager_name(staff):
    """카톡 보낸 사람 이름을 전산 사용자 이름으로 다듬는다. 예: '김도준(j)' → '김도준', '김원영차장' → '김원영'.

    전산에 없는 이름(외부 인원 등)은 data/import_order_config.json 의 manager_aliases 로 지정한다:
    {"manager_aliases": {"카톡 이름": "전산 사용자 이름 또는 ID"}}. 추측으로 다른 사람에게 붙이지 않는다.
    """
    from core import import_order
    raw = str(staff or '').strip()
    alias = (import_order.config().get('manager_aliases') or {}).get(raw)
    if alias:
        return str(alias).strip()
    name = re.sub(r'\s*[(\[（][^)\]）]*[)\]）]\s*', ' ', raw).strip()
    changed = True
    while changed:
        changed = False
        for title in TITLES:
            if name.endswith(title) and len(name) - len(title) >= 2:
                name = name[:-len(title)].strip()
                changed = True
    return name


def source_date(row):
    """주문 원문이 카톡에 올라온 날. 수집·분석이 늦어져도 원문 날짜를 기준으로 삼는다."""
    event = row.get('event') or {}
    source = str(event.get('source_timestamp') or '')
    if re.match(r'^20\d{2}-\d{2}-\d{2}', source):
        return datetime.fromisoformat(source[:10])
    from core.keyword_forward import timestamp
    stamp = timestamp(str(event.get('timestamp') or ''))
    if stamp is not None:
        return stamp.replace(tzinfo=None)
    return datetime.fromtimestamp(row['created_at'])


def order_year(day, week):
    """연도가 적히지 않은 차수의 연도. 원문 날짜와 가장 가까운 해의 차수로 본다.

    12월 말에 올라온 '1-1차'는 다음 해 1차 물량이고, 1월 초에 올라온 '52-1차'는 지난해 차수다.
    """
    base_year, current_week, _ = day.isocalendar()
    if current_week - week > 26:
        return base_year + 1
    if week - current_week > 26:
        return base_year - 1
    return base_year


def scope(row):
    match = re.fullmatch(r'\s*(?:(20\d{2})[년 /-]+)?(\d{1,2})\s*(?:차)?[-.]([123])(?:차)?\s*', str(row.get('week', '')))
    if not match or not 1 <= int(match[2]) <= 53:
        raise ValueError('주문 차수를 41-1처럼 확인해주세요. 불량 차수 이월 규칙은 주문에 적용하지 않습니다.')
    year = match[1] or str(order_year(source_date(row), int(match[2])))
    return {'year': year, 'week': f'{int(match[2]):02d}-{int(match[3]):02d}'}


def journal_path(row):
    if not re.fullmatch(r'ORD-[A-F0-9]{8}', row['id']):
        raise ValueError('invalid_order_id')
    return JOURNALS / (row['id'] + '.json')


def validate_job(job):
    from core import import_order as orders
    rows = orders._read(orders.STATE, {})
    matches = [r for r in rows.values() if r.get('id') == job['request_id']]
    if len(matches) != 1 or not orders.config().get('enabled'):
        raise ValueError('order_request_missing')
    row = matches[0]
    expected_scope = scope(row)
    customers = {int(i['customer_key']) for i in row['items'] if i.get('customer_key')}
    if job['method'] == 'GET':
        params = job.get('params') or {}
        if params not in [{**expected_scope, 'custKey': key} for key in customers] or job.get('body') is not None:
            raise ValueError('order_read_scope_changed')
        return
    if job['method'] != 'POST' or not orders.config().get('write_enabled') or row.get('simulation'):
        raise ValueError('order_write_disabled')
    if row.get('confirmed_revision') != orders.revision(row) or row.get('status') != 'registering' or orders.validate(row):
        raise ValueError('order_approval_required')
    journal = read(journal_path(row))
    if journal.get('revision') != orders.revision(row):
        raise ValueError('order_revision_changed')
    group = journal.get('groups', {}).get(str((job.get('body') or {}).get('custKey')))
    if not group or group.get('state') != 'write_unknown' or group.get('payload') != job.get('body') or job.get('params') is not None:
        raise ValueError('order_write_ahead_required')


def normalize_unit(unit):
    """네노바웹 lib/orderUtils.normalizeOrderUnit 과 같은 표기 통일(박스·단·송이).

    저장 응답의 단위는 서버가 통일한 값이고 조회의 OutUnit 은 원장 원문(Box, 스팀, 대 등)이라
    글자 그대로 비교하면 정상 저장도 불일치로 보인다. 모르는 표기는 통일하지 않고 원문 그대로 둔다.
    """
    raw = str(unit or '').strip()
    lower = raw.lower()
    if raw in ('박스', '단', '송이'): return raw
    if raw in ('개', '대') or re.fullmatch(r'st|ea', lower) or re.search(r'stem|steam|송이|개|대|스팀|스템', lower): return '송이'
    if re.search(r'bunch|bun|단', lower): return '단'
    if re.search(r'box|박스|박', lower): return '박스'
    return raw


def load_products(request, row, customer_key):
    params = {**scope(row), 'custKey': int(customer_key)}
    result = request(row['id'], 'GET', params=params)
    if str(result.get('year')) != params['year'] or result.get('week') != params['week'] or not isinstance(result.get('products'), list):
        raise ValueError('주문 조회 연도·차수 또는 품목 목록 불일치')
    products = result['products']
    if len({int(p['ProdKey']) for p in products}) != len(products):
        raise ValueError('주문 조회 품목 중복')
    return products


def verify_receipt(group, products):
    receipt = group.get('server_receipt') or {}
    expected = {int(i['prodKey']) for i in group['payload']['items']}
    results = receipt.get('results') or []
    if receipt.get('verified') is not True or not receipt.get('orderMasterKey') or receipt.get('verifiedCount') != len(expected):
        raise ValueError('서버 주문 저장 검증 누락; 자동 재등록하지 않습니다.')
    if {int(r.get('prodKey', 0)) for r in results} != expected or len(results) != len(expected):
        raise ValueError('서버 주문 품목 범위 불일치')
    by_key = {int(p['ProdKey']): p for p in products}
    for result in results:
        product = by_key.get(int(result['prodKey']))
        if result.get('status') not in {'OK', 'ADDED', 'UPDATED'} or not product:
            raise ValueError('주문 저장 품목 확인 실패')
        if normalize_unit(product.get('OutUnit')) != normalize_unit(result.get('unit')) or not math.isclose(float(product['CurrentQty']), float(result['finalQty']), abs_tol=0.0001):
            raise ValueError('저장 후 주문 수량·단위 불일치; 재조회 필요')


def register(row, request=None, managers=None):
    """managers: {업체키(str): 전산에 지정된 그 업체 담당자}. 주면 주문 담당자는 업체 기준으로 넣는다.

    카톡에 올린 사람은 기록(row['staff'], 처리 기록)에만 남기고 등록을 막는 조건으로 쓰지 않는다.
    업체에 담당자가 없거나 전산이 그 담당자를 모르면 담당자를 비워 보내 로그인 계정으로 등록한다.
    """
    from core import import_order as orders
    from core.moyi_control import is_paused
    if request is None:
        if orders.config().get('transport') == 'direct-api':
            from core.order_api import client
            request = client().request
        else:
            from core.browser_session_bridge import request
    if orders.validate(row) or row.get('confirmed_revision') != orders.revision(row):
        raise ValueError('확정된 주문만 등록할 수 있습니다.')
    path = journal_path(row)
    journal = read(path) if path.exists() else {'revision': orders.revision(row), 'groups': {}}
    if journal['revision'] != orders.revision(row):
        raise ValueError('진행 중 주문 내용 변경; 기존 처리 내역 확인 필요')
    groups = {}
    for item in row['items']:
        groups.setdefault(str(item['customer_key']), []).append(item)
    final_orders = []
    for key, items in groups.items():
        if is_paused():
            raise RuntimeError('일시정지: 주문 상태 보존')
        group = journal['groups'].get(key)
        if group and group['state'] == 'write_unknown' and not group.get('server_receipt'):
            raise RuntimeError('주문 응답 불명: 원장 확인 필요, 자동 재등록 금지')
        products = load_products(request, row, key)
        if not group:
            by_key = {int(p['ProdKey']): p for p in products}
            payload_items = []
            for item in items:
                pk = int(item['product_key'])
                if any(p['prodKey'] == pk for p in payload_items):
                    raise ValueError('같은 업체·품목이 여러 줄입니다. 수량을 확인해주세요.')
                product = by_key.get(pk, {})
                if int(product.get('CurrentOrderRows', 0)) > 1:
                    raise ValueError('동일 품목 주문 원장이 여러 건입니다.')
                payload_items.append({'prodKey': pk, 'prodName': item['product'], 'qty': item['quantity'],
                    'unit': '송이' if item['unit'] == '스팀' else item['unit'],
                    'expectedCurrentQty': float(product.get('CurrentQty', 0))})
            payload = {'source': 'sales-paste', 'orderMode': 'ADD', 'manager': (str(managers.get(str(key)) or '').strip() if managers is not None
                                   else manager_name(row['staff'])),
                       'custKey': int(key), 'custName': items[0]['customer'], **scope(row), 'items': payload_items}
            group = {'state': 'write_unknown', 'payload': payload, 'started_at': time.time()}
            journal['groups'][key] = group
            save(path, journal)  # durable BEFORE external write
            try:
                try:
                    receipt = request(row['id'], 'POST', body=payload, timeout=50)
                except OrderRejected as exc:
                    # 담당자를 전산이 모르는 것은 등록을 막을 이유가 아니다. 쓰인 것이 없으므로
                    # 담당자만 비워(로그인 계정) 사전 기록을 고친 뒤 한 번 더 보낸다.
                    if managers is None or not payload['manager'] or 'SALES_MANAGER_INVALID' not in str(exc):
                        raise
                    group['manager_fallback'] = payload['manager']
                    payload['manager'] = ''
                    save(path, journal)
                    receipt = request(row['id'], 'POST', body=payload, timeout=50)
            except OrderRejected:
                # 쓰인 것이 없으므로 사전 기록을 지운다. 남겨 두면 이 업체 묶음이 영구히 '응답 불명'으로 묶인다.
                journal['groups'].pop(key, None)
                save(path, journal)
                raise
            group['server_receipt'] = receipt
            save(path, journal)  # durable BEFORE the second read
            products = load_products(request, row, key)
        verify_receipt(group, products)
        group['state'] = 'verified'
        save(path, journal)
        final_orders.append({'customer_key': key, 'customer': items[0]['customer'], 'week': row['week'], 'complete': True,
            'items': [{'product': p.get('DisplayName') or p['ProdName'], 'product_key': p['ProdKey'],
                       'quantity': p['CurrentQty'], 'unit': p['OutUnit']}
                      for p in products if float(p.get('CurrentQty', 0)) > 0]})
    return {'verified': True, 'request_id': row['id'], 'revision': orders.revision(row),
            'outcome': 'registered', 'final_orders': final_orders}
