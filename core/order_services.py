"""Existing paste-order master access and fail-closed nenova registration."""
import os, re, time
import requests

ORBIT_DEFAULT = 'https://mindmap-viewer-production-adb2.up.railway.app'
_master_cache = {'at': 0, 'value': None}

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
             'source': 'nenova-read-api'}
    _master_cache.update(at=time.time(), value=value)
    return value


def register_bulk(draft):
    if os.getenv('NENOVA_ORDER_WRITE_ENABLED') != '1':
        raise RuntimeError('네노바 주문 쓰기 비활성: NENOVA_ORDER_WRITE_ENABLED=1 필요')
    from core.credential_store import load
    profile = draft.get('staff_room') or draft.get('staff', '')
    credential = load(profile)
    if not credential: raise RuntimeError(f"담당자 네노바 로그인 미설정: {profile}")
    user, password = credential['username'], credential['password']
    base = os.getenv('NENOVA_SERVER', 'https://nenovaweb.com').rstrip('/')
    session = requests.Session()
    login = session.post(base + '/api/auth/login', json={'userId': user, 'password': password}, timeout=20)
    login.raise_for_status()
    token = login.json().get('token')
    if not token: raise RuntimeError('네노바 로그인 토큰 없음')
    headers = {'Authorization': 'Bearer ' + token, 'Idempotency-Key': draft['id']}
    payload = {'requestId': draft['id'], 'approvedBy': draft['staff'], 'week': draft['week'], 'customerId': draft['customer_key'],
               'items': [{'productCode': i['product_key'], 'qty': i['quantity'], 'unit': i['unit']}
                         for i in draft['items']]}
    response = session.post(base + '/api/orders', headers=headers, json=payload, timeout=30)
    response.raise_for_status()
    result = response.json()
    verify = session.get(base + '/api/orders', headers=headers, params={'requestId': draft['id']}, timeout=30)
    verify.raise_for_status()
    if draft['id'] not in json_dumps(verify.json()):
        raise RuntimeError('주문등록 후 요청번호 재조회 실패')
    return result


def json_dumps(value):
    import json
    return json.dumps(value, ensure_ascii=False)
