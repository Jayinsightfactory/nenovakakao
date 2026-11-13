import copy

from core import import_order as orders
from core.order_input import quantity_evidence
from core.order_registration import scope


def test_source_header_restores_company_without_changing_week_or_input():
    row = {'week': '46-1', 'created_at': 1793577600, 'status': 'waiting',
           'event': {'content': '46-1 콜 카네이션 발주\n주광\n돈셀 10단'},
           'items': [{'index': 1, 'raw_product': '돈셀', 'raw_customer': '', 'category': '콜 카네이션'}]}
    original = copy.deepcopy(row)
    master = {'customers': [{'name': '주광농원', 'nenova_key': 533, 'name_alias': ['주광']}],
              'products': [{'name': 'CARNATION Doncel', 'category': '카네이션', 'origin': '콜롬비아', 'nenova_key': 11},
                           {'name': 'Doncel', 'category': '장미', 'origin': '콜롬비아', 'nenova_key': 12}],
              'customer_histories': {'533': {'status': 'available', 'customer_key': 533,
                                           'products': [{'ProdKey': 11, 'orderFrequency': 4}]}}}
    proposed = orders.reanalyze_draft(row, master)
    assert row == original
    assert proposed['items'][0]['customer_key'] == 533
    assert proposed['items'][0]['product_key'] == 11
    assert proposed['items'][0]['quantity'] == 10
    assert proposed['items'][0]['order_history']['ProdKey'] == 11
    assert scope(proposed)['week'] == '46-01'
    assert proposed['status'] == 'waiting' and 'confirmed_revision' not in proposed


def test_unresolved_explicit_customer_is_not_overwritten_by_previous_header():
    row = {'event': {'content': '주광\nCL3\n블루 2박스'},
           'items': [{'raw_product': '블루', 'raw_customer': 'CL3'}]}
    proposed = orders.reanalyze_draft(row, {'customers': [{'name': '주광농원', 'nenova_key': 533, 'name_alias': ['주광']}], 'products': []})
    assert not proposed['items'][0].get('customer_key')


def test_order_unit_header_is_evidence_but_bare_quantity_without_header_is_not():
    assert quantity_evidence('*발주단위/박스\n블루 2', '블루')[0][1] == [('2', '박스')]
    assert quantity_evidence('블루 2', '블루')[0][1] == []


def test_explicit_length_cannot_fall_back_to_curated_50cm():
    item = {'raw_product': '몬디알화이트 60cm', 'category': '콜롬비아 장미', 'origin': '콜롬비아'}
    products = [{'name': 'ROSE / Mondial White 50cm', 'category': '장미', 'origin': '콜롬비아', 'nenova_key': 50},
                {'name': 'ROSE / Mondial White 60cm', 'category': '장미', 'origin': '콜롬비아', 'nenova_key': 60}]
    orders._rematch_item(item, {'products': products})
    assert item['product_key'] == 60
    orders._rematch_item(item, {'products': products[:1]})
    assert not item['matched'] and item['product_key'] is None


def test_prefix_codes_are_not_customer_aliases():
    assert orders.match_customer('CL2', [{'name': '주광농원', 'code': 'YCL2'}])[0] is None


def test_bare_family_variety_prefers_exact_name_over_length_variant():
    item = {'raw_product': 'Gerry', 'category': '심비디움', 'origin': '네덜란드'}
    orders._rematch_item(item, {'products': [
        {'name': 'Cymbidium / Gerry', 'category': '심비디움', 'origin': '네덜란드', 'nenova_key': 1},
        {'name': 'Cymbidium / Gerry 80cm', 'category': '심비디움', 'origin': '네덜란드', 'nenova_key': 2}]})
    assert item['product_key'] == 1


def test_exact_color_annotation_needs_unique_customer_history():
    products = [{'name': 'Hydrangea White (화이트)', 'category': '수국', 'nenova_key': 1},
                {'name': 'Hydrangea Mini White (화이트)', 'category': '수국', 'nenova_key': 2}]
    item = {'raw_product': '화이트', 'category': '수국', 'customer_key': 53}
    master = {'products': products, 'customer_histories': {'53': {
        'status': 'available', 'customer_key': 53, 'products': [{'ProdKey': 1, 'orderFrequency': 2}]}}}
    orders._rematch_item(item, master)
    assert item['product_key'] == 1 and item['order_history']['ProdKey'] == 1
    master['customer_histories']['53']['products'].append({'ProdKey': 2, 'orderFrequency': 100})
    orders._rematch_item(item, master)
    assert not item['matched']
    master['customer_histories']['53']['customer_key'] = 54
    orders._rematch_item(item, master)
    assert not item['matched']
