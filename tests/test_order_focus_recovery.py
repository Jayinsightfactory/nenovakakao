from unittest.mock import Mock
import pytest
from core import safe_worker_room as room
from core import import_order as order

@pytest.mark.parametrize("recovered", [True, False])
def test_activation_exception_requires_verified_focus(monkeypatch, recovered):
    window = Mock(_hWnd=123)
    monkeypatch.setattr(room.time, 'sleep', lambda _: None)
    monkeypatch.setattr(room.pyautogui, 'press', Mock())
    monkeypatch.setattr(room.win32gui, 'ShowWindow', Mock())
    activate = Mock(side_effect=RuntimeError('1421'))
    monkeypatch.setattr(room.win32gui, 'SetForegroundWindow', activate)
    checks = iter([False, recovered, False])
    monkeypatch.setattr(room, '_foreground_belongs_to', lambda _: next(checks))
    if recovered:
        room._activate_verified(window)
        assert activate.call_count == 1
    else:
        with pytest.raises(RuntimeError, match='입력·전송 차단'):
            room._activate_verified(window)
        assert activate.call_count == 2

@pytest.mark.parametrize("issues", [[], ['1번 거래처 확인 필요']])
def test_registration_invitation_requires_complete_draft(monkeypatch, issues):
    monkeypatch.setattr(order, 'config', lambda: {'browser_order_registration': True})
    monkeypatch.setattr(order, 'validate', lambda _: issues)
    text = order.review_message({'id': 'ORD-test', 'week': '41-1', 'items': []})
    assert ('확인 → 주문등록' in text) == (not issues)
    assert ('미등록:' in text) == bool(issues)
