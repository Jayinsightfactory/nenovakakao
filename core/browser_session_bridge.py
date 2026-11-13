"""Disk queue transport to a Chrome native host; never reads browser cookies."""
import json
import re
import time
import uuid
from pathlib import Path
from core.atomic_json import save, read

ROOT = Path(__file__).resolve().parents[1]
QUEUE = ROOT / 'data' / 'browser_session_bridge'


def screenshot_scope(row):
    return [{k: r.get(k) for k in ('customerName', 'matchedProductName', 'quantity',
             'sourceUnit', 'orderYear', 'orderWeek', 'managerName', 'note')}
            for r in row['receipt']]


def validate_job(job):
    from core import defect_approval as d
    from core.defect_services import DefectAdapter
    from core.moyi_control import is_paused
    if is_paused() or job['expires_at'] <= time.time():
        raise ValueError('paused_or_expired')
    rid = job.get('request_id', '')
    if rid == 'order-history':
        from core import import_order
        params = job.get('params') or {}
        if (not import_order.config().get('browser_order_registration') or job.get('method') != 'GET'
                or job.get('body') is not None or set(params) != {'custKey', 'year', 'week'}
                or not isinstance(params['custKey'], int) or params['custKey'] <= 0
                or not re.fullmatch(r'20\d{2}', str(params['year']))
                or not re.fullmatch(r'\d{2}-0[123]', str(params['week']))):
            raise ValueError('invalid_order_history_scope')
        return
    if rid == 'defect-master' and job.get('method') == 'MASTER':
        if job.get('params') not in ({'entity':'products'}, {'entity':'customers'}) or job.get('body') is not None:
            raise ValueError('invalid_master_scope')
        cfg = read(d.ROOT.parent / 'defect_config.json')
        if not cfg.get('enabled') or cfg.get('transport') != 'browser-session':
            raise ValueError('browser_master_disabled')
        return
    if re.fullmatch(r'ORD-[A-F0-9]{8}', rid):
        from core.order_registration import validate_job as validate_order
        return validate_order(job)
    if not re.fullmatch(r'불량-[A-F0-9]{12}', rid):
        raise ValueError('invalid_request_id')
    row = d.load(d.ROOT / (rid + '.json'))
    if job['method'] == 'SCREENSHOT':
        if row.get('status') != 'completed' or not row.get('receipt'):
            raise ValueError('verified_completion_required')
        expected = screenshot_scope(row)
        if job.get('params') != expected or job.get('body') is not None:
            raise ValueError('invalid_screenshot_scope')
        return
    if job['method'] == 'GET':
        if row['recipient'] != d.recipient_for(row['event']):
            raise ValueError('recipient_mismatch')
        scope = DefectAdapter.scope(row)
        allowed = (scope, {**scope, 'view': 'support', 'manager': ''})
        if job.get('params') not in allowed or job.get('body') is not None:
            raise ValueError('invalid_scope')
        return
    if (row.get('approved_revision') != row['revision']
            or row.get('status') not in ('approved', 'write_unknown')
            or not (row.get('approval_event_id') or d.auto_authorized(row)) or not d.ready(row)
            or row['recipient'] != d.recipient_for(row['event'])):
        raise ValueError('approval_required')
    if job['method'] == 'POST':
        expected = DefectAdapter().payload(row)
        body = dict(job.get('body') or {})
        action = body.get('action')
        if action == 'save':
            cfg_path = d.ROOT.parent / 'defect_config.json'
            if cfg_path.exists() and d.load(cfg_path).get('write_enabled') is False:
                raise ValueError('defect_writes_disabled')
            if row['status'] != 'write_unknown':
                raise ValueError('write_ahead_required')
            if body.pop('managerName', None) != row['extracted']['staff'] or not body.pop('managerId', None):
                raise ValueError('owner_required')
        elif action == 'rematch':
            expected['action'] = 'rematch'
        else:
            raise ValueError('action_not_allowed')
        if body != expected:
            raise ValueError('approved_payload_changed')
    else:
        raise ValueError('method_not_allowed')


def request(request_id, method, params=None, body=None, timeout=20):
    from core import browser_session_auth as auth
    auth.guard(method)
    QUEUE.mkdir(parents=True, exist_ok=True)
    key = uuid.uuid4().hex
    path = QUEUE / (key + '.json')
    job = {'id': key, 'request_id': request_id, 'method': method,
           'params': params, 'body': body, 'expires_at': time.time()+timeout,
           'state': 'queued'}
    validate_job(job)
    save(path, job)
    response_path = QUEUE / (key + '.response.json')
    while time.time() < job['expires_at']:
        state = read(path)
        if state.get('state') == 'blocked':
            raise RuntimeError('웹 연결 요청 차단: ' + state.get('error', '연결 프로그램 버전 확인 필요'))
        if response_path.exists():
            response = read(response_path)
            if response.get('status') == 401:
                auth.expired()
            if response.get('status') not in (200, 201) or response.get('data', {}).get('success') is not True:
                detail = response.get('data', {}).get('error', 'unknown_error')
                raise RuntimeError('웹 세션 응답 확인 필요: ' + str(response.get('status', 0)) + ' / ' + str(detail)[:180])
            auth.recovered()
            return response['data']
        time.sleep(0.1)
    raise TimeoutError('웹 세션 연결 대기 시간 초과; 전송 결과 불명 시 자동 재시도 금지')


class BrowserDefectAdapter:
    """Reuse the exact sales-input contract; swap transport only."""
    def __new__(cls, profile='browser-session'):
        from core.defect_services import DefectAdapter
        class Adapter(DefectAdapter):
            def validate(self, row):
                connection = QUEUE / 'connection.json'
                status = json.loads(connection.read_text(encoding='utf-8')) if connection.exists() else {}
                if time.time()-status.get('at',0)>10:
                    raise RuntimeError('네노바 브라우저 연결 필요: 확장 ON 및 영업입력 탭 확인')
                self.request_id = row['id']
                return super().validate(row)

            def request(self, method, **kwargs):
                return request(self.request_id, method, kwargs.get('params'), kwargs.get('json'))
        return Adapter(profile)
