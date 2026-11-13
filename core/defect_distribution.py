"""Read-only, customer-scoped shipment evidence for defect matching."""
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
import requests

_executor = ThreadPoolExecutor(max_workers=2)
_jobs = {}


def scopes(row):
    year = int(row['event']['timestamp'].split('년')[0])
    week = int(row['extracted']['sequence'].split('-')[0])
    day = date.fromisocalendar(year, week, 1)
    return [(d.isocalendar().year, d.isocalendar().week)
            for d in (day, day - timedelta(days=7))]


def fetch(customer, periods):
    from core.order_services import ORBIT_DEFAULT
    base = os.getenv('ORBIT_SERVER', ORBIT_DEFAULT).rstrip('/')
    token = os.getenv('ORBIT_TOKEN', '').strip()
    with requests.Session() as session:
        if token:
            session.headers['Authorization'] = 'Bearer ' + token

        def get(path, params=None):
            response = session.get(base + '/api/nenova/' + path, params=params, timeout=5)
            response.raise_for_status()
            value = response.json()
            if value.get('ok') is not True:
                raise ValueError('분배 조회 응답 오류')
            return value

        result = []
        headers = {}
        for year, _ in periods:
            if year in headers:
                continue
            headers[year] = []
            for offset in range(0, 1000, 100):
                listing = get('shipments', {'year': year, 'custKey': customer,
                                           'limit': 100, 'offset': offset})
                headers[year].extend(listing['items'])
                if len(headers[year]) >= listing['total']:
                    break
                if not listing['items']:
                    raise ValueError('분배 조회 결과 불완전')
            if len(headers[year]) != listing['total']:
                raise ValueError('분배 조회 결과 불완전')
        for year, week in periods:
            keys = set()
            for header in headers[year]:
                if int(str(header['OrderWeek']).split('-')[0]) != week:
                    continue
                detail = get('shipments/' + str(int(header['ShipmentKey'])))['shipment']
                master = detail['master']
                if (str(master['CustKey']) != str(customer)
                        or int(master['OrderYear']) != year
                        or int(str(master['OrderWeek']).split('-')[0]) != week
                        or master.get('isDeleted')):
                    raise ValueError('분배 조회 범위 불일치')
                for item in detail['details']:
                    if item.get('isDeleted') or str(item.get('CustKey')) != str(customer):
                        continue
                    if any(float(item.get(q) or 0) > 0 for q in
                           ('BoxQuantity', 'BunchQuantity', 'SteamQuantity')):
                        keys.add(str(item['ProdKey']))
            result.append({'year': year, 'week': week, 'product_keys': sorted(keys)})
        return {'status': 'available', 'customer_key': customer, 'periods': result}


def poll(row, customer):
    """Never block the UI worker on network reads; cache for five minutes."""
    try:
        periods = scopes(row)
    except (ValueError, KeyError, TypeError):
        return {'status': 'unavailable', 'reason': 'source_scope_invalid'}
    key = (str(customer), tuple(periods))
    now = time.monotonic()
    entry = _jobs.get(key)
    # Consume a completed lookup at least once before expiring it. A busy
    # single UI executor may return after the whole cache interval has passed.
    if entry is None or (entry[2] and now - entry[0] > 300):
        entry = (now, _executor.submit(fetch, customer, periods), False)
        _jobs[key] = entry
    if not entry[1].done():
        return None
    if not entry[2]:
        _jobs[key] = (now, entry[1], True)
    try:
        return entry[1].result()
    except Exception:
        return {'status': 'unavailable', 'reason': 'shipment_lookup_failed'}
