import json

from core import defect_approval as approval
from core import defect_phonetic as phonetic


def product(name, category, key):
    return {'name': name, 'name_en': name, 'name_alias': [], 'category': category,
            'origin': '', 'nenova_key': key}


CATALOG = [
    product('ALSTROMERIA Fifi', '알스트로', 1),
    product('ALSTROMERIA Fifi Butterplus', '알스트로', 2),
    product('ALSTROMERIA Voyager', '알스트로', 3),
    product('ROSE / Orange Crush 40cm', '장미', 4),
    product('ROSE / Orange Crush 50cm', '장미', 5),
    product('ROSE / Orange Crush 60cm', '장미', 6),
    product('ROSE CHINA / 카푸치노 (Capuchino)', '장미', 7),
    product('ROSE CHINA / 카푸치노 (Capuchino) 70cm-75cm', '장미', 8),
    product('[MEL] SPRAY ROSE CHINA / 미드나잇 매직 (Midnight magic)', '장미', 9),
    product('SPRAY ROSE CHINA / 미드나잇 매직 (Midnight magic)', '장미', 10),
    product('SPRAY ROSE CHINA / Fairy Lola', '장미', 11),
    product('ROSE CHINA / 빌라 릴라(Villa Lila)', '장미', 12),
]


def request(category, term):
    return {'extracted': {'category': category, 'origin': '', 'customer': '', 'customer_candidates': [],
                          'issues': [], 'items': [{'product_raw': term, 'quantity_raw': '3', 'unit_raw': '단'}]}}


def matched(category, term, learned=None):
    master = {'products': {'data': CATALOG}, 'customers': {'data': []}}
    item = approval.rematch(request(category, term), master, learned=learned or {})['items'][0]
    return (item['product'] or {}).get('name'), item['matched_by'], item['default_size']


def test_repeated_consonants_survive_so_fifi_is_not_empty():
    assert phonetic.hangul_key('피피') == phonetic.latin_key('Fifi') == 'pp'
    assert phonetic.hangul_key('보야저') == phonetic.latin_key('Voyager')


def test_category_written_in_full_still_reaches_master_category():
    assert matched('알스트로메리아', '피피') == ('ALSTROMERIA Fifi', 'phonetic', None)
    assert matched('알스트로', '보야저')[0] == 'ALSTROMERIA Voyager'


def test_sound_match_defaults_to_fifty_centimetres():
    assert matched('장미', '오렌지 크러쉬') == ('ROSE / Orange Crush 50cm', 'phonetic', '50cm')
    assert matched('장미', '오렌지 크러쉬 60cm')[0] == 'ROSE / Orange Crush 60cm'


def test_korean_catalog_text_picks_base_row_not_mel_or_long_stem():
    assert matched('', '카푸치노')[0] == 'ROSE CHINA / 카푸치노 (Capuchino)'
    assert matched('', '미드나잇 매직')[0] == 'SPRAY ROSE CHINA / 미드나잇 매직 (Midnight magic)'


def test_close_rival_variety_needs_a_clear_margin():
    assert matched('', '페어리 로라')[0] == 'SPRAY ROSE CHINA / Fairy Lola'
    name, how, _ = matched('', '선샤인')
    assert name is None and how is None


def test_learned_alias_outranks_sound():
    learned = {'알스트로': {'피피': {'target': 'ALSTROMERIA Fifi Butterplus', 'support': 2}}}
    assert matched('알스트로', '피피', learned)[:2] == ('ALSTROMERIA Fifi Butterplus', 'learned')


def test_approved_staff_correction_is_recorded(tmp_path, monkeypatch):
    target = tmp_path / 'learned.json'
    monkeypatch.setattr(approval, 'LEARNED_ALIASES', target)
    row = {'extracted': {'category': '알스트로'},
           'items': [{'original_product_raw': '피삐', 'product_raw': '피피', 'product': CATALOG[0]},
                     {'product_raw': '보야저', 'product': CATALOG[2]}]}
    assert approval.learn_from_approval(row) == 1
    assert approval.learn_from_approval(row) == 1
    saved = json.loads(target.read_text(encoding='utf-8'))['aliases']['알스트로']
    assert saved == {'피삐': {'target': 'ALSTROMERIA Fifi', 'support': 2, 'source': 'staff_correction'}}
