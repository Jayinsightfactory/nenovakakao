"""Browser-independent transport; preserves the order journal/approval contract."""
from core.defect_services import DefectAdapter, BASE


class OrderAPI:
    def __init__(self, profile):
        self.auth = DefectAdapter(profile)

    def read(self, path, params):
        if path not in ('/api/master', '/api/orders/my-customers'):
            raise ValueError('허용되지 않은 주문 조회 경로')
        return self._call('GET', path, params=params)

    def _call(self, method, path, **kwargs):
        self.auth.login()
        response = self.auth.session.request(method, BASE + path, timeout=50,
                                            allow_redirects=False, **kwargs)
        if response.status_code == 401:
            self.auth.authenticated = False
            if method == 'GET':
                self.auth.login()
                response = self.auth.session.request(method, BASE + path, timeout=50,
                                                    allow_redirects=False, **kwargs)
                if response.status_code == 401:
                    self.auth.authenticated = False
        # 네노바웹 주문 저장(POST /api/orders)은 성공 시 201을 돌려준다. 200만 인정하면 저장된 주문을
        # 실패로 오인해 영수증을 잃고 '응답 불명'으로 영구 보류된다. 조회(GET)는 200만 인정.
        accepted = (200, 201) if method == 'POST' else (200,)
        if response.status_code not in accepted:
            raise RuntimeError('주문 API 실패: HTTP ' + str(response.status_code))
        value = response.json()
        if not isinstance(value, dict) or value.get('success') is not True:
            raise RuntimeError('주문 API 성공 응답 없음')
        return value

    def request(self, request_id, method, params=None, body=None, timeout=None):
        from core.moyi_control import is_paused
        from core.order_registration import validate_job
        if is_paused():
            raise RuntimeError('일시정지: 주문 API 실행 보류')
        job = {'request_id': request_id, 'method': method, 'params': params, 'body': body}
        validate_job(job)
        if method == 'POST':
            if body.get('source') != 'sales-paste' or body.get('orderMode') != 'ADD':
                raise ValueError('승인된 발주 추가만 허용')
            # Recheck after authentication, before the only possible write.
            self.auth.login()
            if is_paused():
                raise RuntimeError('일시정지: 주문 API 저장 보류')
            validate_job(job)
            return self._call('POST', '/api/orders', json=body)
        return self.read('/api/orders/my-customers', params)


_client = None


def client():
    global _client
    from core.import_order import config
    profile = config().get('credential_profile', '강현우')
    if _client is None or _client.auth.profile != profile:
        _client = OrderAPI(profile)
    return _client
