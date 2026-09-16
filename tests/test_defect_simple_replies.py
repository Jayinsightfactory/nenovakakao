from unittest.mock import Mock
from core import defect_approval as d
from core.atomic_json import save
from tests.test_defect_approval import waiting, MASTER


def test_numeric_reply_space_optional_and_exact_number(tmp_path):
    _,row=waiting(tmp_path)
    row['reply_number']=2
    for text in ['2맞아','2 맞아']:
        event={'event_id':'a','sender_name':row['recipient'],'content':text}
        assert d.apply_reply(row,event,{'a'})['status']=='approved'
    for text in ['12맞아','20 맞아','맞아','1 맞아','2 맞아 취소']:
        event={'event_id':'a','sender_name':row['recipient'],'content':text}
        assert d.apply_reply(row,event,{'a'})==row


def test_simple_correction_new_question_number_blocks_old_reply(tmp_path):
    path,row=waiting(tmp_path); row['reply_number']=2
    event={'event_id':'a','sender_name':row['recipient'],'content':'2 품목 수정품목'}
    updated=d.apply_reply(row,event,{'a'})
    assert updated['status']=='needs_match' and updated['revision']==2
    updated=d.rematch(updated,MASTER); save(path,updated)
    sent=[]
    def export(_):
        return [{'event_id':'new-q','content':sent[0]}] if sent else []
    d.send_question(path,export,lambda recipient,text:sent.append(text),lambda:False)
    updated=d.load(path)
    assert updated['reply_number']==3 and updated['status']=='waiting'
    old={'event_id':'b','sender_name':row['recipient'],'content':'2 맞아'}
    assert d.apply_reply(updated,old,{'b'})==updated
    assert d.apply_reply(updated,{**old,'content':'3맞아'},{'b'})['status']=='approved'
    assert '맞으면 3 맞아' in sent[0]
    assert '매칭: 수정품목 15단' in sent[0]


def test_number_does_not_replace_sender_or_readback_checks(tmp_path):
    _,row=waiting(tmp_path); row['reply_number']=1
    e={'event_id':'a','sender_name':'다른사람','content':'1맞아'}
    assert d.apply_reply(row,e,{'a'})==row
    e['sender_name']=row['recipient']
    assert d.apply_reply(row,e,set())==row


def test_new_aliases_respect_size_origin_and_color():
    from core.defect_matching import match,PRODUCT_NAMES
    rows=[{'nenova_key':1337,'name':'ROSE / Momentum 50cm'},
          {'nenova_key':1424,'name':'ROSE / Momentum 40cm'},
          {'nenova_key':1312,'name':'ROSE / Lollipop White Blue 50cm'},
          {'nenova_key':2873,'name':'ROSE / Lollipop White Pink 50cm'}]
    assert match('모멘텀',rows,PRODUCT_NAMES)[0]['nenova_key']==1337
    assert match('모멘텀 40cm',rows,PRODUCT_NAMES)[0]['nenova_key']==1424
    assert match('롤리팝 화이트 블루',rows,PRODUCT_NAMES)[0]['nenova_key']==1312
    assert match('모멘텀',[{'name':'ROSE CHINA / Momentum','nenova_key':2474}],PRODUCT_NAMES)[0] is None
