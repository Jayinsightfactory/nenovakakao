from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from core import moyi_inbound as inbound, safe_worker_room as rooms, operator_settings


def test_operator_uses_friends_even_if_same_title_window_exists(monkeypatch):
    operator_settings.configure('강현우')
    from core.window_detector import KakaoWindow
    main = KakaoWindow('카카오톡', 0, 0, 500, 800)
    monkeypatch.setattr('core.window_detector._exact_main_window', lambda: SimpleNamespace(_hWnd=1))
    monkeypatch.setattr('core.window_detector.activate_kakaotalk', Mock(return_value=main))
    monkeypatch.setattr(rooms, '_foreground_belongs_to', lambda hwnd: True)
    monkeypatch.setattr(inbound.time, 'sleep', Mock())
    monkeypatch.setattr(inbound.pyautogui, 'click', Mock())
    monkeypatch.setattr(inbound.pyautogui, 'doubleClick', Mock())
    monkeypatch.setattr(inbound, 'replace_room_search', Mock())
    chat = Mock(side_effect=AssertionError('chat search forbidden'))
    monkeypatch.setattr(inbound, 'open_room_by_name', chat)
    monkeypatch.setattr(inbound.gw, 'getAllWindows', Mock(side_effect=AssertionError('no reuse shortcut')))
    verified = Mock(return_value=99)
    monkeypatch.setattr(inbound, 'open_unique_exact_room', verified)
    assert inbound._open_or_reuse_exact_room('강현우') == 99
    inbound.pyautogui.click.assert_called_once_with(33, 57)
    inbound.replace_room_search.assert_called_once_with(main, '강현우')
    verified.assert_called_once_with('강현우', allow_main_activation=False, require_foreground=True)
    chat.assert_not_called()


@pytest.mark.parametrize('existing', [False, True])
def test_failed_friend_open_never_activates_group_or_chat_tab(monkeypatch, existing):
    window = SimpleNamespace(visible=True, title='강현우', width=500, height=600, _hWnd=99)
    monkeypatch.setattr(rooms.gw, 'getAllWindows', lambda: [window] if existing else [])
    monkeypatch.setattr(rooms, '_foreground_belongs_to', lambda _: False)
    monkeypatch.setattr(rooms.win32gui, 'GetWindowText', lambda _: '강현우')
    activate = Mock(side_effect=AssertionError('activation forbidden'))
    chat = Mock(side_effect=AssertionError('chat tab forbidden'))
    monkeypatch.setattr(rooms, '_activate_verified', activate)
    monkeypatch.setattr(rooms, 'activate_kakaotalk', chat)
    with pytest.raises(RuntimeError):
        rooms.open_unique_exact_room('강현우', allow_main_activation=False, require_foreground=True)
    activate.assert_not_called()
    chat.assert_not_called()
