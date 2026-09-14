import pytest
from core.defect_deduction_parser import extract


def parse(text, sender='박성수'):
    return extract({'event_id':'test', 'sender_name':sender, 'timestamp':'2026년 9월 14일 오후 4:35', 'content':text})


def test_header_quantity_and_customer_on_next_line():
    r = parse('37-1 카네이션 SP믹스  15단\n\n검증꽃집')
    assert r['status'] == 'matching_candidate'
    assert (r['sequence'],r['category'],r['customer']) == ('37-1','카네이션','검증꽃집')
    assert [(i['product_raw'],i['quantity_raw'],i['unit_raw']) for i in r['items']] == [('SP믹스','15','단')]
    assert r['approved'] is False


def test_many_varieties_share_one_customer():
    r = parse('37-1차 콜 카네이션\n검증꽃집\n문라이트 4단 불량\n돈셀 2단 불량')
    assert r['status'] == 'matching_candidate'
    assert [i['quantity_raw'] for i in r['items']] == ['4','2']
    assert [i['product_raw'] for i in r['items']] == ['문라이트','돈셀']


@pytest.mark.parametrize('unit',['단','대','송이','박스','스팀','BOX','묶음'])
def test_units_preserved_without_conversion(unit):
    r = parse(f'37-1 장미 품종A 1.5{unit}\n검증꽃집')
    assert r['items'][0]['quantity_raw'] == '1.5'
    assert r['items'][0]['unit_raw'] == unit


@pytest.mark.parametrize('text,issue',[
    ('37-1 카네이션\n검증꽃집\n휘슬러 2박스 (32단)', '한 줄 복수 수량'),
    ('36-2~37-1 카네이션\n검증꽃집\n문라이트 3단', '여러 차수'),
    ('37-1 장미\n산지후보A\n검증꽃집\n품종A 2단', '후보 복수'),
    ('검증꽃집 15단 더 추가입니다', '차수 없음'),
    ('37차 호주 소재\n검증꽃집\n수량 파악중', '품목·수량 없음'),
    ('37-1 장미 품종A -2단\n검증꽃집', '음수'),
])
def test_ambiguous_text_requires_review(text, issue):
    r = parse(text)
    assert r['status'] == 'review'
    assert any(issue in value for value in r['issues'])


def test_comment_quantity_is_not_an_extra_item():
    r = parse('37-1 장미 품종A 2단\n검증꽃집\n-> 이번주 2박스째 문제입니다')
    assert len(r['items']) == 1


def test_inline_customer_and_stem_length_are_preserved():
    r = parse('37-1 장미\n품종A 60cm 5단 / 검증꽃집')
    assert r['customer'] == '검증꽃집'
    assert r['items'][0]['product_raw'] == '품종A 60cm'


@pytest.mark.parametrize('text,status',[('사진 9장','attachment_only'),('동영상','attachment_only'),
    ('37-1 수국 화이트 2단\n검증꽃집\n메시지가 삭제되었습니다.','deleted')])
def test_non_input_messages(text,status):
    assert parse(text)['status'] == status


def test_exact_sender_alias_and_no_cross_message_inheritance():
    assert parse('사진', '김원영차장')['staff'] == '김원영'
    assert parse('사진', '김원영비슷한사람')['status'] == 'excluded_sender'
    parse('37-1 장미 품종A 2단\n검증꽃집')
    assert parse('품종B 3단')['customer'] is None
