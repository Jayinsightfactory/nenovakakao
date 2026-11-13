"""Sales-input-only adapter for nenovaweb's verified defect deduction API.

Contract checked against deployed 5d87c9dd. Never calls register, incoming-
confirm, DELETE, or updates an existing deduction key.
"""
from collections import Counter
from decimal import Decimal
from datetime import datetime
import requests
from core.credential_store import load
from core.defect_approval import ready

BASE = 'https://nenovaweb.com'
API = '/api/sales/defect-deductions'
UNITS = {'단': '단', '박스': '박스', 'BOX': '박스', 'box': '박스',
         '대': '스팀(대)', '스팀': '스팀(대)', '스팀(대)': '스팀(대)', '송이': '스팀(대)'}


class DuplicateDefect(ValueError):
    def __init__(self, entries, partial=False):
        super().__init__('동일 거래처/품목/수량 원장 존재: 중복 검토 필요')
        self.entries, self.partial = entries, partial


class DefectAdapter:
    def resolve_registration_period(self, row):
        from core import defect_approval as d
        path = d.ROOT.parent / 'defect_config.json'
        cfg = d.load(path) if path.exists() else {}
        if cfg.get('registration_scope_policy') == 'source_next' and row.get('status') not in ('completed', 'write_unknown', 'resolved_existing'):
            from core.defect_scope import upload_scope
            period = upload_scope(row)
            row.update(registration_scope=period, period_resolution={'policy':'source_next',
                'source_sequence':row['extracted']['sequence'], 'examined':[dict(period)]})
            return
        if cfg.get('registration_scope_policy') != 'support_registration' or row.get('status') in ('completed', 'write_unknown'):
            return
        from core.defect_period_transition import resolve, complete_support_history
        period, evidence = resolve(row, cfg['current_registration_period'],
            lambda scope: complete_support_history(scope, self.request),
            history_timestamp_mode=cfg.get('history_timestamp_mode'))
        row.update(registration_scope=period, period_resolution=evidence)

    def registration_listing(self, row, fallback):
        from core import defect_approval as d
        cfg_path = d.ROOT.parent / 'defect_config.json'
        cfg = d.load(cfg_path) if cfg_path.exists() else {}
        if cfg.get('registration_scope_policy') not in ('open_period', 'source_next') or row.get('status') == 'write_unknown':
            return fallback
        return self.request('GET', params={**self.scope(row), 'view': 'support', 'manager': ''})

    def ensure_period_open(self, row, listing):
        from core import defect_approval as d
        cfg_path = d.ROOT.parent / 'defect_config.json'
        cfg = d.load(cfg_path) if cfg_path.exists() else {}
        if cfg.get('registration_scope_policy') not in ('open_period', 'source_next') or row.get('status') == 'write_unknown':
            return
        scope = self.scope(row)
        if not isinstance(listing.get('rows'), list):
            raise ValueError('등록 차수 마감 조회 결과 없음')
        # REGISTERED describes an individual deduction, not a closed period.
        # The server applies the period-wide guard to correct-period only;
        # this adapter creates new drafts and never corrects existing periods.
        if listing.get('periodClosed') is True:
            raise ValueError(f"{scope['week']}차 마감: 서버 명시적 마감")
    def __init__(self, profile='강현우', session=None):
        self.profile = profile
        self.session = session or requests.Session()
        self.authenticated = session is not None
        self.owner = None

    def login(self):
        if self.authenticated:
            return
        credential = load(self.profile)
        if not credential:
            raise RuntimeError('불량 입력 네노바 계정 미설정: ' + self.profile)
        response = self.session.post(BASE + '/api/auth/login',
            json={'userId': credential['username'], 'password': credential['password']}, timeout=15)
        if response.status_code != 200 or not response.json().get('success'):
            raise RuntimeError('불량 입력 네노바 로그인 실패: HTTP ' + str(response.status_code))
        self.authenticated = True

    def request(self, method, **kwargs):
        self.login()
        response = self.session.request(method, BASE + API, timeout=15, **kwargs)
        if response.status_code == 401:
            self.authenticated = False
            # Only reads may be replayed. A write must go through the existing
            # write_unknown / receipt reconciliation workflow, never blind retry.
            if method.upper() == 'GET':
                self.login()
                response = self.session.request(method, BASE + API, timeout=15, **kwargs)
                if response.status_code == 401:
                    self.authenticated = False
        if response.status_code != 200:
            raise RuntimeError('불량 입력 API 실패: HTTP ' + str(response.status_code))
        data = response.json()
        if data.get('success') is not True:
            raise RuntimeError('불량 입력 API 성공 응답 없음')
        return data

    @staticmethod
    def scope(row):
        from core.defect_scope import upload_scope
        return upload_scope(row)

    @staticmethod
    def marker(row):
        return 'kakao-defect:' + row['id']

    def payload(self, row):
        customer = row['customer']
        authorization = ('자동처리' if row.get('authorization', {}).get('mode') == 'automatic' and not row.get('approval_event_id')
                         else '승인')
        rows = []
        for index, item in enumerate(row['items'], 1):
            product = item['product']
            unit = UNITS.get(item['unit_raw'])
            if not unit:
                raise ValueError('자동 환산 불가 단위: ' + item['unit_raw'])
            quantity = Decimal(item['quantity_raw'])
            if not quantity.is_finite() or quantity <= 0 or quantity.as_tuple().exponent < -4:
                raise ValueError('수량 정밀도/범위 확인 필요')
            rows.append({'customerName': customer['name'], 'custKey': customer['nenova_key'],
                'productName': product['name'], 'prodKey': product['nenova_key'],
                'colorName': row['extracted']['category'], 'quantity': float(quantity),
                'sourceUnit': unit, 'creditApplied': False, 'farmName': '',
                'deductionType': '불량차감',
                'note': f"{self.marker(row)}:{index} / 원차수 {row['extracted']['sequence']} / 원문 {row['event']['timestamp']} / {authorization} {row['recipient']} v{row['revision']}"})
        return {'action': 'save', **self.scope(row), 'rows': rows,
                'sourceFileName': self.marker(row)}

    def validate(self, row):
        self.resolve_registration_period(row)
        if not ready(row):
            raise ValueError('불량 매칭 미확정')
        payload = self.payload(row)
        listing = self.request('GET', params=self.scope(row))
        self.ensure_period_open(row, self.registration_listing(row, listing))
        staff = row['extracted']['staff']
        options = [o for o in listing.get('managerOptions', [])
                   if (o.get('managerName') or o.get('ManagerName') or o.get('name')) == staff]
        if len(options) != 1:
            raise ValueError('불량 작성자 입력 담당자 매칭 확인 필요: ' + staff)
        owner = options[0]
        owner_id = owner.get('managerId') or owner.get('ManagerId') or owner.get('id')
        if not owner_id:
            raise ValueError('불량 입력 담당자 ID 없음')
        self.owner = {'managerId': owner_id, 'managerName': staff}
        # Server rematch resolves authoritative keys without saving a row.
        checked = self.request('POST', json={**payload, 'action': 'rematch'})
        actual = checked.get('rows', [])
        if len(actual) != len(payload['rows']):
            raise ValueError('불량 재검증 항목 수 불일치')
        for a, b in zip(actual, payload['rows']):
            if (a.get('needsReview') or a.get('prodKey') != b['prodKey']
                    or a.get('custKey') != b['custKey']):
                raise ValueError('승인된 불량 매칭 재검증 실패')

    def lookup(self, row):
        data = self.request('GET', params=self.scope(row))
        entries = data.get('rows')
        if not isinstance(entries, list):
            raise ValueError('불량 원장 조회 rows 누락')
        for period in row.get('period_resolution', {}).get('examined', []):
            if period == self.scope(row):
                continue
            prior = self.request('GET', params=period).get('rows')
            if not isinstance(prior, list):
                raise ValueError('이전 차수 원장 대조 결과 누락')
            entries = entries + prior
        found = [r for r in entries if self.owned(row,r)]
        if found:
            return found
        # Manual identical entries must not be silently duplicated or adopted.
        desired = {self.signature(r) for r in self.payload(row)['rows']}
        duplicates = [r for r in entries if not r.get('isDeleted')
                      and (row.get('period_resolution') or
                           (str(r.get('orderYear')) == str(self.scope(row)['year'])
                            and str(r.get('orderWeek')) == str(self.scope(row)['week'])))
                      and r.get('deductionType') == '불량차감'
                      and self.signature(r) in desired]
        if duplicates:
            raise DuplicateDefect(duplicates, {self.signature(r) for r in duplicates} != desired)
        return None

    @staticmethod
    def signature(item):
        return (str(item.get('custKey')), str(item.get('prodKey')),
                str(Decimal(str(item.get('quantity', 0))).normalize()), item.get('sourceUnit'))

    def owned(self,row,item):
        # The deployed API omits sourceFileName in readback. It preserves the
        # full per-item request/revision note; accept only an exact note match.
        return item.get('sourceFileName') == self.marker(row) or (not item.get('sourceFileName') and item.get('note') in {
            r['note'] for r in self.payload(row)['rows']})

    def matches(self, row, receipt):
        desired = self.payload(row)['rows']
        return (isinstance(receipt, list) and len(receipt) == len(desired)
                and all(r.get('deductionKey') and self.owned(row,r)
                        and r.get('orderYear') == self.scope(row)['year']
                        and str(r.get('orderWeek')) == str(self.scope(row)['week'])
                        and r.get('managerName') == row['extracted']['staff']
                        and r.get('deductionType') == '불량차감' for r in receipt)
                and Counter(self.signature(r) for r in receipt) == Counter(self.signature(r) for r in desired))

    def insert(self, row):
        # Recheck immediately before saving; write_unknown here is only our
        # write-ahead marker, not permission to ignore a newly closed period.
        check_row = {**row, 'status': 'approved'}
        self.resolve_registration_period(check_row)
        if self.scope(check_row) != self.scope(row):
            raise ValueError('저장 직전 차수 전환 감지; 재대조 필요')
        listing = self.request('GET', params=self.scope(row))
        self.ensure_period_open(check_row, self.registration_listing(check_row, listing))
        if not self.owner:
            raise ValueError('입력 담당자 검증 필요')
        result = self.request('POST', json={**self.payload(row), **self.owner})
        if result.get('saved') != len(row['items']):
            raise ValueError('저장 항목 수 불일치; 자동 재시도 금지')
