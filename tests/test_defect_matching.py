from core.defect_matching import match, customer, PRODUCT_NAMES


def test_number_inside_variety_never_matches_product_id():
    rows=[{'nenova_key':3,'code':3,'name':'Acasia / Hamburyana'}]
    assert match('로사네 브라운 3호', rows)[0] is None
    assert match('3', rows)[0] == rows[0]


def test_farm_partial_match_cannot_become_customer():
    rows=[{'nenova_key':1,'name':'네이버클라우드 주식회사'},
          {'nenova_key':2,'name':'친구플라워'}]
    source={'customer':None,'customer_candidates':['클라우드','친구플라워'],
            'issues':['거래처/농장/품종 후보 복수; 확인 필요']}
    found,_=customer(source,rows)
    assert found['nenova_key']==2
    assert not source['issues']
    source={'customer':'클라우드','customer_candidates':['클라우드'],'issues':[]}
    assert customer(source,rows)[0] is None


def test_multiple_real_customers_stay_unresolved():
    rows=[{'nenova_key':1,'name':'친구플라워'}, {'nenova_key':2,'name':'로뎀농원'}]
    source={'customer':None,'customer_candidates':['친구플라워','로뎀농원'],'issues':['확인 필요']}
    assert customer(source,rows)[0] is None
    assert source['issues']


def test_origin_pool_and_explicit_length_cannot_be_overridden():
    rows=[{'nenova_key':1,'name':'Carnation CHINA / 문라이트 (Moonlight)'}]
    assert match('문라이트',rows,PRODUCT_NAMES)[0] is None
    rows=[{'nenova_key':1,'name':'ROSE / Be Sweet 50cm'},
          {'nenova_key':2,'name':'ROSE / Be Sweet 60cm'}]
    assert match('비스윗 60cm',rows,PRODUCT_NAMES)[0]['nenova_key']==2


def test_duplicate_catalog_targets_do_not_auto_select():
    rows=[{'nenova_key':1,'name':'CARNATION Moon Light'},
          {'nenova_key':2,'name':'CARNATION Moon Light'}]
    assert match('문라이트',rows,PRODUCT_NAMES)[0] is None


def test_china_header_does_not_select_same_name_colombian_rose():
    from core.defect_deduction_parser import extract
    from core.defect_approval import rematch
    source=extract({'sender_name':'박성수','content':'33-2 중국 장미\n수경\n만달라 10단'})
    assert source['origin']=='중국'
    master={'customers':[], 'products':[
        {'nenova_key':1,'name':'ROSE / Mandala 50cm','origin':'콜롬비아','category':'장미'},
        {'nenova_key':2,'name':'ROSE CHINA / 만달라(Mandala) MQ','origin':'중국','category':'장미'}]}
    assert rematch({'extracted':source},master)['items'][0]['product']['nenova_key']==2
    master['products'].pop()
    assert rematch({'extracted':source},master)['items'][0]['product'] is None


def test_conflicting_origins_require_review():
    from core.defect_deduction_parser import extract
    source=extract({'sender_name':'박성수','content':'33-2 중국 콜 장미\n수경\n만달라 10단'})
    assert '여러 원산지; 항목별 확인 필요' in source['issues']
