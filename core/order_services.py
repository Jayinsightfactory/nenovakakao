"""Existing paste-order master access and fail-closed nenova registration."""
import os, re, time
import requests

ORBIT_DEFAULT = 'https://mindmap-viewer-production-adb2.up.railway.app'
_master_cache = {'at': 0, 'value': None}
_customer_history_cache = {}


def customer_history(customer_key):
    """Read-only top-ten order frequencies; quantity columns are not input units."""
    key = str(int(customer_key))
    cached = _customer_history_cache.get(key)
    if cached and time.time() - cached['at'] < 300:
        return cached['value']
    from core import import_order
    if import_order.registration_enabled():
        from datetime import datetime
        from core.browser_session_bridge import request
        today = datetime.now()
        params = {'custKey': int(key), 'year': str(today.year), 'week': f'{today.isocalendar().week:02d}-01'}
        if import_order.config().get('transport') == 'direct-api':
            from core.order_api import client
            value = client().read('/api/orders/my-customers', params)
        else:
            value = request('order-history', 'GET', params=params)
        if not isinstance(value.get('products'), list):
            raise RuntimeError('주문 이력 목록 누락')
        result = {'status': 'available', 'customer_key': key,
                  'products': [{**p, 'orderFrequency': p['UsageCount']} for p in value['products']],
                  'scope': '업체 전체 주문 품목 이력', 'fetched_at': time.time()}
        _customer_history_cache[key] = {'at': time.time(), 'value': result}
        return result
    base = os.getenv('ORBIT_SERVER', ORBIT_DEFAULT).rstrip('/')
    token = os.getenv('ORBIT_TOKEN', '').strip()
    headers = {'Authorization': 'Bearer ' + token} if token else {}
    response = requests.get(base + '/api/nenova/customers/' + key,
                            headers=headers, timeout=10)
    response.raise_for_status()
    value = response.json()
    if value.get('ok') is not True or str(value.get('customer', {}).get('CustKey')) != key:
        raise RuntimeError('거래처 주문 이력 응답 불일치')
    if not isinstance(value.get('topProducts'), list):
        raise RuntimeError('거래처 주문 이력 누락')
    result = {'status': 'available', 'customer_key': key, 'products': value['topProducts'],
              'scope': '상위 10개', 'fetched_at': time.time()}
    _customer_history_cache[key] = {'at': time.time(), 'value': result}
    return result

PRODUCT_ALIASES = {
    'washingtonwhite': ['워싱턴 화이트', '워싱턴화이트'],
    'europalpink': ['유로파 핑크', '유로파핑크', '유로파 라이트핑크'],
}


def _fetch_pages(base, path, headers, page_size=500):
    items, offset, total = [], 0, None
    while total is None or offset < total:
        response = requests.get(base + path, headers=headers,
                                params={'limit': page_size, 'offset': offset}, timeout=45)
        response.raise_for_status()
        value = response.json()
        page = value.get('items', [])
        total = int(value.get('total', len(page)))
        items.extend(page)
        if not page:
            break
        offset += len(page)
    return items


def _product_row(row):
    name = str(row.get('ProdName') or '').strip()
    alias_key = re.sub(r'[^a-z]', '', name.lower().replace('[ez]', ''))
    aliases = next((list(values) for suffix, values in PRODUCT_ALIASES.items()
                    if alias_key.endswith(suffix)), [])
    # The farm-prefixed and general products remain separate candidates. Never
    # silently discard [EZ] because Kakao text does not identify that variant.
    return {'name': name, 'name_en': name, 'name_alias': aliases,
            'category': row.get('FlowerName') or row.get('flowerCategory'),
            'origin': row.get('CounName') or row.get('countryName'),
            'code': row.get('ProdKey'), 'nenova_key': row.get('ProdKey')}


def _customer_row(row):
    aliases = [row.get('OrderCode'), row.get('CustCode')]
    descr = str(row.get('Descr') or '')
    aliases.extend(part.strip() for part in descr.split('/') if part.strip())
    return {'name': row.get('CustName') or row.get('OrderCode') or '',
            'name_alias': [a for a in aliases if a], 'code': row.get('OrderCode'),
            'nenova_key': row.get('CustKey'), 'staff': row.get('Manager') or ''}


def master():
    if _master_cache['value'] and time.time() - _master_cache['at'] < 1800:
        return _master_cache['value']
    from core import import_order
    if import_order.config().get('transport') == 'direct-api':
        from core.order_api import client
        def entity(name):
            data = client().read('/api/master', {'entity': name})
            rows = data.get('data', data.get(name))
            if not isinstance(rows, list):
                raise RuntimeError('주문 기준표 목록 누락')
            return rows
        products, customers = entity('products'), entity('customers')
        if len(products) < 1000 or len(customers) < 500:
            raise RuntimeError('주문 API 기준표 불완전')
        value = {'products': {'data': [_product_row(r) for r in products]},
                 'customers': {'data': [_customer_row(r) for r in customers]},
                 'source': 'direct-api', 'customer_history_loader': customer_history}
        _master_cache.update(at=time.time(), value=value)
        return value
    base = os.getenv('ORBIT_SERVER', ORBIT_DEFAULT).rstrip('/')
    headers = {}
    token = os.getenv('ORBIT_TOKEN', '').strip()
    if token: headers['Authorization'] = 'Bearer ' + token
    try:
        products = _fetch_pages(base, '/api/nenova/products', headers)
        customers = _fetch_pages(base, '/api/nenova/customers', headers)
    except requests.RequestException:
        # The verified browser-session catalog is shared with defect matching.
        # This read-only fallback does not authorize writes or invent history.
        from core.defect_runtime import MASTER
        from core.atomic_json import read
        if not MASTER.exists() or time.time() - MASTER.stat().st_mtime > 86400:
            raise
        value = read(MASTER)
        from core.defect_approval import _data
        if len(_data(value, 'products')) < 1000 or len(_data(value, 'customers')) < 500:
            raise RuntimeError('캐시 마스터 불완전')
        value.update(source='browser-session-catalog-cache', customer_history_loader=customer_history)
        _master_cache.update(at=time.time(), value=value)
        return value
    if len(products) < 1000 or len(customers) < 500:
        raise RuntimeError(f'네노바 실마스터 응답 불완전: 품목 {len(products)}, 거래처 {len(customers)}')
    value = {'products': {'data': [_product_row(row) for row in products]},
             'customers': {'data': [_customer_row(row) for row in customers]},
             'source': 'nenova-read-api', 'customer_history_loader': customer_history}
    _master_cache.update(at=time.time(), value=value)
    return value


def customer_managers():
    """업체키 → 전산에 지정된 담당자. 기준표를 못 읽으면 빈 표(담당자 없이 등록)로 진행한다."""
    try:
        rows = master().get('customers', {}).get('data', [])
    except Exception:
        return {}
    return {str(r.get('nenova_key')): str(r.get('staff') or '').strip()
            for r in rows if r.get('nenova_key') is not None}


def register_bulk(draft):
    from core import import_order
    if not import_order.registration_enabled() or not import_order.config().get('write_enabled'):
        raise RuntimeError('네노바 주문 쓰기 비활성')
    from core.order_registration import register
    return register(draft, managers=customer_managers())


def json_dumps(value):
    import json
    return json.dumps(value, ensure_ascii=False)
