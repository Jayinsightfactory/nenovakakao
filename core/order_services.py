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
    base = os.getenv('ORBIT_SERVER', ORBIT_DEFAULT).rstrip('/')
    token = os.getenv('ORBIT_TOKEN', '').strip()
    headers = {'Authorization': 'Bearer ' + token} if token else {}
    response = requests.get(base + '/api/nenova/customers/' + key,
                            headers=headers, timeout=45)
    response.raise_for_status()
    value = response.json()
    if value.get('ok') is not True or str(value.get('customer', {}).get('CustKey')) != key:
        raise RuntimeError('거래처 주문 이력 응답 불일치')
    if not isinstance(value.get('topProducts'), list):
        raise RuntimeError('거래처 주문 이력 누락')
    result = {'status': 'available', 'products': value['topProducts'], 'scope': '상위 10개'}
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
    base = os.getenv('ORBIT_SERVER', ORBIT_DEFAULT).rstrip('/')
    headers = {}
    token = os.getenv('ORBIT_TOKEN', '').strip()
    if token: headers['Authorization'] = 'Bearer ' + token
    products = _fetch_pages(base, '/api/nenova/products', headers)
    customers = _fetch_pages(base, '/api/nenova/customers', headers)
    if len(products) < 1000 or len(customers) < 500:
        raise RuntimeError(f'네노바 실마스터 응답 불완전: 품목 {len(products)}, 거래처 {len(customers)}')
    value = {'products': {'data': [_product_row(row) for row in products]},
             'customers': {'data': [_customer_row(row) for row in customers]},
             'source': 'nenova-read-api', 'customer_history_loader': customer_history}
    _master_cache.update(at=time.time(), value=value)
    return value


def register_bulk(draft):
    if os.getenv('NENOVA_ORDER_WRITE_ENABLED') != '1':
        raise RuntimeError('네노바 주문 쓰기 비활성: NENOVA_ORDER_WRITE_ENABLED=1 필요')
    from core.credential_store import load
    profile = draft.get('staff_room') or draft.get('staff', '')
    credential = load(profile)
    if not credential: raise RuntimeError(f"담당자 네노바 로그인 미설정: {profile}")
    # The former /api/orders payload and requestId-substring check were not
    # verified against the real ERP. Never enable those guessed write calls.
    # A production adapter must implement duplicate detection, per-customer
    # writes, exact unit verification, and a full-order receipt after reading.
    raise RuntimeError('네노바 실제 등록 API·중복방지·최종조회 규격 검증 필요: 등록 차단')


def json_dumps(value):
    import json
    return json.dumps(value, ensure_ascii=False)
