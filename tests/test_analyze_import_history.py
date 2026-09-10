from scripts.analyze_import_history import extract, reconcile, learning_candidates


CUSTOMERS = [{'CustKey': 659, 'CustName': 'CL73'}]
PRODUCTS = [{'ProdKey': 230, 'ProdName': 'Astilbe / Washington White',
             'FlowerName': '아스틸베', 'CounName': '네덜란드'}]


def event(content, ordinal=1):
    return dict(content=content, ordinal=ordinal, event_id=str(ordinal),
                sender_name='정재훈', timestamp='2026년 8월 27일 오전 9:25')


def test_context_does_not_cross_messages():
    rows, _ = extract([event('35-2 네덜란드 발주\nCL73\n아스틸베 워싱턴 화이트 50스팀'),
                       event('워싱턴 화이트 50스팀', 2)], CUSTOMERS)
    assert rows[0]['week'] == '35-02'
    assert rows[0]['customer_key'] == 659
    assert rows[1]['week'] is None
    assert rows[1]['customer_key'] is None


def test_multiple_units_are_one_line_and_require_review():
    rows, _ = extract([event('35-2 발주\nCL73\n수국 진그린 7박스 210스팀')], CUSTOMERS)
    assert len(rows) == 1
    assert rows[0]['quantity_expressions'] == [
        {'quantity': 7, 'unit': 'box'}, {'quantity': 210, 'unit': 'stem'}]
    assert '복수 수량·단위 표기' in rows[0]['flags']


def test_conditional_and_color_only_are_not_confirmed():
    rows, _ = extract([event('35-2 발주\nCL73\n수국\n화이트 50스팀\n안 될 경우 취소')], CUSTOMERS)
    assert '색상만 표기·품종 확인 필요' in rows[0]['flags']
    assert '조건·문의·차수이동 확인 필요' in rows[0]['flags']


def test_retrospective_match_uses_native_unit_and_ignores_deleted_rows():
    items, _ = extract([event('35-2 네덜란드 발주 추가\nCL73\n아스틸베 워싱턴 화이트 50스팀 추가')], CUSTOMERS)
    row = dict(OrderYear=2026, OrderWeek='35-02', CustKey=659, ProdKey=230,
               OrderMasterKey=6243, OrderDetailKey=74192, MasterDeleted=False,
               DetailDeleted=False, SteamQuantity=50, BunchQuantity=5, BoxQuantity=0)
    results = reconcile(items, PRODUCTS, [row, dict(row, DetailDeleted=True)], [], [])
    assert results[0]['status'] == 'same_product_quantity'
    assert results[0]['actual_quantity'] == 50
    items[0]['unit'] = 'bunch'
    assert reconcile(items, PRODUCTS, [row], [], [])[0]['status'] == 'quantity_differs'
    candidates = learning_candidates(results * 3)
    assert candidates[0]['distinct_orders'] == 1
    assert candidates[0]['status'] == '근거 부족 또는 다중 품목'
    assert candidates[0]['auto_apply'] is False
