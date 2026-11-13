from copy import deepcopy
from core.defect_approval import rematch
from core.defect_distribution import scopes


def fixture():
    row = {'event': {'timestamp': '2026년 9월 22일 오후 4:35'},
           'extracted': {'sequence': '38-1', 'customer': '수연원예',
                         'category': '장미', 'issues': [],
                         'items': [{'product_raw': 'Coral Reef', 'quantity_raw': '6', 'unit_raw': '단'}]}}
    master = {'customers': [{'name': '수연원예', 'nenova_key': 439}],
              'products': [{'name': 'ROSE / Coral Reef 50cm', 'category': '장미',
                            'origin': origin, 'nenova_key': key} for origin, key in [('콜롬비아', 1), ('에콰도르', 2)]],
              'distribution': {'status': 'available', 'customer_key': 439,
                               'periods': [{'year': 2026, 'week': 38, 'product_keys': ['2']},
                                           {'year': 2026, 'week': 37, 'product_keys': ['1']}]}}
    return row, master


def test_current_customer_distribution_resolves_ambiguity_before_previous():
    row, master = fixture()
    item = rematch(row, master, learned={})['items'][0]
    assert item['product']['nenova_key'] == 2
    assert item['matched_by'] == 'customer_distribution'
    assert item['distribution_evidence']['week'] == 38


def test_previous_period_and_origin_constraint():
    row, master = fixture()
    row['extracted']['origin'] = '콜롬비아'
    item = rematch(row, master, learned={})['items'][0]
    assert item['product']['nenova_key'] == 1
    assert item['distribution_evidence']['week'] == 37


def test_foreign_customer_and_wrong_year_evidence_ignored():
    row, master = fixture()
    for invalid in ('customer', 'year'):
        candidate = deepcopy(master)
        if invalid == 'customer':
            candidate['distribution']['customer_key'] = 999
        else:
            for p in candidate['distribution']['periods']:
                p['year'] = 2025
        assert rematch(row, candidate, learned={})['items'][0]['distribution_evidence'] is None


def test_no_name_or_explicit_size_match_does_not_accept_allocated_item():
    row, master = fixture()
    for term in ('unrelated flower', 'Coral Reef 70cm'):
        row['extracted']['items'][0]['product_raw'] = term
        assert rematch(row, master, learned={})['items'][0]['product'] is None


def test_year_boundary_uses_source_not_upload_week():
    row, _ = fixture()
    row['event']['timestamp'] = '2026년 1월 2일 오전 9:00'
    row['extracted']['sequence'] = '1-1'
    assert scopes(row) == [(2026, 1), (2025, 52)]

def test_slow_worker_consumes_finished_lookup_before_cache_refresh(monkeypatch):
    from concurrent.futures import Future
    from core import defect_distribution as mod
    row, _ = fixture()
    future = Future()
    submits = []
    class Executor:
        def submit(self, *args):
            submits.append(args)
            return future
    clock = [0]
    monkeypatch.setattr(mod, '_jobs', {})
    monkeypatch.setattr(mod, '_executor', Executor())
    monkeypatch.setattr(mod.time, 'monotonic', lambda: clock[0])
    assert mod.poll(row, 439) is None
    result = {'status': 'available', 'customer_key': 439, 'periods': []}
    future.set_result(result)
    clock[0] = 900
    assert mod.poll(row, 439) == result
    assert len(submits) == 1
    clock[0] = 1100
    assert mod.poll(row, 439) == result
    assert len(submits) == 1
    clock[0] = 1201
    mod.poll(row, 439)
    assert len(submits) == 2
