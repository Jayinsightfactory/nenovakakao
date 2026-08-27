"""Existing paste-order master access and fail-closed nenova registration."""
import os, time
import requests

ORBIT_DEFAULT = 'https://mindmap-viewer-production-adb2.up.railway.app'
_master_cache = {'at': 0, 'value': None}


def master():
    if _master_cache['value'] and time.time() - _master_cache['at'] < 1800:
        return _master_cache['value']
    base = os.getenv('ORBIT_SERVER', ORBIT_DEFAULT).rstrip('/')
    headers = {}
    token = os.getenv('ORBIT_TOKEN', '').strip()
    if token: headers['Authorization'] = 'Bearer ' + token
    response = requests.get(base + '/api/automation/master', headers=headers, timeout=30)
    response.raise_for_status()
    value = response.json()
    if not value.get('products') or not value.get('customers'):
        raise RuntimeError('붙여넣기 주문등록 마스터 응답 불완전')
    _master_cache.update(at=time.time(), value=value)
    return value


def register_bulk(draft):
    if os.getenv('NENOVA_ORDER_WRITE_ENABLED') != '1':
        raise RuntimeError('네노바 주문 쓰기 비활성: NENOVA_ORDER_WRITE_ENABLED=1 필요')
    user = os.getenv('NENOVA_USER_ID', '').strip()
    password = os.getenv('NENOVA_PASSWORD', '').strip()
    if not user or not password: raise RuntimeError('네노바 로그인 정보 미설정')
    base = os.getenv('NENOVA_SERVER', 'https://nenovaweb.com').rstrip('/')
    session = requests.Session()
    login = session.post(base + '/api/auth/login', json={'userId': user, 'password': password}, timeout=20)
    login.raise_for_status()
    token = login.json().get('token')
    if not token: raise RuntimeError('네노바 로그인 토큰 없음')
    headers = {'Authorization': 'Bearer ' + token, 'Idempotency-Key': draft['id']}
    payload = {'requestId': draft['id'], 'week': draft['week'], 'customerId': draft['customer_key'],
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
