import json
from unittest.mock import Mock
import pytest
from core import keyword_forward as k, keyword_missing_notices as n


@pytest.fixture
def notice(monkeypatch,tmp_path):
    monkeypatch.setattr(n,'STATE',tmp_path/'notices.json')
    monkeypatch.setattr(k,'STATE',tmp_path/'forward.json')
    cfg={'target':'현장방','approval_missing_notice_recipients':['강현우','강현우']}
    monkeypatch.setattr(k,'config',lambda:cfg)
    monkeypatch.setattr(n.time,'time',lambda:10000)
    event={'event_id':'source','sender_name':'영업','timestamp':'1970년 1월 1일 오전 10:00','content':'40-1 장미 추가\n업체\n화이트 2단\n핑크 1단'}
    request={'id':'ABC','approver_name':'기존승인자'}
    k.save_json(k.STATE,{'source':{'status':'미응답 보류'}})
    target=[{'event_id':'old','timestamp':'1970년 1월 1일 오전 9:59','content':'다른 내용'}]
    history=[]
    def export(room):return room+' 님과 카카오톡 대화'
    def parse(text,binding):return list(target if text.startswith('현장방 ') else history)
    def send(room,payload):
        assert room=='강현우'
        assert next(iter(k.read_json(n.STATE,{}).values()))['status']=='unknown'
        history.append({'event_id':'sent','content':payload})
    monkeypatch.setattr('core.moyi_inbound.parse_export',parse)
    n.enqueue(request,event,{'confirmed_checks':2})
    return cfg,request,event,target,history,Mock(side_effect=export),Mock(side_effect=send)


def test_secondary_recipient_dedup_and_verified_journal(notice):
    cfg,request,event,target,history,export,send=notice
    n.enqueue(request,event,{'confirmed_checks':2})
    assert len(k.read_json(n.STATE,{}))==1
    n.poll(export,send,lambda:False)
    send.assert_called_once()
    row=next(iter(k.read_json(n.STATE,{}).values()))
    assert row['status']=='sent' and row['event_id']=='sent'
    assert event['content'] in row['payload'] and '누락 확정' in row['payload']
    n.poll(export,send,lambda:False)
    send.assert_called_once()


def test_unknown_notice_is_reconciled_without_resend(notice,monkeypatch):
    cfg,request,event,target,history,export,send=notice
    send.side_effect=RuntimeError('uncertain')
    n.poll(export,send,lambda:False)
    row=next(iter(k.read_json(n.STATE,{}).values()))
    assert row['status']=='unknown'
    history.append({'event_id':'later','content':row['payload']})
    monkeypatch.setattr(n.time,'time',lambda:10301)
    n.poll(export,send,lambda:False)
    assert next(iter(k.read_json(n.STATE,{}).values()))['status']=='sent'
    send.assert_called_once()


def test_resolved_forward_is_not_sent_as_missing(notice):
    cfg,request,event,target,history,export,send=notice
    k.save_json(k.STATE,{'source':{'status':'전송 성공'}})
    n.poll(export,send,lambda:False)
    send.assert_not_called(); export.assert_not_called()


def test_manual_delivery_since_enqueue_cancels_secondary_notice(notice):
    cfg,request,event,target,history,export,send=notice
    target.append({**event,'event_id':'manual','timestamp':'1970년 1월 1일 오전 10:01'})
    n.poll(export,send,lambda:False)
    send.assert_not_called()
    assert next(iter(k.read_json(n.STATE,{}).values()))['status']=='resolved_before_send'


def test_partial_delivery_also_blocks_whole_missing_claim(notice):
    cfg,request,event,target,history,export,send=notice
    target.append({'event_id':'partial','timestamp':'1970년 1월 1일 오전 10:01',
                   'content':'40-1 장미 추가\n업체\n화이트 2단'})
    n.poll(export,send,lambda:False)
    send.assert_not_called()
    assert next(iter(k.read_json(n.STATE,{}).values()))['target_event_ids']==['partial']


def test_paused_or_removed_recipient_never_sends(notice):
    cfg,request,event,target,history,export,send=notice
    n.poll(export,send,lambda:True)
    export.assert_not_called()
    cfg['approval_missing_notice_recipients']=[]
    n.poll(export,send,lambda:False)
    export.assert_not_called(); send.assert_not_called()


def test_approver_is_not_notified_twice(notice):
    cfg,request,event,target,history,export,send=notice
    n.STATE.unlink()
    n.enqueue({**request,'approver_name':'강현우'},event,{'confirmed_checks':2})
    assert not k.read_json(n.STATE,{})


def test_insufficient_target_history_does_not_notify(notice):
    cfg,request,event,target,history,export,send=notice
    target[0]['timestamp']='1970년 1월 1일 오전 10:01'
    n.poll(export,send,lambda:False)
    send.assert_not_called()
    assert next(iter(k.read_json(n.STATE,{}).values()))['status']=='queued'


def test_wrong_recipient_room_blocks_send(notice):
    cfg,request,event,target,history,export,send=notice
    export.side_effect=lambda room: ('다른방' if room=='강현우' else room)+' 님과 카카오톡 대화'
    n.poll(export,send,lambda:False)
    send.assert_not_called()


def test_previous_day_queued_notice_expires(notice,monkeypatch):
    cfg,request,event,target,history,export,send=notice
    monkeypatch.setattr(n.time,'time',lambda:10000+86400)
    n.poll(export,send,lambda:False)
    send.assert_not_called(); export.assert_not_called()
    assert next(iter(k.read_json(n.STATE,{}).values()))['status']=='expired'
