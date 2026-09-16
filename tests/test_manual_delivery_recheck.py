from core import keyword_forward as k, keyword_approval as a
from tests.test_missing_recheck import case,run
import pytest

SOURCE='37-2 콜 수국 변경사항\n레바논\n화이트 2박스\n피치 1박스\n오늘 출고 요청드립니다\n늦게 말씀드려 죄송합니다.'
TARGET=SOURCE.replace('37-2','38-1').replace('오늘 출고','오늘 선출고')

def events():
    return ({'event_id':'s','sender_name':'영업','timestamp':'2026년 9월 15일 오후 4:36','content':SOURCE},
            {'event_id':'t','timestamp':'2026년 9월 15일 오후 4:37','content':TARGET})

def test_changed_week_is_review_evidence_not_exact_duplicate():
    source,target=events()
    assert not k.duplicate(SOURCE,[target])
    assert k.possible_manual_delivery(source,[target])==[target]

@pytest.mark.parametrize('old,new',[('2박스','3박스'),('레바논','다른업체'),('변경사항','취소'),('9월 15일','9월 16일'),('오후 4:37','오후 4:35')])
def test_distinct_orders_not_treated_as_same_delivery(old,new):
    source,target=events()
    target={key:value.replace(old,new) for key,value in target.items()}
    assert not k.possible_manual_delivery(source,[target])

def test_similar_manual_delivery_never_sends_missing_notice(case):
    row,target,messages,send=case
    source,event=events()
    row['event']=source;row['events']=[source];row['item_labels']=['가']
    k.save_json(k.STATE,{'s':{'status':'미응답 보류'}})
    target[:]=[event]
    run(row,messages,send)
    send.assert_not_called()
    assert row['item_review_holds']['0']['target_event_ids']==['t']
