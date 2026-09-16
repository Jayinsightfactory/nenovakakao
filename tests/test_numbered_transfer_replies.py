import pytest
from core import keyword_approval as a


@pytest.mark.parametrize('text,result',[
    ('1가 승인',{0:'approve'}),('1가승인',{0:'approve'}),
    ('1가 거절',{0:'reject'}),('1가 승인 1나 거절',{0:'approve',1:'reject'}),
    ('1가,1나 승인',{0:'approve',1:'approve'}),
    ('2가 승인',None),('11가 승인',None),('1 맞아',None),
    ('1가 승인 1가 거절',None),('1가 승인 취소',None),
])
def test_numbered_transfer_commands(text,result):
    row={'id':'ABC','events':[{},{}],'item_labels':['1가','1나'],
         'choice_format':'per_item','status':'waiting','request_event_id':'q'}
    history=[{'event_id':'q','sender_name':'봇','content':'질문'},
             {'event_id':'a','sender_name':a.APPROVER,'content':text}]
    assert a.decision(history,row,False)==result


def test_legacy_recheck_alias_only_after_verified_recheck_boundary():
    row={'id':'ABC','events':[{}],'item_labels':['가하'],'recheck_labels':['1가'],
         'choice_format':'per_item','status':'waiting','request_event_id':'q',
         'missing_recheck_event_id':'recheck'}
    history=[{'event_id':'q','sender_name':'봇','content':'질문'}]
    answer={'event_id':'a','sender_name':a.APPROVER,'content':'1가 승인'}
    assert a.decision(history+[answer],row,False) is None
    history.append({'event_id':'recheck','sender_name':'봇','content':'재확인'})
    assert a.decision(history+[answer],row,False)=={0:'approve'}
    answer['content']='가하 승인 1가 거절'
    assert a.decision(history+[answer],row,False) is None


def test_concatenated_answers_across_three_batches():
    history=[{'event_id':'q','sender_name':'봇','content':'질문'},
             {'event_id':'a','sender_name':a.APPROVER,'content':'1가승인2가거절3가승인'}]
    for number,answer in [(1,'approve'),(2,'reject'),(3,'approve')]:
        row={'id':str(number),'events':[{}],'item_labels':[f'{number}가'],
             'choice_format':'per_item','status':'waiting','request_event_id':'q'}
        assert a.decision(history,row,False)=={0:answer}
