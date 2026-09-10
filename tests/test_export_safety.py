from unittest.mock import Mock
import pytest
from PIL import Image, ImageDraw
from core import moyi_inbound as inbound, moyi_control, safe_worker_room
from core.badge_monitor import detect_badge_positions

import win32con


def test_exact_search_badge_scan_can_exclude_bottom_ad(tmp_path):
    image = Image.new('RGB', (400, 800), 'white')
    draw = ImageDraw.Draw(image)
    draw.ellipse((340, 70, 360, 90), fill=(240, 40, 40))
    draw.rectangle((300, 700, 380, 760), fill=(240, 40, 40))
    path = tmp_path / 'room_search.png'
    image.save(path)

    assert len(detect_badge_positions(path)) == 2
    assert detect_badge_positions(path, y_end_ratio=0.35) == [80]


@pytest.fixture
def export_io(monkeypatch):
    monkeypatch.setattr(moyi_control, 'is_paused', lambda: False)
    monkeypatch.setattr(inbound, '_txt_files', Mock(return_value={}))
    monkeypatch.setattr(inbound, '_open_or_reuse_exact_room', Mock(return_value=123))
    monkeypatch.setattr(inbound.win32process, 'GetWindowThreadProcessId', lambda _: (1, 2))
    monkeypatch.setattr(inbound, '_visible_dialogs_for_process', lambda _: set())
    monkeypatch.setattr(inbound.win32gui, 'GetWindowText', lambda _: '대상방')
    monkeypatch.setattr(inbound.win32gui, 'IsWindowEnabled', lambda _: True)
    monkeypatch.setattr(inbound.win32gui, 'IsWindow', lambda _: True)
    monkeypatch.setattr(inbound.win32gui, 'IsWindowVisible', lambda _: True)
    monkeypatch.setattr(inbound.win32gui, 'GetWindow', lambda hwnd, cmd: 0)
    monkeypatch.setattr(safe_worker_room, '_foreground_belongs_to', lambda _: True)
    hotkey = Mock()
    monkeypatch.setattr(inbound.pyautogui, 'hotkey', hotkey)
    monkeypatch.setattr(inbound, '_dismiss_export_complete_dialog', Mock())
    monkeypatch.setattr(inbound, 'close_room', Mock())
    return hotkey


def test_paused_export_does_not_touch_ui_or_files(export_io, monkeypatch):
    monkeypatch.setattr(moyi_control, 'is_paused', lambda: True)
    with pytest.raises(RuntimeError, match='일시정지'):
        inbound.export_exact_room('대상방')
    inbound._txt_files.assert_not_called()
    inbound._open_or_reuse_exact_room.assert_not_called()
    export_io.assert_not_called()


def test_pause_during_file_scan_does_not_open_room(export_io, monkeypatch):
    def scan():
        monkeypatch.setattr(moyi_control, 'is_paused', lambda: True)
        return {}
    monkeypatch.setattr(inbound, '_txt_files', scan)
    with pytest.raises(RuntimeError, match='일시정지'):
        inbound.export_exact_room('대상방')
    inbound._open_or_reuse_exact_room.assert_not_called()
    export_io.assert_not_called()


@pytest.mark.parametrize('wrong', ['focus', 'title'])
def test_wrong_target_never_receives_save_shortcut(export_io, monkeypatch, wrong):
    if wrong == 'focus':
        monkeypatch.setattr(safe_worker_room, '_foreground_belongs_to', lambda _: False)
    else:
        monkeypatch.setattr(inbound.win32gui, 'GetWindowText', lambda _: '다른방')
    with pytest.raises(RuntimeError, match='Ctrl\\+S 미입력'):
        inbound.export_exact_room('대상방')
    export_io.assert_not_called()


def test_scan_finishes_before_focus_and_pause_stops_dialog(export_io, monkeypatch):
    order = []
    monkeypatch.setattr(inbound, '_txt_files', lambda: order.append('scan') or {})
    monkeypatch.setattr(inbound, '_open_or_reuse_exact_room', lambda _: order.append('open') or 123)
    def pause(*args):
        monkeypatch.setattr(moyi_control, 'is_paused', lambda: True)
    export_io.side_effect = pause
    with pytest.raises(RuntimeError, match='일시정지'):
        inbound.export_exact_room('대상방')
    assert order == ['scan', 'open']
    export_io.assert_called_once_with('ctrl', 's')
    inbound.close_room.assert_not_called()


# ── new: save-dialog safety gate tests ──


@pytest.fixture
def dialog_env(monkeypatch):
    """Provide a controllable _save_export_dialog environment.

    The fixture makes ``_visible_dialogs_for_process`` return a single dialog
    (hwnd 500) so the wait-loop inside ``_save_export_dialog`` finds a dialog
    immediately.  Safety-gate stubs default to the happy path — individual
    tests override the specific stub they want to break.
    """
    DIALOG_HWND = 500
    monkeypatch.setattr(moyi_control, 'is_paused', lambda: False)
    monkeypatch.setattr(inbound.win32process, 'GetWindowThreadProcessId', lambda _: (1, 2))
    monkeypatch.setattr(inbound, '_visible_dialogs_for_process', lambda _: {DIALOG_HWND})
    monkeypatch.setattr(inbound.win32gui, 'IsWindow', lambda _: True)
    monkeypatch.setattr(inbound.win32gui, 'IsWindowVisible', lambda _: True)
    monkeypatch.setattr(inbound.win32gui, 'GetWindow', lambda hwnd, cmd: 123)
    monkeypatch.setattr(inbound.win32gui, 'GetWindowText', lambda h: '다른 이름으로 저장' if h == 500 else '대상방')
    monkeypatch.setattr(inbound.win32gui, 'ShowWindow', Mock())
    monkeypatch.setattr(inbound.win32gui, 'SetForegroundWindow', Mock())
    monkeypatch.setattr(inbound.win32gui, 'GetDlgItem', lambda dlg, btn_id: 999)
    send_message = Mock()
    monkeypatch.setattr(inbound.win32api, 'SendMessage', send_message)
    return send_message


def test_dialog_owner_mismatch_blocks_save(dialog_env, monkeypatch):
    """Save dialog owned by a different chat window must not be confirmed."""
    monkeypatch.setattr(
        inbound.win32gui, 'GetWindow',
        lambda hwnd, cmd: 777 if cmd == win32con.GW_OWNER else 0,
    )
    with pytest.raises(RuntimeError, match='different chat window'):
        inbound._save_export_dialog(
            chat_hwnd=123, title='대상방', dialogs_before=set(), timeout=0.1,
        )
    dialog_env.assert_not_called()


def test_title_change_before_save_click_blocks(dialog_env, monkeypatch):
    """Room title changing between Ctrl+S and Save click must abort."""
    monkeypatch.setattr(inbound.win32gui, 'GetWindowText', lambda h: '다른 이름으로 저장' if h == 500 else '다른방')
    with pytest.raises(RuntimeError, match='방 제목 변경'):
        inbound._save_export_dialog(
            chat_hwnd=123, title='대상방', dialogs_before=set(), timeout=0.1,
        )
    dialog_env.assert_not_called()


def test_dialog_vanished_before_click_blocks(dialog_env, monkeypatch):
    """Dialog closed by user before we click Save must raise, not proceed."""
    monkeypatch.setattr(inbound.win32gui, 'IsWindow', lambda h: False)
    monkeypatch.setattr(inbound.win32gui, 'IsWindowVisible', lambda h: False)
    with pytest.raises(RuntimeError, match='closed unexpectedly'):
        inbound._save_export_dialog(
            chat_hwnd=123, title='대상방', dialogs_before=set(), timeout=0.1,
        )
    dialog_env.assert_not_called()


def test_dialog_failed_skips_close_room(export_io, monkeypatch):
    """If the save dialog raises, close_room must NOT be called (dialog may still be open)."""
    def fail_dialog(*args, **kwargs):
        raise RuntimeError('Kakao Save As dialog was not opened')
    monkeypatch.setattr(inbound, '_save_export_dialog', fail_dialog)
    with pytest.raises(RuntimeError, match='was not opened'):
        inbound.export_exact_room('대상방')
    inbound.close_room.assert_not_called()


def test_unknown_owner_blocks_save(dialog_env, monkeypatch):
    monkeypatch.setattr(inbound.win32gui, 'GetWindow', lambda *args: 0)
    with pytest.raises(RuntimeError, match='different chat window'):
        inbound._save_export_dialog(123, '대상방', set(), timeout=0.1)
    dialog_env.assert_not_called()


def test_export_completion_modal_blocks_new_shortcut(export_io, monkeypatch):
    monkeypatch.setattr(inbound.win32gui, 'IsWindowEnabled', lambda _: False)
    monkeypatch.setattr(inbound, '_dismiss_export_complete_dialog', Mock(
        side_effect=RuntimeError('대화 저장 완료 안내 확인 실패')))
    with pytest.raises(RuntimeError, match='완료 안내'):
        inbound.export_exact_room('대상방')
    export_io.assert_not_called()


def test_export_clears_verified_stale_completion_before_shortcut(export_io, monkeypatch):
    enabled = [False]
    monkeypatch.setattr(inbound.win32gui, 'IsWindowEnabled', lambda _: enabled[0])
    dismiss = Mock(side_effect=lambda *args, **kwargs: enabled.__setitem__(0, True))
    monkeypatch.setattr(inbound, '_dismiss_export_complete_dialog', dismiss)
    monkeypatch.setattr(inbound, '_save_export_dialog', Mock(
        side_effect=RuntimeError('stop after shortcut')))
    with pytest.raises(RuntimeError, match='stop after shortcut'):
        inbound.export_exact_room('대상방')
    dismiss.assert_called_once_with(123, '대상방', set(), foreground_before=123, timeout=1.0)
    export_io.assert_called_once_with('ctrl', 's')


def test_unrelated_dialog_blocks_save(dialog_env, monkeypatch):
    monkeypatch.setattr(inbound.win32gui, 'GetWindowText', lambda h: '삭제 확인' if h == 500 else '대상방')
    with pytest.raises(RuntimeError, match='not a verified Save As'):
        inbound._save_export_dialog(123, '대상방', set(), timeout=0.1)
    dialog_env.assert_not_called()


def test_verified_export_completion_dialog_is_dismissed(dialog_env, monkeypatch):
    monkeypatch.setattr(inbound.win32gui, 'GetWindowText',
                        lambda h: '대화 내보내기' if h == 500 else '대상방')
    monkeypatch.setattr(inbound.win32gui, 'IsWindow', lambda _: not dialog_env.called)
    monkeypatch.setattr(inbound.win32gui, 'IsWindowVisible', lambda _: not dialog_env.called)
    inbound._dismiss_export_complete_dialog(123, '대상방', set(), timeout=0.1)
    dialog_env.assert_called_once_with(999, win32con.BM_CLICK, 0, 0)


def test_foreground_kakao_completion_window_is_accepted(dialog_env, monkeypatch):
    monkeypatch.setattr(inbound, '_visible_dialogs_for_process', lambda _: set())
    monkeypatch.setattr(inbound.win32gui, 'GetForegroundWindow', lambda: 500)
    monkeypatch.setattr(inbound.win32gui, 'GetWindowText',
                        lambda h: '대화 내보내기' if h == 500 else '대상방')
    monkeypatch.setattr(inbound.win32gui, 'IsWindow', lambda _: not dialog_env.called)
    monkeypatch.setattr(inbound.win32gui, 'IsWindowVisible', lambda _: not dialog_env.called)
    inbound._dismiss_export_complete_dialog(123, '대상방', set(), timeout=0.1)
    dialog_env.assert_called_once()


def test_kakao_custom_completion_window_requires_exact_controls(dialog_env, monkeypatch):
    monkeypatch.setattr(inbound, '_visible_dialogs_for_process', lambda _: set())
    monkeypatch.setattr(inbound.win32gui, 'GetForegroundWindow', lambda: 500)
    monkeypatch.setattr(inbound.win32gui, 'GetWindowText', lambda h: '대상방' if h == 123 else '')
    monkeypatch.setattr(inbound.win32gui, 'GetClassName', lambda _: 'EVA_Window_Dblclk')
    monkeypatch.setattr(inbound, '_child_controls', lambda _: {})
    monkeypatch.setattr(inbound.win32gui, 'GetDlgItem', lambda *args: 0)
    monkeypatch.setattr(inbound.win32gui, 'IsWindowEnabled', lambda _: False)
    press = Mock()
    monkeypatch.setattr(inbound.pyautogui, 'press', press)
    monkeypatch.setattr(inbound.win32gui, 'IsWindow', lambda _: not press.called)
    monkeypatch.setattr(inbound.win32gui, 'IsWindowVisible', lambda _: not press.called)
    inbound._dismiss_export_complete_dialog(123, '대상방', set(), foreground_before=123, timeout=0.1)
    press.assert_called_once_with('enter')
    dialog_env.assert_not_called()


def test_missing_completion_dialog_is_ok_only_when_room_reenabled(dialog_env, monkeypatch):
    monkeypatch.setattr(inbound, '_visible_dialogs_for_process', lambda _: set())
    monkeypatch.setattr(inbound.win32gui, 'IsWindowEnabled', lambda _: True)
    inbound._dismiss_export_complete_dialog(123, '대상방', set(), timeout=0)
    monkeypatch.setattr(inbound.win32gui, 'IsWindowEnabled', lambda _: False)
    with pytest.raises(RuntimeError, match='완료 안내 확인 실패'):
        inbound._dismiss_export_complete_dialog(123, '대상방', set(), timeout=0)
