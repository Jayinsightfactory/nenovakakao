from unittest.mock import Mock
import pytest
from core import keyword_approval as a, keyword_forward as k


@pytest.fixture
def case(monkeypatch, tmp_path):
    monkeypatch.setattr(k, 'STATE', tmp_path / 'forward.json')
    monkeypatch.setattr(a, 'REQUESTS', tmp_path / 'requests.json')
    monkeypatch.setattr(k, 'config', lambda: {
        'enabled': True, 'target': '현장방', 'approval_missing_recheck': True})
    monkeypatch.setattr(a.time, 'time', lambda: 10000)
    row = dict(id='ABC', status='awaiting_late_reply', choice_format='per_item',
               item_labels=['가', '나'], request_event_id='original', sent_at=100,
               approver_name=a.operator_name(), baseline=[],
               event={'event_id': '1', 'sender_name': '영업', 'content': '장미 추가'})
    row['events'] = [row['event'], {'event_id': '2', 'sender_name': '영업', 'content': '국화 취소'}]
    for event in row['events']:
        event['timestamp'] = '1970년 1월 1일 오전 10:00'
    # Existing reminder tests begin with one earlier verified absence check.
    row['missing_absence_checks']={str(i):{'source_event_id':e['event_id'],
        'source_content':k.normalize(e['content']),'first_absent_at':100,
        'last_absent_at':100,'confirmed_checks':1} for i,e in enumerate(row['events'])}
    monkeypatch.setattr('core.keyword_missing_notices.STATE',tmp_path/'missing_notices.json')
    k.save_json(k.STATE, {'1': {'status': '미응답 보류'}, '2': {'status': '미응답 보류'}})
    target = [{'event_id': 'existing', 'content': '다른 내용','timestamp':'1970년 1월 1일 오전 9:59'}]
    monkeypatch.setattr('core.moyi_inbound.parse_export', lambda *args: target)
    messages = [dict(event_id='original', content='원 질문', sender_name='봇')]
    def send(room, payload):
        messages.append(dict(event_id='reminder', content=payload, sender_name='봇'))
    return row, target, messages, Mock(side_effect=send)


def run(row, messages, send):
    a.recheck_missing({'ABC': row}, Mock(return_value='현장방 님과 카카오톡 대화'),
                      send, lambda: False, lambda **kwargs: list(messages))


def test_recheck_only_missing_and_no_spam(case):
    row, target, messages, send = case
    target.append({'event_id': 'manual', 'timestamp':'1970년 1월 1일 오전 10:01', 'content': '영업 - 장미 추가'})
    run(row, messages, send)
    assert '국화 취소' in send.call_args.args[1]
    assert '장미 추가' not in send.call_args.args[1]
    assert row['missing_recheck_status'] == 'sent'
    run(row, messages, send)
    send.assert_called_once()


def test_no_recheck_for_declined_or_unknown_delivery(case):
    row, target, messages, send = case
    row['item_answers'] = {'0': 'reject'}
    k.save_json(k.STATE, {'1': {'status': '승인거절'}, '2': {'status': '결과 불명'}})
    run(row, messages, send)
    send.assert_not_called()


def test_unknown_reminder_never_retried(case, monkeypatch):
    row, target, messages, send = case
    send.side_effect = RuntimeError('uncertain')
    with pytest.raises(RuntimeError):
        run(row, messages, send)
    assert k.read_json(a.REQUESTS, {})['ABC']['missing_recheck_status'] == 'unknown'
    monkeypatch.setattr(a.time, 'time', lambda: 20000)
    run(row, messages, send)
    send.assert_called_once()


def test_yes_no_only_after_verified_recheck_and_correct_sender(case):
    row, target, messages, send = case
    early = dict(event_id='early', sender_name=row['approver_name'], content='가 네')
    messages.append(early)
    assert a.decision(messages, row, True) is None
    run(row, messages, send)
    messages.append(dict(event_id='wrong', sender_name='다른사람', content='가 아니요'))
    messages.append(dict(event_id='answer', sender_name=row['approver_name'], content='가 네 나 아니요'))
    assert a.decision(messages, row, True) == {0: 'approve', 1: 'reject'}


def test_grouped_yes_no(case):
    row, target, messages, send = case
    run(row, messages, send)
    messages.append(dict(event_id='answer', sender_name=row['approver_name'], content='가,나 네'))
    assert a.decision(messages, row, True) == {0: 'approve', 1: 'approve'}


def test_empty_target_is_not_proof_of_missing(case):
    row, target, messages, send = case
    target.clear()
    run(row, messages, send)
    send.assert_not_called()


@pytest.mark.parametrize('stamp', ['1969년 12월 31일 오후 11:59', '', '1970년 1월 2일 오전 12:00'])
def test_non_today_never_rechecked(case, stamp):
    row, target, messages, send = case
    for event in row['events']: event['timestamp'] = stamp
    run(row, messages, send)
    send.assert_not_called()
    assert 'missing_recheck_checked_at' not in row


def test_mixed_batch_keeps_indexes_but_only_today(case):
    row, target, messages, send = case
    row['events'][0]['timestamp'] = '1969년 12월 31일 오후 11:59'
    run(row, messages, send)
    assert '장미 추가' not in send.call_args.args[1]
    assert '국화 취소' in send.call_args.args[1]
    assert row['events'][0]['event_id'] == '1'


def test_kst_midnight_boundary():
    from datetime import datetime
    now = datetime(2026,10,8,0,0,tzinfo=k.KST).timestamp()
    assert a.recheck_today({'timestamp':'2026년 10월 8일 오전 12:00'}, now)
    assert not a.recheck_today({'timestamp':'2026년 10월 7일 오후 11:59'}, now)


def test_repeated_source_is_not_completed_by_one_target(case):
    row, target, messages, send = case
    row['events'][1]['content']=row['events'][0]['content']
    target[:]=[{'event_id':'t','content':'장미 추가','timestamp':'1970년 1월 1일 오전 10:01'}]
    run(row,messages,send)
    send.assert_not_called()
    assert row['item_review_holds']['0']['target_event_ids']==['t']
    assert k.read_json(k.STATE,{})['1']['status']=='미응답 보류'


def test_first_absence_does_not_notify_until_second_fresh_check(case,monkeypatch):
    row,target,messages,send=case
    row.pop('missing_absence_checks')
    run(row,messages,send)
    send.assert_not_called()
    assert row['missing_absence_checks']['0']['confirmed_checks']==1
    monkeypatch.setattr(a.time,'time',lambda:11801)
    run(row,messages,send)
    send.assert_called_once()


def test_target_history_starting_after_source_does_not_prove_missing(case):
    row,target,messages,send=case
    target[0]['timestamp']='1970년 1월 1일 오전 10:01'
    run(row,messages,send)
    send.assert_not_called()
    assert row['missing_recheck_evidence']['0']['status']=='insufficient_target_history'


def test_target_title_substring_is_not_valid_room(case):
    row,target,messages,send=case
    with pytest.raises(RuntimeError,match='방 제목'):
        a.recheck_missing({'ABC':row},Mock(return_value='다른방 님과 카카오톡 대화\n현장방'),
                          send,lambda:False,lambda **kwargs:list(messages))
    send.assert_not_called()


def test_copied_request_same_source_id_is_not_repeated_source(case):
    row,target,messages,send=case
    target.append({'event_id':'t','content':'장미 추가','timestamp':'1970년 1월 1일 오전 10:01'})
    duplicate={**row,'id':'OTHER','status':'resolved_existing'}
    a.recheck_missing({'ABC':row,'OTHER':duplicate},Mock(return_value='현장방 님과 카카오톡 대화'),
                      send,lambda:False,lambda **kwargs:list(messages))
    assert '0' not in row.get('item_review_holds',{})
    assert k.read_json(k.STATE,{})['1']['status']=='중복 생략'


def test_missing_notification_adds_recipient_without_changing_approval(case,monkeypatch):
    from core import keyword_missing_notices as n
    row,target,messages,send=case
    approver=row['approver_name']
    monkeypatch.setattr(k,'config',lambda:{'enabled':True,'target':'현장방',
        'approval_missing_recheck':True,'approval_missing_notice_recipients':['강현우']})
    run(row,messages,send)
    queued=k.read_json(n.STATE,{})
    assert len(queued)==2 and {r['recipient'] for r in queued.values()}=={'강현우'}
    assert row['approver_name']==approver
    assert send.call_args.args[0]==approver
    assert {r['status'] for r in k.read_json(k.STATE,{}).values()}=={'미응답 보류'}


def test_direct_warehouse_instruction_complete_is_review_not_missing(case):
    row,target,messages,send=case
    row['events']=[{**row['event'],'content':'수국 변경사항(출고일 변경)\n주광\n화이트 5박스\n견적서 작업 부탁합니다. (현장 지시 완료)'}]
    run(row,messages,send)
    send.assert_not_called()
    assert '현장 지시' in row['item_review_holds']['0']['reason']
    assert k.read_json(k.STATE,{})['1']['status']=='미응답 보류'


def test_fixed_slot_performs_two_checks_without_legacy_30min_delay(case,monkeypatch):
    row,target,messages,send=case
    row.pop('missing_absence_checks')
    monkeypatch.setattr(k,'config',lambda:{'enabled':True,'target':'현장방',
        'approval_missing_recheck':True,'approval_missing_recheck_mode':'fixed',
        'approval_missing_recheck_sec':1800})
    args=({'ABC':row},Mock(return_value='현장방 님과 카카오톡 대화'),send,
          lambda:False,lambda **kwargs:list(messages))
    result=a.recheck_missing(*args,schedule_slot='1970-01-01:13:00',request_id='ABC')
    assert result['status']=='awaiting_confirmation';send.assert_not_called()
    monkeypatch.setattr(a.time,'time',lambda:10061)
    result=a.recheck_missing(*args,schedule_slot='1970-01-01:13:00',request_id='ABC')
    assert result['status']=='reminder_sent';send.assert_called_once()
