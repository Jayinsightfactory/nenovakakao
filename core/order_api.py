"""Browser-independent transport; preserves the order journal/approval contract."""
from core.defect_services import DefectAdapter, BASE
from core.order_registration import OrderRejected

# 네노바웹이 아무것도 쓰지 않고 돌려주는 거절. 409 는 트랜잭션 첫 단계의 동시편집 잠금·현재수량 불일치만 인정한다.
REJECTED_STATUS = (400, 401, 403, 404)
REJECTED_409_CODES = ('ERP_EDIT_LOCKED', 'STALE_CURRENT_QTY')


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
        if method == 'POST' and response.status_code not in accepted:
            try:
                detail = response.json()
            except Exception:
                detail = {}
            detail = detail if isinstance(detail, dict) else {}
            code = str(detail.get('code') or '')
            if response.status_code in REJECTED_STATUS or (
                    response.status_code == 409 and code in REJECTED_409_CODES):
                raise OrderRejected(f"{code or 'HTTP ' + str(response.status_code)}: "
                                    + str(detail.get('error') or '전산 거절')[:200])
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
            try:
                self.auth.login()
                if is_paused():
                    raise RuntimeError('일시정지: 주문 API 저장 보류')
                validate_job(job)
            except Exception as exc:
                # 여기까지는 저장 요청을 보내기 전이다.
                raise OrderRejected('저장 요청 전 중단: ' + str(exc)[:200]) from exc
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
