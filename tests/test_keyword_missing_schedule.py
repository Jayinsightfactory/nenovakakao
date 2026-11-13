from datetime import datetime
from unittest.mock import Mock
import pytest
from core import keyword_missing_schedule as s, keyword_approval as a, keyword_forward as k


def at(day,hour,minute=0):
    return datetime(2026,10,day,hour,minute,tzinfo=k.KST).timestamp()


CFG={'enabled':True,'approval_missing_recheck':True,'approval_missing_recheck_mode':'fixed',
     'approval_missing_schedule':{'weekdays':[0,1,2,3,4],'hours':[13,16]}}


def test_weekday_slots_kst_boundaries_and_latest_catchup():
    assert s.due_slot(CFG,at(23,12,59)) is None
    assert s.due_slot(CFG,at(23,13))=='2026-10-23:13:00'
    assert s.due_slot(CFG,at(23,15,59))=='2026-10-23:13:00'
    assert s.due_slot(CFG,at(23,16))=='2026-10-23:16:00'
    assert s.due_slot(CFG,at(23,17))=='2026-10-23:16:00'
    assert s.due_slot(CFG,at(24,13)) is None
    assert s.due_slot(CFG,at(25,16)) is None
    assert s.due_slot(CFG,at(26,13))=='2026-10-26:13:00'


@pytest.fixture
def setup(monkeypatch,tmp_path):
    clock=[at(23,13)]
    monkeypatch.setattr(s.time,'time',lambda:clock[0])
    monkeypatch.setattr(k,'config',lambda:CFG.copy())
    monkeypatch.setattr(s,'STATE',tmp_path/'schedule.json')
    monkeypatch.setattr(a,'REQUESTS',tmp_path/'requests.json')
    monkeypatch.setattr(a,'question_hours_open',lambda cfg:True)
    rows={rid:{'id':rid,'status':'waiting','event':{'timestamp':'2026년 10월 23일 오전 10:00'}} for rid in ('A','B')}
    k.save_json(a.REQUESTS,rows)
    return clock


def test_both_requests_checked_once_per_slot_not_every_poll(setup,monkeypatch):
    check=Mock(return_value={'status':'checked_no_missing'})
    monkeypatch.setattr(a,'recheck_missing',check)
    export,send=Mock(),Mock()
    s.poll(export,send,lambda:False)
    assert check.call_count==2
    s.poll(export,send,lambda:False)
    assert check.call_count==2
    setup[0]=at(23,16)
    s.poll(export,send,lambda:False)
    assert check.call_count==4


def test_confirmation_has_separated_pass_and_reuses_target_within_pass(setup,monkeypatch):
    count=[0]
    def check(rows,export,send,paused,history,**kwargs):
        export('target');count[0]+=1
        return {'status':'awaiting_confirmation' if count[0]<=2 else 'checked_no_missing'}
    monkeypatch.setattr(a,'recheck_missing',check)
    export=Mock(return_value='text')
    s.poll(export,Mock(),lambda:False)
    assert export.call_count==1
    setup[0]+=59;s.poll(export,Mock(),lambda:False)
    assert export.call_count==1
    setup[0]+=1;s.poll(export,Mock(),lambda:False)
    assert export.call_count==2
    assert k.read_json(s.STATE,{})['2026-10-23:13:00']['status']=='completed'


def test_pause_and_weekend_do_not_read_chat_or_claim_slot(setup,monkeypatch):
    check=Mock();monkeypatch.setattr(a,'recheck_missing',check)
    s.poll(Mock(),Mock(),lambda:True)
    setup[0]=at(24,13);s.poll(Mock(),Mock(),lambda:False)
    check.assert_not_called();assert not s.STATE.exists()


def test_failed_history_is_not_marked_success_or_retried_forever(setup,monkeypatch):
    check=Mock(return_value={'status':'history_unavailable'})
    monkeypatch.setattr(a,'recheck_missing',check)
    for _ in range(4):
        s.poll(Mock(),Mock(),lambda:False);setup[0]+=60
    slot=k.read_json(s.STATE,{})['2026-10-23:13:00']
    assert slot['status']=='completed_with_holds'
    assert all(r['status']=='blocked_verification' for r in slot['results'].values())
    assert check.call_count==6


def test_restart_reads_completed_slot_and_does_not_duplicate(setup,monkeypatch):
    k.save_json(s.STATE,{'2026-10-23:13:00':{'status':'completed'}})
    check=Mock();monkeypatch.setattr(a,'recheck_missing',check)
    s.poll(Mock(),Mock(),lambda:False);check.assert_not_called()


def test_fixed_mode_disables_legacy_periodic_rechecks(monkeypatch):
    monkeypatch.setattr(k,'config',lambda:CFG.copy())
    export,send=Mock(),Mock()
    a.recheck_missing({},export,send,lambda:False,Mock())
    export.assert_not_called();send.assert_not_called()
