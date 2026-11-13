from core.defect_llm import apply
from core.defect_approval import question


def row():
    return {'short_label': '불량135', 'revision': 1, 'information_only': True,
            'extracted': {'customer': '아이엠', 'sequence': '39-2', 'issues': [], 'items': []},
            'customer': None, 'customer_candidates': [
                {'name': '아이엠 (I am)', 'nenova_key': 457},
                {'name': '아이엠（미우）', 'nenova_key': 456}],
            'items': [{'product_raw': 'sp화이트', 'quantity_raw': '30', 'unit_raw': '단',
                       'product': {'nenova_key': 10}, 'candidates': [{'nenova_key': 10}]}]}


def test_confidence_alone_cannot_choose_customer_branch():
    r = apply(row(), {'customer_key': 457, 'customer_confidence': 1}, {})
    assert r['customer'] is None


def test_exclusive_complete_shipment_evidence_allows_branch():
    evidence = {str(k): {'status': 'available', 'periods': [{'product_keys': keys}]}
                for k, keys in [(457, ['10']), (456, [])]}
    r = apply(row(), {'customer_key': 457, 'customer_confidence': .98}, evidence)
    assert r['customer']['nenova_key'] == 457
    evidence['456']['status'] = 'unavailable'
    assert apply(row(), {'customer_key': 457, 'customer_confidence': 1}, evidence)['customer'] is None


def test_unknown_product_key_cannot_be_invented():
    r = row()
    r['items'][0]['product'] = None
    result = apply(r, {'products': [{'index': 0, 'key': 999, 'confidence': 1}]}, {})
    assert result['items'][0]['product'] is None
    assert result['items'][0]['quantity_raw'] == '30'


def test_customer_question_shows_catalog_names():
    text = question(row())
    assert '아이엠 (I am)' in text and '아이엠（미우）' in text
    assert '어느 업체 불량인가요' not in text
