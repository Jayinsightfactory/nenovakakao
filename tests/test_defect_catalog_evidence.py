from core.defect_approval import rematch
from core.defect_matching import CATEGORY_NAMES, PRODUCT_NAMES, customer, match


def row(category, origin, product):
    return {'extracted': {'category': category, 'origin': origin, 'customer': '알파',
                         'customer_candidates': ['알파'], 'issues': [],
                         'items': [{'product_raw': product, 'quantity_raw': '2', 'unit_raw': '단'}]}}


def test_china_delphinium_cataloged_as_other_is_not_a_missing_variety():
    catalog = {'customers': [{'name': '알파플라워', 'nenova_key': 1}], 'products': [
        {'name': 'Delphinium CHINA / 화이트 White', 'nenova_key': 2560,
         'category': '기타', 'origin': '중국'},
        {'name': 'Delphinium / Centurion White', 'nenova_key': 584,
         'category': '델피늄', 'origin': '네덜란드'}]}
    for product in ('화이트', '화이트 델피늄'):
        result = rematch(row('델피늄', '중국', product), catalog)
        assert result['items'][0]['product']['nenova_key'] == 2560
    result = rematch(row('카네이션', '중국', '화이트'), catalog)
    assert result['items'][0]['product'] is None
    result = rematch(row('델피늄', '네덜란드', '화이트'), {'customers': catalog['customers'], 'products': catalog['products'][:1]})
    assert result['items'][0]['product'] is None


def test_light_pink_does_not_become_light_pink_peach():
    pink = {'name': 'Hydrangea light pink (연핑크)', 'nenova_key': 3498}
    peach = {'name': 'Hydrangea Light pink peach (연핑크피치) (GL)', 'nenova_key': 3247}
    assert match('연핑크', [pink, peach], CATEGORY_NAMES['수국'])[0] == pink
    assert match('연핑크', [peach], CATEGORY_NAMES['수국'])[0] is None


def test_curated_lege_pink_and_sofia_grade_require_exact_catalog_target():
    lege = {'name': 'CARNATION Lege Pink', 'nenova_key': 425}
    assert match('레게핑크', [lege], PRODUCT_NAMES)[0] == lege
    sofia = {'name': 'SPRAY ROSE CHINA / 소피아 베베 (Spray-Rose Sofia babe)', 'nenova_key': 2129}
    long = {'name': sofia['name'] + ' 70-75cm', 'nenova_key': 3477}
    assert match('스프레이장미 소피아베베A급', [sofia, long], PRODUCT_NAMES)[0] == sofia
    assert match('스프레이장미 소피아베베A급', [long], PRODUCT_NAMES)[0] is None


def test_customer_a_suffix_stays_distinct_from_main_customer():
    rows = [{'name': '태림원예', 'nenova_key': 565}, {'name': '태림A', 'nenova_key': 689}]
    source = {'customer': '태림원예A', 'customer_candidates': [], 'issues': []}
    assert customer(source, rows)[0]['nenova_key'] == 689
    source['customer'] = '태림'
    assert customer(source, rows)[0] is None
