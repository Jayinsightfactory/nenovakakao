import pytest
from core import order_analysis_queue as queue


def test_capture_failure_keeps_original_for_retry(tmp_path, monkeypatch):
    monkeypatch.setattr(queue, 'QUEUE', tmp_path)
    event = {'event_id': 'a', 'content': 'order'}
    queue.enqueue(event)
    queue.enqueue(event)
    assert len(list(tmp_path.glob('*.json'))) == 1
    def fail(event):
        raise RuntimeError('offline')
    with pytest.raises(RuntimeError):
        queue.drain(fail, lambda: False)
    saved = []
    queue.drain(saved.append, lambda: True)
    assert not saved
    queue.drain(saved.append, lambda: False)
    assert saved == [event]
    assert not list(tmp_path.glob('*.json'))


def test_enqueue_does_not_invoke_analysis(tmp_path, monkeypatch):
    monkeypatch.setattr(queue, 'QUEUE', tmp_path)
    from core import import_order
    def forbidden(*args):
        raise AssertionError('analysis must not run in collector')
    monkeypatch.setattr(import_order, 'capture', forbidden)
    queue.enqueue({'event_id': 'new', 'content': 'raw'})
    assert len(list(tmp_path.glob('*.json'))) == 1


def test_collection_queues_before_checkpoint_and_retains_on_disk_error(tmp_path, monkeypatch):
    from unittest.mock import Mock
    from core import moyi_inbound as inbound, import_order, workflow_settings, keyword_forward
    monkeypatch.setattr(inbound, 'STATE_FILE', tmp_path / 'inbound.json')
    monkeypatch.setattr(queue, 'QUEUE', tmp_path / 'queue')
    monkeypatch.setattr(workflow_settings, 'config', lambda: {'order_review_only': True})
    monkeypatch.setattr(import_order, 'config', lambda: {'enabled': True, 'source': '수입방'})
    monkeypatch.setattr(keyword_forward, 'config', lambda: {'enabled': False})
    monkeypatch.setattr(import_order, 'capture', Mock(side_effect=AssertionError('blocking capture')))
    rooms = Mock(json=lambda: {'items': [{'room_binding_id': 'b', 'exact_title': '수입방'}]})
    monkeypatch.setattr(inbound.requests, 'get', Mock(return_value=rooms))
    monkeypatch.setattr(inbound.requests, 'post', Mock(return_value=Mock()))
    monkeypatch.setattr(inbound, 'has_unread_exact_room', lambda title: True)
    monkeypatch.setattr(inbound, 'export_exact_room', lambda title: 'raw')
    event = {'event_id': 'new', 'content': 'order', 'sender_name': 'staff', 'timestamp': 't'}
    monkeypatch.setattr(inbound, 'parse_export', lambda *args: [event])
    monkeypatch.setattr('core.mindmap_sink.enqueue_events', Mock())
    inbound._save_state({'b': ['old']})
    inbound.poll_once('https://example.test', 'test', only_title='수입방', defer_archive=True)
    assert list(queue.QUEUE.glob('*.json'))
    assert 'new' in inbound._load_state()['b']
    from core import inbound_archive_queue
    assert list(inbound_archive_queue.QUEUE.glob('*.json'))
    assert not any(call.args[0].endswith('/kakao/agent/inbound')
                   for call in inbound.requests.post.call_args_list)
    inbound._save_state({'b': ['old']})
    monkeypatch.setattr(queue, 'enqueue', Mock(side_effect=OSError('disk full')))
    with pytest.raises(OSError):
        inbound.poll_once('https://example.test', 'test', only_title='수입방', defer_archive=True)
    assert 'new' not in inbound._load_state()['b']
