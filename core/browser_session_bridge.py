"""Disk queue transport to a Chrome native host; never reads browser cookies."""
import json
import re
import time
import uuid
from pathlib import Path
from core.atomic_json import save

ROOT = Path(__file__).resolve().parents[1]
QUEUE = ROOT / 'data' / 'browser_session_bridge'


def validate_job(job):
    from core import defect_approval as d
    from core.defect_services import DefectAdapter
    from core.moyi_control import is_paused
    if is_paused() or job['expires_at'] <= time.time():
        raise ValueError('paused_or_expired')
    rid = job.get('request_id', '')
    if not re.fullmatch(r'불량-[A-F0-9]{12}', rid):
        raise ValueError('invalid_request_id')
    row = d.load(d.ROOT / (rid + '.json'))
    if (row.get('approved_revision') != row['revision']
            or row.get('status') not in ('approved', 'write_unknown')
            or not row.get('approval_event_id') or not d.ready(row)
            or row['recipient'] != d.recipient_for(row['event'])):
        raise ValueError('approval_required')
    scope = DefectAdapter.scope(row)
    if job['method'] == 'GET':
        if job.get('params') != scope or job.get('body') is not None:
            raise ValueError('invalid_scope')
    elif job['method'] == 'POST':
        expected = DefectAdapter().payload(row)
        body = dict(job.get('body') or {})
        action = body.get('action')
        if action == 'save':
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
        if response_path.exists():
            response = json.loads(response_path.read_text(encoding='utf-8'))
            if response.get('status') != 200 or response.get('data', {}).get('success') is not True:
                raise RuntimeError('웹 세션 응답 확인 필요: ' + str(response.get('status', 0)))
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
                if not status.get('page_ready') or time.time()-status.get('at',0)>10:
                    raise RuntimeError('네노바 브라우저 연결 필요: 확장 ON 및 영업입력 탭 확인')
                self.request_id = row['id']
                return super().validate(row)

            def request(self, method, **kwargs):
                return request(self.request_id, method, kwargs.get('params'), kwargs.get('json'))
        return Adapter(profile)
