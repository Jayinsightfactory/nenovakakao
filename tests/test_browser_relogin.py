from unittest.mock import Mock

import pytest

from core import browser_relogin as r
from core import browser_session_auth as a
from core.atomic_json import save


@pytest.fixture
def recovery(monkeypatch, tmp_path):
    monkeypatch.setattr(a, 'path', lambda: tmp_path / 'auth.json')
    monkeypatch.setattr('core.defect_approval.load', lambda _: {'automatic_relogin': True})
    notify = Mock()
    monkeypatch.setattr('core.error_notifications.notify', notify)
    save(a.path(), {'blocked': True, 'next_probe_at': 9999999999})
    return notify


def test_ui_success_still_blocks_writes_until_read_succeeds(recovery, monkeypatch):
    login = Mock()
    monkeypatch.setattr(r, 'login', login)
    r.maybe_recover()
    assert a.state()['blocked']
    assert a.state()['next_probe_at'] == 0
    with pytest.raises(a.SessionExpired):
        a.guard('POST')
    r.maybe_recover()
    login.assert_called_once()
    recovery.assert_not_called()


def test_failure_throttles_retries_and_notifies_once(recovery, monkeypatch):
    login = Mock(side_effect=RuntimeError('no window'))
    monkeypatch.setattr(r, 'login', login)
    r.maybe_recover()
    r.maybe_recover()
    login.assert_called_once()
    state = a.state()
    state['relogin_retry_at'] = 0
    save(a.path(), state)
    r.maybe_recover()
    assert login.call_count == 2
    recovery.assert_called_once()


def test_pause_propagates_without_notification(recovery, monkeypatch):
    from core.moyi_control import OperationPaused
    monkeypatch.setattr(r, 'login', Mock(side_effect=OperationPaused('stop')))
    with pytest.raises(OperationPaused):
        r.maybe_recover()
    recovery.assert_not_called()


def test_automatic_recovery_suppresses_initial_manual_login_notice(recovery, monkeypatch):
    monkeypatch.setattr(a, 'automatic_relogin_enabled', lambda: True)
    save(a.path(), {})
    with pytest.raises(a.SessionExpired):
        a.expired()
    recovery.assert_not_called()


def test_offscreen_document_rejected_even_when_child_looks_visible():
    bounds = Mock(left=0, right=1280, top=0, bottom=900)
    doc = Mock()
    doc.element_info.control_type = 'Document'
    doc.rectangle.return_value = Mock(left=0, right=1920, top=0, bottom=900)
    child = Mock()
    child.parent.return_value = doc
    assert not r._active_document(child, bounds)


@pytest.mark.parametrize('url', ['http://nenovaweb.com/login', 'https://nenovaweb.com.evil.example/login'])
def test_wrong_origin_rejected(url):
    field = Mock()
    field.element_info.name = 'Address and search bar'
    field.get_value.return_value = url
    window = Mock()
    window.descendants.return_value = [field]
    with pytest.raises(RuntimeError):
        r._url(window)
