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


def scope(row):
    match = re.fullmatch(r'\s*(?:(20\d{2})[년 /-]+)?(\d{1,2})\s*(?:차)?[-.]([123])(?:차)?\s*', str(row.get('week', '')))
    if not match or not 1 <= int(match[2]) <= 53:
        raise ValueError('주문 차수를 41-1처럼 확인해주세요. 불량 차수 이월 규칙은 주문에 적용하지 않습니다.')
    source = str(row.get('event', {}).get('source_timestamp', ''))
    year = match[1] or (source[:4] if re.match(r'^20\d{2}', source) else None)
    if not year:
        year = str(datetime.fromtimestamp(row['created_at']).year)
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
        if product.get('OutUnit') != result.get('unit') or not math.isclose(float(product['CurrentQty']), float(result['finalQty']), abs_tol=0.0001):
            raise ValueError('저장 후 주문 수량·단위 불일치; 재조회 필요')


def register(row, request=None):
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
            payload = {'source': 'sales-paste', 'orderMode': 'ADD', 'manager': row['staff'].replace('차장', '').replace('대리', '').strip(),
                       'custKey': int(key), 'custName': items[0]['customer'], **scope(row), 'items': payload_items}
            group = {'state': 'write_unknown', 'payload': payload, 'started_at': time.time()}
            journal['groups'][key] = group
            save(path, journal)  # durable BEFORE external write
            receipt = request(row['id'], 'POST', body=payload, timeout=50)
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
