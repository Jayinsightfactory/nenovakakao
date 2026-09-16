import json
from pathlib import Path
from unittest.mock import Mock
import pytest
from core import keyword_approval as approval, atomic_json, moyi_worker as worker


def reply(key, text, sender=approval.APPROVER):
    return {'event_id': key, 'sender_name': sender, 'content': text}


def test_short_reply_is_bound_to_latest_visible_request():
    row = {'id': 'ABC', 'request_event_id': 'prompt'}
    prompt = reply('prompt', '[전달 승인 ABC]', '네노바')
    assert approval.decision([prompt, reply('yes', '보내')], row, True) == '보내'
    later = reply('later', '[전달 승인 DEF]', '네노바')
    assert approval.decision([prompt, later, reply('yes', '보내')], row, True) is None
    assert approval.decision([prompt, later, reply('yes', '보내 ABC')], row, True) == '보내'
    assert approval.decision([prompt, reply('yes', '보내', '다른사람')], row, True) is None
    assert approval.decision([reply('yes', '보내'), prompt], row, True) is None


def test_short_batch_number_before_next_prompt():
    row = {'id': 'ABC', 'request_event_id': 'prompt', 'events': [{}, {}]}
    prompt = reply('prompt', '[전달 승인 요청 ABC]', '네노바')
    assert approval.decision([prompt, reply('no', '4')], row, True) == []
    assert approval.decision([prompt, reply('yes', '3')], row, True) == [0, 1]
    assert approval.decision([prompt, reply('bad', '9')], row, True) is None


def test_waiting_question_does_not_block_newer_queued_question(monkeypatch, tmp_path):
    from core import keyword_forward as forwarding
    monkeypatch.setattr(approval, 'REQUESTS', tmp_path / 'approvals.json')
    monkeypatch.setattr(forwarding, 'STATE', tmp_path / 'forward.json')
    monkeypatch.setattr(forwarding, 'CONFIG', tmp_path / 'config.json')
    forwarding.save_json(forwarding.CONFIG, {'enabled': True, 'source': '영업방',
        'target': '현장 추가취소방', 'keywords': ['추가', '취소', '변경'],
        'start_at': '2026-08-26T09:00:00+09:00'})
    old_event = {'event_id': 'old', 'sender_name': '직원', 'content': '기존 장미 추가',
                 'timestamp': '2026년 8월 26일 오전 9:05'}
    new_event = {'event_id': 'new', 'sender_name': '직원', 'content': '신규 장미 추가',
                 'timestamp': '2026년 8월 26일 오전 9:10'}
    forwarding.save_json(approval.REQUESTS, {
        'OLD': {'id': 'OLD', 'event': old_event, 'status': 'waiting', 'created_at': 1,
                'request_event_id': 'old-prompt', 'sent_at': 1, 'baseline': []},
        'NEW': {'id': 'NEW', 'event': new_event, 'status': 'queued', 'created_at': 2},
    })
    histories = {
        approval.APPROVER: approval.APPROVER + ' 님과 카카오톡 대화\n[나] [오전 9:00] old',
        '현장 추가취소방': '현장 추가취소방 님과 카카오톡 대화\n[직원] [오전 9:00] 기존',
    }
    sent = []
    def send(room, body):
        sent.append((room, body)); histories[room] += '\n[나] [오전 9:11] ' + body
    approval.poll(histories.__getitem__, send, lambda: False, Mock())
    assert sent and '[전달 승인 요청 NEW]' in sent[0][1]


def test_atomic_save_retries_only_local_sharing_lock(tmp_path, monkeypatch):
    target = tmp_path / 'state.json'
    original = Path.replace
    calls = []
    def replace(path, destination):
        calls.append(path)
        if len(calls) < 3:
            raise PermissionError('sharing violation')
        return original(path, destination)
    monkeypatch.setattr(Path, 'replace', replace)
    monkeypatch.setattr(atomic_json.time, 'sleep', Mock())
    atomic_json.save(target, {'state': 'safe'})
    assert json.loads(target.read_text()) == {'state': 'safe'}
    assert len(calls) == 3 and list(tmp_path.glob('*.tmp')) == []


def test_atomic_save_failure_preserves_original(tmp_path, monkeypatch):
    target = tmp_path / 'state.json'
    target.write_text('{"old": true}')
    monkeypatch.setattr(Path, 'replace', Mock(side_effect=PermissionError('locked')))
    monkeypatch.setattr(atomic_json.time, 'sleep', Mock())
    with pytest.raises(PermissionError):
        atomic_json.save(target, {'new': True})
    assert json.loads(target.read_text()) == {'old': True}
    assert not list(tmp_path.glob('*.tmp'))


def test_sales_and_import_precede_each_background_room():
    rooms = [{'exact_title': t} for t in ['현장방', '영업방', '수입방', '견적방']]
    assert [r['exact_title'] for r in worker._inbound_schedule(rooms)] == [
        '영업방', '수입방', '현장방', '영업방', '수입방', '견적방']


def test_stage_timing_records_failure_without_payload(monkeypatch):
    emit = Mock()
    monkeypatch.setattr(worker, '_event', emit)
    with pytest.raises(RuntimeError):
        worker._timed('approval', Mock(side_effect=RuntimeError('private payload')))
    assert 'error' in emit.call_args.args[2]
    assert 'private payload' not in emit.call_args.args[2]


@pytest.mark.parametrize('review_only', [False, True])
def test_worker_checks_replies_between_lower_priority_stages(monkeypatch, tmp_path, review_only):
    from core import import_order, moyi_inbound, mindmap_sink, error_notifications
    from core import workflow_settings, order_review
    settings = workflow_settings.config()
    monkeypatch.setattr(workflow_settings, 'config', lambda: dict(settings, order_review_only=review_only))
    stages = []
    clock = [100.0]
    monkeypatch.setattr(worker, '_config', lambda: ('https://example.test', 'test'))
    monkeypatch.setattr(worker, 'is_paused', lambda: False)
    monkeypatch.setattr(worker, '_event', Mock())
    monkeypatch.setattr(worker, 'AGENT_LOG', tmp_path / 'agents.jsonl')
    monkeypatch.setattr(worker.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(approval, 'poll', lambda *args: stages.append('approval'))
    monkeypatch.setattr(error_notifications, 'poll', lambda *args, **kwargs:
                        stages.append('receipts') if kwargs.get('receipts_only') else None)
    monkeypatch.setattr(import_order, 'poll', lambda *args: stages.append('order'))
    def get(url, **kwargs):
        if url.endswith('/pending'):
            stages.append('pending'); clock[0] += 6
            assert kwargs['params']['limit'] == 1
            return Mock(json=lambda: {'items': []})
        return Mock(json=lambda: {'items': [{'exact_title': '수입방'}]})
    monkeypatch.setattr(worker.requests, 'get', get)
    def inbound(*args, **kwargs):
        stages.append('sales' if kwargs.get('only_title') == '영업방' else 'inbound'); clock[0] += 6
        assert kwargs['defer_archive'] is True and kwargs['max_events'] == 5
        return {'sent': 0, 'initialized': 0}
    monkeypatch.setattr(moyi_inbound, 'poll_once', inbound)
    monkeypatch.setattr(mindmap_sink, 'flush_pending', lambda **kwargs: stages.append('archive'))
    class EndCycle(Exception): pass
    monkeypatch.setattr(worker.time, 'sleep', Mock(side_effect=EndCycle))
    with pytest.raises(EndCycle): worker.run()
    assert stages == ['approval', 'receipts', 'sales', 'inbound'] + ([] if review_only else ['order']) + [
        'pending', 'approval', 'receipts', 'sales', 'approval', 'receipts', 'sales', 'archive']
    order_review.start_sync.assert_called_once()
