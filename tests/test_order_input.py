from core.order_input import body, structured, grounded
from core.import_order import match_customer
from core import order_llm


def test_existing_registration_statement_blocks_new_add_only():
    from core.order_input import reports_existing_registration, source_issues
    text = '44차 미국 발주\n레몬잎 70박스\n->전산등록완료'
    assert reports_existing_registration(text)
    assert any('신규 ADD 금지' in issue for issue in source_issues(text))
    assert not reports_existing_registration('44차 발주\n레몬잎 70박스\n전산 미등록')
    assert not reports_existing_registration('44차 발주\n레몬잎 70박스\n[기타사항]\n다른 건 전산등록완료')

def test_origin_not_customer():
    rows = [{'name': '콜롬비아 대사관 상무관실', 'nenova_key': 588}]
    assert match_customer('콜', rows)[0] is None
    assert match_customer('CL90', [{'name':'경남꽃도매','name_alias':['CL90']}])[0]

def test_ambiguous_alias_not_accepted():
    assert match_customer('호텔', [{'name':'호텔'}, {'name':'다른호텔','name_alias':['호텔']}])[0] is None

def test_structured_excludes_other_section_without_api(monkeypatch):
    monkeypatch.delenv('ANTHROPIC_API_KEY', raising=False)
    value = order_llm.parse('[발주]\n차수: 41-2\n업체: 주광\n프라우드 레드박스 / 10단\n[기타사항 — 자동처리 제외]\n포장 20박스 개선')
    assert len(value['items']) == 1
    assert value['items'][0]['unit'] == '단'
    assert value['items'][0]['quantity'] == 10

def test_correct_wrong_unit_from_source():
    value = grounded('프라우드 레드박스 10단', {'items':[{'product':'프라우드 레드박스','quantity':10,'unit':'박스'}]})
    assert value['items'][0]['unit'] == '단'

def test_no_invented_default_or_ambiguous_total():
    for text in ['라운드로 부탁드립니다', '노비아 480단 중 21단']:
        item = grounded(text, {'items':[{'product':'노비아','quantity':1,'unit':'박스'}]})['items'][0]
        assert item['quantity'] is None

def test_unknown_structured_line_not_silently_dropped():
    assert structured('[발주]\n업체: 주광\n노비아 / 2박스\n좌 100 우 80') is None

def test_missing_api_is_failure_not_ignored(monkeypatch):
    monkeypatch.delenv('ANTHROPIC_API_KEY', raising=False)
    import pytest
    def fail(*args, **kwargs): raise RuntimeError('CLI unavailable')
    monkeypatch.setattr('core.subscription_ai.ask', fail)
    with pytest.raises(RuntimeError): order_llm.parse('41-2 주광 노비아 2박스')


def test_context_reply_without_quantity_does_not_call_ai(monkeypatch):
    monkeypatch.delenv('ANTHROPIC_API_KEY', raising=False)
    assert order_llm.parse('라운드로 부탁드립니다.')['items'] == []


def test_real_common_unit_header():
    text='38-2 콜 카네이션 발주 추가\n*단위/박스\n문라이트 2\n돈셀 2\n이케바나1'
    r=grounded(text,{'items':[{'product':p} for p in ['문라이트','돈셀','이케바나']]})
    assert [(i['quantity'],i['unit']) for i in r['items']]==[(2,'박스'),(2,'박스'),(1,'박스')]


def test_box_and_size_not_quantity():
    r=grounded('1. 문라이트 5box',{'items':[{'product':'문라이트'}]})
    assert r['items'][0]['quantity']==5 and r['items'][0]['unit']=='박스'
    r=grounded('*단위/박스\n장미 50cm',{'items':[{'product':'장미'}]})
    assert r['items'][0]['quantity'] is None


def test_mixed_cancel_and_add_is_not_new_order():
    from core.order_input import source_issues
    assert source_issues('41-2 호텔 수국 선발주 변경\n버건디 18박스 취소\n레드 14박스 추가')
    assert source_issues('41-2 또는 42-1 차에 발주해주세요\n좌 100대\n우 80대')
    assert source_issues('41-2 콜 수국 컨펌여부 확인 요청\n피치 8박스')
    assert not source_issues('[발주]\n노비아 / 2박스\n[기타사항]\n항공편 취소')
