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


class DefectAdapter:
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
        if response.status_code != 200:
            raise RuntimeError('불량 입력 API 실패: HTTP ' + str(response.status_code))
        data = response.json()
        if data.get('success') is not True:
            raise RuntimeError('불량 입력 API 성공 응답 없음')
        return data

    @staticmethod
    def scope(row):
        sequence = row['extracted']['sequence']
        year = int(row['event']['timestamp'].split('년')[0])
        week = int(sequence.split('-')[0])
        if not 2000 <= year <= 2100 or not 1 <= week <= 53:
            raise ValueError('불량 원문 연도/차수 확인 필요')
        return {'year': year, 'week': week}

    @staticmethod
    def marker(row):
        return 'kakao-defect:' + row['id']

    def payload(self, row):
        customer = row['customer']
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
                'note': f"{self.marker(row)}:{index} / 원차수 {row['extracted']['sequence']} / 원문 {row['event']['timestamp']} / 승인 {row['recipient']} v{row['revision']}"})
        return {'action': 'save', **self.scope(row), 'rows': rows,
                'sourceFileName': self.marker(row)}

    def validate(self, row):
        if not ready(row):
            raise ValueError('불량 매칭 미확정')
        payload = self.payload(row)
        listing = self.request('GET', params=self.scope(row))
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
        found = [r for r in entries if r.get('sourceFileName') == self.marker(row)]
        if found:
            return found
        # Manual identical entries must not be silently duplicated or adopted.
        desired = {self.signature(r) for r in self.payload(row)['rows']}
        if any(self.signature(r) in desired for r in entries):
            raise ValueError('동일 거래처/품목/수량 원장 존재: 중복 검토 필요')
        return None

    @staticmethod
    def signature(item):
        return (str(item.get('custKey')), str(item.get('prodKey')),
                str(Decimal(str(item.get('quantity', 0))).normalize()), item.get('sourceUnit'))

    def matches(self, row, receipt):
        desired = self.payload(row)['rows']
        return (isinstance(receipt, list) and len(receipt) == len(desired)
                and all(r.get('deductionKey') and r.get('sourceFileName') == self.marker(row)
                        and r.get('orderYear') == self.scope(row)['year']
                        and str(r.get('orderWeek')) == str(self.scope(row)['week'])
                        and r.get('managerName') == row['extracted']['staff']
                        and r.get('deductionType') == '불량차감' for r in receipt)
                and Counter(self.signature(r) for r in receipt) == Counter(self.signature(r) for r in desired))

    def insert(self, row):
        if not self.owner:
            raise ValueError('입력 담당자 검증 필요')
        result = self.request('POST', json={**self.payload(row), **self.owner})
        if result.get('saved') != len(row['items']):
            raise ValueError('저장 항목 수 불일치; 자동 재시도 금지')
