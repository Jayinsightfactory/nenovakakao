import json
from unittest.mock import Mock
from core import inbound_archive_queue as queue


def test_uncertain_submission_is_not_replayed(tmp_path, monkeypatch):
    monkeypatch.setattr(queue, 'QUEUE', tmp_path)
    queue.enqueue({'event_id': 'a'})
    post = Mock(side_effect=RuntimeError('timeout'))
    queue.drain(post, lambda: False)
    queue.enqueue({'event_id': 'a'})
    queue.drain(post, lambda: False)
    post.assert_called_once()
    assert json.loads(next(tmp_path.glob('*.json')).read_text())['status'] == 'unknown'


def test_pause_retains_pending_and_success_acknowledges(tmp_path, monkeypatch):
    monkeypatch.setattr(queue, 'QUEUE', tmp_path)
    queue.enqueue({'event_id': 'a'})
    post = Mock()
    queue.drain(post, lambda: True)
    post.assert_not_called()
    queue.drain(post, lambda: False)
    post.assert_called_once_with({'event_id': 'a'})
    assert not list(tmp_path.glob('*.json'))
