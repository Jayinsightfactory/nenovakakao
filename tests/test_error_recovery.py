from unittest.mock import Mock
import pytest
import requests
from core import moyi_worker as worker, window_detector as windows


def http_error(code):
    response = requests.Response(); response.status_code = code
    return requests.HTTPError(response=response)


def test_pending_outage_backs_off_and_reports_recovery_once(monkeypatch):
    clock = [0]
    monkeypatch.setattr(worker.time, 'monotonic', lambda: clock[0])
    event = Mock(); monkeypatch.setattr(worker, '_event', event)
    report = Mock(); monkeypatch.setattr('core.error_notifications.report', report)
    get = Mock(side_effect=[http_error(502), http_error(502), Mock(json=lambda: {'items': []})])
    monkeypatch.setattr(worker.requests, 'get', get)
    poller = worker.PendingPoller()
    assert poller.fetch('https://example.test', 'secret') == []
    assert poller.next_at == 5
    clock[0] = 4
    assert poller.fetch('https://example.test', 'secret') == [] and get.call_count == 1
    clock[0] = 5; poller.fetch('https://example.test', 'secret')
    assert poller.next_at == 15
    clock[0] = 15; poller.fetch('https://example.test', 'secret')
    assert poller.failures == 0
    assert sum(c.args[1] == 'pending_unavailable' for c in event.call_args_list) == 1
    report.assert_called_once_with('server_recovered')


def test_auth_failure_never_retried(monkeypatch):
    monkeypatch.setattr(worker.requests, 'get', Mock(side_effect=http_error(403)))
    monkeypatch.setattr(worker, '_event', Mock())
    with pytest.raises(requests.HTTPError): worker.PendingPoller().fetch('https://example.test', 'secret')


def test_main_window_requires_exact_unique_title(monkeypatch):
    real = Mock(title='카카오톡', width=400, height=600)
    other = Mock(title='카카오톡 도움말', width=1200, height=900)
    monkeypatch.setattr(windows.gw, 'getWindowsWithTitle', lambda _: [other, real])
    assert windows._exact_main_window() is real
    monkeypatch.setattr(windows.gw, 'getWindowsWithTitle', lambda _: [other])
    with pytest.raises(RuntimeError): windows._exact_main_window()
    monkeypatch.setattr(windows.gw, 'getWindowsWithTitle', lambda _: [real, real])
    with pytest.raises(RuntimeError): windows._exact_main_window()


def test_foreground_waits_for_transition_without_input(monkeypatch):
    import win32gui
    clock = [0]
    monkeypatch.setattr(windows.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(windows.time, 'sleep', lambda delay: clock.__setitem__(0, clock[0] + delay))
    monkeypatch.setattr(win32gui, 'GetForegroundWindow', lambda: 2 if clock[0] >= 0.2 else 1)
    assert windows._wait_for_foreground(2)
    assert not windows._wait_for_foreground(3, timeout=0.1)


def test_room_circuit_breaker_quarantines_only_repeated_failure():
    breaker = worker.RoomCircuitBreaker()
    assert not breaker.failed('실패방', 0)
    assert not breaker.failed('실패방', 1)
    assert breaker.failed('실패방', 2)
    assert not breaker.available('실패방', 3)
    assert breaker.available('정상방', 3)
    assert breaker.available('실패방', 1802)


def test_room_success_resets_failure_count():
    breaker = worker.RoomCircuitBreaker()
    breaker.failed('방', 0); breaker.failed('방', 1)
    breaker.succeeded('방')
    assert not breaker.failed('방', 2)


def test_open_existing_exact_room_skips_main_window_activation(monkeypatch):
    from core import safe_worker_room as rooms
    existing = Mock(visible=True, title='영업방', width=500, height=600, _hWnd=77)
    monkeypatch.setattr(rooms.gw, 'getAllWindows', lambda: [existing])
    activate_main = Mock()
    monkeypatch.setattr(rooms, 'activate_kakaotalk', activate_main)
    monkeypatch.setattr(rooms, '_activate_verified', Mock())
    monkeypatch.setattr(rooms, '_foreground_belongs_to', lambda _: True)
    monkeypatch.setattr(rooms.win32gui, 'GetWindowText', lambda _: '영업방')
    assert rooms.open_unique_exact_room('영업방') == 77
    activate_main.assert_not_called()
