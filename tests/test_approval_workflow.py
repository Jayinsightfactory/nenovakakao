from unittest.mock import Mock
import pytest
from core import keyword_approval as a, keyword_forward as k, error_notifications as n
from core.moyi_inbound import parse_export


def test_unique_labels_persist_and_do_not_reuse_legacy_names():
    old = {'id': 'OLD', 'event': {}, 'events': [{}, {}]}
    first = {'id': 'FIRST', 'event': {}}
    rows = {'OLD': old, 'FIRST': first}
    a.assign_item_labels(first, rows)
    assert first['item_labels'] == ['1가']
    second = {'id': 'SECOND', 'event': {}}
    rows['SECOND'] = second
    a.assign_item_labels(second, rows)
    assert second['item_labels'] == ['2가']
    a.assign_item_labels(first, rows)
    assert first['item_labels'] == ['1가']


@pytest.mark.parametrize('uncertain', [False, True])
def test_multiple_late_questions_to_delivery_and_receipts(tmp_path, monkeypatch, uncertain):
    monkeypatch.setattr(k, 'CONFIG', tmp_path / 'config.json')
    monkeypatch.setattr(k, 'STATE', tmp_path / 'routes.json')
    monkeypatch.setattr(k, 'LOG', tmp_path / 'routes.jsonl')
    k.save_json(k.CONFIG, {'enabled': True, 'source': '영업방', 'target': '현장방',
        'keywords': ['추가'], 'start_at': '2026-09-01T00:00:00+09:00'})
    header = '--------------- 2026년 9월 14일 월요일 ---------------\n'
    histories = {a.APPROVER: a.APPROVER+' 님과 카카오톡 대화\n'+header,
                 '현장방': '현장방 님과 카카오톡 대화\n'+header+'[직원] [오전 9:00] 기존 대화'}
    rows = {}
    state = {}
    for rid, label, body in [('AAA111','다','장미 2박스 추가'),('BBB222','라','수국 1박스 추가')]:
        event = dict(event_id=rid, sender_name='직원', timestamp='2026년 9월 14일 오전 9:10', content=body)
        row = dict(id=rid, event=event, status='awaiting_late_reply', item_labels=[label],
            choice_format='per_item', baseline=[], created_at=1, sent_at=1)
        histories[a.APPROVER] += '\n[네노바] [오전 9:15] '+a.request_message(row)
        row['request_event_id'] = parse_export(histories[a.APPROVER], 'keyword-approval')[-1]['event_id']
        rows[rid] = row
        state[rid] = {'status': '미응답 보류'}
    histories[a.APPROVER] += '\n['+a.APPROVER+'] [오후 3:00] 다 보내 라 안보내'
    k.save_json(a.REQUESTS, rows)
    k.save_json(k.STATE, state)
    sent = []
    def send(room, text):
        sent.append((room, text))
        if room == '현장방' and uncertain:
            raise RuntimeError('send result unknown')
        histories[room] += '\n[네노바] [오후 3:01] '+text
    a.poll(histories.__getitem__, send, lambda: False, Mock())
    assert [room for room, _ in sent] == ['현장방']
    result = k.read_json(k.STATE, {})
    assert result['AAA111']['status'] == ('결과 불명' if uncertain else '전송 성공')
    assert k.read_json(a.REQUESTS, {})['AAA111']['status'] == ('delivery_held' if uncertain else 'resolved')
    assert result['BBB222']['status'] == '승인거절'
    for _ in range(3):
        n.poll(histories.__getitem__, send, lambda: False, receipts_only=True)
    assert len(sent) == (2 if uncertain else 3)
    assert all(r['status']=='sent' for r in n._rows().values() if r.get('kind')=='receipt')
    # Reload and replay exactly the same history: no second send or receipt.
    a.poll(histories.__getitem__, send, lambda: False, Mock())
    n.poll(histories.__getitem__, send, lambda: False, receipts_only=True)
    assert len(sent) == (2 if uncertain else 3)
    from core.approval_progress import snapshot
    assert {r['stage'] for r in snapshot()} == ({'결과 확인 필요', '완료'} if uncertain else {'완료'})


def test_verified_question_does_not_expire_at_midnight():
    from datetime import datetime
    row = {'id':'A', 'event': {'timestamp':'2026년 9월 13일 오후 3:00'},
           'status':'awaiting_late_reply', 'request_event_id':'prompt'}
    assert not a.hold_old_requests({'A':row}, {'approval_current_day_only':True}, datetime(2026,9,14))
    assert row['status'] == 'awaiting_late_reply'
